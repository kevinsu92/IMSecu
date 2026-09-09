# -*- coding: utf-8 -*-
"""하루 전체 순서를 실제 명령으로 돌린다. 실주문은 내지 않는다.

15:05 plan -> 15:10 sell -> 15:21 buy -> 15:40 reconcile -> 16:00 status
그리고 2일차를 보유가 있는 상태에서 다시 돌린다.
"""
import io, json, os, shutil, subprocess, sys
sys.stdout.reconfigure(encoding='utf-8')
from datetime import date
from pathlib import Path

S = Path('state')
DAY = f"{date.today():%Y%m%d}"
KEEP = ['positions.json', 'trades.json', 'leaderboard.json',
        f'orders_{DAY}.json', f'meta_{DAY}.json', f'submitted_{DAY}.json',
        f'reconcile_{DAY}.json', f'reconcile_{DAY}.done', f'status_{DAY}.done',
        f'decision_{DAY}_execute.json']   # 시험이 만든 결정 기록이 상황판에 남지 않게
bak = []
for f in KEEP:
    p = S / f
    if p.exists():
        b = p.with_suffix(p.suffix + '.e2e')
        shutil.copy(p, b); bak.append((p, b))
    else:
        bak.append((p, None))

fails, checks = [], 0
def ck(name, cond, detail=""):
    global checks
    checks += 1
    print(("  OK   " if cond else "  FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond: fails.append(name)

os.environ["IMRL_QUIET"] = "1"          # 시험은 텔레그램을 보내지 않는다 (imrl/notify.py)

def run(*args, timeout=900):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", IMRL_QUIET="1")
    return subprocess.run([sys.executable, "-X", "utf8", *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env=env, timeout=timeout)

def clean():
    for f in KEEP:
        (S / f).unlink(missing_ok=True)

try:
    print("=" * 64)
    print("1일차 — 상태 파일이 하나도 없는 상태에서 시작")
    print("=" * 64)
    clean()

    r = run("run.py", "plan")
    ck("plan 종료코드 0", r.returncode == 0, r.stderr[-160:] if r.returncode else "")
    op = S / f'orders_{DAY}.json'
    ck("주문서 생성", op.exists())
    orders = json.loads(op.read_text(encoding='utf-8')) if op.exists() else []
    ck("주문 3건 (주식2 + ETF관측1)", len(orders) == 3, str(len(orders)))
    ck("전부 매수", all(o['side'] == 'BUY' for o in orders))
    ck("meta 생성", (S / f'meta_{DAY}.json').exists())
    ck("결정 기록 생성", (S / f'decision_{DAY}_execute.json').exists())

    r = run("execute.py", "--side", "SELL", "--date", DAY)
    ck("매도 실행 (매도 없음 -> 정상 종료)", r.returncode == 0,
       f"rc={r.returncode}")
    ck("매도 없음을 보고", "SELL 주문이 없다" in r.stdout or "0건" in r.stdout)

    r = run("run.py", "reconcile", "--date", DAY)
    ck("대조 종료코드 0", r.returncode == 0)
    ck("전송 안 됨 3건 보고", r.stdout.count("전송 안 됨") == 3,
       str(r.stdout.count("전송 안 됨")))

    r = run("run.py", "status")
    ck("자격 리포트 종료코드 0", r.returncode == 0)
    ck("회전율 0% 보고", "0.0% / 500%" in r.stdout)

    print()
    print("=" * 64)
    print("2일차 — 1일차 전량 체결 가정, 보유 있는 상태")
    print("=" * 64)
    trades = [{"date": "2026-09-08", "side": o["side"], "code": o["code"],
               "name": o["name"], "market": o.get("market", ""),
               "qty": o["qty"], "price": o["limit_price"], "assumed": True}
              for o in orders]
    (S / 'trades.json').write_text(json.dumps(trades, ensure_ascii=False), encoding='utf-8')
    (S / 'positions.json').write_text(json.dumps(
        {o["code"]: {"name": o["name"], "market": o.get("market", ""),
                     "qty": o["qty"], "avg_price": o["limit_price"],
                     "entry_date": "2026-09-08"} for o in orders},
        ensure_ascii=False), encoding='utf-8')
    (S / f'orders_{DAY}.json').unlink(missing_ok=True)
    (S / f'meta_{DAY}.json').unlink(missing_ok=True)

    r = run("run.py", "plan")
    ck("2일차 plan 종료코드 0", r.returncode == 0, r.stderr[-200:] if r.returncode else "")
    o2 = json.loads((S / f'orders_{DAY}.json').read_text(encoding='utf-8')) \
        if (S / f'orders_{DAY}.json').exists() else []
    # 보유 종목을 **다시** 사는 것만 중복이다. 순위가 바뀌어 다른 종목으로 교체하는 매수는
    # 정상 리밸런싱이다 — 15:30~16:30 에는 네이버가 당일 봉을 확정하면서 두 계획 사이에
    # 순위가 바뀔 수 있어(2026-09-08 실측) 총액으로 보면 오탐이 난다.
    held_codes = {o["code"] for o in orders}
    dup = sum(x['qty'] * x['limit_price'] for x in o2
              if x['side'] == 'BUY' and x['code'] in held_codes and x['qty'] * x['limit_price'] > 5_000_000)
    ck("보유 종목 중복 매수 없음", dup == 0, f"중복 매수 {dup:,}원")
    ck("수익률을 자체 추정", "자체 추정" in r.stdout)
    sells = [x for x in o2 if x['side'] == 'SELL']
    ck("ETF 매도가 중복되지 않음",
       len([x for x in sells if x['code'] == '459580']) <= 1,
       str([x['code'] for x in sells]))

    if o2:
        r = run("execute.py", "--side", "SELL", "--date", DAY)
        # 비승격 실행이므로 매도가 있으면 권한(2)에서 멈추는 것이 정상이다.
        ck("2일차 매도: 권한 단계까지 도달", r.returncode in (0, 2, 3),
           f"rc={r.returncode}")

    print()
    print("=" * 64)
    print("경계 상황")
    print("=" * 64)
    (S / 'KILL').touch()
    r = run("execute.py", "--side", "BUY", "--date", DAY, "--no-notify")
    ck("킬 스위치 -> exit 7", r.returncode == 7, f"rc={r.returncode}")
    (S / 'KILL').unlink(missing_ok=True)

    r = run("execute.py", "--date", "19990101")
    ck("없는 날짜 주문서 -> 오류로 보고(계획 미실행)", r.returncode == 1, f"rc={r.returncode}")
    ck("주문 없음을 보고", "처리할 주문이 없다" in r.stdout or "없다" in r.stdout)

    r = run("run.py", "reconcile", "--date", "19990101")
    ck("없는 날짜 대조 -> 정상 종료", r.returncode == 0, f"rc={r.returncode}")

finally:
    clean()
    for p, b in bak:
        if b and b.exists():
            shutil.move(b, p)
    print()
    print("  상태 파일 복원됨")

print()
print("=" * 64)
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
