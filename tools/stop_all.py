# -*- coding: utf-8 -*-
"""전부 멈춘다 — 사용자 지시 "작동 멈춰라" (2026-09-09).

1. state/KILL 을 만든다 (신규 주문 전송 중지 — 집행·감시자가 본다)
2. IMRL_* 스케줄 작업을 전부 비활성화한다 (자기 자신 IMRL_Execute 는 마지막)
3. 제어 서버(control.py)·감시자(watchdog.py) 프로세스를 끝낸다

되돌리기: tools/resume_all.py 가 없으므로 손으로 — Enable-ScheduledTask IMRL_* 뒤
Start-ScheduledTask IMRL_Control, state/KILL 삭제.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ps(cmd: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          capture_output=True, text=True, timeout=60).stdout.strip()


def main() -> int:
    kill = ROOT / "state" / "KILL"
    kill.parent.mkdir(parents=True, exist_ok=True)
    kill.write_text(f"사용자 지시 '작동 멈춰라' {datetime.now():%Y-%m-%d %H:%M:%S}\n", encoding="utf-8")
    print(f"stop_all: KILL 생성 {kill}")

    names = _ps("(Get-ScheduledTask -TaskName 'IMRL_*' | Select-Object -ExpandProperty TaskName) -join ','")
    tasks = [n for n in names.split(",") if n]
    tasks = [t for t in tasks if t != "IMRL_Execute"] + (["IMRL_Execute"] if "IMRL_Execute" in tasks else [])
    for t in tasks:
        out = _ps(f"try {{ Disable-ScheduledTask -TaskName '{t}' -ErrorAction Stop | Out-Null; 'disabled' }} catch {{ 'FAIL ' + $_.Exception.Message }}")
        print(f"stop_all: {t} -> {out[:60]}")

    pids = _ps("Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object { $_.CommandLine -match 'control\\.py|watchdog\\.py' } | ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }")
    for line in pids.splitlines():
        pid, _, cmd = line.partition("|")
        if pid.strip().isdigit():
            r = subprocess.run(["taskkill", "/PID", pid.strip(), "/F"], capture_output=True, text=True, timeout=30)
            print(f"stop_all: pid {pid.strip()} ({'control' if 'control.py' in cmd else 'watchdog'}) rc={r.returncode}")
    print("stop_all: 끝")
    return 0


if __name__ == "__main__":
    sys.exit(main())
