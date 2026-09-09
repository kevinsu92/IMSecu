# -*- coding: utf-8 -*-
"""무인 운영 준비 점검. **한 번의 로그인이 대회 내내 유지되는가.**

자동 로그인은 이 프로그램이 하지 않는다. 그래서 답은 로그인을 자동화하는 것이
아니라 **로그인이 다시 필요해지는 상황을 없애는 것**이다.

세션이 끊기는 원인은 대체로 셋이다.
  1. 재부팅 — Windows 업데이트 자동 재시작이 압도적으로 흔하다
  2. 절전·최대절전 — 복귀 후 HTS 세션이 살아 있다는 보장이 없다
  3. 화면 잠금 — 집행 시각에 걸리면 전역 입력이 Winlogon 데스크톱으로 막힌다

여기서는 **점검만 하고 바꾸지 않는다.** 전원·보안 설정은 사람이 결정할 일이고,
무엇을 왜 바꿔야 하는지 알려주는 편이 조용히 바꾸는 것보다 낫다.

    python tools/readiness.py
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone

for st in (sys.stdout, sys.stderr):
    try:
        st.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OK, BAD, WARN = "[정상]", "[조치]", "[확인]"


def ps(cmd: str) -> str:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        return (r.stdout or "").strip()
    except Exception as exc:
        return f"__ERR__{exc}"


def check_updates() -> tuple[str, str, str]:
    v = ps(r"(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'"
           r" -EA SilentlyContinue).PauseUpdatesExpiryTime")
    if not v or v.startswith("__ERR__"):
        return BAD, "Windows 업데이트가 일시중지되어 있지 않다", \
            "설정 > Windows 업데이트 > 업데이트 일시 중지 (최대 5주). 자동 재시작이 재로그인의 가장 흔한 원인이다."
    try:
        exp = datetime.fromisoformat(v.replace("Z", "+00:00"))
        left = (exp - datetime.now(timezone.utc)).days
        if left >= 32:
            return OK, f"업데이트 일시중지 — {exp:%Y-%m-%d} 까지 ({left}일 남음)", ""
        return WARN, f"업데이트 일시중지가 {exp:%Y-%m-%d} 에 끝난다 ({left}일)", \
            "대회 종료(10-08) 이후까지 연장할 것."
    except Exception:
        return WARN, f"일시중지 만료값을 해석하지 못했다: {v}", ""


def check_sleep() -> list[tuple[str, str, str]]:
    out = []
    for label, sub, guid in (("절전(AC)", "SUB_SLEEP", "STANDBYIDLE"),
                             ("최대절전(AC)", "SUB_SLEEP", "HIBERNATEIDLE"),
                             ("모니터 끄기(AC)", "SUB_VIDEO", "VIDEOIDLE")):
        # powercfg 출력에서 **현재 AC 설정 색인** 줄만 본다.
        # 예전에는 0x 패턴을 전부 긁어 최소/최대/증가값까지 섞였다.
        v = ps(f"$l = (powercfg /query SCHEME_CURRENT {sub} {guid} 2>$null) "
               f"-match 'AC'; if ($l) {{ $m = [regex]::Match(($l -join ' '), "
               f"'0x([0-9a-fA-F]{{8}})'); if ($m.Success) "
               f"{{ [Convert]::ToInt32($m.Groups[1].Value,16) }} }}")
        if v.startswith("__ERR__") or not v:
            out.append((WARN, f"{label} 설정을 읽지 못했다", ""))
            continue
        try:
            secs = int(v)
        except ValueError:
            out.append((WARN, f"{label}: {v}", ""))
            continue
        if secs == 0:
            out.append((OK, f"{label} 안 함", ""))
        else:
            out.append((BAD, f"{label} {secs // 60}분 후 작동",
                        "장중에 작동하면 HTS 세션과 집행이 모두 위험하다. "
                        "제어판 > 전원 옵션에서 '안 함' 으로."))
    return out


def check_fast_startup() -> tuple[str, str, str]:
    v = ps(r"(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'"
           r" -EA SilentlyContinue).HiberbootEnabled")
    if v == "0":
        return OK, "빠른 시작 꺼짐", ""
    if v == "1":
        return WARN, "빠른 시작 켜짐", \
            "재부팅이 완전한 종료가 아니라 상태가 어긋날 수 있다. 필수는 아니다."
    return WARN, "빠른 시작 설정을 읽지 못했다", ""


def check_lock() -> list[tuple[str, str, str]]:
    out = []
    # ScreenSaveActive 만 보면 안 된다. 그 값이 1 이어도 화면보호기가 (없음)
    # 이면 아무것도 안 돈다. 실제로 도는 조건은 **실행 파일과 시간이 함께**
    # 설정된 경우다.
    exe = ps(r"(Get-ItemProperty 'HKCU:\Control Panel\Desktop' "
             r"-EA SilentlyContinue).'SCRNSAVE.EXE'")
    tmo = ps(r"(Get-ItemProperty 'HKCU:\Control Panel\Desktop' "
             r"-EA SilentlyContinue).ScreenSaveTimeOut")
    runs = bool(exe) and not exe.startswith("__ERR__") and tmo.isdigit() and int(tmo) > 0
    out.append((BAD, f"화면보호기 작동 ({exe}, {tmo}초)",
                "잠금 화면은 입력 데스크톱을 Winlogon 으로 바꿔 주문이 전부 거부된다.")
               if runs else (OK, "화면보호기 실질 미작동", ""))
    v = ps(r"(Get-ItemProperty 'HKCU:\Control Panel\Desktop' -EA SilentlyContinue).ScreenSaverIsSecure")
    if v == "1":
        out.append((BAD, "화면보호기 복귀 시 암호 요구", "끌 것."))
    v = ps(r"(Get-ItemProperty 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced'"
           r" -EA SilentlyContinue).DynamicLock")
    out.append((BAD, "동적 잠금 켜짐", "휴대폰이 멀어지면 PC 가 잠긴다. 끌 것.")
               if v == "1" else (OK, "동적 잠금 꺼짐", ""))
    return out


def check_hts() -> list[tuple[str, str, str]]:
    out = []
    v = ps(r"(Get-ItemProperty 'HKCU:\Software\cyKhanPlus\AXIS Workstation V04.00\Workstation'"
           r" -EA SilentlyContinue).IdleTimeout")
    out.append((OK, "HTS 유휴 자동 로그아웃 없음 (IdleTimeout=0)", "") if v == "0" else
               (WARN, f"HTS IdleTimeout={v}", "0 이 아니면 유휴 시 로그아웃될 수 있다."))
    v = ps(r"(Get-ItemProperty 'HKCU:\Software\cyKhanPlus\AXIS Workstation V04.00\Workstation'"
           r" -EA SilentlyContinue).SaveID")
    out.append((OK, "HTS ID 저장됨 — 비밀번호만 입력하면 된다", "") if v == "1" else
               (WARN, "HTS ID 미저장", "로그인 창에서 ID 저장을 켜면 조작이 줄어든다."))
    return out


def check_tasks() -> list[tuple[str, str, str]]:
    out = []
    n = ps("(Get-ScheduledTask -TaskPath '\\' | "
           "Where-Object { $_.TaskName -like 'IMRL*' }).Count")
    out.append((OK, f"스케줄러 작업 {n}개 등록됨", "") if n and n.isdigit() and int(n) >= 12
               else (BAD, f"스케줄러 작업 {n}개 — 12개여야 한다",
                     "python execute.py --setup-tasks 를 관리자로 실행."))
    v = ps("(Get-ScheduledTask -TaskName 'IMRL_Watchdog' -EA SilentlyContinue)"
           ".Triggers.Count")
    out.append((OK, f"감시자 트리거 {v}개 (반복 + 로그온)", "") if v and v.isdigit() and int(v) >= 2
               else (BAD, f"감시자 트리거 {v}개 — 2개여야 한다",
                     "재부팅 후 자동 복구가 안 된다. 작업 재등록 필요."))
    return out


def main() -> int:
    print("=" * 66)
    print(" 무인 운영 준비 점검 — 한 번의 로그인이 대회 내내 유지되는가")
    print("=" * 66)
    groups = [
        ("재부팅을 유발하는 것", [check_updates(), check_fast_startup()]),
        ("절전 · 전원", check_sleep()),
        ("화면 잠금", check_lock()),
        ("HTS 세션", check_hts()),
        ("자동 실행", check_tasks()),
    ]
    todo = []
    for title, rows in groups:
        print()
        print(f" {title}")
        for mark, text, fix in rows:
            print(f"  {mark} {text}")
            if mark == BAD and fix:
                todo.append((text, fix))
    print()
    print("=" * 66)
    if todo:
        print(f" 조치가 필요한 항목 {len(todo)}건")
        for text, fix in todo:
            print(f"\n  · {text}")
            print(f"    → {fix}")
    else:
        print(" 조치할 항목 없음 — 재로그인을 유발하는 설정이 남아 있지 않다.")
    print("=" * 66)
    return 1 if todo else 0


if __name__ == "__main__":
    sys.exit(main())
