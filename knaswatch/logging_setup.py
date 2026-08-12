"""Logging with a redaction filter applied at the formatter level.

The filter is the safety net, not the primary defence: no call site is supposed
to pass an identifying number to the logger in the first place. It exists so
that a future careless log line cannot leak one either.
"""

import logging
import re
from logging.handlers import RotatingFileHandler

from .state import LOG_FILE, ensure_dir

# Any run of 7+ digits is treated as potentially identifying and masked.
# Fine amounts are 3-4 digits, so ordinary output is unaffected.
_LONG_DIGIT_RUN = re.compile(r"\d{7,}")


def redact(text: str) -> str:
    return _LONG_DIGIT_RUN.sub(lambda m: "*" * len(m.group()), text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(
                redact(a) if isinstance(a, str) else a for a in record.args
            )
        return True


def setup_logging(verbose: bool = False) -> logging.Logger:
    ensure_dir()
    logger = logging.getLogger("knaswatch")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    redactor = RedactingFilter()
    formatter = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s")

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=512_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    console.addFilter(redactor)
    logger.addHandler(console)

    return logger
