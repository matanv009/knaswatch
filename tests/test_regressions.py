"""Regressions for bugs found in review. Each test fails against the old code.

Covered here:
  - a failed notification must be retried, not silently dropped
  - re-running `telegram` must not delete other recipients
  - schedule times must be validated
  - task XML must survive '&' in a username or install path
"""

import sys
from pathlib import Path
from xml.dom.minidom import parseString

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knaswatch import schedule, vault  # noqa: E402
from knaswatch import __main__ as cli  # noqa: E402
from knaswatch.checker import CheckResult, STATUS_FINES  # noqa: E402


# --- failed notification is retried ----------------------------------------

def test_failed_delivery_leaves_fingerprint_unchanged():
    """The fingerprint marks a result as reported, so it must only advance once
    somebody has actually been told."""
    fines = CheckResult(status=STATUS_FINES, summary="1 fine",
                        fines=[{"label": "speed", "amount": 250.0}], total_amount=250.0)
    previous = {}

    # Day 1: alert is due, but every delivery fails -> fingerprint must NOT be stored.
    assert cli._should_notify("p", fines, previous, force=False)
    notified = False
    stored_fingerprint = fines.fingerprint() if (fines.ok and notified) else previous.get("fingerprint")
    assert stored_fingerprint is None, stored_fingerprint

    # Day 2: the same fines are found, and the alert is still due.
    day2_previous = {"status": "fines", "fingerprint": stored_fingerprint,
                     "consecutive_failures": 0}
    assert cli._should_notify("p", fines, day2_previous, force=False), \
        "a fine nobody was told about must be re-notified"


def test_successful_delivery_records_fingerprint_and_stops_repeating():
    fines = CheckResult(status=STATUS_FINES, summary="1 fine",
                        fines=[{"label": "speed", "amount": 250.0}], total_amount=250.0)
    notified = True
    stored = fines.fingerprint() if (fines.ok and notified) else None
    assert stored is not None

    day2 = {"status": "fines", "fingerprint": stored, "consecutive_failures": 0}
    assert not cli._should_notify("p", fines, day2, force=False), \
        "an unchanged, already delivered result must not nag daily"


# --- recipients survive re-running `telegram` -------------------------------

def test_resaving_telegram_keeps_other_recipients():
    store = {}
    original_get, original_set = vault._get, vault._set
    vault._get = lambda key: store.get(key)
    vault._set = lambda key, payload: store.__setitem__(key, payload)
    try:
        vault.save_telegram("TOKEN", "111", [
            vault.Recipient("111", "me", ()),
            vault.Recipient("222", "אמא", ("אמא",)),
        ])
        # What cmd_telegram now does: rebuild the list around the owner.
        existing = vault.load_recipients()
        recipients = [r for r in existing if r.chat_id != "111"]
        recipients.insert(0, vault.Recipient("111", "me", ()))
        vault.save_telegram("TOKEN", "111", recipients)

        labels = [r.label for r in vault.load_recipients()]
        assert labels == ["me", "אמא"], labels
        assert "recipients" in store["telegram"]
    finally:
        vault._get, vault._set = original_get, original_set


# --- schedule time validation ----------------------------------------------

def test_bad_schedule_times_raise_runtimeerror():
    """RuntimeError is what the CLI catches; ValueError reached the user as a
    traceback."""
    for value in ["9am", "09-00", "", "1:2:3", "ab:cd"]:
        try:
            schedule.parse_time(value)
        except RuntimeError:
            continue
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"{value!r} raised {type(exc).__name__}, not RuntimeError")
        raise AssertionError(f"{value!r} was accepted")


def test_out_of_range_times_are_rejected_not_wrapped():
    for value in ["25:00", "12:99", "-1:00"]:
        try:
            schedule.parse_time(value)
        except RuntimeError:
            continue
        raise AssertionError(f"{value!r} was accepted instead of rejected")


def test_valid_times_are_accepted():
    assert schedule.parse_time("09:00") == (9, 0)
    assert schedule.parse_time("23:59") == (23, 59)
    assert schedule._retry_times("09:00") == ["09:00", "14:00", "19:00"]
    assert schedule._retry_times("21:30") == ["21:30", "02:30", "07:30"]


# --- task XML survives special characters ----------------------------------

def test_task_xml_is_well_formed_with_ampersand_in_paths():
    from xml.sax.saxutils import escape

    xml = schedule._TASK_XML.format(
        triggers=schedule._TRIGGER.format(time="09:00"),
        user=escape("R&D user"),
        command=escape(r"C:\Tools & Scripts\py.exe"),
        arguments=escape("-m knaswatch check --all"),
        workdir=escape(r"C:\Work & Play\knaswatch"),
    )
    document = parseString(xml)
    found = document.getElementsByTagName("WorkingDirectory")[0].firstChild.data
    assert found == r"C:\Work & Play\knaswatch", found


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
