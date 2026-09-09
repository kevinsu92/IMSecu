# -*- coding: utf-8 -*-
"""변동성을 통제한 무작위 대조군.

왜 필요한가
  기존 무작위 대조는 후보군 전체에서 2종목을 뽑는다. 그런데 신호가 변동성에
  +0.5 가중치를 주므로 신호가 뽑은 종목은 구조적으로 고변동이다. 그러면
  "신호 P(+50%) 14.8% vs 무작위 0.6%" 의 상당 부분이 **동어반복**이다 --
  고변동 종목을 뽑았으니 꼬리가 두껍다는 말과 같다.

  일평균 비교(+0.673% vs -0.060%)는 오염되지 않는다. 변동성은 기대수익을
  올리지 못하기 때문이다. 그러나 꼬리 비교는 통제군이 필요하다.

  여기서는 신호가 고른 종목과 **같은 변동성 분위**에서 무작위로 뽑아,
  변동성을 뺀 신호의 순수 기여를 잰다.
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
W = cfg["alpha"]["weights"]; ZERO = {k: 0.0 for k in W}
COST = bt.ROUND_TRIP_COST
dates = close.index

# 하루마다: 신호 top2, 후보군 전체, 각 종목의 20일 변동성
sig, pool, volq, nxt = [], [], [], []
for i in range(25, len(dates) - 1):
    s = bt.score_at(close, vol, i, W)
    p = bt.score_at(close, vol, i, ZERO)
    if s.empty or p.empty:
        sig.append([]); pool.append([]); volq.append(None); nxt.append({}); continue
    cand = list(p.index)
    win = close.iloc[i - 24:i + 1][cand]
    v = win.pct_change().iloc[-20:].std(ddof=0)
    sig.append(list(s.head(2).index)); pool.append(cand); volq.append(v)
    e = close.iloc[i][cand]; x = close.iloc[i + 1][cand]
    r = (x / e - 1.0)
    nxt.append({c: float(r[c]) for c in cand if np.isfinite(r[c]) and e[c] > 0 and x[c] > 0})

def run_series(picker, rng=None):
    rets, idx, prev = [], [], set()
    for k in range(len(sig)):
        if not pool[k]:
            rets.append(0.0); idx.append(dates[k + 26]); prev = set(); continue
        sel = picker(k, rng)
        vals = [nxt[k][c] for c in sel if c in nxt[k]]
        if not vals:
            rets.append(0.0); idx.append(dates[k + 26]); prev = set(sel); continue
        rets.append(float(np.mean(vals)) - len(set(sel) - prev) / max(len(sel), 1) * COST)
        idx.append(dates[k + 26]); prev = set(sel)
    return pd.Series(rets, index=pd.DatetimeIndex(idx))

def stats(r):
    cum = (1 + r).cumprod(); roll = (cum.shift(-20) / cum - 1).dropna()
    return (float(r.mean() * 100), float((roll > 0.50).mean() * 100),
            float((roll > 0.80).mean() * 100))

# 신호가 고른 종목의 변동성 분위(5분위)를 그날 통제 밴드로 쓴다
bands = []
for k in range(len(sig)):
    if not pool[k] or volq[k] is None or not sig[k]:
        bands.append(None); continue
    v = volq[k].dropna()
    if len(v) < 10: bands.append(None); continue
    q = pd.qcut(v.rank(method="first"), 5, labels=False)
    tgt = {int(q.get(c, -1)) for c in sig[k] if c in q.index}
    bands.append([c for c in v.index if int(q[c]) in tgt])

def pick_signal(k, rng): return sig[k]
def pick_free(k, rng):
    p = pool[k]
    return [p[j] for j in rng.choice(len(p), min(2, len(p)), replace=False)]
def pick_band(k, rng):
    b = bands[k] or pool[k]
    return [b[j] for j in rng.choice(len(b), min(2, len(b)), replace=False)]

s_daily, s50, s80 = stats(run_series(pick_signal))
print("신호 top2            일평균 %+.3f%%  P(+50%%) %.1f%%  P(+80%%) %.1f%%" % (s_daily, s50, s80))

TRIALS = 300
out = {"signal": {"daily_mean_pct": s_daily, "over_50pct": s50, "over_80pct": s80},
       "trials": TRIALS, "seed": 20260906}
for label, picker, key in (("자유 무작위", pick_free, "free"),
                           ("변동성 통제 무작위", pick_band, "band")):
    rng = np.random.default_rng(20260906)
    d, a50, a80, beat = [], [], [], 0
    for _ in range(TRIALS):
        m, p50, p80 = stats(run_series(picker, rng))
        d.append(m); a50.append(p50); a80.append(p80)
        if p80 >= s80: beat += 1
    out[key] = {"daily_mean_pct": float(np.mean(d)), "over_50pct": float(np.mean(a50)),
                "over_80pct": float(np.mean(a80)), "over_80pct_p95": float(np.percentile(a80, 95)),
                "trials_matching_signal_tail": beat,
                "tail_p_value": float((beat + 1) / (TRIALS + 1))}
    print("%-20s 일평균 %+.3f%%  P(+50%%) %.1f%%  P(+80%%) %.1f%%  (P80 95분위 %.1f%%, p=%.3f)"
          % (label, out[key]["daily_mean_pct"], out[key]["over_50pct"], out[key]["over_80pct"],
             out[key]["over_80pct_p95"], out[key]["tail_p_value"]))

path = ROOT / "state" / "backtest_current.json"
j = json.loads(path.read_text(encoding="utf-8"))
j["volatility_controlled_control"] = dict(out,
    generated_at=datetime.now().isoformat(timespec="seconds"),
    note=("신호가 뽑은 종목과 같은 변동성 5분위에서 무작위 2종목을 뽑은 대조군. "
          "자유 무작위 대조는 신호의 변동성 틸트 때문에 꼬리 비교가 동어반복이 된다."))
path.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
print("\n저장:", path)
