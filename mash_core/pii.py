"""Local, pattern-based PII scanner — no LLM, no network call, ever.

Part of the cloud-LLM-call audit wrapper (contract
2026-08-27-cloud-llm-call-audit-wrapper). The scanner runs over text the audit
wrapper already logged and names the PII-shaped patterns it matched. It returns
pattern NAMES only; the caller writes those names to the flag log and never
copies the matched substring, so the flag log does not become a second copy of
the sensitive payload.

Design choices that keep false positives near zero on ordinary book/code text:

- Phone: the 3-3-4 groups must be separator-delimited, so a bare 10-digit run,
  a year, or a cache size never matches.
- SSN: hyphenated form only (``NNN-NN-NNNN``); nine bare digits are far too
  common in code to flag safely.
- Credit card: a digit-run candidate is Luhn-validated before it counts, which
  rejects the overwhelming majority of random or structured digit strings
  (SHA prefixes, ids, timestamps). Residual risk: an ordinary (non-card)
  digit run of length 13-19 that happens to be Luhn-valid by chance (roughly
  1 in 10 for a random run) will still flag as ``credit_card`` — the check
  rejects the overwhelming majority of non-card content, not all of it.
- API key / secret: PREFIX-anchored to real provider key shapes
  (``sk-``, ``sk-ant-``, ``ghp_`` ...). Deliberately NOT entropy-based, because
  this codebase is full of ``sha256(...)`` cache keys and git commit hashes that
  a generic high-entropy matcher would flag on sight.

``scan_text`` returns one entry per match (three emails -> three entries), so
the flag count matches the contract's "one JSON object per flagged match".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- simple, independently-compiled patterns -------------------------------

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# 3-3-4 groups REQUIRING a separator between each group, optional country code.
# The lookarounds stop a longer digit run from being clipped into a false match.
_PHONE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[ .\-])?\(?\d{3}\)?[ .\-]\d{3}[ .\-]\d{4}(?!\w)"
)

# Hyphenated SSN only. Bare 9-digit runs are intentionally not matched.
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")

# Provider API-key / secret shapes, prefix-anchored (order-independent; a
# string can match more than one and each match is reported).
_API_KEY_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),        # Anthropic
    re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}"),         # OpenRouter
    re.compile(r"sk-proj-[A-Za-z0-9_\-]{16,}"),       # OpenAI project keys
    re.compile(r"sk-[A-Za-z0-9]{20,}"),               # OpenAI classic
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),        # GitHub tokens
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),     # Slack tokens
    re.compile(r"AKIA[0-9A-Z]{16}"),                  # AWS access key id
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),            # Google API key
    re.compile(r"xai-[A-Za-z0-9]{20,}"),              # xAI
]

# Credit-card CANDIDATE: 13-19 digits, optionally split into groups by a single
# space or hyphen. Validated with Luhn before it counts as a match.
_CC_CANDIDATE = re.compile(r"(?<![\d\-])(?:\d[ \-]?){13,19}(?![\d\-])")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass(frozen=True)
class PiiMatch:
    """One PII hit. ``value`` is kept only in-process for immediate handling;
    it is NEVER written to the flag log — callers persist ``pattern`` only."""

    pattern: str
    value: str


def scan_text(text: str) -> list[PiiMatch]:
    """Return one ``PiiMatch`` per PII-shaped substring found in ``text``.

    Pure and side-effect-free. Never raises on ordinary input; callers still
    wrap the call so a pathological regex backtrack can never break the audited
    LLM call (the wrapper's fail-open contract).
    """
    if not text:
        return []

    matches: list[PiiMatch] = []

    for m in _EMAIL.finditer(text):
        matches.append(PiiMatch("email", m.group()))
    for m in _PHONE.finditer(text):
        matches.append(PiiMatch("phone", m.group()))
    for m in _SSN.finditer(text):
        matches.append(PiiMatch("ssn", m.group()))
    for pat in _API_KEY_PATTERNS:
        for m in pat.finditer(text):
            matches.append(PiiMatch("api_key", m.group()))
    for m in _CC_CANDIDATE.finditer(text):
        digits = re.sub(r"[ \-]", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            matches.append(PiiMatch("credit_card", m.group()))

    return matches
