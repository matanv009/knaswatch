"""Checksum and parsing tests. No network, no credentials."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knaswatch.checker import CheckResult, _extract_fines, _looks_like_no_debt
from knaswatch.logging_setup import redact
from knaswatch.validate import (
    is_plausible_license,
    is_valid_israeli_id,
    is_valid_profile_name,
    normalize_digits,
)

VALID_IDS = ["123456782", "000000018", "12345678-2", "0000 0001 8"]
INVALID_IDS = ["123456789", "000000019", "", "abcdefghi", "1234567890", "12345678a9"]


def test_israeli_id_checksum():
    for value in VALID_IDS:
        assert is_valid_israeli_id(value), f"{value!r} should be valid"
    for value in INVALID_IDS:
        assert not is_valid_israeli_id(value), f"{value!r} should be invalid"


def test_short_ids_are_left_padded():
    # "18" padded to 000000018 is a valid ID, as printed on older cards.
    assert is_valid_israeli_id("18")


def test_licence_shape():
    # The site's k_id_num field has maxLength 7, so longer input is rejected here
    # rather than being silently truncated by the form.
    assert is_plausible_license("1234567")
    assert is_plausible_license("123456")
    assert not is_plausible_license("12345678")
    assert not is_plausible_license("")


def test_profile_names():
    assert is_valid_profile_name("אבא")
    assert not is_valid_profile_name("")
    assert not is_valid_profile_name("a::b")


def test_normalize_digits():
    assert normalize_digits(" 12-34 56 ") == "123456"


def test_redaction_masks_long_runs_only():
    assert redact("id 123456782 seen") == "id ********* seen"
    assert redact("amount 250.00") == "amount 250.00"


def test_no_debt_phrase_detection():
    assert _looks_like_no_debt(["לא נמצאו חובות במערכת"])
    assert not _looks_like_no_debt(["שגיאת מערכת, נסה שוב"])


def test_extract_fines_finds_nested_amounts():
    payload = {
        "basket": {
            "items": [
                {"description": "דוח מהירות", "sum": 250.0},
                {"description": "דוח חנייה", "amount": "100.50"},
                {"description": "כותרת בלבד", "sum": 0},
            ]
        }
    }
    fines = _extract_fines(payload)
    labels = {f["label"] for f in fines}
    assert labels == {"דוח מהירות", "דוח חנייה"}
    assert sum(f["amount"] for f in fines) == 350.5


def test_fingerprint_changes_with_content():
    a = CheckResult(status="fines", summary="", fines=[{"label": "x", "amount": 100}])
    b = CheckResult(status="fines", summary="", fines=[{"label": "x", "amount": 200}])
    clear = CheckResult(status="clear", summary="")
    assert a.fingerprint() != b.fingerprint()
    assert clear.fingerprint() == "clear"


def test_result_ok_flag():
    assert CheckResult(status="clear", summary="").ok
    assert CheckResult(status="fines", summary="").ok
    assert not CheckResult(status="error", summary="").ok


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL  {name}: {exc}")
    print(f"\n{'all tests passed' if not failures else f'{failures} failing'}")
    sys.exit(1 if failures else 0)
