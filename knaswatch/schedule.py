"""Creates the daily Windows scheduled task.

Two deliberate choices: the task runs only when the user is logged on (so no
Windows account password ever has to be stored), and it starts as soon as
possible after a missed start (so the machine does not need to be on 24/7 -
a check missed overnight simply runs at the next boot).
"""

import getpass
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from .state import DATA_DIR, ensure_dir

TASK_NAME = "KnasWatch Daily Check"

_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>KnasWatch - daily check for Israeli traffic fines.</Description>
  </RegistrationInfo>
  <Triggers>
{triggers}  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _python_for_background() -> str:
    """Prefer pythonw.exe so the daily run does not flash a console window."""
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else executable)


_TRIGGER = """    <CalendarTrigger>
      <StartBoundary>2026-01-01T{time}:00</StartBoundary>
      <Enabled>true</Enabled>
      <RandomDelay>PT20M</RandomDelay>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
"""


def parse_time(at_time: str) -> tuple:
    """Validate an HH:MM string, raising RuntimeError with a readable message.

    RuntimeError rather than ValueError because that is what the CLI catches;
    an unvalidated value used to surface as a raw traceback, and '25:00' used to
    be accepted and silently wrapped round to 01:00.
    """
    parts = (at_time or "").split(":")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        raise RuntimeError(f"'{at_time}' is not a time. Use HH:MM, for example 09:00.")

    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise RuntimeError(f"'{at_time}' is not a valid time of day (00:00 to 23:59).")
    return hour, minute


def _retry_times(at_time: str, count: int = 3, gap_hours: int = 5) -> list[str]:
    """The main time plus spare attempts later the same day.

    Only the first one normally contacts the site: the later runs use --if-stale
    and do nothing once the day's check has succeeded. They exist so that a rare
    CAPTCHA challenge costs a few hours rather than the whole day.
    """
    hour, minute = parse_time(at_time)
    return [f"{(hour + gap_hours * i) % 24:02d}:{minute:02d}" for i in range(count)]


def create_task(at_time: str = "09:00") -> str:
    if sys.platform != "win32":
        raise RuntimeError(
            "Automatic scheduling is implemented for Windows only. "
            "On macOS/Linux add a cron entry - see the README."
        )

    ensure_dir()
    project_root = Path(__file__).resolve().parent.parent
    triggers = "".join(_TRIGGER.format(time=t) for t in _retry_times(at_time))
    # Every interpolated value is XML-escaped: a username or an install path
    # containing '&' (or '<') would otherwise produce a malformed document and
    # schtasks would fail with an unhelpful error.
    xml = _TASK_XML.format(
        triggers=triggers,
        user=escape(getpass.getuser()),
        command=escape(_python_for_background()),
        # --if-stale 20 keeps this to roughly one real visit per day: the later
        # triggers return immediately unless the earlier one failed.
        arguments=escape("-m knaswatch check --all --unattended --if-stale 20"),
        workdir=escape(str(project_root)),
    )

    xml_path = DATA_DIR / "task.xml"
    xml_path.write_text(xml, encoding="utf-16")

    result = subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"],
        capture_output=True,
        text=True,
    )
    xml_path.unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"schtasks failed: {(result.stderr or result.stdout).strip()}"
        )
    return TASK_NAME


def delete_task() -> None:
    subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
    )


def task_status() -> str:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "not scheduled"
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "scheduled"
