"""Telegram delivery.

Messages carry the profile nickname and the outcome only. Identifying numbers
are never passed to this module, let alone sent over the network.
"""

import html
import logging
import sys
import time
from typing import NamedTuple, Optional

import httpx

from . import INVOCATION
from .checker import (
    STATUS_CHALLENGE,
    STATUS_CLEAR,
    STATUS_ERROR,
    STATUS_FINES,
    CheckResult,
)
from .vault import TelegramConfig

log = logging.getLogger("knaswatch")

API_BASE = "https://api.telegram.org"
TIMEOUT = 20.0


class NotifyError(RuntimeError):
    pass


def _redact_token(text: str, token: str) -> str:
    """Bot API URLs embed the token, and httpx error strings embed the URL.
    Nothing that might contain the token may leave this module unredacted."""
    return text.replace(token, "***token***") if token else text


def send_message(config: TelegramConfig, text: str) -> None:
    send_to_chat(config.token, config.chat_id, text)


def broadcast(token: str, recipients: list, profile: str, text: str) -> list:
    """Send one alert to every recipient subscribed to `profile`.

    Returns the labels that failed. Delivery is attempted for all of them even
    if one fails: a blocked bot or a stale chat id must not silence the others.
    """
    failed = []
    for recipient in recipients:
        if not recipient.wants(profile):
            continue
        try:
            send_to_chat(token, recipient.chat_id, text)
        except NotifyError as exc:
            log.error("Could not notify %s: %s", recipient.label, exc)
            failed.append(recipient.label)
    return failed


def send_to_chat(token: str, chat_id: str, text: str) -> None:
    url = f"{API_BASE}/bot{token}/sendMessage"
    try:
        response = httpx.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise NotifyError(
            f"Could not reach Telegram: {_redact_token(str(exc), token)}"
        ) from None

    if response.status_code != 200:
        # The token appears in the URL, so only the body is surfaced.
        raise NotifyError(f"Telegram rejected the message ({response.status_code}): "
                          f"{response.text[:200]}")


class PairedChat(NamedTuple):
    chat_id: str
    description: str
    chat_type: str

    @property
    def is_private(self) -> bool:
        return self.chat_type == "private"


def _find_code_match(updates: list, code: str) -> Optional[PairedChat]:
    """Return the chat that sent exactly `code`, or None.

    Only an exact text match counts. A stranger who merely messages the bot can
    never match, because the code exists only on the owner's screen. The chat
    type is carried back so the caller can refuse group chats, where every
    member would see the fine details.
    """
    for update in reversed(updates):
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("id") is None:
            continue
        if (message.get("text") or "").strip() != code:
            continue
        name = " ".join(
            part for part in (chat.get("first_name"), chat.get("last_name")) if part
        ) or chat.get("title") or "chat"
        username = chat.get("username")
        description = f"{name} (@{username})" if username else name
        return PairedChat(str(chat["id"]), description, chat.get("type") or "private")
    return None


def pair_chat(token: str, code: str, wait_seconds: int = 120) -> Optional[PairedChat]:
    """Wait for the pairing code to arrive at the bot.

    Each poll confirms the updates it has seen by advancing `offset`. Without
    that, Telegram keeps returning the same first 100 unconfirmed updates, and a
    bot with a backlog would never surface the newly sent code.

    raise_for_status is deliberately not used anywhere here: its exception text
    contains the full request URL, which contains the token.
    """
    deadline = time.monotonic() + wait_seconds
    url = f"{API_BASE}/bot{token}/getUpdates"
    offset = None

    while True:
        params = {"offset": offset} if offset is not None else {}
        try:
            response = httpx.get(url, params=params, timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise NotifyError(
                f"Could not reach Telegram: {_redact_token(str(exc), token)}"
            ) from None

        if response.status_code == 401:
            raise NotifyError("Telegram rejected the token (401). Check it with @BotFather.")
        if response.status_code == 200:
            updates = response.json().get("result", [])
            found = _find_code_match(updates, code)
            if found:
                return found
            if updates:
                # Acknowledge what we have read so the next poll can return
                # newer messages instead of the same backlog.
                offset = max(u.get("update_id", 0) for u in updates) + 1

        if time.monotonic() > deadline:
            return None
        time.sleep(2)


def format_result(profile: str, result: CheckResult) -> str:
    """Build the notification body. Takes only the nickname and the result.

    Everything interpolated here is escaped: the message is sent with
    parse_mode=HTML, and fine descriptions come from the government site, so a
    single '<' in a description would otherwise make Telegram reject the whole
    message and the fine would go unreported.
    """
    name = html.escape(profile)

    if result.status == STATUS_FINES:
        lines = [f"🚨 <b>KnasWatch</b> - נמצאו קנסות עבור <b>{name}</b>", ""]
        for fine in result.fines:
            label = html.escape(str(fine["label"]))
            lines.append(f"• {label} - {fine['amount']:,.2f} ₪")
        if result.total_amount is not None:
            lines.append("")
            lines.append(f"<b>סה\"כ: {result.total_amount:,.2f} ₪</b>")
        lines.append("")
        lines.append("https://ecom.gov.il/voucherspa/input/318")
        return "\n".join(lines)

    if result.status == STATUS_CLEAR:
        return f"✅ <b>KnasWatch</b> - אין קנסות עבור <b>{name}</b>"

    if result.status == STATUS_CHALLENGE:
        # Deliberately not 'check --profile <name>'. The nicknames are Hebrew,
        # and a Hebrew argument cannot be typed into a Windows console - so the
        # message used to name a command its reader was unable to run. The menu
        # entry needs no typing, and --if-stale means the people already checked
        # today are not sent back to the site for nothing.
        if sys.platform == "win32":
            how = ("פתח את <code>knaswatch.bat</code> ובחר באפשרות 12 "
                   "(<code>Finish an interrupted check</code>).")
        else:
            how = ("הרץ במחשב:\n"
                   f"<code>{html.escape(INVOCATION)} check --all --if-stale 12</code>")
        return (
            f"🔐 <b>KnasWatch</b> - האתר ביקש אימות CAPTCHA עבור <b>{name}</b>\n\n"
            f"הבדיקה לא הושלמה. {how}\n"
            "פתור את האימות בחלון שנפתח, והבדיקה תמשיך מעצמה."
        )

    return (
        f"⚠️ <b>KnasWatch</b> - הבדיקה עבור <b>{name}</b> נכשלה\n\n"
        f"{html.escape(result.summary or '')}"
    )


__all__ = [
    "NotifyError",
    "PairedChat",
    "TelegramConfig",
    "send_message",
    "send_to_chat",
    "broadcast",
    "pair_chat",
    "format_result",
]
