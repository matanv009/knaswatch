"""A real fine, driven through the whole tool.

Every profile has always come back clean, so the one path that actually matters
- a fine exists, and somebody has to be told - has never run against the live
site. This exercises it end to end with a fabricated site response: the real
classifier, the real notify decision, the real scope filtering, the real
message, and the real fingerprint bookkeeping. Only two things are stubbed, and
only because they leave the machine: the browser and the Telegram HTTP call.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knaswatch import __main__ as cli  # noqa: E402
from knaswatch import notify, state, vault  # noqa: E402
from knaswatch.checker import (  # noqa: E402
    STATUS_CLEAR,
    STATUS_FINES,
    _classify,
    _ResponseCollector,
)
from knaswatch.vault import Credentials, Recipient, TelegramConfig  # noqa: E402

# The delivery-failure tests make the real notify module log real errors. They
# are expected, and printing them next to the results reads like a failure.
_app_log = logging.getLogger("knaswatch")
_app_log.addHandler(logging.NullHandler())
_app_log.propagate = False

PROFILE = "יעל אלון"
SPEEDING = ("דוח מהירות - נהיגה במהירות של 96 קמ\"ש", 750.0)
PHONE = ("שימוש בטלפון בזמן נהיגה", 300.0)


def _site_result(*fines):
    """Build a CheckResult the way a real visit would: hand the site's own JSON
    to the classifier rather than constructing the verdict by hand."""
    collector = _ResponseCollector()
    collector.submit = {"success": True, "errors": [], "warnings": [], "inputsErrors": []}
    collector.basket = [
        {"items": [{"description": label, "sum": amount} for label, amount in fines]}
    ]
    return _classify(collector)


def _clean_result():
    collector = _ResponseCollector()
    collector.submit = {
        "success": False,
        "errors": ["חייב לא זוהה במערכת / לזיהוי זה אין תיקים פתוחים"],
        "warnings": [],
        "inputsErrors": [],
    }
    return _classify(collector)


class _Install:
    """One KnasWatch installation, held in memory.

    The vault, config.json, state.json, the browser and Telegram are all
    replaced, so the test drives cmd_check itself - the loop, the notify
    decision and the fingerprint write - and not a re-implementation of it.
    """

    def __init__(self, recipients, notify_all_clear=False, profiles=(PROFILE,)):
        self.profiles = list(profiles)
        self.recipients = list(recipients)
        self.config = {"profiles": list(profiles), "notify_all_clear": notify_all_clear}
        self.saved = {}       # what state.json would hold
        self.sent = []        # (chat_id, text) that reached Telegram
        self.sending_fails = False
        self.results = {}     # profile -> what the site says this run
        self._undo = []

    # -- patching ------------------------------------------------------------

    def _patch(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def __enter__(self):
        log = logging.getLogger("knaswatch-test")
        log.addHandler(logging.NullHandler())
        log.propagate = False

        self._patch(cli, "log", log)
        # The gap between people is real behaviour, but nobody should wait
        # minutes for a test to finish.
        self._patch(cli, "_profile_gap_seconds", lambda interactive: 0.0)
        self._patch(cli, "check", self._check)

        self._patch(vault, "load_credentials",
                    lambda p: Credentials("000000018", "1234567") if p in self.profiles else None)
        self._patch(vault, "load_telegram", lambda: TelegramConfig("123:token", "1"))
        self._patch(vault, "load_recipients", lambda: list(self.recipients))

        self._patch(state, "list_profiles", lambda: list(self.profiles))
        self._patch(state, "load_config", lambda: dict(self.config))
        self._patch(state, "get_profile_state", lambda p: dict(self.saved.get(p, {})))
        self._patch(state, "update_profile_state", self._update)
        self._patch(state, "hours_since_success", lambda p: None)

        # The single point where data would leave the machine.
        self._patch(notify, "send_to_chat", self._send)
        return self

    def __exit__(self, *exc):
        for obj, name, original in reversed(self._undo):
            setattr(obj, name, original)
        return False

    # -- stubs ---------------------------------------------------------------

    def _check(self, credentials, profile, headless=False, interactive=True):
        return self.results[profile]

    def _send(self, token, chat_id, text):
        if self.sending_fails:
            raise notify.NotifyError("Telegram is unreachable")
        self.sent.append((chat_id, text))

    def _update(self, profile, *, status, fingerprint=None, summary="",
                failures=0, success=False):
        self.saved[profile] = {
            "status": status,
            "fingerprint": fingerprint,
            "summary": summary,
            "consecutive_failures": failures,
        }

    # -- driving -------------------------------------------------------------

    def run_day(self, **results):
        """One scheduled run. Returns the messages sent during it."""
        self.results = dict(results)
        before = len(self.sent)
        self.exit_code = cli.cmd_check(argparse.Namespace(
            all=True, profile=None, headless=False, unattended=True,
            force_notify=False, if_stale=None,
        ))
        return self.sent[before:]


def _two_phones():
    return [Recipient("111", "me", ()), Recipient("222", "יעל", ())]


# --- the fine reaches people ------------------------------------------------

def test_a_fine_reaches_every_recipient():
    with _Install(_two_phones()) as install:
        messages = install.run_day(**{PROFILE: _site_result(SPEEDING)})

    assert [chat for chat, _ in messages] == ["111", "222"], messages
    assert install.exit_code == 0


def test_the_message_names_the_fine_the_amount_and_the_total():
    with _Install(_two_phones()) as install:
        (_, body), _ = install.run_day(**{PROFILE: _site_result(SPEEDING, PHONE)})

    assert "🚨" in body
    assert PROFILE in body
    assert "750.00" in body and "300.00" in body, "each fine must be listed"
    assert "1,050.00" in body, "the total must be there"
    assert "דוח מהירות" in body
    assert "ecom.gov.il" in body, "the message should link to where you pay"


def test_the_fine_is_recorded_as_reported():
    with _Install(_two_phones()) as install:
        install.run_day(**{PROFILE: _site_result(SPEEDING)})

    saved = install.saved[PROFILE]
    assert saved["status"] == STATUS_FINES
    assert saved["fingerprint"], "a delivered fine must be fingerprinted"


# --- it does not turn into daily nagging ------------------------------------

def test_the_same_fine_is_silent_the_next_day():
    with _Install(_two_phones()) as install:
        first = install.run_day(**{PROFILE: _site_result(SPEEDING)})
        second = install.run_day(**{PROFILE: _site_result(SPEEDING)})

    assert len(first) == 2
    assert second == [], "an unchanged fine must not be reported again"


def test_a_second_fine_reopens_the_alert():
    with _Install(_two_phones()) as install:
        install.run_day(**{PROFILE: _site_result(SPEEDING)})
        messages = install.run_day(**{PROFILE: _site_result(SPEEDING, PHONE)})

    assert len(messages) == 2, "a new fine is news even though the old one was reported"
    assert "1,050.00" in messages[0][1]


def test_paying_the_fine_is_reported_even_with_all_clear_off():
    """The daily all-clear is off here, so this message can only come from the
    debt having disappeared - which is exactly what you want to hear."""
    with _Install(_two_phones(), notify_all_clear=False) as install:
        install.run_day(**{PROFILE: _site_result(SPEEDING)})
        messages = install.run_day(**{PROFILE: _clean_result()})

    assert len(messages) == 2, messages
    assert "✅" in messages[0][1]
    assert install.saved[PROFILE]["status"] == STATUS_CLEAR


# --- a fine is never lost ---------------------------------------------------

def test_a_fine_nobody_could_be_told_about_is_reported_the_next_day():
    """The failure that matters most: Telegram is down on the day the fine
    appears. The fingerprint must not advance, or the fine is buried forever."""
    with _Install(_two_phones()) as install:
        install.sending_fails = True
        assert install.run_day(**{PROFILE: _site_result(SPEEDING)}) == []
        assert install.exit_code == 1
        assert not install.saved[PROFILE]["fingerprint"], "nobody heard; do not mark it reported"

        install.sending_fails = False
        recovered = install.run_day(**{PROFILE: _site_result(SPEEDING)})

    assert len(recovered) == 2, "the same fine must be retried until somebody hears"
    assert install.saved[PROFILE]["fingerprint"]


def test_one_phone_being_blocked_does_not_silence_the_other():
    class _OnlyFirstWorks(_Install):
        def _send(self, token, chat_id, text):
            if chat_id == "222":
                raise notify.NotifyError("bot was blocked by the user")
            self.sent.append((chat_id, text))

    with _OnlyFirstWorks(_two_phones()) as install:
        messages = install.run_day(**{PROFILE: _site_result(SPEEDING)})
        assert [chat for chat, _ in messages] == ["111"], messages
        assert install.exit_code == 1, "a failed recipient is still an error"
        # One person heard, so the result counts as reported and is not repeated.
        assert install.saved[PROFILE]["fingerprint"]
        assert install.run_day(**{PROFILE: _site_result(SPEEDING)}) == []


# --- household routing ------------------------------------------------------

def test_a_scoped_recipient_hears_nothing_about_other_people():
    recipients = [Recipient("111", "me", ()), Recipient("333", "אבא", ("מתן אלון",))]
    with _Install(recipients) as install:
        messages = install.run_day(**{PROFILE: _site_result(SPEEDING)})

    assert [chat for chat, _ in messages] == ["111"], messages


def test_each_persons_fine_is_reported_separately():
    with _Install(_two_phones(), profiles=("מתן אלון", PROFILE)) as install:
        messages = install.run_day(**{
            "מתן אלון": _clean_result(),
            PROFILE: _site_result(SPEEDING),
        })

    fines = [body for _, body in messages if "🚨" in body]
    assert len(fines) == 2, "both phones hear about the one person who owes money"
    assert all(PROFILE in body for body in fines)


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
