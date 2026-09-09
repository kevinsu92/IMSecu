# -*- coding: utf-8 -*-
"""외부 검토(2026-09-07 개발·투자 전문가 평가)가 재현한 결함을 못박는다.

각 검사는 검토 문서의 재현 입력을 그대로 쓴다. 통과는 "그 결함이 다시 생기지
않는다"는 뜻이지 전략의 우위나 우승 확률의 증명이 아니다. 전부 임시 폴더·메모리에서
돈다 — 운영 원장·설정·주문을 건드리지 않는다.
"""
import io, json, os, shutil, sys, tempfile, threading, time, urllib.request, urllib.error
os.environ["IMRL_QUIET"] = "1"
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd

fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)

from imrl import contest_sim as CS, scenarios as SC, decision as DEC, state as ST, risk as RK
from imrl import experts as EX, cycle as CY, notify

def zero_batch(codes, n_days=1, n_field=2):
    return SimpleNamespace(codes=list(codes), returns=np.zeros((2, n_days, len(codes))),
                           field_multiplier=np.ones((2, n_field)), market=np.zeros((2, n_days)))

print("[1] 시뮬레이터 자산 보존 — 가격 고정·비용 0 이면 어떤 체결률에서도 수익 0")
PX = {"A": 10_000, "B": 10_000}
for f in (0.0, 0.25, 0.5, 0.85, 1.0):
    rules = CS.Rules(100_000_000, 0, 0, 0, 0.5, 0, 0, f)
    o = CS.simulate({"A": 0.45, "B": 0.45}, CS.Account(100_000_000, {}, 0, 0, frozenset()),
                    PX, zero_batch(["A", "B"]), rules, frozenset(), 0.0, None)
    ck(f"매수 체결률 {f:.0%} → 수익 0 (예전 85%: +13.5%)", abs(float(o.final_return[0])) < 1e-9, f"{float(o.final_return[0])*100:+.4f}%")
    o2 = CS.simulate({}, CS.Account(0, {"A": 10_000}, 0, 0, frozenset()),
                     PX, zero_batch(["A", "B"]), rules, frozenset(), 0.0, None)
    ck(f"전량 매도 체결률 {f:.0%} → 수익 0 (미체결 잔량 보유)", abs(float(o2.final_return[0])) < 1e-9, f"{float(o2.final_return[0])*100:+.4f}%")
rules_c = CS.Rules(100_000_000, 0, 0, 0, 0.5, 0.0011, 0, 0.85)
oc = CS.simulate({"A": 0.45, "B": 0.45}, CS.Account(100_000_000, {}, 0, 0, frozenset()),
                 PX, zero_batch(["A", "B"]), rules_c, frozenset(), 0.0, None)
ck("비용이 있으면 정확히 비용만큼 준다 (85% 체결, 편도 0.11%: -0.08415%)",
   abs(float(oc.final_return[0]) + 0.0008415) < 1e-6, f"{float(oc.final_return[0])*100:+.5f}%")
ck("체결률 0 이면 거래·비용·회전 없음", float(CS.simulate({"A": 0.45}, CS.Account(100_000_000, {}, 0, 0, frozenset()), PX, zero_batch(["A"]), CS.Rules(100_000_000, 0, 0, 0, 0.5, 0.0011, 0, 0.0), frozenset(), 0.0, None).turnover[0]) == 0.0)

print()
print("[2] 이미 채운 자격은 현금을 들고만 있어도 유지된다")
rules_q = CS.Rules(100_000_000, 5.0, 5, 5, 0.5, 0, 0, 1.0)
q = CS.simulate({}, CS.Account(101_000_000, {}, 500_000_000, 5, frozenset("ABCDE")),
                PX, zero_batch(["A", "B"]), rules_q, frozenset(), 0.01, None)
ck("회전율 500% 유지 (예전 0%)", abs(float(q.turnover[0]) - 5.0) < 1e-9, f"{float(q.turnover[0]):.2f}")
ck("자격 유지 (예전 미달)", int(q.qualified[0]) == 1)
ck("+1% 현금 계좌가 0% 경쟁자 상대로 승리", int(q.win[0]) == 1)

print()
print("[3] 없는 종목을 산 것으로 자격을 채우지 않는다")
b20 = SimpleNamespace(codes=["A", "B"], returns=np.full((1, 20, 2), 0.001), field_multiplier=np.ones((1, 1)), market=np.full((1, 20), 0.001))
a3 = CS.simulate({"A": 0.45, "B": 0.45}, CS.Account(10_000_000, {"A": 4500, "B": 4500}, 90_000_000, 1, frozenset({"A", "B"})),
                 PX, b20, rules_q, frozenset(), 0.0, None, daily_replacement=0.245)
ck("배치에 A·B 뿐이면 종목수 요건 5 를 못 채운다 (예전: 자격 1)", int(a3.qualified[0]) == 0)
ck("회전율은 교체율로 여전히 오른다", float(a3.turnover[0]) > 1.0)
a3u = CS.simulate({"A": 0.45, "B": 0.45}, CS.Account(10_000_000, {"A": 4500, "B": 4500}, 90_000_000, 1, frozenset({"A", "B"})),
                  PX, b20, rules_q, frozenset(), 0.0, None, daily_replacement=0.245, universe_n=429)
ck("실제 유니버스 크기를 주면 교체로 종목수가 늘 수 있다 (엔진은 유니버스 크기를 넘긴다)", int(a3u.qualified[0]) == 1)
ck("엔진이 universe_n 을 넘긴다", "universe_n=uni_n" in Path("imrl/decision_cmd.py").read_text(encoding="utf-8"))

print()
print("[4] 경쟁자 고유 변동성은 남은 날에 비례해 줄어든다")
rng0 = np.random.default_rng(3)
HIST = rng0.normal(0.0, 1e-9, size=(60, 3))     # 시장 0
def spread(nd):
    b = SC.sample_paths("FIELD_SHARED_MARKET", HIST, ["A", "B", "C"], 400, nd, 97, np.random.default_rng(19))
    return float(b.field_multiplier.std(axis=1).mean())
s1, s20, s0 = spread(1), spread(20), spread(0)
ck("1일 분산 < 20일 분산 / 3 (예전엔 같았다)", s1 < s20 / 3, f"1일 {s1:.4f} vs 20일 {s20:.4f}")
ck("남은 날 0 이면 미래 수익 없음 (배수 전부 1)", s0 < 1e-12)
b42 = SC.sample_paths("FIELD_SHARED_MARKET", HIST, ["A", "B", "C"], 10, 5, 5, np.random.default_rng(42))
ck("정수 시드도 받는다 (예전 TypeError)", b42.seed_entropy == (42,), str(b42.seed_entropy))

print()
print("[5] 쌍별 차이 구간 — 신뢰수준이 반영되고 퇴화 표본의 SE 가 0 이 아니다")
a = {"m": np.array([1, 1, 1, 1, 1, 1, 1, 1, 1, 1], dtype=float)}; b = {"m": np.zeros(10)}
d80 = DEC.paired_delta(a, b, conf=0.80)[2]; d95 = DEC.paired_delta(a, b, conf=0.95)[2]; d99 = DEC.paired_delta(a, b, conf=0.99)[2]
ck("99% 하한 < 95% 하한 < 80% 하한", d99 < d95 < d80, f"{d99:.3f} < {d95:.3f} < {d80:.3f}")
ck("전승 표본도 하한 < 1 (예전 lower=1, SE 0)", d95 < 1.0)
z = DEC.paired_delta({"m": np.zeros(1000)}, {"m": np.zeros(1000)})
ck("전부 0 인 표본은 SE > 0, 하한 < 0 → 전환 없음", z[1] > 0 and z[2] < 0, f"se={z[1]:.4f}")

print()
print("[6] 최대낙폭은 시작 자산을 고점에 넣는다")
import backtest as BT
mdd = BT.summarize(pd.Series([-0.5] + [0.0] * 21), "first_day_loss")["max_drawdown_pct"]
ck("첫날 -50% 의 MDD = -50% (예전 0%)", abs(mdd + 50.0) < 1e-9, f"{mdd:.2f}")

print()
print("[7] CASH 결정은 빈 비중이지 결정 없음이 아니다")
src_run = Path("run.py").read_text(encoding="utf-8")
ck("계획 연결이 `w is not None` 으로 분기한다", "if w is not None:" in src_run and "len(w) == 0" in src_run)
ck("관문 결과가 결정 파일에 다시 기록된다", "adopted_action" in Path("imrl/decision_cmd.py").read_text(encoding="utf-8"))
ck("시작 계좌가 실제 종목 집합·공식 일수를 받는다", "traded_syms" in Path("imrl/decision_cmd.py").read_text(encoding="utf-8") and "effective_days" in Path("imrl/decision_cmd.py").read_text(encoding="utf-8"))

print()
print("[8] 원장 — 확정 체결은 추정을 대체하고, 보유는 원장의 투영이다")
tmp = Path(tempfile.mkdtemp(prefix="imrl_review_"))
bak = (ST.TRADES_FILE, ST.POSITIONS_FILE)
ST.TRADES_FILE = tmp / "trades.json"; ST.POSITIONS_FILE = tmp / "positions.json"
try:
    a100 = {"date": "2026-09-09", "side": "BUY", "code": "A", "name": "가", "market": "KOSPI", "qty": 100, "price": 1000, "assumed": True, "key": "BUY:A"}
    add, _ = ST.record_trades([a100]); ST.apply_fills_to_positions(add)
    ck("추정 매수 100주 반영", ST.load_positions()["A"]["qty"] == 100)
    removed = ST.drop_assumed("2026-09-09", [("BUY", "A")])
    add, _ = ST.record_trades([{"date": "2026-09-09T15:31:02", "side": "BUY", "code": "A", "qty": 40, "price": 990}])
    pos = ST.apply_fills_to_positions(add)
    ck("실제 40주 입력 → 보유 40주 (예전 140주)", removed == 1 and pos["A"]["qty"] == 40, str(pos.get("A")))
    add2, dup2 = ST.record_trades([{"date": "2026-09-09T15:31:05", "side": "BUY", "code": "A", "qty": 40, "price": 990}])
    ck("같은 체결을 3초 뒤 다시 넣으면 중복 (예전 2건 80주)", len(add2) == 0 and dup2 == 1)
    ST.POSITIONS_FILE.unlink()
    ck("보유 파일이 없으면 원장에서 다시 만든다", ST.heal_positions() and ST.load_positions()["A"]["qty"] == 40)
    time.sleep(0.05)
    os.utime(ST.POSITIONS_FILE, (time.time() - 100, time.time() - 100))
    ck("원장이 보유보다 새로우면 다시 만든다 (기록 도중 중단)", ST.heal_positions() is True)
    ck("정상이면 손대지 않는다", ST.heal_positions() is False)
    import run as RUN
    real_lp = ST.load_positions
    buf = io.StringIO(); so = sys.stdout; sys.stdout = buf
    try:
        RUN.cmd_fill({}, SimpleNamespace(from_orders=False, side="SELL", code="A", qty="200", price="1000", name=None, market=None))
    finally:
        sys.stdout = so
    ck("보유 40주인데 200주 매도 입력은 거부 (예전엔 0 으로 절사)", "거부" in buf.getvalue() and ST.load_positions()["A"]["qty"] == 40, buf.getvalue()[:60])
finally:
    ST.TRADES_FILE, ST.POSITIONS_FILE = bak
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("[9] 감시자 완료 판정 — 조각 전부를 본다")
import watchdog as W
real_o, real_l = W._orders_today, W._ledger_today
try:
    W._orders_today = lambda: [{"side": "SELL", "code": "000001", "qty": 90}]
    W._ledger_today = lambda: {"SELL:000001#1/3": "submitted"}
    ck("1/3 만 접수면 미완료 (예전 True)", W.step_done("sell") is False)
    W._ledger_today = lambda: {"SELL:000001#1/3": "submitted", "SELL:000001#2/3": "submitted", "SELL:000001#3/3": "submitted"}
    ck("세 조각 모두 접수면 완료", W.step_done("sell") is True)
    W._ledger_today = lambda: {"SELL:000001#1/3": "submitted", "SELL:000001#2/3": "not_accepted", "SELL:000001#3/3": "submitted"}
    ck("확인된 미접수 조각이 남으면 미완료 (재시도 대상)", W.step_done("sell") is False)
    W._ledger_today = lambda: {"SELL:000001#1/3": "submitted", "SELL:000001#2/3": "unknown", "SELL:000001#3/3": "submitted"}
    ck("접수 불명은 다시 누르지 않으므로 완료로 친다", W.step_done("sell") is True)
    W._orders_today = lambda: [{"side": "BUY", "code": "000002", "qty": 10}]
    W._ledger_today = lambda: {"BUY:000002": "not_accepted"}
    ck("매수 미접수만 있으면 미완료", W.step_done("buy") is False)
finally:
    W._orders_today, W._ledger_today = real_o, real_l

print()
print("[10] 대조 — 마지막 상태만 보고, 결과를 PASS/FAIL/UNKNOWN 으로 남긴다")
import run as RUN
tmp2 = Path(tempfile.mkdtemp(prefix="imrl_review2_"))
bak2 = (RUN.ROOT, ST.TRADES_FILE, ST.POSITIONS_FILE, notify.alert)
try:
    RUN.ROOT = tmp2; (tmp2 / "state").mkdir()
    ST.TRADES_FILE = tmp2 / "state" / "trades.json"; ST.POSITIONS_FILE = tmp2 / "state" / "positions.json"
    alerts = []; notify.alert = lambda t, d: alerts.append(t) or True
    D = "20260909"
    (tmp2 / "state" / f"orders_{D}.json").write_text(json.dumps([{"side": "BUY", "code": "000001", "name": "가", "qty": 100, "limit_price": 1000}]), encoding="utf-8")
    (tmp2 / "state" / f"submitted_{D}.json").write_text(json.dumps([
        {"key": "BUY:000001", "status": "pending_send"}, {"key": "BUY:000001", "status": "submitted", "qty": 100, "price": 1000}]), encoding="utf-8")
    ST.TRADES_FILE.write_text(json.dumps([{"date": "2026-09-09", "side": "BUY", "code": "000001", "name": "가", "qty": 100, "price": 1000, "assumed": True, "key": "BUY:000001"}]), encoding="utf-8")
    ST.rebuild_positions()
    buf = io.StringIO(); so = sys.stdout; sys.stdout = buf
    try:
        rc = RUN.cmd_reconcile({}, SimpleNamespace(date=D))
    finally:
        sys.stdout = so
    out = buf.getvalue()
    ck("pending→submitted 는 불명으로 보고하지 않는다 (예전 오경보)", "접수 여부 불명" not in out and alerts == [])
    ck("추정 체결은 '완전체결'이 아니라 '추정 체결(접수 기준, 미확인)'", "추정 체결(접수 기준" in out and "완전체결" not in out)
    rec = json.loads((tmp2 / "state" / f"reconcile_{D}.json").read_text(encoding="utf-8"))
    ck("결과 파일 UNKNOWN (추정 체결 1건)", rec["status"] == "UNKNOWN" and rec["assumed_trades"] == 1 and rc == 0, str(rec.get("status")))
    (tmp2 / "state" / f"submitted_{D}.json").write_text(json.dumps([{"key": "BUY:000001", "status": "unknown", "qty": 100, "price": 1000}]), encoding="utf-8")
    buf = io.StringIO(); sys.stdout = buf
    try:
        RUN.cmd_reconcile({}, SimpleNamespace(date=D))
    finally:
        sys.stdout = so
    rec = json.loads((tmp2 / "state" / f"reconcile_{D}.json").read_text(encoding="utf-8"))
    ck("접수 불명이 남으면 FAIL + 경보", rec["status"] == "FAIL" and rec["pending"] == ["BUY:000001"] and alerts)
finally:
    RUN.ROOT, ST.TRADES_FILE, ST.POSITIONS_FILE, notify.alert = bak2
    shutil.rmtree(tmp2, ignore_errors=True)

print()
print("[11] 리스크 검사 — 미래 시세와 현금 불명")
cfg = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
now = datetime.now(RK.KST)
fut = {"000001": {"price": 10000, "traded_at": (now.replace(microsecond=0) + __import__("datetime").timedelta(days=1)).isoformat(), "tradable": True}}
buy = [{"side": "BUY", "code": "000001", "name": "가", "qty": 10, "limit_price": 10000}]
r1 = RK.check_orders(cfg, buy, fut, 100_000_000, 100_000_000, now=now, market_open=True, cash=50_000_000)
ck("하루 뒤 시각의 시세는 차단 (예전 통과)", any(v.code == "QUOTE_FROM_FUTURE" for v in r1.blocks))
okq = {"000001": {"price": 10000, "traded_at": now.isoformat(), "tradable": True}}
r2 = RK.check_orders(cfg, buy, okq, 100_000_000, 100_000_000, now=now, market_open=True, cash=None)
ck("현금 불명 + 신규 매수 = 차단 (예전 경고만)", any(v.code == "CASH_UNKNOWN" and v.level == "BLOCK" for v in r2.violations))
sell = [{"side": "SELL", "code": "000001", "name": "가", "qty": 10, "limit_price": 10000}]
r3 = RK.check_orders(cfg, sell, okq, 100_000_000, 100_000_000, now=now, market_open=True, cash=None)
ck("현금 불명이어도 매도만이면 진행", not r3.blocked)

print()
print("[12] 전문가 점검 — 기록 없음은 정상이 아니다")
tmp3 = Path(tempfile.mkdtemp(prefix="imrl_review3_"))
bakS = EX.S
try:
    EX.S = tmp3
    ops = {o["key"]: o for o in EX.evaluate(EX.context(cfg, "20260909", datetime(2026, 9, 9, 10, 0), {}, {}))}
    ck("심장박동·중계실 둘 다 없으면 경고 (예전 '신선도 정상')", ops.get("rt.hb", {}).get("level") == "warn" and ops.get("rt.relay", {}).get("level") == "warn" and "rt.fresh" not in ops)
    (tmp3 / "reconcile_20260909.json").write_text(json.dumps({"status": "FAIL", "issues": 2, "pending": ["x"]}), encoding="utf-8")
    ops = {o["key"]: o for o in EX.evaluate(EX.context(cfg, "20260909", datetime(2026, 9, 9, 16, 30), {}, {}))}
    ck("대조 FAIL 이면 레드팀 경고", ops.get("rt.reconcile_fail", {}).get("level") == "warn")
finally:
    EX.S = bakS
    shutil.rmtree(tmp3, ignore_errors=True)

print()
print("[13] 경보 — 전달 성공 뒤에만 중복 방지 표시")
tmp4 = Path(tempfile.mkdtemp(prefix="imrl_review4_"))
bakE = CY.EXP_DIR
try:
    CY.EXP_DIR = tmp4
    ck("처음엔 미전달", not CY._alert_sent("20260909", "k1"))
    CY._mark_alert("20260909", "k1")
    ck("표시 뒤엔 전달됨", CY._alert_sent("20260909", "k1"))
    src_cy = Path("imrl/cycle.py").read_text(encoding="utf-8")
    ck("run() 이 전송 결과가 참일 때만 표시한다", "if ok_send:" in src_cy and "_mark_alert(day, o[\"key\"])" in src_cy and "alerts_failed" in src_cy)
finally:
    CY.EXP_DIR = bakE
    shutil.rmtree(tmp4, ignore_errors=True)

print()
print("[14] 상황판 데이터 — 공식 우선·실제 스키마·거절 표시·실행 판정")
import dashboard as DASH
d = DASH.gather(with_news=False)
ck("자격 일수·종목수가 공식 우선(effective)", "official" in d["qual"] and "internal" in d["qual"] and "source" in d["qual"])
ck("실행 판정·신선도·격차 필드", all(k in d for k in ("readiness", "fresh", "gap", "reconcile")))
ck("실행 판정 상태 문구", d["readiness"]["status"] in ("시작 전 · 휴장", "실행 준비", "확인 필요", "신규 주문 중지"), d["readiness"]["status"])
if d.get("last_decision"):
    ld = d["last_decision"]
    ck("후보 전부 + aggregate_win 그대로", isinstance(ld.get("candidates"), list) and len(ld["candidates"]) >= 3 and any(c.get("win") is not None for c in ld["candidates"]))
src_d = Path("tools/dashboard.py").read_text(encoding="utf-8")
ck("not_accepted 는 '미접수(거절)'", "미접수(거절)" in src_d)
ck("긴급정지는 두 경로를 본다", "C:/imrl_state/KILL" in src_d and "C:/imrl_state/KILL" in Path("watchdog.py").read_text(encoding="utf-8"))
body = DASH.build(with_news=False).read_text(encoding="utf-8")
ck("첫 화면에 실행 판정·1위 격차 카드", "실행 판정" in body and "1위 격차" in body)
ck("입력 6개에 aria-label", body.count("aria-label=") >= 6)
ck("입력 중이면 새로고침하지 않는다", "window._dirty" in body)
ck("확률 문구가 '과거 모형 실험'으로 조건부 표기", "과거 모형 실험" in body and "1등을 포기하고" not in body)

print()
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
