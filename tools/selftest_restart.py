# -*- coding: utf-8 -*-
"""죽었다 살아나는 시나리오. 감시자를 죽였다 재시작해 상태가 이어지는지 본다."""
import io, json, os, shutil, subprocess, sys, threading, time
os.environ["IMRL_QUIET"] = "1"          # 시험은 텔레그램을 보내지 않는다 (imrl/notify.py)
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
import importlib

CFG = Path('config/settings.json'); S = Path('state'); DAY = f"{date.today():%Y%m%d}"
FILES = [f'orders_{DAY}.json', f'submitted_{DAY}.json',
         f'reconcile_{DAY}.done', f'status_{DAY}.done']
shutil.copy(CFG, CFG.with_suffix('.rbak'))
bak = [(S/f, shutil.copy(S/f, (S/f).with_suffix((S/f).suffix+'.r')) if (S/f).exists() else None)
       for f in FILES]
fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)
def clean():
    for f in FILES: (S/f).unlink(missing_ok=True)

def fresh_watchdog():
    """모듈을 새로 로드한다 = 프로세스를 새로 띄운 것과 같은 상태."""
    import watchdog
    return importlib.reload(watchdog)

try:
    print("[1] 감시자를 죽였다 다시 켜도 완료 단계를 다시 안 한다")
    clean()
    (S/f'orders_{DAY}.json').write_text(json.dumps(
        [{"side":"BUY","code":"003010","qty":10,"limit_price":1000}], ensure_ascii=False),
        encoding='utf-8')
    (S/f'submitted_{DAY}.json').write_text(json.dumps(
        [{"key":"BUY:003010","status":"submitted"}], ensure_ascii=False), encoding='utf-8')
    w1 = fresh_watchdog()
    ck("1차: plan 완료로 인식", w1.step_done("plan"))
    ck("1차: buy 완료로 인식", w1.step_done("buy"))
    del sys.modules['watchdog']
    w2 = fresh_watchdog()          # 재시작
    ck("재시작 후에도 plan 완료", w2.step_done("plan"))
    ck("재시작 후에도 buy 완료", w2.step_done("buy"), "원장이 디스크에 있어 기억이 이어진다")

    print()
    print("[2] 중간에 죽으면 남은 단계는 다시 잡는다")
    clean()
    (S/f'orders_{DAY}.json').write_text(json.dumps(
        [{"side":"SELL","code":"A","qty":9,"limit_price":100},
         {"side":"BUY","code":"B","qty":9,"limit_price":100}], ensure_ascii=False),
        encoding='utf-8')
    # 매도는 3분할이라 조각 키가 셋이다. 셋 다 접수돼야 끝난 것이다.
    (S/f'submitted_{DAY}.json').write_text(json.dumps(
        [{"key":"SELL:A#1/3","status":"submitted"}, {"key":"SELL:A#2/3","status":"submitted"},
         {"key":"SELL:A#3/3","status":"submitted"}], ensure_ascii=False), encoding='utf-8')
    w3 = fresh_watchdog()
    ck("매도는 끝났다고 본다", w3.step_done("sell"))
    ck("매수는 아직이라고 본다", not w3.step_done("buy"), "재시작해도 남은 일을 잡는다")

    print()
    print("[3] HTS 가 꺼졌다 켜지면 상태를 다시 인식한다")
    w4 = fresh_watchdog()
    ok, why = w4.hts_ready()
    ck("현재 HTS 준비 상태를 읽는다", isinstance(ok, bool), f"{ok} - {why}")
    orig = w4.hts_exec if hasattr(w4, 'hts_exec') else None
    import imrl.hts_exec as he
    real = he.axis_pid
    he.axis_pid = lambda: 0
    ok2, why2 = w4.hts_ready()
    ck("HTS 없으면 미준비로 본다", not ok2, why2)
    he.axis_pid = real
    ok3, _ = w4.hts_ready()
    ck("HTS 돌아오면 다시 준비로 본다", ok3 == ok, "재로그인 감지")

    print()
    print("[4] 실제 프로세스를 죽였다 살린다")
    # 감시자는 START~END 밖에서는 뜨자마자 스스로 끝난다. 그게 올바른 동작이라
    # "떠 있어야 한다"고 못박으면 저녁에 돌린 검사가 코드 잘못 없이 빨개진다.
    # 시각에 따라 **기대하는 바를 바꾼다.**
    import watchdog as _w0
    _now = datetime.now().time()
    IN_WINDOW = _w0.START <= _now < _w0.END
    AFTER_END = _now >= _w0.END
    # START 전에는 감시자가 08:30 을 기다리며 **살아 있는 것**이 맞다. 끝내는
    # 것은 END 뒤뿐이다. 새벽에 돌리면 '밖' 이지만 기대는 '안' 과 같다.
    STAYS_UP = IN_WINDOW or not AFTER_END
    print(f"  (지금 {_now:%H:%M} — 운영시간 {_w0.START:%H:%M}~{_w0.END:%H:%M} "
          f"{'안' if IN_WINDOW else ('전' if not AFTER_END else '뒤')})")
    cfg = json.loads(CFG.read_text(encoding='utf-8'))
    cfg['contest']['start_date'] = str(date.today())
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    clean()
    (S/'watchdog.log').unlink(missing_ok=True)
    p = subprocess.Popen([sys.executable, '-X', 'utf8', 'watchdog.py'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(6)
    if STAYS_UP:
        ck("감시자가 떠 있다", p.poll() is None)
    else:
        ck("마감 뒤면 스스로 끝낸다", p.poll() is not None)
    log1 = (S/'watchdog.log').read_text(encoding='utf-8') if (S/'watchdog.log').exists() else ''
    ck("로그를 남긴다", '감시자 시작' in log1)
    if not STAYS_UP:
        ck("끝낸 이유를 남긴다", '마감' in log1 or '종료' in log1)
    p.terminate(); p.wait(timeout=10)
    ck("종료됨", p.poll() is not None)
    p2 = subprocess.Popen([sys.executable, '-X', 'utf8', 'watchdog.py'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(6)
    if STAYS_UP:
        ck("다시 띄우면 정상 기동", p2.poll() is None)
    else:
        ck("다시 띄워도 마감 뒤면 곧 끝낸다", p2.poll() is not None)
    log2 = (S/'watchdog.log').read_text(encoding='utf-8')
    ck("재시작 기록이 로그에 이어 붙는다", log2.count('감시자 시작') >= 2,
       f"{log2.count('감시자 시작')}회")
    p2.terminate(); p2.wait(timeout=10)

    print()
    print("[5] KILL 파일이 있으면 재시작해도 안 돈다")
    (S/'KILL').touch()
    p3 = subprocess.Popen([sys.executable, '-X', 'utf8', 'watchdog.py'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(6)
    log3 = (S/'watchdog.log').read_text(encoding='utf-8')
    tailing = log3.rsplit('감시자 시작', 1)[-1]
    if IN_WINDOW:
        ck("KILL 감지를 로그에 남긴다", 'KILL' in tailing)
    elif STAYS_UP:
        # START 전에는 회차가 안 돌아 KILL 을 볼 기회가 없다. 살아만 있으면 된다.
        ck("START 전에는 아무 단계도 실행하지 않는다", '실행:' not in tailing)
    else:
        # 운영시간 밖에서는 KILL 을 확인할 회차 자체가 없다. 중요한 것은
        # **주문이 나가지 않았다**는 것이고, 그건 마감 종료로 보장된다.
        ck("운영시간 밖이면 아무 단계도 실행하지 않는다",
           '실행:' not in tailing, tailing.strip().splitlines()[-1][:50] if tailing.strip() else '')
    p3.terminate(); p3.wait(timeout=10)
    (S/'KILL').unlink(missing_ok=True)

    print()
    print("[6] HTS 가 꺼져 있어도 프로세스 탐지가 죽지 않는다")
    # tasklist 는 콘솔 코드페이지로 쓴다. axis.exe 가 없을 때의 한국어 안내문을
    # UTF-8 로 디코드하면 subprocess 가 stdout 을 None 으로 주고, 그 다음 줄이
    # AttributeError 로 터졌다. 감시자가 HTS 를 띄워야 하는 바로 그 상황이다.
    import imrl.hts_exec as he2
    import subprocess as _sp
    real_run = _sp.run

    class _R:
        def __init__(self, out): self.stdout = out

    none_msg = '정보: 지정한 조건에 맞는 작업을 실행하고 있지 않습니다.'
    for label, raw in (("cp949 안내문", none_msg.encode('cp949')),
                       ("utf-8 안내문", none_msg.encode('utf-8')),
                       ("빈 출력", b""),
                       ("None (디코드 실패 흉내)", None)):
        _sp.run = lambda *a, **k: _R(raw)
        try:
            got = he2.axis_pid()
            ok = (got == 0)
        except Exception as exc:
            ok, got = False, f"예외 {exc.__class__.__name__}"
        ck(f"{label} -> 0", ok, str(got))

    csv = ('"axis.exe","4321","Console","1","250,000 K"'
           + chr(13) + chr(10))
    _sp.run = lambda *a, **k: _R(csv.encode("cp949"))
    ck("정상 CSV 에서 pid 를 읽는다", he2.axis_pid() == 4321)
    _sp.run = real_run
    ck("복원 후 실제 호출도 정수", isinstance(he2.axis_pid(), int))
    ck("console_text 가 깨진 바이트에서 안 죽는다",
       isinstance(he2.console_text(bytes([0xc1, 0xa4, 0xba, 0xb8])), str))
finally:
    shutil.move(CFG.with_suffix('.rbak'), CFG)
    clean()
    (S/'KILL').unlink(missing_ok=True)
    (S/'watchdog.log').unlink(missing_ok=True)
    for p_, b in bak:
        if b: shutil.move(b, p_)
    print()
    print("  상태 파일 복원됨")
print()
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
