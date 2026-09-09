# -*- coding: utf-8 -*-
"""무인 운영 경로 자가 점검. 실주문은 내지 않는다."""
import io, json, os, shutil, sys
os.environ["IMRL_QUIET"] = "1"          # 시험은 텔레그램을 보내지 않는다 (imrl/notify.py)
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from pathlib import Path
from imrl import portfolio, risk, state, turnover

CFG = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
P = 100_000_000
fails, checks = [], 0

def ck(name, cond, detail=""):
    global checks
    checks += 1
    print(("  OK   " if cond else "  FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        fails.append(name)

print("[1] 국면 전환")
for dl, ret, gap, want in [
    (15, 5.0, 2.0, "phase_early"),
    (15, -3.0, 2.0, "phase_early"),   # 잔여 15일이면 아직 막판이 아니다
    (5, -3.0, None, "phase_final_attack"),
    (5, 5.0, None, "phase_final_push"),
    (5, 5.0, 20.0, "phase_final_attack"),
    (5, 5.0, 3.0, "phase_final_push"),
    (5, 5.0, -40.0, "phase_final_push"),
    (15, 5.0, None, "phase_early"),
]:
    got = portfolio.choose_phase(CFG, dl, ret, gap)
    ck(f"잔여{dl:>2} 수익{ret:+5.1f} 격차{str(gap):>5} -> {got}", got == want, "" if got == want else f"기대 {want}")

print("\n[2] 목표 비중 - 평가금액별")
sc = __import__("pandas").DataFrame({
    "code": [f"{i:06d}" for i in range(1, 9)], "name": [f"N{i}" for i in range(8)],
    "market": ["KOSPI"] * 8, "close": [10000] * 8, "score": [3.0 - .2 * i for i in range(8)]})
# 승격 상한 3 (2026-09-07, config._capped_note): 평가금액이 원금의 1.38배를 넘으면 상위 3종목에
# 원금 기준 45% 씩만 두고 나머지는 현금이다. 4~6번째로 흩뿌리는 것보다 우측 꼬리가 두꺼웠다
# (P(+80%) 1.46 -> 1.91%, 두 하위 기간 모두 같은 방향). 그래서 2.0억이면 3 x 22.5% = 68% 투자.
# 상한 여유 0.01 · 현금 5% (2026-09-07 밤): 평시 2 x 47.5% = 95%, 상한이 걸리면 3종목 x (49%/배수).
CAPW = CFG["requirements"]["max_single_weight_rule"] - CFG["requirements"].get("single_weight_margin", 0.05)
for eq, wn, winv in [(100, 2, 0.95), (150, 3, 0.95), (200, 3, 0.735), (250, 3, 0.588)]:
    tg = portfolio.build_targets(CFG, sc, "phase_early", eq * 1_000_000, P)
    w = float(tg["target_weight"].iloc[0]); inv = w * len(tg)
    single = w * eq * 1_000_000 / P
    ck(f"{eq/100:.2f}억 -> {len(tg)}종목 투자율 {inv:.0%} 단일/원금 {single:.1%}",
       len(tg) == wn and abs(inv - winv) < 0.02 and single <= CAPW + 1e-9)

print("\n[3] 리스크 게이트")
q = {"A": {"price": 10000, "tradable": True}, "B": {"price": 10000, "tradable": True}}
buy = {"side": "BUY", "code": "A", "name": "A", "qty": 100, "limit_price": 10000}
sell = {"side": "SELL", "code": "A", "name": "A", "qty": 100, "limit_price": 10000}
cases = [
    ("정상 매수",        [buy],  P, P, 100_000_000, False),
    ("현금 부족",        [dict(buy, qty=9000)], P, P, 1_000_000, True),
    ("손실한도+매수",     [buy],  60_000_000, P, P, True),
    ("손실한도+매도",     [sell], 60_000_000, P, P, False),
    ("시세 없음+매수",    [buy],  P, P, P, True),
    ("수량 0",          [dict(buy, qty=0)], P, P, P, True),
    ("지정가 괴리 50%",  [dict(buy, limit_price=15000)], P, P, P, True),
    ("단일주문 과대",     [dict(buy, qty=6000)], P, P, P, True),
]
for name, orders, eq, pr, cash, want_block in cases:
    quotes = {} if "시세 없음" in name else q
    r = risk.check_orders(CFG, orders, quotes, eq, pr, market_open=False, cash=cash)
    ck(f"{name} -> 차단={r.blocked}", r.blocked == want_block,
       str([v.code for v in r.violations]))

print("\n[4] 분할 매도 주문 생성")
pos = {"A": {"name": "A", "market": "KOSPI", "qty": 3000, "avg_price": 10000}}
tg = portfolio.build_targets(CFG, sc, "phase_early", P, P)
qq = {c: {"price": 10000, "change_pct": 0.0} for c in list(sc["code"]) + ["A"]}
od = portfolio.make_orders(CFG, tg, pos, P, qq)
sells = [o for o in od if o.side == "SELL"]
ck("목표 이탈 종목 전량 매도", len(sells) == 1 and sells[0].qty == 3000,
   f"{[(o.code, o.qty) for o in sells]}")
ck("매도가 매수보다 앞", od[0].side == "SELL")
ck("매도 지정가 = -1%", sells and sells[0].limit_price < 10000,
   f"{sells[0].limit_price if sells else '-'}")

print("\n[5] 파밍 ETF 이중 매도 방지")
pos2 = dict(pos); pos2["459580"] = {"name": "CD", "market": "KOSPI", "qty": 1, "avg_price": 1_000_000}
qq["459580"] = {"price": 1_000_000, "change_pct": 0.0}
od2 = portfolio.make_orders(CFG, tg, pos2, P, qq)
ck("알파북이 파밍 ETF 를 팔지 않는다",
   not any(o.code == "459580" for o in od2), f"{[o.code for o in od2 if o.side=='SELL']}")

print("\n[6] 회전율 계획")
tp = turnover.plan_daily(650, 100.0, 10, P, {}, max_daily_ratio=0.35, total_days=20)
ck("부족분 있으면 매수 계획", len(tp.buys) > 0, tp.note[:50])
tp2 = turnover.plan_daily(650, 900.0, 10, P, {}, max_daily_ratio=0.35, total_days=20)
ck("페이스 충분하면 매수 없음", len(tp2.buys) == 0, tp2.note[:40])

print("\n[7] 체결 원장")
POS, TR = Path('state/positions.json'), Path('state/trades.json')
bak = [(p, p.with_suffix('.st')) for p in (POS, TR) if p.exists()]
for a, b in bak: shutil.copy(a, b)
POS.unlink(missing_ok=True); TR.unlink(missing_ok=True)
try:
    f = [{"date": "2026-09-08", "side": "BUY", "code": "A", "name": "A",
          "market": "KOSPI", "qty": 100, "price": 10000}]
    a1, _ = state.record_trades(f); state.apply_fills_to_positions(a1)
    a2, d2 = state.record_trades(f)
    ck("중복 체결 무시", len(a2) == 0 and d2 == 1)
    st = state.compute_constraints(P, 20)
    ck("회전율 1건분", abs(st.turnover_a - 1.0) < 0.01, f"{st.turnover_a:.2f}%")
    r = state.estimate_my_return({"A": {"price": 11000}}, P)
    ck("수익률 추정", r is not None and abs(r - 0.0999) < 0.02, f"{r:+.3f}%")
    ck("시세 결측이면 추정 거부", state.estimate_my_return({}, P) is None)
finally:
    POS.unlink(missing_ok=True); TR.unlink(missing_ok=True)
    for a, b in bak: shutil.move(b, a)

print()
print("[8] 대회 시작 전 상태 청결")
# 테스트가 만든 가짜 보유가 남으면 1일차가 이미 보유 중이라고 판단하고,
# 목표 수량 차액만 주문해 실제로는 아무것도 안 산다. 실제로 남을 뻔했다.
from datetime import date as _d
_S = Path('state')
_start = CFG.get("contest", {}).get("start_date", "")
if _start and str(_d.today()) < _start:
    for _f in ('positions.json', 'trades.json'):
        _p = _S / _f
        _n = len(json.loads(_p.read_text(encoding='utf-8'))) if _p.exists() else 0
        ck(f'대회 전인데 {_f} 가 비어 있다', _n == 0, f'{_n}건')
    _stale = [p.name for p in _S.glob('orders_*.json')]
    ck('대회 전인데 주문서가 없다', not _stale, str(_stale))
    ck('KILL 파일 없음', not (_S / 'KILL').exists())
    ck('exec_args.txt 없음', not (_S / 'exec_args.txt').exists())
else:
    print('  건너뜀 (대회 기간)')

print()
print("=" * 60)
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails:
    print("  X " + f)
sys.exit(1 if fails else 0)
