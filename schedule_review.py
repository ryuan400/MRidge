"""
  py -3.12 schedule_review.py 16:30      runs it every weekday at 4:30 PM (use any time, 24-hour format)
  py -3.12 schedule_review.py --remove   turns it off
  py -3.12 schedule_review.py --status   shows whether it's set up
"""

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

TASK = "MRidge Daily Review"
HERE = os.path.dirname(os.path.abspath(__file__))


def python_for_task():
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return pyw if os.path.exists(pyw) else exe


def task_xml(hhmm):
    start = (datetime.now() + timedelta(days=0)).strftime("%Y-%m-%d") + f"T{hhmm}:00"
    script = os.path.join(HERE, "daily_review.py")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek><Monday/><Tuesday/><Wednesday/><Thursday/><Friday/></DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(python_for_task())}</Command>
      <Arguments>"{escape(script)}"</Arguments>
      <WorkingDirectory>{escape(HERE)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def create(hhmm):
    try:
        datetime.strptime(hhmm, "%H:%M")
    except ValueError:
        print("Please give the time as HH:MM in 24-hour format, like 16:30.")
        return 1
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as f:
        f.write(task_xml(hhmm))
        xml_path = f.name
    code, out = run(["schtasks", "/Create", "/TN", TASK, "/XML", xml_path, "/F"])
    os.remove(xml_path)
    if code != 0:
        code, out2 = run(["schtasks", "/Create", "/TN", TASK, "/SC", "WEEKLY", "/D", "MON,TUE,WED,THU,FRI",
                          "/ST", hhmm, "/TR", f'"{python_for_task()}" "{os.path.join(HERE, "daily_review.py")}"', "/F"])
        out = out + "\n(used the simple setup instead)\n" + out2
    print(out.strip())
    if code == 0:
        print(f"\nDone. Your portfolio will be reviewed every weekday at {hhmm}.")
        print("Open the app afterwards and the review windows will be waiting for you.")
        print("Test it right now:  schtasks /Run /TN \"" + TASK + "\"   (then check daily_review.log)")
    else:
        print("\nCouldn't set it up. Copy the message above and send it to me.")
    return code


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg == "--remove":
        code, out = run(["schtasks", "/Delete", "/TN", TASK, "/F"])
        print(out.strip() or "Removed.")
        return code
    if arg == "--status":
        code, out = run(["schtasks", "/Query", "/TN", TASK])
        print(out.strip() if code == 0 else "Not set up yet.")
        return 0
    if not arg:
        print(__doc__)
        return 0
    return create(arg)


if __name__ == "__main__":
    sys.exit(main())
