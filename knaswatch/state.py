"""Non-secret configuration and last-check results.

Everything here lives outside the repository folder (%LOCALAPPDATA% on Windows)
so it can never be committed by accident, and it deliberately contains no
identifying numbers - only profile nicknames and outcomes.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from platformdirs import user_data_dir

from . import APP_NAME

DATA_DIR = Path(user_data_dir(APP_NAME, appauthor=False))
CONFIG_FILE = DATA_DIR / "config.json"
STATE_FILE = DATA_DIR / "state.json"
LOG_FILE = DATA_DIR / "knaswatch.log"

DEFAULT_CONFIG: dict[str, Any] = {
    "profiles": [],
    "notify_all_clear": False,
}


def ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return dict(fallback)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(fallback)


def _write_json(path: Path, payload: dict) -> None:
    ensure_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_config() -> dict:
    config = _read_json(CONFIG_FILE, DEFAULT_CONFIG)
    for key, value in DEFAULT_CONFIG.items():
        config.setdefault(key, value)
    return config


def save_config(config: dict) -> None:
    _write_json(CONFIG_FILE, config)


def list_profiles() -> list[str]:
    return list(load_config()["profiles"])


def add_profile(name: str) -> None:
    config = load_config()
    if name not in config["profiles"]:
        config["profiles"].append(name)
    save_config(config)


def rename_profile(old: str, new: str) -> None:
    """Move a profile's config entry and history to a new nickname."""
    config = load_config()
    config["profiles"] = [new if p == old else p for p in config["profiles"]]
    save_config(config)

    state = load_state()
    if old in state:
        state[new] = state.pop(old)
        save_state(state)


def reset_profile(name: str) -> None:
    """Forget a profile's results while keeping the profile itself.

    Used when the stored numbers are corrected: the recorded outcome describes
    whoever the old numbers belonged to. Left in place, last_success_at would
    make --if-stale skip the first check with the new numbers, and the stored
    fingerprint would be compared against a different person's fines.
    """
    state = load_state()
    if state.pop(name, None) is not None:
        save_state(state)


def remove_profile(name: str) -> None:
    config = load_config()
    config["profiles"] = [p for p in config["profiles"] if p != name]
    save_config(config)

    state = load_state()
    state.pop(name, None)
    save_state(state)


def load_state() -> dict:
    return _read_json(STATE_FILE, {})


def save_state(state: dict) -> None:
    _write_json(STATE_FILE, state)


def get_profile_state(profile: str) -> dict:
    return load_state().get(profile, {})


def update_profile_state(
    profile: str,
    *,
    status: str,
    fingerprint: Optional[str] = None,
    summary: str = "",
    failures: int = 0,
    success: bool = False,
) -> None:
    state = load_state()
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    previous = state.get(profile, {})
    state[profile] = {
        "status": status,
        "fingerprint": fingerprint,
        "summary": summary,
        "consecutive_failures": failures,
        "checked_at": now,
        # Kept separate from checked_at: a failed attempt must not make the day
        # look done, or a CAPTCHA would silently cost us that day's check.
        "last_success_at": now if success else previous.get("last_success_at"),
    }
    save_state(state)


# Kept as literals rather than imported from checker, which already imports this
# module. Only successful statuses count as a completed check.
_SUCCESS_STATUSES = ("clear", "fines")


def hours_since_success(profile: str) -> Optional[float]:
    """Hours since this profile last completed a real check, or None if never."""
    profile_state = get_profile_state(profile)
    stamp = profile_state.get("last_success_at")
    if not stamp and profile_state.get("status") in _SUCCESS_STATUSES:
        # State written before last_success_at existed: the old checked_at is a
        # success timestamp whenever the recorded status was a successful one.
        stamp = profile_state.get("checked_at")
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds() / 3600
