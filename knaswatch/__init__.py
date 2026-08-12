"""KnasWatch - a daily checker for Israeli traffic fines.

Runs entirely on the user's own machine. Identifying numbers live only in the
operating system's credential vault; see SECURITY.md.
"""

import sys

__version__ = "1.0.0"

APP_NAME = "KnasWatch"

# How to invoke the tool, for messages shown to the user. On Windows the batch
# wrapper is preferred because it uses the project's virtual environment without
# the user having to activate it first.
INVOCATION = "knaswatch.bat" if sys.platform == "win32" else "python -m knaswatch"
