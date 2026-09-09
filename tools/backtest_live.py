# -*- coding: utf-8 -*-
"""라이브 제약을 반영한 20일 구간 백테스트.

왜 필요한가
  backtest.py 의 run() 은 top_n 동일비중 순수 신호 백테스트다. principal 도
  max_weight 도 없다. 그런데 실제 코드는 **원금 기준 단일종목 상한**을 걸고,
  평가금액이 커지면 종목당 비중이 내려가 차순위로 확장된다.

  그 확장은 계좌가 불어난 경로에서만 발동한다 -- 즉 P(+80%) 를 만드는 바로 그
  경로에서만. 따라서 클램프를 모델링하지 않은 분포는 **라이브 상방을 과대평가**
  한다. 문서가 인용하던 P(+80%) 3.7% 는 도달 불가능한 수치였다.

  여기서는 구간마다 평가금액 경로를 추적하며 매일 상한과 확장을 실제 코드와
  같은 규칙으로 적용한다.
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
import numpy as np, pandas as pd
import backtest as bt

cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
close = bt.load_panel(cfg, 4.0, 600)
vol = pd.read_parquet(bt.CACHE.with_name("bars_volume.parquet")).reindex(close.index)
W = cfg["alpha"]["weights"]
PH = cfg["portfolio"]["phase_early"]
RULE = cfg["requirements"]["max_single_weight_rule"] - 0.05   # 0.45
NMAX = int(cfg["portfolio"].get("max_positions_when_capped", 6))
INVESTABLE = 1.0 - PH["cash_buffer"]
COST = bt.ROUND_TRIP_COST
WIN = 20

# 1) 하루치 상위 NMAX 종목과 그 다음날 수익률을 미리 뽑는다.
dates = close.index
picks_by_day, rets_by_day = [], []
for i in range(25, len(dates) - 1):
    s = bt.score_at(close, vol, i, W)
    if s.empty:
        picks_by_day.append([]); rets_by_day.append(np.array([])); continue
    p = list(s.head(NMAX).index)
    e = close.iloc[i][p].values.astype(float)
    x = close.iloc[i + 1][p].values.astype(float)
    ok = np.isfinite(e) & np.isfinite(x) & (e > 0) & (x > 0)
    picks_by_day.append([c for c, k in zip(p, ok) if k])
    rets_by_day.append(x[ok] / e[ok] - 1.0)

def simulate(clamp: bool, n_fixed: int = 2):
    """구간별 20일 누적 수익률. clamp=False 면 기존 백테스트와 같은 가정."""
    out = []
    for s0 in range(0, len(picks_by_day) - WIN):
        eq, prev = 1.0, set()
        for k in range(s0, s0 + WIN):
            picks, r = picks_by_day[k], rets_by_day[k]
            if len(picks) == 0:
                continue
            if clamp:
                cap_w = min(PH["max_weight"], RULE)
                if eq > 1.0:                      # 원금 = 1.0
                    cap_w = min(cap_w, RULE / eq)
                n = n_fixed
                if cap_w < min(PH["max_weight"], RULE):
                    n = max(n, min(int(np.ceil(INVESTABLE / cap_w - 1e-9)), NMAX))
                n = min(n, len(picks))
                w = min(INVESTABLE / n, cap_w)
            else:
                n = min(n_fixed, len(picks)); w = 1.0 / n
            sel = picks[:n]
            gross = float(np.dot(r[:n], [w] * n))          # 현금은 0% 수익
            turned = len(set(sel) - prev) / max(n, 1)
            eq *= (1.0 + gross - turned * COST * (w * n))
            prev = set(sel)
        out.append(eq - 1.0)
    return np.array(out)

def stat(a, label):
    return {"label": label, "n_windows": int(len(a)),
            "median_pct": float(np.median(a) * 100),
            "mean_pct": float(a.mean() * 100),
            "over_50pct": float((a > 0.50).mean() * 100),
            "over_80pct": float((a > 0.80).mean() * 100),
            "over_100pct": float((a > 1.00).mean() * 100),
            "negative_pct": float((a < 0).mean() * 100),
            "max_pct": float(a.max() * 100)}

rows = [stat(simulate(False), "클램프 없음 (기존 문서 가정)"),
        stat(simulate(True),  "클램프+차순위 승격 (수정 후 실제)")]
# 수정 전 코드: 클램프는 걸리는데 확장이 없어 현금으로 눕던 상태
def simulate_old():
    out = []
    for s0 in range(0, len(picks_by_day) - WIN):
        eq, prev = 1.0, set()
        for k in range(s0, s0 + WIN):
            picks, r = picks_by_day[k], rets_by_day[k]
            if len(picks) == 0: continue
            cap_w = min(PH["max_weight"], RULE)
            if eq > 1.0: cap_w = min(cap_w, RULE / eq)
            n = min(2, len(picks)); w = min(INVESTABLE / n, cap_w)
            gross = float(np.dot(r[:n], [w] * n))
            turned = len(set(picks[:n]) - prev) / max(n, 1)
            eq *= (1.0 + gross - turned * COST * (w * n))
            prev = set(picks[:n])
        out.append(eq - 1.0)
    return np.array(out)
rows.insert(1, stat(simulate_old(), "클램프만 (수정 전 코드 - 현금으로 눕던 상태)"))

print("%-38s %8s %8s %8s %9s %8s" % ("", "중앙값", "P(+50%)", "P(+80%)", "P(+100%)", "P(<0)"))
for s in rows:
    print("%-38s %7.1f%% %7.1f%% %7.1f%% %8.1f%% %7.1f%%"
          % (s["label"], s["median_pct"], s["over_50pct"], s["over_80pct"],
             s["over_100pct"], s["negative_pct"]))

path = ROOT / "state" / "backtest_current.json"
d = json.loads(path.read_text(encoding="utf-8"))
d["live_constrained"] = {"generated_at": datetime.now().isoformat(timespec="seconds"),
    "note": ("원금 기준 단일종목 상한과 차순위 승격을 반영한 20일 구간 시뮬레이션. "
             "기존 backtest.run() 은 이 제약이 없어 라이브 상방을 과대평가한다."),
    "params": {"rule_cap": RULE, "n_max": NMAX, "investable": INVESTABLE,
               "cost_round_trip": COST, "window": WIN},
    "runs": rows}
path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print("\n저장:", path)
