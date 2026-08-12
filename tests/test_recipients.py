"""Routing tests for multi-recipient alerts.

The properties that matter: an existing single-recipient install keeps working,
scoped recipients only hear about their own profiles, and no edit can silently
widen someone's scope to "everything".
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knaswatch.vault import Recipient  # noqa: E402


def test_empty_profiles_means_every_profile():
    r = Recipient(chat_id="1", label="me", profiles=())
    assert r.wants("אבא")
    assert r.wants("אמא")
    assert r.wants("anyone at all")


def test_scoped_recipient_only_wants_their_profile():
    r = Recipient(chat_id="2", label="אמא", profiles=("אמא",))
    assert r.wants("אמא")
    assert not r.wants("אבא")


def test_describe_shows_scope():
    assert "all profiles" in Recipient("1", "me", ()).describe()
    assert "אמא" in Recipient("2", "אמא", ("אמא",)).describe()


def _migrate(data):
    """Mirror of vault.load_recipients' migration branch, without the keyring."""
    stored = data.get("recipients")
    if not stored:
        return [Recipient(chat_id=data["chat_id"], label="me", profiles=())]
    return [
        Recipient(
            chat_id=str(i["chat_id"]),
            label=i.get("label") or str(i["chat_id"]),
            profiles=tuple(i.get("profiles") or ()),
        )
        for i in stored
    ]


def test_old_config_migrates_to_single_recipient():
    """An install predating multi-recipient support must keep working."""
    got = _migrate({"token": "t", "chat_id": "555"})
    assert got == [Recipient("555", "me", ())], got
    assert got[0].wants("any profile")


def test_stored_recipients_are_parsed():
    got = _migrate({
        "token": "t",
        "chat_id": "555",
        "recipients": [
            {"chat_id": 555, "label": "me", "profiles": []},
            {"chat_id": 777, "label": "אמא", "profiles": ["אמא"]},
        ],
    })
    assert got[0] == Recipient("555", "me", ()), got[0]
    assert got[1] == Recipient("777", "אמא", ("אמא",)), got[1]


def _retarget(recipients, old, new=None):
    """Mirror of _retarget_recipients' pure logic."""
    updated, dropped = [], []
    for r in recipients:
        if not r.profiles or old not in r.profiles:
            updated.append(r)
            continue
        profiles = tuple(p for p in r.profiles if p != old)
        if new:
            profiles += (new,)
        if profiles:
            updated.append(r._replace(profiles=profiles))
        else:
            dropped.append(r.label)
    return updated, dropped


def test_rename_follows_the_profile():
    before = [Recipient("2", "אמא", ("Mum",))]
    after, dropped = _retarget(before, "Mum", "אמא")
    assert after[0].profiles == ("אמא",), after
    assert not dropped
    assert after[0].wants("אמא")


def test_delete_drops_a_recipient_rather_than_widening_scope():
    """The dangerous case: an emptied filter would mean 'all profiles'."""
    before = [Recipient("1", "me", ()), Recipient("2", "אמא", ("אמא",))]
    after, dropped = _retarget(before, "אמא")
    assert dropped == ["אמא"], dropped
    assert [r.label for r in after] == ["me"], after
    # The unscoped owner is untouched.
    assert after[0].wants("אבא")


def test_delete_keeps_other_profiles_in_a_multi_scope_recipient():
    before = [Recipient("3", "both", ("אבא", "אמא"))]
    after, dropped = _retarget(before, "אמא")
    assert after[0].profiles == ("אבא",), after
    assert not dropped


def test_unscoped_recipient_is_never_touched_by_rename():
    before = [Recipient("1", "me", ())]
    after, dropped = _retarget(before, "אבא", "someone else")
    assert after == before
    assert not dropped


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
