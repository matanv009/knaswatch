"""Tests for what keeps the site from seeing one machine as one busy robot.

Two people checked from a single browser profile, seconds apart, is the least
human pattern this tool can produce. These cover the three parts of the fix:
a browser profile per person, a gap between people, and a CAPTCHA message whose
instruction can actually be followed.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knaswatch import checker  # noqa: E402
from knaswatch import __main__ as cli  # noqa: E402
from knaswatch.checker import CheckResult, STATUS_CHALLENGE  # noqa: E402
from knaswatch.notify import format_result  # noqa: E402


# --- one browser profile per person -----------------------------------------

def test_each_person_gets_their_own_browser_profile():
    a = checker.browser_profile_dir("מתן אלון")
    b = checker.browser_profile_dir("יעל אלון")
    assert a != b, (a, b)


def test_the_same_person_always_gets_the_same_directory():
    """It is a persistent profile: a different path each run would throw away
    the browsing history that keeps reCAPTCHA quiet."""
    assert checker.browser_profile_dir("יעל אלון") == checker.browser_profile_dir("יעל אלון")


def test_directory_name_holds_no_hebrew_and_no_real_name():
    """A non-ASCII user-data-dir has broken Chrome on Windows before, and a
    nickname here is somebody's actual name."""
    name = checker.browser_profile_dir("יעל אלון").name
    assert name.isascii(), name
    assert "יעל" not in name


def test_new_directories_do_not_collide_with_the_shared_one():
    got = checker.browser_profile_dir("מתן אלון")
    assert got != checker.LEGACY_PROFILE_DIR, got


def _with_temp_data_dir(fn):
    """Run fn(tmp) with the module's directories pointed at a temporary folder."""
    original_data, original_legacy = checker.DATA_DIR, checker.LEGACY_PROFILE_DIR
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        checker.DATA_DIR = tmp
        checker.LEGACY_PROFILE_DIR = tmp / "browser-profile"
        try:
            fn(tmp)
        finally:
            checker.DATA_DIR, checker.LEGACY_PROFILE_DIR = original_data, original_legacy


def test_the_shared_profile_is_moved_rather_than_abandoned():
    """The old directory carries whatever standing we have with reCAPTCHA.
    Dropping it would cold-start everybody at once."""
    def scenario(tmp):
        legacy = tmp / "browser-profile"
        (legacy / "Default").mkdir(parents=True)
        (legacy / "Default" / "Cookies").write_text("history")

        target = tmp / "browser-profile--aaaaaaaaaaaa"
        checker._claim_legacy_profile(target)

        assert (target / "Default" / "Cookies").read_text() == "history"
        assert not legacy.exists(), "the shared profile should have moved, not been copied"

    _with_temp_data_dir(scenario)


def test_only_the_first_person_claims_the_shared_profile():
    """Handing the same history to two browsers would link them together, which
    is worse than one of them starting cold."""
    def scenario(tmp):
        legacy = tmp / "browser-profile"
        legacy.mkdir()
        (legacy / "marker").write_text("x")

        first = tmp / "browser-profile--111111111111"
        checker._claim_legacy_profile(first)

        second = tmp / "browser-profile--222222222222"
        checker._claim_legacy_profile(second)

        assert (first / "marker").exists()
        assert not second.exists(), "the second person must start fresh"

    _with_temp_data_dir(scenario)


def test_an_existing_profile_is_never_overwritten():
    def scenario(tmp):
        legacy = tmp / "browser-profile"
        legacy.mkdir()
        (legacy / "old").write_text("old")

        target = tmp / "browser-profile--333333333333"
        target.mkdir()
        (target / "mine").write_text("mine")

        checker._claim_legacy_profile(target)

        assert (target / "mine").exists()
        assert not (target / "old").exists()
        assert legacy.exists(), "nothing to migrate into an occupied directory"

    _with_temp_data_dir(scenario)


def test_missing_shared_profile_is_not_an_error():
    """A fresh install has no directory to migrate."""
    _with_temp_data_dir(
        lambda tmp: checker._claim_legacy_profile(tmp / "browser-profile--444444444444")
    )


# --- a gap between people ---------------------------------------------------

def test_unattended_runs_wait_longer_than_watched_ones():
    """Nobody is sitting in front of the nightly run, so it can afford to look
    unhurried; a person waiting at the screen cannot."""
    watched = [cli._profile_gap_seconds(interactive=True) for _ in range(200)]
    nightly = [cli._profile_gap_seconds(interactive=False) for _ in range(200)]
    assert max(watched) < min(nightly), (max(watched), min(nightly))


def test_gaps_stay_inside_their_range():
    for _ in range(200):
        assert 15.0 <= cli._profile_gap_seconds(interactive=True) <= 45.0
        assert 120.0 <= cli._profile_gap_seconds(interactive=False) <= 420.0


def test_the_gap_is_not_a_fixed_number():
    """A constant delay is itself a signature."""
    seen = {round(cli._profile_gap_seconds(interactive=False), 3) for _ in range(50)}
    assert len(seen) > 1


# --- a CAPTCHA message you can act on ---------------------------------------

def _challenge_body(platform: str) -> str:
    original = sys.platform
    sys.platform = platform
    try:
        return format_result("יעל אלון", CheckResult(status=STATUS_CHALLENGE,
                                                     summary="נדרש אימות CAPTCHA"))
    finally:
        sys.platform = original


def test_windows_message_points_at_the_menu_not_a_hebrew_argument():
    """The old text asked the reader to run 'check --profile <Hebrew name>',
    which cannot be typed into a Windows console at all."""
    body = _challenge_body("win32")
    # The bodies are Hebrew, so failures report a flag rather than the text: a
    # Windows console that is not UTF-8 cannot print it.
    assert "--profile" not in body, "the message still names --profile"
    assert "12" in body, "the message does not name the menu entry"


def test_other_platforms_still_get_a_runnable_command():
    body = _challenge_body("linux")
    assert "--profile" not in body, "the message still names --profile"
    assert "--if-stale 12" in body, "no runnable command in the message"


def test_the_challenged_person_is_still_named():
    for platform in ("win32", "linux"):
        assert "יעל אלון" in _challenge_body(platform)


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
