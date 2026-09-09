# -*- coding: utf-8 -*-
"""P(1등)을 라이브 제약 분포로 다시 계산한다.

기존 추정(14.4/5.9/2.0/0.7%)은 **클램프를 모델링하지 않은** 분포로 만든 것이다.
클램프는 계좌가 불어난 경로에서만 발동하므로, 하필 1등을 만드는 경로에서만
분포를 깎는다. 그 분포로 낸 P(1등)은 과대추정이다.
"""
import json, sys
from datetime import datetime
from pathlib import Path
sys.argv = ["x"]
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for st in (sys.stdout, sys.stderr):
    try: st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
import numpy as np

sys.path.insert(0, str(ROOT / "tools"))
import importlib.util
spec = importlib.util.spec_from_file_location("bl", ROOT / "tools" / "backtest_live.py")

# backtest_live 를 다시 돌리지 않고, 저장된 파라미터로 구간 수익률만 재생성한다.
import backtest as bt, pandas as pd
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

def sim(clamp):
    out = []
    for s0 in range(0, len(picks) - WIN):
        eq, prev = 1.0, set()
        for k in range(s0, s0 + WIN):
            pk, r = picks[k], rets[k]
            if not len(pk): continue
            if clamp:
                cap = min(PH["max_weight"], RULE)
                if eq > 1.0: cap = min(cap, RULE / eq)
                n = 2
                if cap < min(PH["max_weight"], RULE):
                    n = max(n, min(int(np.ceil(INV / cap - 1e-9)), NMAX))
                n = min(n, len(pk)); w = min(INV / n, cap)
            else:
                n = min(2, len(pk)); w = 1.0 / n
            eq *= (1 + float(np.dot(r[:n], [w] * n))
                   - len(set(pk[:n]) - prev) / n * COST * (w * n))
            prev = set(pk[:n])
        out.append(eq - 1.0)
    return np.array(out)

DISTS = {"문서 가정 (클램프 미반영)": sim(False), "라이브 제약 반영": sim(True)}
N_FIELD, TRIALS, MU_F = 300, 200_000, 0.0
rng = np.random.default_rng(20260906)

def p_first(mine, sigma_f_pct):
    """필드는 shifted-lognormal, 참가자 300명. 나는 경험분포에서 부트스트랩."""
    s = sigma_f_pct / 100.0; m = MU_F / 100.0
    s_log = np.sqrt(np.log(1 + (s / (1 + m + 1e-9)) ** 2))
    mu_log = np.log(1 + m + 1e-9) - 0.5 * s_log ** 2
    wins = 0
    for i in range(0, TRIALS, 20_000):
        b = min(20_000, TRIALS - i)
        others = np.exp(rng.normal(mu_log, s_log, size=(b, N_FIELD - 1))) - 1.0
        me = rng.choice(mine, size=b)
        wins += int((me > others.max(axis=1)).sum())
    return wins / TRIALS * 100

print("P(1등) — 필드 300명, shifted-lognormal, 필드평균 0%, 20만회")
print("%-26s %8s %8s %8s %8s" % ("분포", "σf 15%p", "20%p", "25%p", "30%p"))
out = {}
for label, d in DISTS.items():
    row = [p_first(d, s) for s in (15, 20, 25, 30)]
    out[label] = {"p_first_pct": dict(zip(["15", "20", "25", "30"], [round(x, 2) for x in row])),
                  "median_pct": round(float(np.median(d)) * 100, 2),
                  "over_80pct": round(float((d > 0.8).mean()) * 100, 2)}
    print("%-26s %7.2f%% %7.2f%% %7.2f%% %7.2f%%" % (label, *row))

path = ROOT / "state" / "backtest_current.json"
j = json.loads(path.read_text(encoding="utf-8"))
j["p_first"] = dict(out, generated_at=datetime.now().isoformat(timespec="seconds"),
    assumptions={"n_field": N_FIELD, "field_mean_pct": MU_F, "trials": TRIALS,
                 "field_model": "shifted-lognormal", "seed": 20260906},
    note="기존 14.4/5.9/2.0/0.7% 는 클램프 미반영 분포로 낸 값이라 과대추정이다.")
path.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
print("\n저장:", path)
