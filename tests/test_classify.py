"""Classification tests using payloads captured from the live site.

The critical property: a clean record and a wrong ID/licence pair must never be
confused. Reporting a mismatch as "no fines" would be a silent false all-clear.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knaswatch.checker import (  # noqa: E402
    STATUS_CHALLENGE,
    STATUS_CLEAR,
    STATUS_ERROR,
    STATUS_FINES,
    CheckResult,
    _classify,
    _ResponseCollector,
)


def collector_with(submit, basket=None):
    c = _ResponseCollector()
    c.submit = submit
    c.basket = basket or []
    return c


def test_clean_record_is_clear():
    """Captured live: identification recognised, nothing owed."""
    payload = {
        "warnings": [],
        "inputsErrors": [],
        "errors": ["חייב לא זוהה במערכת / לזיהוי זה אין תיקים פתוחים"],
        "success": False,
    }
    result = _classify(collector_with(payload))
    assert result.status == STATUS_CLEAR, result
    assert result.summary == "לא נמצאו קנסות"


def test_mismatched_details_is_rejection_not_clear():
    """Captured live with a checksum-valid but unreal ID."""
    payload = {
        "warnings": [],
        "inputsErrors": [],
        "errors": ["אין התאמה בין מס. זהות ורישיון נהיגה או תיק מרכז"],
        "success": False,
    }
    result = _classify(collector_with(payload))
    assert result.status == STATUS_ERROR, result
    # The flag, not the wording, is what stops the retry loop re-submitting
    # numbers the site has already rejected.
    assert result.retryable is False, result


def test_mismatch_wins_over_no_debt_wording():
    """If both ever appear, the safe reading is rejection, never an all-clear."""
    payload = {
        "warnings": [],
        "inputsErrors": [],
        "errors": ["אין התאמה בין מס. זהות ורישיון נהיגה", "אין תיקים פתוחים"],
        "success": False,
    }
    result = _classify(collector_with(payload))
    assert result.status == STATUS_ERROR, result


def test_unknown_error_still_fails_loudly():
    payload = {"warnings": [], "inputsErrors": [], "errors": ["תקלה זמנית"], "success": False}
    result = _classify(collector_with(payload))
    assert result.status == STATUS_ERROR, result
    # An unrecognised error might be transient, so it stays worth retrying.
    assert result.retryable is True, result


def test_retry_loop_stops_on_non_retryable_results():
    """The loop must decide from the flag, never from message wording - a
    reworded or translated summary used to silently re-enable retrying."""
    rejected = CheckResult(status=STATUS_ERROR, summary="anything at all",
                           retryable=False)
    transient = CheckResult(status=STATUS_ERROR, summary="anything at all")

    assert rejected.ok is False and rejected.retryable is False
    assert (rejected.ok or not rejected.retryable) is True, "must stop retrying"
    assert (transient.ok or not transient.retryable) is False, "must keep retrying"


def test_challenge_is_not_retried():
    challenge = CheckResult(status=STATUS_CHALLENGE, summary="captcha",
                            retryable=False)
    assert (challenge.ok or not challenge.retryable) is True


def test_success_with_no_basket_is_error_not_clear():
    """A site change must never be able to look like good news."""
    result = _classify(collector_with({"success": True, "errors": [], "warnings": []}))
    assert result.status == STATUS_ERROR, result


def test_fines_are_reported():
    submit = {"success": True, "errors": [], "warnings": []}
    basket = [{"items": [{"description": "דוח מהירות", "sum": 250.0}]}]
    result = _classify(collector_with(submit, basket))
    assert result.status == STATUS_FINES, result
    assert result.total_amount == 250.0, result


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL {name}: {exc}")
    print("all tests passed" if not failures else f"{failures} test(s) failed")
    sys.exit(1 if failures else 0)
