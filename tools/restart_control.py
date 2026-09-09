# -*- coding: utf-8 -*-
"""제어 서버(포트 8765)를 끝낸다 — 새 코드를 읽히기 위해.

감시자가 관리자 권한으로 띄운 제어 서버는 비승격 셸에서 끝낼 수 없다(액세스 거부).
그래서 승격 도구 러너(IMRL_Execute, --run-tool restart_control)로 태운다. 끝내기만 한다.
다시 띄우는 것은 `Start-ScheduledTask IMRL_Control` 또는 다음 감시자 루프(장중 30초)가 한다.
"""
from __future__ import annotations

import subprocess
import sys


def _owner_of_port(port: int) -> int:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess"],
        capture_output=True, text=True, timeout=30).stdout.strip()
    return int(out) if out.isdigit() else 0


def _cmdline(pid: int) -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CommandLine"],
        capture_output=True, text=True, timeout=30).stdout.strip()
    return out


def main() -> int:
    pid = _owner_of_port(8765)
    if not pid:
        print("restart_control: 포트 8765 에 아무도 없다")
        return 0
    cmd = _cmdline(pid)
    if "control.py" not in cmd:
        print(f"restart_control: pid {pid} 는 제어 서버가 아니다 ({cmd[:80]!r}) — 건드리지 않는다")
        return 2
    r = subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True, timeout=30)
    print(f"restart_control: pid {pid} 종료 rc={r.returncode} {r.stdout.strip()[:80]}{r.stderr.strip()[:80]}")
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
