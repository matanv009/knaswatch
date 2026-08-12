"""The only place identifying numbers are ever stored: the OS credential vault.

On Windows this is the Credential Manager, on macOS the Keychain, on Linux the
Secret Service (GNOME Keyring / KWallet). Nothing here ever writes to a file.
"""

import json
from typing import NamedTuple, Optional

import keyring

from . import APP_NAME

SERVICE = APP_NAME
_TELEGRAM_KEY = "telegram"


class Credentials(NamedTuple):
    id_number: str
    license_number: str


class TelegramConfig(NamedTuple):
    token: str
    chat_id: str


class Recipient(NamedTuple):
    """A Telegram chat that receives alerts.

    An empty `profiles` means "every profile"; otherwise the recipient only
    hears about the profiles named, which is how one household can route each
    person's fines to that person without sharing everyone's.
    """

    chat_id: str
    label: str
    profiles: tuple = ()

    def wants(self, profile: str) -> bool:
        return not self.profiles or profile in self.profiles

    def describe(self) -> str:
        scope = "all profiles" if not self.profiles else ", ".join(self.profiles)
        return f"{self.label} [{scope}]"


class VaultError(RuntimeError):
    """Raised when the OS credential vault is unavailable or refuses a write."""


def _profile_key(profile: str) -> str:
    return f"profile::{profile}"


def _get(key: str) -> Optional[dict]:
    try:
        raw = keyring.get_password(SERVICE, key)
    except Exception as exc:  # keyring raises backend-specific errors
        raise VaultError(f"Could not read from the credential vault: {exc}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VaultError(f"Stored entry '{key}' is corrupt; re-run setup.") from exc


def _set(key: str, payload: dict) -> None:
    try:
        keyring.set_password(SERVICE, key, json.dumps(payload))
    except Exception as exc:
        raise VaultError(f"Could not write to the credential vault: {exc}") from exc


def _delete(key: str) -> None:
    try:
        keyring.delete_password(SERVICE, key)
    except keyring.errors.PasswordDeleteError:
        pass
    except Exception as exc:
        raise VaultError(f"Could not delete from the credential vault: {exc}") from exc


def save_credentials(profile: str, id_number: str, license_number: str) -> None:
    _set(_profile_key(profile), {"id": id_number, "license": license_number})


def load_credentials(profile: str) -> Optional[Credentials]:
    data = _get(_profile_key(profile))
    if not data:
        return None
    return Credentials(id_number=data["id"], license_number=data["license"])


def delete_credentials(profile: str) -> None:
    _delete(_profile_key(profile))


def save_telegram(token: str, chat_id: str, recipients=None) -> None:
    payload = {"token": token, "chat_id": chat_id}
    if recipients is not None:
        payload["recipients"] = [
            {"chat_id": r.chat_id, "label": r.label, "profiles": list(r.profiles)}
            for r in recipients
        ]
    _set(_TELEGRAM_KEY, payload)


def load_telegram() -> Optional[TelegramConfig]:
    data = _get(_TELEGRAM_KEY)
    if not data:
        return None
    return TelegramConfig(token=data["token"], chat_id=data["chat_id"])


def load_recipients() -> list:
    """Every chat that should be notified.

    Configurations written before multi-recipient support have no `recipients`
    key; for those the original chat id is returned as the sole recipient, so an
    existing install keeps working untouched.
    """
    data = _get(_TELEGRAM_KEY)
    if not data:
        return []

    stored = data.get("recipients")
    if not stored:
        return [Recipient(chat_id=data["chat_id"], label="me", profiles=())]

    return [
        Recipient(
            chat_id=str(item["chat_id"]),
            label=item.get("label") or str(item["chat_id"]),
            profiles=tuple(item.get("profiles") or ()),
        )
        for item in stored
    ]


def save_recipients(recipients) -> None:
    """Replace the recipient list, keeping the token and owner chat id."""
    data = _get(_TELEGRAM_KEY)
    if not data:
        raise VaultError("Telegram is not configured yet.")
    save_telegram(data["token"], data["chat_id"], recipients)


def delete_telegram() -> None:
    _delete(_TELEGRAM_KEY)


def vault_backend_name() -> str:
    """Human-readable backend name, shown during setup so the user knows where
    their numbers went."""
    try:
        backend = keyring.get_keyring()
        return f"{type(backend).__module__}.{type(backend).__name__}"
    except Exception as exc:
        return f"unavailable ({exc})"
