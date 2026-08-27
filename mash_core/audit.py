"""Audit + PII-flagging wrapper around outbound cloud-LLM calls.

Contract: 2026-08-27-cloud-llm-call-audit-wrapper (slice 1). This is the shared
implementation; book-mash imports it through its ``mash-core`` path dependency,
so both repos get one copy of the logic. ai-native-org carries a standalone
mirror (``scripts/lib/llm_audit.py``) because it is not a Python package that
can depend on mash-core.

What it does, per real outbound call:
  1. bumps a per-repo call-count marker (``logs/.llm-call-marker.json``);
  2. appends one audit record to ``logs/llm-call-audit.jsonl``;
  3. runs the LOCAL, regex-only PII scan (``mash_core.pii``) over prompt+response
     and appends one entry per match to ``logs/llm-call-pii-flags.jsonl``
     (pattern name only — never the matched text);
  4. on a real (non-test) payload with any PII match, pages the ntfy channel
     immediately, sending only {repo, caller, pattern_matched, audit_record_ts}.

Two hard invariants from the contract:

* **Fail open.** A wrapper-internal error (disk failure, regex crash, malformed
  response) must NEVER block or degrade the underlying LLM call. Every audit
  step is individually guarded; a failure is logged loudly to stderr and to
  ``logs/llm-call-coverage-gaps.jsonl`` (sentinel-visible), never swallowed.
  The underlying ``agent.run`` exception path is left completely untouched.

* **Provably inert when off.** With ``AUDIT_WRAPPER_ENABLED=0`` the wrapper does
  nothing but forward to the original ``run_with_backoff`` call — no filesystem
  touch, no directory creation, no config read beyond the flag itself.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mash_core.pii import scan_text
from mash_core.retry import run_with_backoff

AUDIT_LOG_NAME = "llm-call-audit.jsonl"
PII_LOG_NAME = "llm-call-pii-flags.jsonl"
COVERAGE_GAP_LOG_NAME = "llm-call-coverage-gaps.jsonl"
MARKER_NAME = ".llm-call-marker.json"

# Set by configure(); pure in-memory, no filesystem side effect.
_REPO: str | None = None
_LOG_DIR: Path | None = None

# Serializes the marker read-modify-write and log appends within a process.
_LOCK = threading.Lock()

# Immediate-paging dedup within a single process run, keyed (repo, pattern).
# A book-mash run fans six judges across every unit; without this, one example
# email in a chapter would page once per judge per unit — a self-inflicted flood
# on a public ntfy topic. Exposed PII is a per-incident alert, not per-match:
# the first match of a pattern pages, every match is still written to the flag
# log, and the sentinel surfaces all of them. Cleared per-process only.
_PAGED: set[tuple[str, str]] = set()


def configure(repo: str, log_dir: str | os.PathLike[str]) -> None:
    """Declare the calling repo's identity and log directory.

    Records two module globals; performs NO filesystem work, so importing a repo
    that calls this at import time stays inert when the wrapper is disabled.
    """
    global _REPO, _LOG_DIR
    _REPO = repo
    _LOG_DIR = Path(log_dir)


def enabled() -> bool:
    val = os.environ.get("AUDIT_WRAPPER_ENABLED", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def _is_test_payload() -> bool:
    if os.environ.get("LLM_AUDIT_TEST_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    # Any run under pytest is a test payload — this is what stops the DoD-2/3
    # synthetic-PII tests from paging the operator's real channel.
    return "PYTEST_CURRENT_TEST" in os.environ


def _repo() -> str:
    if _REPO:
        return _REPO
    return os.environ.get("LLM_AUDIT_REPO", "unknown")


def _log_dir() -> Path:
    if os.environ.get("LLM_AUDIT_LOG_DIR"):
        return Path(os.environ["LLM_AUDIT_LOG_DIR"])
    if _LOG_DIR is not None:
        return _LOG_DIR
    return Path.cwd() / "logs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provider_for(model_id: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    if model_id.startswith("openrouter:"):
        return "openrouter"
    if model_id.startswith("openai-compatible:"):
        return "openai-compatible"
    return "anthropic"


def _infer_caller() -> str:
    """Best-effort module.qualname of the first frame outside this module."""
    try:
        for frame in inspect.stack()[1:]:
            mod = frame.frame.f_globals.get("__name__", "")
            if mod == __name__ or mod.startswith("mash_core.audit"):
                continue
            return f"{mod}.{frame.function}"
    except Exception:
        pass
    return "unknown"


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _log_coverage_gap(stage: str, error: BaseException, caller: str) -> None:
    """Loudly record a wrapper-internal failure. Never raises."""
    msg = f"LLM-AUDIT-COVERAGE-GAP repo={_repo()} caller={caller} stage={stage} error={type(error).__name__}: {error}"
    try:
        print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass
    try:
        _append_jsonl(
            _log_dir() / COVERAGE_GAP_LOG_NAME,
            {"ts": _now(), "repo": _repo(), "caller": caller, "stage": stage,
             "error": f"{type(error).__name__}: {error}"},
        )
    except Exception:
        pass  # last resort: stderr line above already fired.


def _bump_marker() -> None:
    """Increment the per-repo call counter. The freshness sentinel compares this
    against the audit-log line count; a marker ahead of the log means calls went
    unlogged (a silent-failure signal). Raises on failure so the caller records
    a coverage gap."""
    marker = _log_dir() / MARKER_NAME
    marker.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    if marker.exists():
        try:
            count = int(json.loads(marker.read_text()).get("call_count", 0))
        except Exception:
            count = 0
    marker.write_text(json.dumps({"call_count": count + 1, "last_call_ts": _now()}))


def _page_pii(caller: str, pattern: str, audit_record_ts: str) -> None:
    """POST a minimal, text-free alert to the ntfy channel. Never raises.

    Sends only {repo, caller, pattern_matched, audit_record_ts} — never the
    matched value or surrounding context. The channel is a public ntfy topic;
    leaking the payload here would recreate the exposure being audited."""
    url = os.environ.get("LLM_AUDIT_ALERT_URL")
    if not url:
        url_file = os.environ.get(
            "LLM_AUDIT_ALERT_URL_FILE",
            str(Path.home() / "Dev" / "ai-native-org" / "config" / "alert-url.txt"),
        )
        try:
            url = Path(url_file).read_text().strip()
        except Exception:
            url = ""
    if not url:
        _log_coverage_gap(
            "page-pii-no-channel",
            RuntimeError("real-payload PII flagged but no alert channel configured"),
            caller,
        )
        return
    body = (
        f"PII FLAG (real payload) repo={_repo()} caller={caller} "
        f"pattern={pattern} audit_record_ts={audit_record_ts}"
    ).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Title": "LLM audit PII flag",
                                              "Priority": "urgent", "Tags": "rotating_light"})
        urllib.request.urlopen(req, timeout=5).close()
    except Exception as e:
        _log_coverage_gap("page-pii-post-failed", e, caller)


def record_call(
    *,
    prompt: str,
    response: str | None,
    model_id: str,
    tokens_in: int | None,
    tokens_out: int | None,
    provider: str | None = None,
    caller: str | None = None,
) -> None:
    """Audit one completed (or attempted) outbound call. Fully guarded — this
    NEVER raises, so it can be called from any provider call site without risk
    of breaking the underlying work."""
    if not enabled():
        return
    caller = caller or _infer_caller()

    # 1) marker first: a bumped marker with no audit line is the silent-stop signal.
    with _LOCK:
        try:
            _bump_marker()
        except Exception as e:
            _log_coverage_gap("marker", e, caller)

        ts = _now()
        # 2) audit record — exact contract shape, no extra fields.
        try:
            _append_jsonl(
                _log_dir() / AUDIT_LOG_NAME,
                {
                    "ts": ts,
                    "repo": _repo(),
                    "caller": caller,
                    "provider": _provider_for(model_id, provider),
                    "model": model_id,
                    "prompt": prompt,
                    "response": response,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                },
            )
        except Exception as e:
            _log_coverage_gap("audit-write", e, caller)

    # 3) PII scan over prompt+response (data only; never executed as instructions).
    try:
        text = "\n".join(p for p in (prompt, response) if p)
        matches = scan_text(text)
    except Exception as e:
        _log_coverage_gap("pii-scan", e, caller)
        matches = []

    real_payload = not _is_test_payload()
    for match in matches:
        try:
            _append_jsonl(
                _log_dir() / PII_LOG_NAME,
                {
                    "ts": _now(),
                    "repo": _repo(),
                    "caller": caller,
                    "pattern_matched": match.pattern,
                    "audit_record_ts": ts,
                },
            )
        except Exception as e:
            _log_coverage_gap("pii-write", e, caller)
        if real_payload:
            key = (_repo(), match.pattern)
            with _LOCK:
                first = key not in _PAGED
                if first:
                    _PAGED.add(key)
            if first:
                _page_pii(caller, match.pattern, ts)


def _extract_text(data: Any) -> str:
    """Serialize a pydantic-ai result payload to auditable text."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    dump = getattr(data, "model_dump_json", None)
    if callable(dump):
        try:
            return dump()
        except Exception:
            pass
    return str(data)


async def audited_agent_run(
    agent: Any,
    prompt: str,
    *,
    model_id: str,
    provider: str | None = None,
    caller: str | None = None,
) -> Any:
    """Drop-in replacement for ``run_with_backoff(lambda: agent.run(prompt))``.

    Runs the real call (with the same retry/backoff behavior as before), then
    audits it. The underlying call's own exceptions propagate UNCHANGED so each
    judge's existing ``except`` still records a clean ERROR score; only the audit
    step is guarded. When disabled, this is byte-for-byte the original call.
    """
    if not enabled():
        return await run_with_backoff(lambda: agent.run(prompt))

    caller = caller or _infer_caller()
    try:
        result = await run_with_backoff(lambda: agent.run(prompt))
    except Exception:
        # Audit the failed outbound attempt (so the freshness counter stays
        # consistent), then re-raise to preserve the caller's error handling.
        record_call(prompt=prompt, response=None, model_id=model_id,
                    tokens_in=None, tokens_out=None, provider=provider, caller=caller)
        raise

    tokens_in = tokens_out = None
    try:
        usage = result.usage()
        tokens_in = getattr(usage, "request_tokens", None)
        tokens_out = getattr(usage, "response_tokens", None)
    except Exception as e:
        _log_coverage_gap("usage-extract", e, caller)

    response_text = _extract_text(getattr(result, "data", None))
    record_call(prompt=prompt, response=response_text, model_id=model_id,
                tokens_in=tokens_in, tokens_out=tokens_out, provider=provider, caller=caller)
    return result
