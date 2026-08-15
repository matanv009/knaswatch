"""Correcting, removing and undoing an installation - and never submitting a
mixed-up pair of numbers in the first place.

All four of these come from one household's first week with the tool:

  - the console printed Hebrew, which Windows lays out back to front;
  - the first run submitted one person's ID with another person's licence;
  - there was no way to correct a number short of deleting the person;
  - and no way to take any of it back off the machine.

Nothing here touches the real credential vault, the real data directory or the
real task scheduler: every one of those is replaced for the duration of a test.
"""

import argparse
import builtins
import contextlib
import getpass
import io
import logging
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knaswatch import checker, state, vault  # noqa: E402
from knaswatch import __main__ as cli  # noqa: E402
from knaswatch import schedule as scheduler  # noqa: E402
from knaswatch.checker import (  # noqa: E402
    STATUS_CHALLENGE,
    STATUS_ERROR,
    CheckResult,
    _classify,
    _ResponseCollector,
)
from knaswatch.notify import format_result  # noqa: E402
from knaswatch.vault import Credentials, Recipient  # noqa: E402

# The retry warning from the fill-and-verify loop is the expected behaviour of
# one of these tests; printed next to the results it reads like a failure.
logging.getLogger("knaswatch").addHandler(logging.NullHandler())
logging.getLogger("knaswatch").propagate = False

BARAK = Credentials(id_number="000000018", license_number="1234567")
DAD = Credentials(id_number="000000026", license_number="7654321")


def _has_hebrew(text: str) -> bool:
    return any("֐" <= ch <= "׿" for ch in text)


# --- nothing Hebrew reaches the console -------------------------------------

def test_every_classified_summary_is_readable_in_a_console():
    """`summary` is what the console, the log file and status.json get. A
    Windows console has no bidirectional text support: Hebrew comes out back to
    front, which is worse than unreadable, because it still looks like words."""
    outcomes = [
        _classify(_collector({"success": False, "errors": ["אין תיקים פתוחים"]})),
        _classify(_collector({"success": False, "errors": ["אין התאמה בין מס. זהות"]})),
        _classify(_collector({"success": False, "errors": ["תקלה זמנית"]})),
        _classify(_collector({"success": True, "errors": []})),
        _classify(_collector({"success": True, "errors": []},
                             [{"items": [{"description": "דוח מהירות", "sum": 250.0}]}])),
        _classify(_ResponseCollector()),
    ]
    for result in outcomes:
        assert not _has_hebrew(result.summary), result.summary
        assert result.summary.isascii(), result.summary


def test_checker_never_writes_a_hebrew_console_summary():
    """A guard on the source itself: the failure paths that build a summary
    mostly need a live browser to reach, so they cannot all be run here."""
    source = Path(checker.__file__).read_text(encoding="utf-8")
    offenders = [
        line.strip() for line in source.splitlines()
        if line.strip().startswith("summary=") and _has_hebrew(line)
    ]
    assert not offenders, offenders


def test_the_hebrew_wording_survives_for_telegram():
    """Telegram renders Hebrew properly, so the phone must not be given the
    English text meant for the console."""
    result = _classify(_collector({"success": False,
                                   "errors": ["אין התאמה בין מס. זהות ורישיון נהיגה"]}))
    body = format_result("Barak", result)
    assert _has_hebrew(body), body
    assert result.summary not in body, "the console wording leaked into Telegram"


def test_rejected_numbers_tell_the_reader_how_to_fix_them():
    """The one failure the person holding the phone can actually act on."""
    result = _classify(_collector({"success": False,
                                   "errors": ["אין התאמה בין מס. זהות ורישיון נהיגה"]}))
    assert result.retryable is False
    body = format_result("Barak", result)
    assert ("13" in body) if sys.platform == "win32" else ("change-numbers" in body)


def test_a_captcha_message_is_unchanged_by_all_this():
    body = format_result("Barak", CheckResult(status=STATUS_CHALLENGE,
                                              summary="a CAPTCHA was required",
                                              retryable=False))
    assert "12" in body if sys.platform == "win32" else "--if-stale 12" in body


def _collector(submit, basket=None):
    collector = _ResponseCollector()
    collector.submit = dict({"warnings": [], "inputsErrors": []}, **submit)
    collector.basket = basket or []
    return collector


# --- the form is never submitted holding the wrong numbers ------------------

class _FakeForm:
    """Just enough of a Playwright page to drive the fill-and-verify loop.

    `interfere` runs during the settle wait, which is where the real page does
    its re-rendering and its restoring of remembered values.
    """

    def __init__(self, interfere=None):
        self.values = {}
        self.interfere = interfere or (lambda form: None)
        self.settles = 0

    def fill(self, selector, value):
        self.values[selector] = value

    def input_value(self, selector):
        return self.values.get(selector, "")

    def wait_for_timeout(self, _ms):
        self.settles += 1
        self.interfere(self)


def test_a_quiet_form_is_filled_and_accepted():
    form = _FakeForm()
    assert checker._fill_identity(form, BARAK) is True
    assert form.values[checker.FIELD_ID] == BARAK.id_number
    assert form.values[checker.FIELD_LICENCE] == BARAK.license_number


def test_one_persons_id_is_never_submitted_with_anothers_licence():
    """The bug this whole path exists for. The page restores the ID it
    remembers from the previous person, so the pair about to be submitted is
    Barak's ID with his father's licence - which the site answers with a flat
    "no match", making it look like a typo for as long as nobody checks."""
    def restore_the_previous_person(form):
        form.values[checker.FIELD_ID] = BARAK.id_number

    form = _FakeForm(restore_the_previous_person)
    assert checker._fill_identity(form, DAD) is False, "a mixed pair was accepted"
    assert form.settles == checker.FILL_ATTEMPTS, "it should have retried first"


def test_a_form_that_settles_on_the_second_try_is_accepted():
    """Losing a check to a one-off race would be its own bug."""
    def only_the_first_time(form):
        if form.settles == 1:
            form.values[checker.FIELD_ID] = BARAK.id_number

    form = _FakeForm(only_the_first_time)
    assert checker._fill_identity(form, DAD) is True
    assert form.values[checker.FIELD_ID] == DAD.id_number


def test_a_form_that_reformats_the_number_is_still_accepted():
    """Digits are what matter; spaces the page adds for readability are not a
    reason to refuse to check somebody."""
    def add_spacing(form):
        form.values[checker.FIELD_ID] = "000 000 026"

    assert checker._fill_identity(_FakeForm(add_spacing), DAD) is True


# --- an installation you can edit and undo ----------------------------------

class _Install:
    """One installation: the vault, config, state and scheduler, in memory."""

    def __init__(self, profiles=("Barak", "Dad"), recipients=None):
        self.profiles = list(profiles)
        self.credentials = {"Barak": BARAK, "Dad": DAD}
        self.results = {name: {"status": "clear", "fingerprint": "clear",
                               "last_success_at": "2026-08-15T09:00:00+03:00"}
                        for name in self.profiles}
        self.recipients = list(recipients or [Recipient("111", "me", ())])
        self.telegram_deleted = False
        self.task_deleted = False
        self.answers = []
        self.secrets = []
        self._undo = []

    # -- patching ------------------------------------------------------------

    def _patch(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def __enter__(self):
        self.data_dir = Path(tempfile.mkdtemp(prefix="knaswatch-test-"))
        (self.data_dir / "state.json").write_text("{}", encoding="utf-8")

        # These commands talk to the person running them, at length. Captured
        # rather than printed, so the test results stay readable - and so the
        # wording itself can be asserted on.
        self._redirect = contextlib.redirect_stdout(io.StringIO())
        self._buffer = self._redirect.__enter__()
        self.output = ""

        self._patch(vault, "load_credentials", lambda n: self.credentials.get(n))
        self._patch(vault, "save_credentials",
                    lambda n, i, l: self.credentials.__setitem__(n, Credentials(i, l)))
        self._patch(vault, "delete_credentials",
                    lambda n: self.credentials.pop(n, None))
        self._patch(vault, "load_telegram", lambda: None)
        self._patch(vault, "delete_telegram", self._delete_telegram)
        self._patch(vault, "load_recipients", lambda: list(self.recipients))
        self._patch(vault, "save_recipients", self._save_recipients)
        self._patch(vault, "vault_backend_name", lambda: "test vault")

        self._patch(state, "DATA_DIR", self.data_dir)
        self._patch(state, "list_profiles", lambda: list(self.profiles))
        self._patch(state, "reset_profile", lambda n: self.results.pop(n, None))
        self._patch(state, "remove_profile", self._remove_profile)

        self._patch(scheduler, "delete_task", self._delete_task)
        self._patch(scheduler, "task_status", lambda: "not scheduled")

        self._patch(checker, "browser_profile_dir",
                    lambda n: self.data_dir / f"browser-{n}")
        self._patch(cli, "browser_profile_dir", lambda n: self.data_dir / f"browser-{n}")

        self._patch(builtins, "input", self._input)
        self._patch(getpass, "getpass", self._getpass)
        # Windows is where these are used, and the platform decides whether the
        # scheduled task is part of an uninstall at all.
        self._patch(sys, "platform", "win32")
        return self

    def __exit__(self, *exc):
        self.output = self._buffer.getvalue()
        self._redirect.__exit__(None, None, None)
        for obj, name, original in reversed(self._undo):
            setattr(obj, name, original)
        shutil.rmtree(self.data_dir, ignore_errors=True)
        return False

    # -- stubs ---------------------------------------------------------------

    def _input(self, prompt=""):
        return self.answers.pop(0)

    def _getpass(self, prompt=""):
        return self.secrets.pop(0)

    def _remove_profile(self, name):
        self.profiles = [p for p in self.profiles if p != name]
        self.results.pop(name, None)

    def _save_recipients(self, recipients):
        self.recipients = list(recipients)

    def _delete_telegram(self):
        self.telegram_deleted = True

    def _delete_task(self):
        self.task_deleted = True

    # -- driving -------------------------------------------------------------

    def change_numbers(self, name=None, change_id=False, licence=False, **answers):
        self.answers = list(answers.get("answers", []))
        self.secrets = list(answers.get("secrets", []))
        return cli.cmd_change_numbers(argparse.Namespace(
            name=name, id=change_id, licence=licence))

    def remove(self, name=None, yes=True, answers=()):
        self.answers = list(answers)
        return cli.cmd_remove_profile(argparse.Namespace(name=name, yes=yes))

    def uninstall(self, yes=True, answers=()):
        self.answers = list(answers)
        return cli.cmd_uninstall(argparse.Namespace(yes=yes))


# --- correcting a number ----------------------------------------------------

def test_correcting_the_licence_leaves_the_id_alone():
    """Half of a correction is still a correction: the other number was right."""
    with _Install() as install:
        code = install.change_numbers("Dad", licence=True, secrets=["7000001"])

    assert code == 0
    assert install.credentials["Dad"] == Credentials("000000026", "7000001")
    assert install.credentials["Barak"] == BARAK, "the other person was touched"


def test_correcting_the_id_leaves_the_licence_alone():
    with _Install() as install:
        install.change_numbers("Dad", change_id=True, secrets=["000000018"])

    assert install.credentials["Dad"] == Credentials("000000018", DAD.license_number)


def test_a_corrected_person_is_checked_again_rather_than_skipped():
    """The stored result belongs to the old numbers - possibly to a different
    person entirely. Keeping it would let --if-stale skip the first real check,
    and would compare the next one against somebody else's fingerprint."""
    with _Install() as install:
        assert "Dad" in install.results
        install.change_numbers("Dad", change_id=True, secrets=["000000018"])

    assert "Dad" not in install.results, "the old result was kept"


def test_retyping_the_same_numbers_changes_nothing():
    with _Install() as install:
        code = install.change_numbers("Dad", change_id=True,
                                      secrets=[DAD.id_number])

    assert code == 0
    assert "Dad" in install.results, "an unchanged profile should not be reset"


def test_a_bad_id_is_rejected_before_it_is_stored():
    """The prompt loops until the check digit passes, so a typo never lands in
    the vault to be discovered a day later as 'no match'."""
    with _Install() as install:
        install.change_numbers("Dad", change_id=True,
                               secrets=["123456789", "000000018"])

    assert install.credentials["Dad"].id_number == "000000018"


def test_the_person_can_be_chosen_by_number():
    """Nicknames are usually Hebrew, and Hebrew cannot be typed into a Windows
    console - so anything that asks for one by name is unusable there."""
    with _Install(profiles=("מתן אלון", "יעל אלון")) as install:
        install.credentials = {"מתן אלון": BARAK, "יעל אלון": DAD}
        code = install.change_numbers(None, licence=True,
                                      answers=["2"], secrets=["7000001"])

    assert code == 0
    assert install.credentials["יעל אלון"].license_number == "7000001"
    assert install.credentials["מתן אלון"] == BARAK
    assert "1. מתן אלון" in install.output and "2. יעל אלון" in install.output


def test_choosing_nobody_changes_nothing():
    with _Install() as install:
        code = install.change_numbers(None, licence=True, answers=["9"])

    assert code == 1
    assert install.credentials["Dad"] == DAD


# --- removing one person ----------------------------------------------------

def test_removing_one_person_leaves_the_other_watched():
    with _Install() as install:
        code = install.remove("Dad")

    assert code == 0
    assert install.profiles == ["Barak"]
    assert "Dad" not in install.credentials
    assert install.credentials["Barak"] == BARAK


def test_removing_a_person_takes_their_browser_session_with_them():
    """It holds the cookies from that person's own visits to the site."""
    with _Install() as install:
        session = install.data_dir / "browser-Dad"
        session.mkdir()
        (session / "Cookies").write_text("theirs", encoding="utf-8")

        install.remove("Dad")
        assert not session.exists(), "their browsing history was left behind"


def test_removal_can_be_declined():
    with _Install() as install:
        code = install.remove("Dad", yes=False, answers=["no"])

    assert code == 1
    assert install.profiles == ["Barak", "Dad"]
    assert install.credentials["Dad"] == DAD


def test_a_recipient_scoped_to_the_removed_person_is_dropped():
    """An empty scope means 'everybody', so leaving it empty would quietly widen
    what that person is told about."""
    recipients = [Recipient("111", "me", ()), Recipient("222", "Dad", ("Dad",))]
    with _Install(recipients=recipients) as install:
        install.remove("Dad")

    assert [r.label for r in install.recipients] == ["me"], install.recipients


# --- undoing the whole thing ------------------------------------------------

def test_uninstall_removes_every_stored_number():
    with _Install() as install:
        code = install.uninstall()

    assert code == 0
    assert install.credentials == {}, install.credentials
    assert install.telegram_deleted
    # Whoever runs this has to be able to see what they are about to lose.
    assert "Barak" in install.output and "Dad" in install.output


def test_uninstall_removes_the_scheduled_task_first():
    """It runs whether or not anybody is watching, and a run that started
    mid-uninstall would write the config back out behind us."""
    with _Install() as install:
        install.uninstall()

    assert install.task_deleted


def test_uninstall_removes_the_data_directory():
    with _Install() as install:
        (install.data_dir / "browser-Barak").mkdir()
        install.uninstall()
        assert not install.data_dir.exists(), "the data directory survived"


def test_uninstall_needs_the_word_typed_in_full():
    """One stray keystroke should not be able to delete numbers that are stored
    in exactly one place."""
    for answer in ("y", "yes", "uninstall", ""):
        with _Install() as install:
            code = install.uninstall(yes=False, answers=[answer])
            assert code == 1, answer
            assert install.credentials, f"{answer!r} was accepted as confirmation"
            assert not install.task_deleted


def test_uninstall_proceeds_when_the_word_is_typed():
    with _Install() as install:
        code = install.uninstall(yes=False, answers=[cli.UNINSTALL_WORD])

    assert code == 0
    assert install.credentials == {}


def test_uninstall_reports_what_it_could_not_delete():
    """Silence after an uninstall has to mean the numbers are gone."""
    with _Install() as install:
        def refuse(name):
            raise vault.VaultError("the vault is locked")

        vault.delete_credentials = refuse
        code = install.uninstall()

    assert code == 1, "a failed deletion must not report success"


def test_uninstall_of_a_bare_installation_is_not_an_error():
    with _Install(profiles=()) as install:
        shutil.rmtree(install.data_dir)
        assert install.uninstall() == 0


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
