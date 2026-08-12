"""Input validation for identifying numbers, so typos are caught before storing."""

import re


def normalize_digits(value: str) -> str:
    """Strip everything that is not a digit."""
    return re.sub(r"\D", "", value or "")


def is_valid_israeli_id(value: str) -> bool:
    """Israeli ID (תעודת זהות) check digit, per the standard Luhn-style algorithm.

    Shorter numbers are left-padded to 9 digits, which is how they are printed
    on older ID cards.
    """
    digits = normalize_digits(value)
    if not digits or len(digits) > 9:
        return False

    digits = digits.zfill(9)
    total = 0
    for position, char in enumerate(digits):
        step = int(char) * (1 if position % 2 == 0 else 2)
        total += step if step < 10 else step - 9
    return total % 10 == 0


def is_plausible_license(value: str) -> bool:
    """Driver's licence numbers have no public check digit, so only shape is checked.

    The upper bound matches the site's own maxLength of 7 on the k_id_num field;
    anything longer would be silently truncated by the form. The lower bound is
    kept loose on purpose, so a legitimate short number is never rejected here.
    """
    digits = normalize_digits(value)
    return 1 <= len(digits) <= 7


def is_valid_profile_name(name: str) -> bool:
    """Profile names become keyring entry names and appear in notifications."""
    return bool(name) and len(name) <= 40 and "::" not in name
