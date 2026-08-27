"""Audit wrapper tests.

Covers the audit record shape, the freshness marker, PII-flag writing, the
fail-open guarantee, and the extraction wiring that reads token counts from a
pydantic-ai result. The extraction test drives the REAL ``audited_agent_run``
path with pydantic-ai's ``TestModel``; it proves tokens flow from
``result.usage()`` into the right JSON fields, but it does NOT satisfy contract
DoD item 1, which requires a real provider's own response metadata. See
``test_audit_live.py`` for that.
"""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import mash_core.audit as audit
from mash_core.audit import audited_agent_run, record_call


class _Out(BaseModel):
    score: int
    note: str


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_AUDIT_REPO", "mash-core")
    monkeypatch.setenv("AUDIT_WRAPPER_ENABLED", "1")
    # No configure(); env-based resolution keeps module globals clean per test.
    audit._PAGED.clear()  # dedup set is process-global; isolate each test
    yield


def _read(tmp_path: Path, name: str) -> list[dict]:
    p = tmp_path / name
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line]


async def test_extraction_wiring_reads_usage(tmp_path):
    """Tokens land in tokens_in/tokens_out from result.usage(); response is the
    serialized structured output. (Plumbing proof, not DoD-1.)"""
    agent = Agent(model=TestModel(), result_type=_Out, system_prompt="sys")
    result = await audited_agent_run(agent, "evaluate this chapter",
                                     model_id="claude-sonnet-4-6")
    assert isinstance(result.data, _Out)
    records = _read(tmp_path, audit.AUDIT_LOG_NAME)
    assert len(records) == 1
    rec = records[0]
    assert rec["repo"] == "mash-core"
    assert rec["provider"] == "anthropic"
    assert rec["model"] == "claude-sonnet-4-6"
    assert rec["prompt"] == "evaluate this chapter"
    assert json.loads(rec["response"]) == {"score": 0, "note": "a"}
    assert rec["tokens_in"] > 0 and rec["tokens_out"] > 0
    assert set(rec) == {"ts", "repo", "caller", "provider", "model",
                        "prompt", "response", "tokens_in", "tokens_out"}


def test_marker_count_matches_audit_lines(tmp_path):
    for i in range(3):
        record_call(prompt=f"p{i}", response="r", model_id="claude-sonnet-4-6",
                    tokens_in=1, tokens_out=1)
    marker = json.loads((tmp_path / audit.MARKER_NAME).read_text())
    assert marker["call_count"] == 3
    assert len(_read(tmp_path, audit.AUDIT_LOG_NAME)) == 3  # counter == log lines -> healthy


def test_pii_flag_written_without_the_value(tmp_path):
    record_call(prompt="reach me at bob@corp.io", response="ok",
                model_id="claude-sonnet-4-6", tokens_in=1, tokens_out=1)
    flags = _read(tmp_path, audit.PII_LOG_NAME)
    assert len(flags) == 1
    assert flags[0]["pattern_matched"] == "email"
    assert set(flags[0]) == {"ts", "repo", "caller", "pattern_matched", "audit_record_ts"}
    # the flag log must not duplicate the sensitive value
    assert "bob@corp.io" not in (tmp_path / audit.PII_LOG_NAME).read_text()
    # flag references the audit record by ts
    rec = _read(tmp_path, audit.AUDIT_LOG_NAME)[0]
    assert flags[0]["audit_record_ts"] == rec["ts"]


def test_test_mode_suppresses_paging(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(audit, "_page_pii", lambda *a, **k: called.append(a))
    # running under pytest => _is_test_payload() is True => no page
    record_call(prompt="bob@corp.io", response="", model_id="claude-sonnet-4-6",
                tokens_in=1, tokens_out=1)
    assert called == []


def test_real_payload_pages(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(audit, "_page_pii", lambda *a, **k: called.append(a))
    monkeypatch.setattr(audit, "_is_test_payload", lambda: False)
    record_call(prompt="bob@corp.io", response="", model_id="claude-sonnet-4-6",
                tokens_in=1, tokens_out=1)
    assert len(called) == 1  # one page for the one email match


def test_paging_deduped_but_all_matches_flagged(tmp_path, monkeypatch):
    """Two identical real-payload email matches -> 2 flag-log lines but only 1
    page (per-incident, not per-match), so a book chapter can't flood ntfy."""
    pages = []
    monkeypatch.setattr(audit, "_page_pii", lambda *a, **k: pages.append(a))
    monkeypatch.setattr(audit, "_is_test_payload", lambda: False)
    record_call(prompt="a@b.com here", response="", model_id="claude-sonnet-4-6",
                tokens_in=1, tokens_out=1)
    record_call(prompt="a@b.com again", response="", model_id="claude-sonnet-4-6",
                tokens_in=1, tokens_out=1)
    flags = _read(tmp_path, audit.PII_LOG_NAME)
    assert len(flags) == 2          # every match still flagged
    assert len(pages) == 1          # but only one page for the repeated pattern


def test_fail_open_on_audit_write_error(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(audit, "_append_jsonl", boom)
    # must NOT raise, despite every write failing
    record_call(prompt="x", response="y", model_id="claude-sonnet-4-6",
                tokens_in=1, tokens_out=1)
    # coverage gap surfaced (marker still bumped -> counter ahead of log -> Red later)
    marker = json.loads((tmp_path / audit.MARKER_NAME).read_text())
    assert marker["call_count"] == 1


async def test_failed_call_audited_then_reraised(tmp_path):
    class _Boom:
        async def run(self, prompt):
            raise ValueError("provider 400")
    with pytest.raises(ValueError):
        await audited_agent_run(_Boom(), "p", model_id="claude-sonnet-4-6")
    rec = _read(tmp_path, audit.AUDIT_LOG_NAME)
    assert len(rec) == 1 and rec[0]["response"] is None  # failed attempt still audited


def test_provider_inference(tmp_path):
    record_call(prompt="a", response="b", model_id="openrouter:deepseek/deepseek-chat",
                tokens_in=1, tokens_out=1)
    record_call(prompt="a", response="b", model_id="openai-compatible:gpt-4o-mini",
                tokens_in=1, tokens_out=1)
    provs = [r["provider"] for r in _read(tmp_path, audit.AUDIT_LOG_NAME)]
    assert provs == ["openrouter", "openai-compatible"]
