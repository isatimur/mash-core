"""PII scanner tests — contract DoD items 2 and 3."""

from pathlib import Path

from mash_core.pii import _luhn_ok, scan_text

_FIXTURE = Path(__file__).parent / "fixtures" / "clean_corpus.txt"


def _patterns(text: str) -> list[str]:
    return sorted(m.pattern for m in scan_text(text))


def test_dod2_email_phone_apikey_three_flags():
    """DoD 2: a payload with an email, a phone number, and an API-key-shaped
    string produces exactly three flagged entries, each naming its pattern."""
    payload = (
        "Contact jane.doe@example.com or call +1 415-555-0132. "
        "The leaked key was sk-ant-api03-abcdefABCDEF0123456789ghij."
    )
    matches = scan_text(payload)
    assert len(matches) == 3
    assert _patterns(payload) == ["api_key", "email", "phone"]


def test_dod3_clean_corpus_zero_flags():
    """DoD 3: >=20 lines of real book/code content, including SHA-256 hashes and
    git commit hashes, produce ZERO flags (false-positive check)."""
    corpus = _FIXTURE.read_text(encoding="utf-8")
    assert len(corpus.splitlines()) >= 20
    matches = scan_text(corpus)
    assert matches == [], f"false positives: {[(m.pattern, m.value) for m in matches]}"


def test_ssn_hyphenated_only():
    assert _patterns("SSN 123-45-6789 on file") == ["ssn"]
    # bare 9 digits must NOT flag as SSN (too false-positive-prone)
    assert "ssn" not in _patterns("id 123456789 recorded")


def test_credit_card_luhn_validated():
    assert _luhn_ok("4242424242424242")  # valid test Visa
    assert not _luhn_ok("4242424242424243")
    assert _patterns("card 4242 4242 4242 4242 expires soon") == ["credit_card"]
    # A 16-digit run that fails Luhn must not flag. Verified directly:
    # _luhn_ok("1111222233334445") is False.
    assert not _luhn_ok("1111222233334445")
    assert "credit_card" not in _patterns("id 1111222233334445")
    # Residual risk, documented in pii.py's module docstring: an ordinary
    # digit run that happens to be Luhn-valid by chance still flags as
    # credit_card. "1234567812345670" is not a real card number, but it is
    # Luhn-valid (_luhn_ok confirms this), so it flags exactly like a real
    # one would. This is the false-positive boundary the check accepts.
    assert _luhn_ok("1234567812345670")
    assert "credit_card" in _patterns("ref 1234567812345670")


def test_multiple_same_pattern_counted_separately():
    two = scan_text("a@b.com and c@d.org")
    assert [m.pattern for m in two] == ["email", "email"]


def test_empty_and_none_safe():
    assert scan_text("") == []
    assert scan_text(None) == []  # type: ignore[arg-type]
