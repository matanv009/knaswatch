"""Entry point for the scheduled task.

`python -m knaswatch` finds the package only when the current directory happens
to be this one, which made the daily task fail silently with "No module named
knaswatch" - and under pythonw.exe there is no stderr to carry the error, so it
looked like nothing ran at all.

Running a script instead makes Python put *this file's* directory on sys.path,
so the package is importable no matter what the working directory is.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from knaswatch.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
