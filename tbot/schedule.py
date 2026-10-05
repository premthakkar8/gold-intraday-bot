"""Register the scheduled run with Windows Task Scheduler."""

from __future__ import annotations

import subprocess
import sys

from tbot.config import ROOT

TASK_NAME = "tBOT Gold"


def _python() -> str:
    exe = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if exe.exists():
        return str(exe)
    current = sys.executable
    windowless = current.replace("python.exe", "pythonw.exe")
    return windowless


def _powershell(script: str) -> tuple[int, str]:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output.strip()


def install(minutes: int) -> tuple[int, str]:
    python = _python().replace("'", "''")
    main = str(ROOT / "main.py").replace("'", "''")
    root = str(ROOT).replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute '{python}' -Argument '"{main}" auto' -WorkingDirectory '{root}'
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes {int(minutes)})
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger $trigger -Settings $settings -Description 'Gold intraday brief every {int(minutes)} minutes while the market is open' -Force | Out-Null
'installed'
"""
    return _powershell(script)


def remove() -> tuple[int, str]:
    script = f"""
$task = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue
if ($task) {{ Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false; 'removed' }} else {{ 'not installed' }}
"""
    return _powershell(script)


def status() -> tuple[int, str]:
    script = f"""
$task = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue
if (-not $task) {{ 'not installed'; exit 0 }}
$info = Get-ScheduledTaskInfo -TaskName '{TASK_NAME}'
"state: " + $task.State
"every: " + $task.Triggers[0].Repetition.Interval
"last run: " + $info.LastRunTime
"last result: " + $info.LastTaskResult
"next run: " + $info.NextRunTime
"""
    return _powershell(script)
