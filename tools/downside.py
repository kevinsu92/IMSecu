# -*- coding: utf-8 -*-
"""1등 확률만으로는 결정을 못 한다. **하방**도 같이 봐야 한다.

수상 조건이 비대칭이다.
  · 수익률이 음수면 상금에서 제외된다 — 등수와 무관하게 0원이다.
  · 1등은 300명 중 최고를 넘어야 한다.
그래서 물어야 하는 것은 셋이다: 1등 확률, 입상권 확률, 그리고 **얼마나 잃을 수 있나.**

표본은 겹치는 20일 창이다. 창이 겹치면 표본끼리 상관돼 유효 표본수가 겉보기보다
훨씬 적다. 백분위는 참고치이지 정밀한 추정이 아니다 — 아래에서 명시한다.
"""
import json, sys
from pathlib import Path
ARGV = sys.argv[1:]
sys.argv = ["x"]
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for st in (sys.stdout, sys.stderr):
    try: st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
import numpy as np, pandas as pd
import backtest as bt

cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
close = bt.load_panel(cfg, 4.0, 600)
vol = pd.read_parquet(bt.CACHE.with_name("bars_volume.parquet")).reindex(close.index)
W = cfg["alpha"]["weights"]; PH = cfg["portfolio"]["phase_early"]
RULE = cfg["requirements"]["max_single_weight_rule"] - 0.05
NMAX = int(cfg["portfolio"].get("max_positions_when_capped", 6))
INV = 1.0 - PH["cash_buffer"]; COST = bt.ROUND_TRIP_COST; WIN = 20

dates = close.index
picks, rets = [], []
for i in range(25, len(dates) - 1):
    s = bt.score_at(close, vol, i, W)
    if s.empty:
        picks.append([]); rets.append(np.array([])); continue
    p = list(s.head(NMAX).index)
    e = close.iloc[i][p].values.astype(float); x = close.iloc[i + 1][p].values.astype(float)
    ok = np.isfinite(e) & np.isfinite(x) & (e > 0) & (x > 0)
    picks.append([c for c, k in zip(p, ok) if k]); rets.append(x[ok] / e[ok] - 1.0)

def run(farm_cost_per_day=0.0, n_min=2):
    """라이브 제약(클램프+승격)을 반영한 20일 경로. 최종수익률과 최대낙폭을 함께."""
    fin, mdd = [], []
    for s0 in range(0, len(picks) - WIN):
        eq, prev, peak, worst = 1.0, set(), 1.0, 0.0
        for k in range(s0, s0 + WIN):
            pk, r = picks[k], rets[k]
            if not len(pk): continue
            cap = min(PH["max_weight"], RULE)
            if eq > 1.0: cap = min(cap, RULE / eq)
            n = n_min
            if cap < min(PH["max_weight"], RULE):
                n = max(n, min(int(np.ceil(INV / cap - 1e-9)), NMAX))
            n = min(n, len(pk)); w = min(INV / n, cap)
            eq *= (1 + float(np.dot(r[:n], [w] * n))
                   - len(set(pk[:n]) - prev) / n * COST * (w * n))
            eq *= (1 - farm_cost_per_day)          # 회전율 파밍 왕복 비용
            prev = set(pk[:n])
            peak = max(peak, eq); worst = min(worst, eq / peak - 1.0)
        fin.append(eq - 1.0); mdd.append(worst)
    return np.array(fin), np.array(mdd)

# 회전율 파밍 비용: 하루 25백만 왕복 x 0.02% / 1억 = 하루 약 0.005%
FARM = 0.25 * 0.0002
fin, mdd = run(FARM)
raw, _ = run(0.0)

#: 중계실 전체현황의 참가자수. 300 은 근거 없는 추정이었다.
N_FIELD = int(ARGV[0]) if ARGV else 97
TRIALS = 200_000
rng = np.random.default_rng(20260906)

PRIZE_PLACES = 25          # 주최측 공지: 1위~25위 입상


def ranks(mine, sigma_f_pct, mu_f_pct=0.0, places=(1, 2, 3, 10, PRIZE_PLACES)):
    """상위 k등 이내에 들 확률과 기대 등수.

    상금이 25위까지 나간다는 것이 하방 판단을 바꾼다. 1등만 보면 이 전략은
    복권이지만, 25위까지면 **입상 자체는 중앙값 경로에서도 가능한 사건**이다.
    수익률이 음수면 등수와 무관하게 제외되므로 그 조건을 함께 건다.
    """
    s = sigma_f_pct / 100.0; m = mu_f_pct / 100.0
    s_log = np.sqrt(np.log(1 + (s / (1 + m + 1e-9)) ** 2))
    mu_log = np.log(1 + m + 1e-9) - 0.5 * s_log ** 2
    hits = {k: 0 for k in places}; rank_sum = 0
    for i in range(0, TRIALS, 20_000):
        b = min(20_000, TRIALS - i)
        oth = np.exp(rng.normal(mu_log, s_log, size=(b, N_FIELD - 1))) - 1.0
        me = rng.choice(mine, size=b)
        above = (oth > me[:, None]).sum(axis=1)     # 나보다 나은 사람 수
        qual = me > 0                               # 음수면 상금 제외
        for k in places:
            hits[k] += int(((above <= k - 1) & qual).sum())
        rank_sum += int((above + 1).sum())
    return tuple(hits[k] / TRIALS * 100 for k in places) + (rank_sum / TRIALS,)

print("=" * 74)
print(" 하방과 상방 — 라이브 제약 반영, 겹치는 20일 창 %d개" % len(fin))
print("=" * 74)
print()
print(" 20일 최종 수익률 분포 (파밍비용 포함)")
for q in (1, 5, 10, 25, 50, 75, 90, 95, 99):
    print("   P%-3d %+8.1f%%" % (q, np.percentile(fin, q) * 100))
print("   평균 %+7.1f%%   표준편차 %6.1f%%p" % (fin.mean() * 100, fin.std() * 100))
print()
print(" 하방 사건 확률")
print("   수익률 < 0   (상금 제외)   %5.1f%%" % ((fin < 0).mean() * 100))
print("   수익률 < -10%%              %5.1f%%" % ((fin < -0.10).mean() * 100))
print("   수익률 < -20%%              %5.1f%%" % ((fin < -0.20).mean() * 100))
print("   수익률 < -30%%              %5.1f%%" % ((fin < -0.30).mean() * 100))
print("   최악 관측치               %+6.1f%%" % (fin.min() * 100))
print()
print(" 기간 중 최대낙폭 (고점 대비)")
for q in (50, 75, 90, 95, 99):
    print("   P%-3d %7.1f%%" % (q, np.percentile(-mdd, q) * 100))
print("   40%% 손실한도 도달 확률      %5.1f%%" % ((-mdd > 0.40).mean() * 100))
print()
print(" 파밍 비용의 값")
print("   비용 없음 중앙값 %+6.2f%%  →  포함 %+6.2f%%   차이 %.2f%%p"
      % (np.median(raw) * 100, np.median(fin) * 100,
         (np.median(raw) - np.median(fin)) * 100))
print()
print(f" 등수 확률 (필드 {N_FIELD}명, 평균 0%)")
hdr = " %-9s %7s %7s %7s %8s %9s %9s"
print(hdr % ("필드 σ", "1등", "2등", "3등", "10등", "25등(상금)", "기대 등수"))
res = {}
for s in (15, 20, 25, 30):
    r = ranks(fin, s)
    res[str(s)] = [round(x, 2) for x in r]
    print(" %-9s %6.2f%% %6.2f%% %6.2f%% %7.2f%% %8.1f%% %8.0f위"
          % (f"{s}%p", *r))
print()
print(" 필드 평균이 양수면 (강세장 20일, 평균 +5%)")
print(hdr % ("필드 σ", "1등", "2등", "3등", "10등", "25등(상금)", "기대 등수"))
for s in (20, 25):
    r = ranks(fin, s, mu_f_pct=5.0)
    print(" %-9s %6.2f%% %6.2f%% %6.2f%% %7.2f%% %8.1f%% %8.0f위"
          % (f"{s}%p", *r))
print("=" * 74)

p = ROOT / "state" / "backtest_current.json"
j = json.loads(p.read_text(encoding="utf-8"))
j["downside"] = {
    "windows": len(fin),
    "pct": {f"P{q}": round(float(np.percentile(fin, q)) * 100, 2)
            for q in (1, 5, 10, 25, 50, 75, 90, 95, 99)},
    "p_negative_pct": round(float((fin < 0).mean()) * 100, 2),
    "p_below_10_pct": round(float((fin < -0.10).mean()) * 100, 2),
    "p_below_20_pct": round(float((fin < -0.20).mean()) * 100, 2),
    "worst_pct": round(float(fin.min()) * 100, 2),
    "mdd_p90_pct": round(float(np.percentile(-mdd, 90)) * 100, 2),
    "p_hit_drawdown_limit_pct": round(float((-mdd > 0.40).mean()) * 100, 2),
    "ranks_by_sigma_f": res,
    "note": "겹치는 20일 창이라 표본끼리 상관된다. 유효 표본수는 창 수보다 훨씬 적다.",
}
p.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
# 집중도를 낮추면 하방이 어디까지 좋아지나. "최소 하방" 의 답이다.
print()
print(" 집중도별 비교 (같은 종목 점수, 보유 종목 수만 다름)")
print(" %-12s %8s %8s %8s %9s %9s %9s"
      % ("보유", "중앙값", "P10", "최악", "P(음수)", "1등(σ25)", "25등(σ25)"))
for nm, label in ((2, "2종목 45%씩"), (3, "3종목"), (4, "4종목"), (6, "6종목")):
    f, m = run(FARM, n_min=nm)
    r = ranks(f, 25)
    print(" %-12s %+7.1f%% %+7.1f%% %+7.1f%% %8.1f%% %8.2f%% %8.1f%%"
          % (label, np.median(f) * 100, np.percentile(f, 10) * 100,
             f.min() * 100, (f < 0).mean() * 100, r[0], r[4]))
print("=" * 74)

print("저장:", p)
