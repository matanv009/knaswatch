"""Logging with a redaction filter applied at the formatter level.

The filter is the safety net, not the primary defence: no call site is supposed
to pass an identifying number to the logger in the first place. It exists so
that a future careless log line cannot leak one either.
"""

import logging
import re
import sys
from logging.handlers import RotatingFileHandler

from .state import LOG_FILE, ensure_dir

# Any run of 7+ digits is treated as potentially identifying and masked.
# Fine amounts are 3-4 digits, so ordinary output is unaffected.
_LONG_DIGIT_RUN = re.compile(r"\d{7,}")

# For things worth keeping but not worth showing: chiefly the site's own error
# text, which is Hebrew. A Windows console has no bidirectional text support, so
# it prints Hebrew back to front - readable enough to be trusted, wrong enough to
# be misread. This logger is a child of "knaswatch" with propagation off, so its
# records reach the log file and stop there.
file_log = logging.getLogger("knaswatch.detail")
file_log.propagate = False


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


def make_console_safe() -> None:
    """Stop Hebrew output from killing the process on a non-UTF-8 console.

    A scheduled task inherits the legacy code page (cp1252 here), so printing a
    Hebrew profile name raised UnicodeEncodeError and the whole run died before
    it could log anything - silently, because pythonw.exe has no stderr to show
    the traceback on. Interactive shells with PYTHONIOENCODING=utf-8 never hit
    it, which is exactly why it survived testing.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def close_log_file() -> None:
    """Let go of the log file, so the data directory can be deleted.

    Windows refuses to delete a file that is still open, and the uninstaller
    would otherwise fail on the one file it is itself holding.
    """
    for logger in (logging.getLogger("knaswatch"), file_log):
        for handler in list(logger.handlers):
            if isinstance(handler, RotatingFileHandler):
                logger.removeHandler(handler)
                handler.close()


def setup_logging(verbose: bool = False) -> logging.Logger:
    ensure_dir()
    make_console_safe()
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

    file_log.setLevel(logging.DEBUG if verbose else logging.INFO)
    file_log.addHandler(file_handler)

    # Under pythonw.exe there is no console at all and sys.stderr is None;
    # attaching a stream handler to it would raise on every single record.
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(message)s"))
        console.addFilter(redactor)
        logger.addHandler(console)

    return logger
