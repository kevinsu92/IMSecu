# -*- coding: utf-8 -*-
"""한 화면에서 오늘 상태를 본다.

무인 운영에서 사람이 물어야 하는 것은 하나다 — **지금 정상인가.**
그 답이 로그 파일 넷과 작업 스케줄러와 상태 파일 여러 개에 흩어져 있으면
아무도 안 본다. 여기 모은다.

`python monitor.py`       한 번 보고 끝
`python monitor.py -w`    10초마다 갱신 (장중에 띄워두는 용도)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
S = ROOT / "state"
sys.path.insert(0, str(ROOT))
for st in (sys.stdout, sys.stderr):
    try:
        st.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OK, NO, WARN = "[정상]", "[문제]", "[대기]"


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _tail(p: Path, n: int) -> list[str]:
    if not p.exists():
        return []
    try:
        return [l.rstrip() for l in p.read_text(encoding="utf-8",
                                                errors="replace").splitlines() if l.strip()][-n:]
    except OSError:
        return []


def hts_line() -> tuple[str, str]:
    try:
        from imrl import hts_exec
    except Exception as exc:
        return NO, f"모듈 로드 실패 {exc}"
    pid = hts_exec.axis_pid()
    if not pid:
        return NO, "HTS 가 꺼져 있다 — 실행하고 로그인할 것"
    try:
        hts_exec.find_order_window(pid)
    except Exception:
        return WARN, f"HTS 실행 중(pid {pid}) — 로그인 전이거나 화면 1200 미개방"
    return OK, f"HTS 준비됨 (pid {pid}, 주문 화면 열림)"


def watchdog_line() -> tuple[str, str]:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-ScheduledTask -TaskName 'IMRL_Watchdog' -EA SilentlyContinue).State"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15)
        state = (r.stdout or "").strip()
    except Exception:
        state = ""
    log = _tail(S / "watchdog.log", 1)
    last = log[0] if log else "로그 없음"
    if state == "Running":
        return OK, f"감시자 실행 중 · {last}"
    if state:
        return WARN, f"감시자 대기({state}) · {last}"
    return NO, "감시자 작업을 찾지 못했다"


def steps_block() -> list[str]:
    """오늘 각 단계가 끝났는지."""
    try:
        import watchdog as w
    except Exception as exc:
        return [f"  {NO} 감시자 모듈 로드 실패: {exc}"]
    # 감시자가 안 도는 날에 "놓침"을 띄우면 화면이 거짓으로 빨개진다.
    # 주말과 휴장일(09-24, 09-25, 10-05)이 그렇다.
    rest, why = w.rest_day(date.today())
    if rest:
        return [f"  {WARN} {why} — 오늘은 실행하지 않는다"]
    out = []
    now = datetime.now().time()
    has_sheet = (S / f"orders_{date.today():%Y%m%d}.json").exists()
    for name, t0, t1, _cmd, need in w.STEPS:
        done = w.step_done(name)
        if name in ("sell", "buy") and not has_sheet:
            # 주문서가 없으면 step_done 은 '할 일 없음'으로 True 를 준다.
            # 그것을 '완료'로 보여주면 15:05 전에 이미 매매한 것처럼 읽힌다.
            out.append(f"  {WARN} {name:<10} 대기 (주문서 생성 전)")
            continue
        if done:
            mark, note = OK, "완료"
        elif now < t0:
            mark, note = WARN, f"대기 ({t0:%H:%M} 시작)"
        elif now >= t1:
            mark, note = NO, f"놓침 (마감 {t1:%H:%M})"
        else:
            mark, note = WARN, f"실행 창 안 ({t0:%H:%M}~{t1:%H:%M})"
        out.append(f"  {mark} {name:<10} {note}")
    return out


def orders_block() -> list[str]:
    day = f"{date.today():%Y%m%d}"
    orders = _read(S / f"orders_{day}.json", []) or []
    led_rows = _read(S / f"submitted_{day}.json", []) or []
    led: dict[str, str] = {}
    for r in led_rows:
        led[str(r.get("key", ""))] = str(r.get("status", "submitted"))
    if not orders:
        return ["  주문서 없음 (15:05 이전이면 정상)"]
    out = []
    for o in orders:
        side, code = str(o.get("side", "")).upper(), str(o.get("code", ""))
        pre = f"{side}:{code}"
        hits = {k: v for k, v in led.items() if k == pre or k.startswith(pre + "#")}
        if not hits:
            mark, note = WARN, "미전송"
        elif any(v in ("unknown", "pending_send", "modal") for v in hits.values()):
            mark, note = NO, "접수 불명 — 확인 필요"
        else:
            mark, note = OK, f"접수 {len(hits)}건"
        out.append(f"  {mark} {side:<4} {o.get('name', code)[:14]:<16} "
                   f"{int(o.get('qty', 0)):>7,}주 @ {int(o.get('limit_price', 0)):>9,}  {note}")
    return out


def qualify_block() -> list[str]:
    try:
        from imrl import data, state as st
        cfg = _read(ROOT / "config" / "settings.json", {}) or {}
        cal = data.TradingCalendar.from_config(cfg["contest"])
        c = st.compute_constraints(int(cfg["contest"]["principal"]), cal.days_left())
        req = cfg["requirements"]
        rows = [
            (c.effective_turnover, req["min_turnover_pct"], "회전율", "%"),
            (getattr(c, "trading_days", 0), req["min_trading_days"], "매매일수", "일"),
            (getattr(c, "distinct_symbols", 0), req["min_distinct_symbols"], "매매종목수", "종목"),
        ]
        out = []
        for got, want, label, unit in rows:
            mark = OK if got >= want else WARN
            out.append(f"  {mark} {label:<10} {got:>6.0f}{unit} / {want}{unit}")
        out.append(f"     잔여 {cal.days_left()}영업일")
        return out
    except Exception as exc:
        return [f"  {WARN} 자격 상태를 읽지 못했다: {exc}"]


def render() -> str:
    L = ["=" * 62,
         f" iM Rookie League 상태  ·  {datetime.now():%Y-%m-%d %H:%M:%S}",
         "=" * 62, ""]
    m, t = hts_line();     L.append(f" {m} {t}")
    m, t = watchdog_line(); L.append(f" {m} {t}")
    if (S / "KILL").exists():
        L.append(f" {NO} KILL 파일이 있다 — 주문이 나가지 않는다")
    L += ["", " 오늘 단계"] + steps_block()
    L += ["", " 오늘 주문"] + orders_block()
    L += ["", " 수상 자격"] + qualify_block()

    log = _tail(S / "watchdog.log", 6)
    if log:
        L += ["", " 감시자 로그 (최근 6줄)"] + [f"   {l}" for l in log]
    # '실패 0건' 같은 정상 요약을 오류로 잡지 않는다. 실제 문제 문구만 본다.
    bad = ("차단됐다", "실패:", "Traceback", "Error", "거부", "알림 실패", "권한이 필요")
    good = ("실패 0건",)
    err = [l for l in _tail(S / "execute.log", 200)
           if any(k in l for k in bad) and not any(g in l for g in good)][-3:]
    if err:                       # 비어 있으면 머리말도 붙이지 않는다
        L += ["", " 최근 실행 오류"] + [f"   {l}" for l in err]
    L += ["", "=" * 62]
    return "\n".join(L)


def session_report() -> str:
    """`python monitor.py --session`. 판정 로직은 imrl/session.py 가 갖는다 —
    16:00 리포트와 같은 결론을 써야 하므로 구현을 두 곳에 두지 않는다."""
    from imrl import session as _sess
    return _sess.report()


def main() -> int:
    if "--session" in sys.argv:
        print(session_report())
        return 0
    watch = "-w" in sys.argv or "--watch" in sys.argv
    if not watch:
        print(render())
        return 0
    try:
        while True:
            # 셸을 거치지 않는다. 고정 문자열이라 주입 위험은 없지만,
            # 셸을 부를 이유도 없다.
            subprocess.run(["cls" if os.name == "nt" else "clear"],
                           shell=(os.name == "nt"), check=False)
            print(render())
            print(" 10초마다 갱신 · Ctrl+C 로 종료")
            time.sleep(10)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
