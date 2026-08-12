"""Offline tests for the Telegram pairing and token-redaction logic.

The pairing property under test: only the chat that echoes the exact code is
linked. An arbitrary message to the bot — which anyone on Telegram can send —
must never be accepted.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knaswatch.checker import CheckResult, STATUS_CLEAR, STATUS_ERROR, STATUS_FINES  # noqa: E402
from knaswatch.notify import _find_code_match, _redact_token, format_result  # noqa: E402


def _update(update_id, chat_id, text, first_name="Someone", username=None,
            chat_type="private"):
    chat = {"id": chat_id, "first_name": first_name, "type": chat_type}
    if username:
        chat["username"] = username
    return {"update_id": update_id, "message": {"chat": chat, "text": text}}


def test_random_greeting_does_not_match():
    """The real-world case: a family member simply says hello to the bot."""
    updates = [_update(1, 111, "Hello, this is Alice", "Alice")]
    assert _find_code_match(updates, "483920") is None


def test_stranger_messages_never_match_without_code():
    updates = [
        _update(1, 999, "hi"),
        _update(2, 999, "/start"),
        _update(3, 999, "483920 is my guess almost"),
    ]
    assert _find_code_match(updates, "483920") is None


def test_exact_code_matches_and_reports_sender():
    updates = [
        _update(1, 999, "hi", "Stranger"),
        _update(2, 111, "483920", "Bob", username="bob1"),
    ]
    match = _find_code_match(updates, "483920")
    assert (match.chat_id, match.description) == ("111", "Bob (@bob1)"), match
    assert match.is_private


def test_code_with_surrounding_whitespace_matches():
    updates = [_update(1, 111, "  483920 \n", "Bob")]
    match = _find_code_match(updates, "483920")
    assert (match.chat_id, match.description) == ("111", "Bob"), match


def test_latest_matching_chat_wins():
    """If the code somehow appears twice, the most recent send is used."""
    updates = [
        _update(1, 111, "483920", "First"),
        _update(2, 222, "483920", "Second"),
    ]
    match = _find_code_match(updates, "483920")
    assert match is not None and match.chat_id == "222", match


def test_group_chat_is_flagged_as_not_private():
    """A group must be refusable: every member would see the fine details."""
    updates = [{"update_id": 1, "message": {
        "chat": {"id": -100123, "title": "Family Group", "type": "supergroup"},
        "text": "483920"}}]
    match = _find_code_match(updates, "483920")
    assert match is not None
    assert not match.is_private, match
    assert match.chat_type == "supergroup"
    assert match.description == "Family Group"


def test_html_special_characters_are_escaped_in_fine_labels():
    """A '<' in a site-supplied description used to make Telegram reject the
    whole message with 400, losing the alert entirely."""
    result = CheckResult(status=STATUS_FINES, summary="x",
                         fines=[{"label": "speed <60 km/h & over", "amount": 100.0}],
                         total_amount=100.0)
    body = format_result("test", result)
    assert "&lt;60" in body, body
    assert "&amp; over" in body, body
    assert "<60" not in body, body


def test_html_special_characters_are_escaped_in_profile_names():
    body = format_result("a<b & c", CheckResult(status=STATUS_CLEAR, summary="clear"))
    assert "a&lt;b &amp; c" in body, body
    # The formatting tags we add ourselves must survive.
    assert "<b>" in body and "</b>" in body


def test_error_summary_is_escaped():
    body = format_result("p", CheckResult(status=STATUS_ERROR, summary="failed <badly>"))
    assert "&lt;badly&gt;" in body, body


def test_code_inside_longer_text_does_not_match():
    updates = [_update(1, 999, "the code is 483920 right?")]
    assert _find_code_match(updates, "483920") is None


def test_updates_without_message_are_skipped():
    updates = [{"update_id": 1, "edited_message": {"chat": {"id": 5}}}]
    assert _find_code_match(updates, "483920") is None


def test_token_is_redacted_from_error_text():
    token = "1234567890:AAEexampleSECRETtokenvalue"
    text = f"ConnectError for url https://api.telegram.org/bot{token}/getUpdates"
    cleaned = _redact_token(text, token)
    assert token not in cleaned
    assert "***token***" in cleaned


def test_redaction_with_empty_token_is_harmless():
    assert _redact_token("some error", "") == "some error"


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
