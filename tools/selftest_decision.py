# -*- coding: utf-8 -*-
"""결정 엔진 자가 점검. 주문을 만들지 않는다."""
import io, json, sys
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
import numpy as np
from pathlib import Path
from imrl import contest_sim, decision as dec, policies, scenarios

fails, checks = [], 0
def ck(name, cond, detail=""):
    global checks
    checks += 1
    print(("  OK   " if cond else "  FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond: fails.append(name)

rng0 = np.random.default_rng(1)
HIST = rng0.normal(0.001, 0.02, size=(400, 5))
CODES = ["A", "B", "C", "D", "E"]

print("[1] 시나리오 재현성과 층화")
b1 = scenarios.make_batches(HIST, CODES, 500, 20, 300, 20260906, "selection")
b2 = scenarios.make_batches(HIST, CODES, 500, 20, 300, 20260906, "selection")
ck("같은 시드 = 같은 경로", np.array_equal(b1[0].returns, b2[0].returns))
bv = scenarios.make_batches(HIST, CODES, 500, 20, 300, 20260906, "validation")
ck("선택용과 확인용은 다른 스트림", not np.array_equal(b1[0].returns, bv[0].returns))
ck("모형 수 = 5", len(b1) == len(scenarios.FIELD_MODELS), str([b.model_id for b in b1]))
ck("경로 총합 = 요청 수", sum(b.returns.shape[0] for b in b1) == 500,
   str(sum(b.returns.shape[0] for b in b1)))
ck("모형끼리 독립 난수", not np.array_equal(b1[0].returns, b1[1].returns))
ck("경쟁자 자산 배수 >= 0", all((b.field_multiplier >= 0).all() for b in b1))
try:
    scenarios.make_batches(HIST, CODES, 5, 20, 300, 1, "selection")
    ck("모형당 2경로 미만은 거부", False)
except ValueError:
    ck("모형당 2경로 미만은 거부", True)

print()
print("[2] 후보 생성")
acts = policies.generate_actions(CODES, ["B", "A", "C"], {"A": 0.5}, 0.45,
                                 turnover_deficit=2.0, days_left=10, farm_available=False)
ids = [a.action_id for a in acts]
ck("HOLD 은 신규 주문 없음", dict(next(a for a in acts if a.action_id == "HOLD").weights) == {"A": 0.5})
ck("CASH 는 비중 0", sum(next(a for a in acts if a.action_id == "CASH").weights.values()) == 0)
ck("종목당 상한 준수 (HOLD 제외 - 신규 매수가 아니다)",
   all(all(w <= 0.45 + 1e-9 for w in a.weights.values())
       for a in acts if a.action_id != "HOLD"))
ck("HOLD 상한 초과를 표시한다",
   "상한을 넘는" in next(a for a in acts if a.action_id == "HOLD").note)
q = next((a for a in acts if a.action_id == "QUALIFY_MIN"), None)
ck("ETF 산입 미확인이면 QUALIFY_MIN 실행 불가",
   q is not None and not q.feasible and "UNCONFIRMED_QUALIFICATION_RULE" in q.exclusion_reasons)
ck("자격 거래는 PLANNED 후보에만", all(a.farm_per_day == 0 for a in acts if a.action_id != "QUALIFY_MIN"))
ck("동일 주문 후보 중복 제거", len(ids) == len(set(ids)), str(ids))

print()
print("[3] 계좌 전개와 최종 보상")
RULES = contest_sim.Rules(principal=100_000_000, min_turnover=5.0, min_trading_days=5,
                          min_distinct_symbols=5, single_limit=0.50,
                          cost_stock=0.0011, cost_etf=0.0001, fill_rate=0.85)
acct = contest_sim.Account(cash=100_000_000, shares={}, turnover_amount=0,
                           trading_days=0, symbols=frozenset())
PX = {c: 10_000 for c in CODES}
w = {"A": 0.45, "B": 0.45}
o = contest_sim.simulate(w, acct, PX, b1[0], RULES, frozenset(), 0.0, None,
                         daily_replacement=0.245)
ck("승리는 0/1", set(np.unique(o.win)) <= {0, 1})
ck("자격 없으면 승리 없음", not np.any((o.win == 1) & (o.qualified == 0)))
ck("마이너스 수익은 자격 없음", not np.any((o.qualified == 1) & (o.final_return <= 0)))
ck("회전율 > 0", o.turnover.mean() > 0, f"{o.turnover.mean():.1%}")
ck("비용 > 0", o.cost.mean() > 0, f"{o.cost.mean():,.0f}원")

o_cash = contest_sim.simulate({}, acct, PX, b1[0], RULES, frozenset(), 0.0, None,
                              daily_replacement=0.245)
ck("현금 후보는 회전 없음", o_cash.turnover.mean() == 0)
ck("현금 후보는 자격 미달", o_cash.qualified.sum() == 0)

# 같은 경로 = 같은 결과
o2 = contest_sim.simulate(w, acct, PX, b1[0], RULES, frozenset(), 0.0, None,
                          daily_replacement=0.245)
ck("결정적(같은 입력 = 같은 출력)", np.array_equal(o.win, o2.win))

print()
print("[4] 쌍별 비교")
a_by = {b.model_id: contest_sim.simulate(w, acct, PX, b, RULES, frozenset(), 0.0, None,
                                         daily_replacement=0.245).win for b in b1}
b_by = {b.model_id: contest_sim.simulate({"A": 0.30, "B": 0.30}, acct, PX, b, RULES,
                                         frozenset(), 0.0, None,
                                         daily_replacement=0.245).win for b in b1}
d, se, lo = dec.paired_delta(a_by, b_by)
ck("차이는 -1~1", -1 <= d <= 1, f"{d:+.4f}")
ck("표준오차 >= 0", se >= 0, f"{se:.4f}")
ck("하한 <= 차이", lo <= d + 1e-12, f"{lo:+.4f}")
per, agg, worst = dec.aggregate(a_by)
ck("최악 모형 <= 평균", worst <= agg + 1e-12, f"{worst:.4f} <= {agg:.4f}")

print()
print("[5] shadow 격리")
cfg = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
mode = cfg["decision_engine"]["mode"]
ck("mode 가 유효한 값", mode in ("shadow", "advisory", "execute"), mode)
run_src = Path("run.py").read_text(encoding="utf-8")
ck("엔진은 mode=execute 일 때만 주문에 연결된다",
   'get("mode") == "execute"' in run_src)
ck("probability_semantics = scenario_only",
   cfg["decision_engine"]["probability_semantics"] == "scenario_only")
ck("ETF 산입은 미확인 상태", cfg["rules_status"]["etf_counts_for_turnover"] is False)
src = Path("imrl/decision_cmd.py").read_text(encoding="utf-8")
# 주석·문자열이 아니라 **실제 쓰기**를 본다. write_text 대상 경로가 decision_ 뿐이어야 한다.
import re
targets = re.findall(r'ROOT / "state" / f"([a-z_]+)', src)
ck("결정 경로가 쓰는 파일은 decision_ 뿐", set(targets) == {"decision_"}, str(set(targets)))
ck("결정 경로가 HTS 를 직접 부르지 않는다",
   "hts_exec" not in src and "import execute" not in src)

print()
print("[관측 필드 분산이 결정에 실제로 먹히는가]")
# 계측기는 있었는데 배선이 없었다. sigma_f 를 매일 재도 엔진은 하드코딩된
# 모수로 돌았다. 우승 확률이 이 값 하나에 30배 흔들리므로, 배선이 끊긴 것은
# 계측을 안 한 것과 같다.
import numpy as _np
base = scenarios.make_batches(HIST, CODES, 500, 20, 300, 20260906, "selection")
b_lo = scenarios.make_batches(HIST, CODES, 500, 20, 300, 20260906, "selection",
                              sigma_obs=0.12)
b_hi = scenarios.make_batches(HIST, CODES, 500, 20, 300, 20260906, "selection",
                              sigma_obs=0.45)

def spread(bs):
    return float(_np.mean([b.field_multiplier.std(axis=1).mean() for b in bs]))

s0, sl, sh = spread(base), spread(b_lo), spread(b_hi)
ck("관측 없으면 기본값 그대로",
   spread(scenarios.make_batches(HIST, CODES, 500, 20, 300, 20260906,
                                 "selection", sigma_obs=None)) == s0, f"{s0:.3f}")
ck("낮은 관측이 필드를 좁힌다", sl < s0, f"{sl:.3f} < {s0:.3f}")
ck("높은 관측이 필드를 넓힌다", sh > s0, f"{sh:.3f} > {s0:.3f}")
ck("좁힌 결과가 관측값 근처", abs(sl - 0.12) < 0.02, f"{sl:.3f} vs 0.12")
ck("넓힌 결과가 관측값 근처", abs(sh - 0.45) < 0.06, f"{sh:.3f} vs 0.45")

# 한 번의 엉뚱한 추정이 모형을 뒤엎으면 안 된다.
b_absurd = scenarios.make_batches(HIST, CODES, 500, 20, 300, 20260906,
                                  "selection", sigma_obs=9.0)
sa = spread(b_absurd)
ck("터무니없는 관측은 배율 상한에 걸린다", sa <= s0 * 2.05, f"{sa:.3f} <= {s0*2.05:.3f}")
ck("음수·0 관측은 무시", spread(scenarios.make_batches(
    HIST, CODES, 500, 20, 300, 20260906, "selection", sigma_obs=0.0)) == s0)

# 같은 씨앗이면 같은 결과여야 한다. 재현이 깨지면 결정을 검증할 수 없다.
b_again = scenarios.make_batches(HIST, CODES, 500, 20, 300, 20260906,
                                 "selection", sigma_obs=0.45)
ck("씨앗이 같으면 결과도 같다",
   all(_np.array_equal(a.field_multiplier, b.field_multiplier)
       for a, b in zip(b_hi, b_again)))

print()
print("[관측을 잔여 구간으로 환산한다]")
# 관측은 대회 시작부터의 누적 분산이고 시뮬레이터가 쓰는 것은 남은 날들의
# 분산이다. 그대로 쓰면 후반으로 갈수록 필드를 크게 과대평가한다.
from imrl import decision_cmd as dcmd, data as _data, state as _st
import json as _json
_cfg = _json.loads(Path('config/settings.json').read_text(encoding='utf-8'))
_cal = _data.TradingCalendar.from_config(_cfg["contest"])
_days = _cal.trading_days()
real_load = _st.load_field
try:
    _st.load_field = lambda: None
    ck("관측이 없으면 None", dcmd._observed_field_sigma(_cal, 10) is None)

    # 10일 경과 시점에 sigma 20%p 를 관측했다.
    _st.load_field = lambda: {"date": f"{_days[9]:%Y%m%d}", "sigma_f_pct": 20.0}
    same = dcmd._observed_field_sigma(_cal, 10)
    ck("경과=잔여 면 그대로", abs(same - 0.20) < 1e-9, f"{same:.4f}")
    less = dcmd._observed_field_sigma(_cal, 2)
    ck("잔여가 짧으면 줄어든다", less < same, f"{less:.4f} < {same:.4f}")
    ck("sqrt(잔여/경과) 로 환산", abs(less - 0.20 * (2 / 10) ** 0.5) < 1e-9,
       f"{less:.4f}")

    _st.load_field = lambda: {"date": "20260908", "sigma_f_pct": -5}
    ck("음수 추정은 버린다", dcmd._observed_field_sigma(_cal, 10) is None)
    _st.load_field = lambda: {"date": "쓰레기", "sigma_f_pct": 20.0}
    ck("깨진 날짜는 버린다", dcmd._observed_field_sigma(_cal, 10) is None)
    _st.load_field = lambda: {"sigma_f_pct": 20.0}
    ck("날짜가 없으면 버린다", dcmd._observed_field_sigma(_cal, 10) is None)
finally:
    _st.load_field = real_load

print()
print("=" * 62)
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
