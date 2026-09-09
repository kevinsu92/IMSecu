# -*- coding: utf-8 -*-
"""감시자 자가 점검. 실주문·실행 없이 판정 로직만 본다."""
import io, json, os, shutil, sys
os.environ["IMRL_QUIET"] = "1"          # 시험은 텔레그램을 보내지 않는다 (imrl/notify.py)
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from datetime import date, time as dtime
from pathlib import Path
import watchdog as w

S = Path('state'); DAY = f"{date.today():%Y%m%d}"
FILES = [f'orders_{DAY}.json', f'submitted_{DAY}.json',
         f'reconcile_{DAY}.done', f'status_{DAY}.done',
         f'notice_handsoff_{DAY}.done', f'notice_handsback_{DAY}.done']
bak = []
for f in FILES:
    p = S / f
    bak.append((p, shutil.copy(p, p.with_suffix(p.suffix + '.w')) if p.exists() else None))

fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)

def clean():
    for f in FILES: (S / f).unlink(missing_ok=True)

def orders(*rows):
    (S / f'orders_{DAY}.json').write_text(json.dumps(list(rows), ensure_ascii=False),
                                          encoding='utf-8')
def ledger(*keys):
    (S / f'submitted_{DAY}.json').write_text(json.dumps(
        [{"key": k, "status": "submitted"} for k in keys], ensure_ascii=False),
        encoding='utf-8')

try:
    print("[1] 단계 완료 판정")
    clean()
    ck("주문서 없으면 plan 미완료", not w.step_done("plan"))
    ck("주문 없으면 sell 은 할 일 없음", w.step_done("sell"))
    orders({"side": "BUY", "code": "003010", "qty": 10, "limit_price": 1000})
    ck("주문서 생기면 plan 완료", w.step_done("plan"))
    ck("매수 주문 있고 원장 없으면 buy 미완료", not w.step_done("buy"))
    ck("매수만 있으면 sell 은 완료", w.step_done("sell"))
    ledger("BUY:003010")
    ck("원장에 있으면 buy 완료", w.step_done("buy"))

    print()
    print("[2] 분할 주문 (조각 키)")
    clean()
    orders({"side": "SELL", "code": "092870", "qty": 30, "limit_price": 1000})
    ck("분할 전 sell 미완료", not w.step_done("sell"))
    ledger("SELL:092870#1/3")
    # 예전에는 조각 하나만 있어도 완료로 봤다(외부 검토 재현: 1/3 접수가 완료). 남은 조각을
    # 감시자가 다시 잡아야 한다 — execute 의 원장 필터가 이미 보낸 조각은 다시 누르지 않으므로 안전하다.
    ck("조각 하나만 있으면 아직 미완료", not w.step_done("sell"))
    ledger("SELL:092870#1/3", "SELL:092870#2/3", "SELL:092870#3/3")
    ck("세 조각이 모두 원장에 있으면 완료", w.step_done("sell"))

    print()
    print("[3] 여러 종목")
    clean()
    orders({"side": "BUY", "code": "A", "qty": 1, "limit_price": 1},
           {"side": "BUY", "code": "B", "qty": 1, "limit_price": 1})
    ledger("BUY:A")
    ck("일부만 원장에 있으면 미완료", not w.step_done("buy"))
    ledger("BUY:A", "BUY:B")
    ck("전부 있으면 완료", w.step_done("buy"))

    print()
    print("[4] 시간 창")
    for name, t0, t1, _, need in w.STEPS:
        ck(f"{name:<10} {t0.strftime('%H:%M')}~{t1.strftime('%H:%M')} HTS={'필요' if need else '불필요'}",
           t0 < t1)
    sell = next(s for s in w.STEPS if s[0] == "sell")
    buy = next(s for s in w.STEPS if s[0] == "buy")
    ck("매도 마감이 매수 시작보다 앞", sell[2] <= buy[1],
       f"{sell[2].strftime('%H:%M')} <= {buy[1].strftime('%H:%M')}")
    ck("매수 마감이 동시호가 종료(15:30) 전", buy[2] < dtime(15, 30),
       buy[2].strftime('%H:%M'))
    ck("HTS 필요 단계는 매도·매수뿐",
       {s[0] for s in w.STEPS if s[4]} == {"sell", "buy"})

    print()
    print("[5] 재부팅 복구")
    ck("HTS 자동 실행 함수가 있다", hasattr(w, "_launch_hts"))
    ck("실행 재시도 간격이 있다", hasattr(w, "LAUNCH_GAP_SEC") and w.LAUNCH_GAP_SEC >= 60,
       f"{getattr(w,'LAUNCH_GAP_SEC','?')}초")
    import imrl.hts_exec as _he
    _real = _he.axis_pid
    _calls = {"n": 0}
    _he.axis_pid = lambda: 0
    _rl = w._launch_hts
    w._launch_hts = lambda: (_calls.__setitem__("n", _calls["n"] + 1), False)[1]
    w._last_launch = 0.0
    ok, why = w.hts_ready()
    w._launch_hts = _rl
    _he.axis_pid = _real
    ck("HTS 없으면 실행을 시도한다", _calls["n"] == 1, f"{_calls['n']}회 호출")
    ck("실행해도 로그인 전이면 미준비", not ok, why)
    src = Path('watchdog.py').read_text(encoding='utf-8')
    ck("작업 잡에서 분리해 띄운다", "BREAKAWAY" in src)
    ck("로그인은 하지 않는다", "비밀번호" not in src.split("_launch_hts")[1].split("def ")[0]
       or "입력" not in src.split("def _launch_hts")[1].split("return False")[0])

    print()
    print("[6] 안전")
    src = Path('watchdog.py').read_text(encoding='utf-8')
    ck("KILL 파일을 매 회차 확인", 'KILL' in src and 'while True' in src)
    ck("대회 전에는 실행하지 않는다", 'start_date' in src and 'dry' in src)
    ck("재시도 최소 간격이 있다", 'RETRY_GAP_SEC' in src)
    ck("HTS 판정에 주문 화면까지 본다", 'find_order_window' in src)

    print()
    print("[6b] 제어 서버가 죽으면 감시자가 다시 띄운다")
    import watchdog as _wc, subprocess as _sp, time as _tm
    calls = []
    real_popen, real_alive, real_log = _sp.Popen, _wc.control_alive, _wc.log
    _wc.log = lambda m: None            # 검사 중 운영 로그(watchdog.log)를 더럽히지 않는다
    class _P:  # Popen 흉내
        def __init__(self, cmd, **kw): calls.append((cmd, kw.get("creationflags", 0)))
    try:
        _sp.Popen = _P
        _wc.control_alive = lambda port=8765: True
        _wc._control_last_try = 0.0
        _wc.ensure_control()
        ck("살아 있으면 안 띄운다", calls == [])
        _wc.control_alive = lambda port=8765: False
        _wc.ensure_control()
        ck("죽어 있으면 띄운다", len(calls) == 1)
        ck("control.py 를 부른다", any("control.py" in str(c) for c in calls[0][0]))
        ck("잡에서 떼어낸다 (BREAKAWAY)", calls[0][1] & 0x01000000)
        _wc.ensure_control()
        ck("재시도 간격 안에서는 다시 안 띄운다", len(calls) == 1)
        _wc._control_last_try = 0.0
        _wc.ensure_control()
        ck("간격이 지나면 다시 시도", len(calls) == 2)
    finally:
        _sp.Popen, _wc.control_alive, _wc.log = real_popen, real_alive, real_log
        _wc._control_last_try = 0.0
    ck("실제 포트 확인 함수가 불리언을 준다", isinstance(_wc.control_alive(), bool))

    print()
    print("[6d] 중계실은 한 시간에 한 번만 긁는다")
    import watchdog as _wr, subprocess as _sp2
    runs = []
    real_run, real_log2 = _sp2.run, _wr.log
    class _R:
        returncode = 0; stdout = "중계실 수집 (20260908 기준) — 테스트 / 참가 109명"; stderr = ""
    mk_bak0 = _wr.RELAY_MARK
    try:
        _sp2.run = lambda *a, **k: runs.append(a[0]) or _R()
        _wr.log = lambda m: None
        # 실제 마커(제어 서버가 방금 찍었을 수 있다)를 건드리지 않도록 검사용 경로로
        _wr.RELAY_MARK = Path('state/_relay_probe_6d.txt'); _wr.RELAY_MARK.unlink(missing_ok=True)
        _wr._relay_last = 0.0
        _wr.ensure_relay()
        ck("첫 회차에 긁는다", len(runs) == 1 and "relay" in runs[0] and "--auto" in runs[0])
        _wr.ensure_relay()
        ck("한 시간 안에는 다시 안 긁는다", len(runs) == 1)
        _wr._relay_last = 0.0; _wr.RELAY_MARK.unlink(missing_ok=True)
        _wr.ensure_relay()
        ck("간격이 지나면 다시", len(runs) == 2)
        def boom(*a, **k): raise OSError("no python")
        _sp2.run = boom; _wr._relay_last = 0.0; _wr.RELAY_MARK.unlink(missing_ok=True)
        _wr.ensure_relay()
        ck("실패해도 예외로 죽지 않는다", True)
    finally:
        _sp2.run, _wr.log = real_run, real_log2
        _wr.RELAY_MARK.unlink(missing_ok=True); _wr.RELAY_MARK = mk_bak0
        _wr._relay_last = 0.0
    ck("감시자 루프가 ensure_relay 를 부른다", "ensure_relay()" in Path('watchdog.py').read_text(encoding='utf-8').split("while True:")[1])

    print()
    print("[6e] 수집 마커는 파일로 공유된다 (감시자·제어서버 이중 수집 방지)")
    import watchdog as _wm, time as _tm2
    mk_bak = _wm.RELAY_MARK
    try:
        _wm.RELAY_MARK = Path('state/_relay_probe.txt')
        _wm.RELAY_MARK.unlink(missing_ok=True)
        ck("마커 없으면 수집 시점", _wm.relay_due())
        _wm.relay_mark()
        ck("찍은 직후는 아님", not _wm.relay_due())
        ck("간격 0이면 다시 시점", _wm.relay_due(gap=0))
        _wm.RELAY_MARK.write_text("쓰레기", encoding="utf-8")
        ck("깨진 마커는 수집 시점으로", _wm.relay_due())
    finally:
        _wm.RELAY_MARK.unlink(missing_ok=True); _wm.RELAY_MARK = mk_bak
    src_w = Path('watchdog.py').read_text(encoding='utf-8')
    ck("시작 시 시각과 무관하게 HTS 를 띄운다", src_w.index("시작 시 HTS") < src_w.index("rest_day(date.today())"))

    print()
    print("[6f] 시간별 사이클 — 마커 공유, 정밀 창 회피, 실패해도 산다")
    import watchdog as _wy, subprocess as _sp3
    from datetime import datetime as _dt3
    runs3 = []
    real_run3, real_log3, real_bo = _sp3.run, _wy.log, _wy.cycle_blackout
    class _R3:
        returncode = 0; stdout = "사이클(cached) 10:00 종목 6개 · 경성 0 · 제안 0 · 의견 9/2/0 · 경보 0건 · 3.1초"; stderr = ""
    mk3 = _wy.CYCLE_MARK
    try:
        _sp3.run = lambda *a, **k: runs3.append(a[0]) or _R3()
        _wy.log = lambda m: None
        _wy.CYCLE_MARK = Path('state/_cycle_probe.txt'); _wy.CYCLE_MARK.unlink(missing_ok=True)
        _wy._cycle_last = 0.0
        ck("마커 없으면 사이클 시점", _wy.cycle_due())
        _wy.cycle_blackout = lambda now=None: True
        _wy.ensure_cycle()
        ck("정밀 창(14:30~15:35)에는 돌지 않는다", runs3 == [] and _wy.cycle_due())
        _wy.cycle_blackout = lambda now=None: False
        _wy.ensure_cycle()
        ck("첫 회차에 run.py cycle --source watchdog", len(runs3) == 1 and "cycle" in runs3[0] and "watchdog" in runs3[0], str(runs3[:1]))
        ck("마커가 찍혔다", not _wy.cycle_due())
        _wy.ensure_cycle()
        ck("한 시간 안에는 다시 안 돈다", len(runs3) == 1)
        _wy._cycle_last = 0.0; _wy.CYCLE_MARK.unlink(missing_ok=True)
        def boom3(*a, **k): raise OSError("no python")
        _sp3.run = boom3
        _wy.ensure_cycle()
        ck("실패해도 예외로 죽지 않는다", True)
        ck("정밀 창 판정은 imrl.cycle 과 같다", real_bo(_dt3(2026, 9, 8, 15, 0)) and not real_bo(_dt3(2026, 9, 8, 9, 0)))
    finally:
        _sp3.run, _wy.log, _wy.cycle_blackout = real_run3, real_log3, real_bo
        _wy.CYCLE_MARK.unlink(missing_ok=True); _wy.CYCLE_MARK = mk3
        _wy._cycle_last = 0.0
    ck("감시자 루프가 ensure_cycle 를 부른다", "ensure_cycle()" in src_w.split("while True:")[1])

    print()
    print("[6c] 장운영알람은 닫고, 모르는 대화상자는 남긴다")
    from imrl import hts_exec as _he
    import win32gui as _wg
    posted = []
    real_post = _wg.PostMessage
    ad = _he.OrderAdapter.__new__(_he.OrderAdapter)
    windows = [(111, "#32770|알람"), (222, "#32770|주문확인"), (333, "#32770|알람")]
    ad._popups = lambda: list(windows)
    try:
        _wg.PostMessage = lambda h, m, w, l: posted.append((h, m))
        closed = ad.dismiss_benign_popups()
        ck("알람 두 개를 닫는다", closed == ["알람", "알람"], str(closed))
        ck("WM_CLOSE 를 그 창에만", sorted(h for h, m in posted) == [111, 333] and all(m == 0x0010 for h, m in posted))
        ck("모르는 대화상자는 건드리지 않는다", 222 not in [h for h, _ in posted])
        posted.clear(); windows[:] = [(222, "#32770|주문확인")]
        ck("무해한 창이 없으면 아무것도 안 한다", ad.dismiss_benign_popups() == [] and posted == [])
        def boom(h, m, w, l): raise OSError(5, "access denied")
        _wg.PostMessage = boom; windows[:] = [(111, "#32770|알람")]
        ck("못 닫아도 예외로 죽지 않는다", ad.dismiss_benign_popups() == [])
    finally:
        _wg.PostMessage = real_post
    src_he = Path('imrl/hts_exec.py').read_text(encoding='utf-8')
    ck("전송 전 검사가 먼저 닫고 나서 본다",
       src_he.index("self.dismiss_benign_popups()") < src_he.index("stale = self._popups()"))
    ck("전송 후 새 팝업 판정에서 알람을 뺀다", "not in self.BENIGN_POPUP_TITLES" in src_he)

    print()
    print("[7] 장이 닫힌 날에는 아무것도 실행하지 않는다")
    # 감시자는 매일(일별+로그온) 뜨고 자기가 직접 단계를 실행한다. 단계 작업이
    # 월~금으로 걸려 있어도 감시자 경로는 주말에 열린다. 휴장일은 평일이라
    # 요일만으로는 걸러지지 않는다.
    import json as _j
    from datetime import date as _d, datetime as _dt, timedelta as _td
    import watchdog as _w
    cfg = _j.loads(Path('config/settings.json').read_text(encoding='utf-8'))
    c = cfg['contest']
    hol = set(c.get('holidays', []))
    start = _dt.strptime(c['start_date'], '%Y-%m-%d').date()
    end = _dt.strptime(c['end_date'], '%Y-%m-%d').date()

    wrong = []
    day, opened = start, 0
    while day <= end:
        want_rest = day.weekday() >= 5 or str(day) in hol
        got_rest = _w.rest_day(day)[0]
        if got_rest != want_rest:
            wrong.append(f"{day}({day.strftime('%a')}) 기대={want_rest} 실제={got_rest}")
        opened += (not got_rest)
        day += _td(days=1)
    ck("대회 기간 전체 판정이 달력과 일치", not wrong, "; ".join(wrong[:3]))
    ck("영업일 20일", opened == 20, f"{opened}일")
    ck("휴장일 3일 모두 정지", all(_w.rest_day(_dt.strptime(h, '%Y-%m-%d').date())[0]
                                for h in hol), str(sorted(hol)))
    ck("대회 시작 전에도 정지", _w.rest_day(start - _td(days=1))[0])
    ck("이유 문구가 남는다", _w.rest_day(start + _td(days=5))[1] != ''
       if _w.rest_day(start + _td(days=5))[0] else True)

    print()
    print("[8] execute --live 가 휴장일에 주문을 막는다")
    import subprocess as _sp
    for label, dstr, want in (("휴장일", sorted(hol)[0].replace('-', ''), 9),
                              ("주말", f"{start + _td(days=5):%Y%m%d}"
                               if (start + _td(days=5)).weekday() >= 5
                               else f"{start + _td(days=4):%Y%m%d}", 9)):
        r = _sp.run([sys.executable, '-X', 'utf8', 'execute.py', '--live',
                     '--no-notify', '--date', dstr],
                    capture_output=True, text=True, encoding='utf-8',
                    errors='replace', timeout=120)
        ck(f"{label} {dstr} -> 종료코드 {want}", r.returncode == want,
           f"rc={r.returncode}")
    # 영업일에는 이 게이트가 걸리지 않는다 (주문서가 없어 다른 코드로 끝난다).
    r = _sp.run([sys.executable, '-X', 'utf8', 'execute.py', '--live',
                 '--no-notify', '--date', f"{start:%Y%m%d}"],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=120)
    ck(f"영업일 {start:%Y%m%d} 은 게이트를 통과", r.returncode != 9, f"rc={r.returncode}")

    print()
    print("[9] 손 떼기 알림 — 15:08 한 번, 입력이 끝나면 한 번, 늦어도 15:30")
    clean()
    sent = []
    snd = lambda t: (sent.append(t), True)[1]
    sell0 = next(s[1] for s in w.STEPS if s[0] == "sell")
    buy1 = next(s[2] for s in w.STEPS if s[0] == "buy")
    ck("손 떼기 알림이 매도 시작보다 앞", w.HANDS_OFF_AT < sell0,
       f"{w.HANDS_OFF_AT:%H:%M} < {sell0:%H:%M}")
    ck("재개 시각이 매수 마감 뒤", w.HANDS_BACK_AT > buy1, f"{w.HANDS_BACK_AT:%H:%M} > {buy1:%H:%M}")
    ck("15:07 에는 보내지 않는다", w.ensure_hands_notice(dtime(15, 7), send=snd) is None and not sent)
    ck("휴장일·대회 전에는 보내지 않는다",
       w.ensure_hands_notice(dtime(15, 8), dry=True, send=snd) is None and not sent)
    orders({"side": "SELL", "code": "092870", "qty": 30, "limit_price": 1000},
           {"side": "BUY", "code": "003010", "qty": 10, "limit_price": 1000})
    r = w.ensure_hands_notice(dtime(15, 8), send=snd)
    ck("15:08 손 떼기 알림", r == "handsoff" and len(sent) == 1 and "손 떼기" in sent[0])
    ck("주문 건수를 적는다", sent and "매도 1건" in sent[0] and "매수 1건" in sent[0])
    ck("같은 날 두 번 보내지 않는다",
       w.ensure_hands_notice(dtime(15, 9), send=snd) is None and len(sent) == 1)
    ledger("SELL:092870#1/3", "SELL:092870#2/3", "SELL:092870#3/3")
    ck("매도만 끝나면 아직 끝 알림 없음",
       w.ensure_hands_notice(dtime(15, 12), send=snd) is None and len(sent) == 1)
    ledger("SELL:092870#1/3", "SELL:092870#2/3", "SELL:092870#3/3", "BUY:003010")
    r = w.ensure_hands_notice(dtime(15, 22), send=snd)
    ck("매수까지 끝나면 끝 알림", r == "handsback" and len(sent) == 2 and "입력 끝" in sent[1])
    ck("조각 집계를 적는다", len(sent) == 2 and "접수 4" in sent[1] and "미전송 0" in sent[1])
    ck("끝 알림도 한 번", w.ensure_hands_notice(dtime(15, 25), send=snd) is None and len(sent) == 2)
    # 미완인 채 마감을 넘기면 15:30 에 강제로 알린다
    clean(); sent.clear()
    orders({"side": "SELL", "code": "092870", "qty": 30, "limit_price": 1000},
           {"side": "BUY", "code": "003010", "qty": 10, "limit_price": 1000})
    ledger("SELL:092870#1/3")
    w.ensure_hands_notice(dtime(15, 8), send=snd)
    ck("미완이면 15:29 에 끝 알림 없음", w.ensure_hands_notice(dtime(15, 29), send=snd) is None)
    r = w.ensure_hands_notice(dtime(15, 30), send=snd)
    ck("15:30 에는 마감 알림", r == "handsback" and "종료" in sent[-1])
    ck("미전송 조각을 센다", "접수 1" in sent[-1] and "미전송 3" in sent[-1])
    # 전송 실패가 루프를 죽이지 않고, 표식이 먼저 찍혀 재전송하지 않는다
    clean()
    def boom(t): raise RuntimeError("telegram down")
    r = w.ensure_hands_notice(dtime(15, 8), send=boom)
    ck("전송 실패해도 예외가 새지 않는다", r == "handsoff")
    ck("실패해도 표식이 찍혀 반복하지 않는다", w.notice_sent("handsoff"))
    src_w9 = Path('watchdog.py').read_text(encoding='utf-8')
    ck("감시자 루프가 알림 함수를 부른다", "ensure_hands_notice(now" in src_w9.split("while True:")[1])
    ck("준비 판정이 화면잠금·재로그인 대화상자를 '미준비'로 읽는다",
       "blocking_popups(" in src_w9 and 'stt == "locked"' in src_w9)
    ck("유휴 잠금 방지 — 이동량 0 마우스 이벤트, 5분 간격, 루프에서 부른다",
       hasattr(w, "keep_hts_awake") and w.KEEPALIVE_SEC >= 60 and "keep_hts_awake()" in src_w9.split("while True:")[1]
       and "mouse_event(0x0001, 0, 0, 0, 0)" in src_w9)
    ck("긴급정지 검사보다 앞에서 부른다 (정지 중에도 알림)",
       src_w9.split("while True:")[1].find("ensure_hands_notice(") <
       src_w9.split("while True:")[1].find("KILL"))
finally:
    clean()
    for p, b in bak:
        if b: shutil.move(b, p)
    print()
    print("  상태 파일 복원됨")

print()
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
