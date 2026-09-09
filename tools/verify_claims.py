"""문서(TRADING_RULES.md)가 인용하는데 저장 파일에 없던 수치를 채운다.

1) 변동성 가중치 스윕 — "1.0 을 넘으면 P(+50%) 가 떨어진다" 주장의 근거
2) 무작위 벤치마크 — 다중검정 부채가 없는 유일한 증거
두 결과를 state/backtest_current.json 에 병합해 검수자가 재현할 수 있게 한다.
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
base = cfg["alpha"]["weights"]

def stats(r):
    cum = (1 + r).cumprod()
    roll = (cum.shift(-20) / cum - 1).dropna()
    sd = r.std()
    return {"days": int(len(r)), "daily_mean_pct": float(r.mean() * 100),
            "t_stat": float(r.mean() / (sd / len(r) ** 0.5)) if sd > 0 else 0.0,
            "win20_median_pct": float(roll.median() * 100),
            "win20_over_50pct_rate": float((roll > 0.50).mean() * 100),
            "win20_over_80pct_rate": float((roll > 0.80).mean() * 100),
            "win20_negative_rate": float((roll < 0).mean() * 100)}

print("[1] 변동성 가중치 스윕 (top2)")
print("%-8s %8s %8s %8s %7s" % ("vol_w", "일평균", "P(+50%)", "P(+80%)", "t"))
sweep = []
for vw in (0.5, 1.0, 1.5, 2.0, 3.0):
    w = dict(base); w["volatility"] = vw
    s = stats(bt.run(close, vol, w, 2, fill="close"))
    s.update({"top_n": 2, "volatility_weight": vw, "period": "full"})
    sweep.append(s)
    print("%-8.1f %7.3f%% %7.1f%% %7.1f%% %6.2f"
          % (vw, s["daily_mean_pct"], s["win20_over_50pct_rate"],
             s["win20_over_80pct_rate"], s["t_stat"]))

print()
print("[2] 무작위 벤치마크 (같은 후보군에서 매일 2종목 무작위, 1000회)")
sig = stats(bt.run(close, vol, dict(base), 2, fill="close"))
zero = {k: 0.0 for k in base}          # 가중치 0 → 정렬만 무의미, index 는 동일 후보군
dates = close.index
pools, entries = [], []
for i in range(25, len(dates) - 1):
    s = bt.score_at(close, vol, i, zero)
    pools.append(list(s.index) if not s.empty else [])
    entries.append(i)

rng = np.random.default_rng(20260906)
cl = close.values
colidx = {c: j for j, c in enumerate(close.columns)}
trial_daily, trial_p50, trial_p80 = [], [], []
for t in range(1000):
    rets, idx, prev = [], [], set()
    for k, i in enumerate(entries):
        pool = pools[k]
        if len(pool) < 2:
            rets.append(0.0); idx.append(dates[i + 1]); prev = set(); continue
        picks = [pool[j] for j in rng.choice(len(pool), 2, replace=False)]
        js = [colidx[p] for p in picks]
        e, x = cl[i, js], cl[i + 1, js]
        ok = np.isfinite(e) & np.isfinite(x) & (e > 0) & (x > 0)
        if not ok.any():
            rets.append(0.0); idx.append(dates[i + 1]); prev = set(picks); continue
        gross = float(np.mean(x[ok] / e[ok] - 1))
        turned = len(set(picks) - prev) / 2
        rets.append(gross - turned * bt.ROUND_TRIP_COST)
        idx.append(dates[i + 1]); prev = set(picks)
    r = pd.Series(rets, index=pd.DatetimeIndex(idx))
    st_ = stats(r)
    trial_daily.append(st_["daily_mean_pct"])
    trial_p50.append(st_["win20_over_50pct_rate"])
    trial_p80.append(st_["win20_over_80pct_rate"])

td = np.array(trial_daily)
beat = int((td >= sig["daily_mean_pct"]).sum())
rand = {"trials": 1000, "seed": 20260906,
        "signal_daily_mean_pct": sig["daily_mean_pct"],
        "signal_win20_over_50pct_rate": sig["win20_over_50pct_rate"],
        "signal_win20_over_80pct_rate": sig["win20_over_80pct_rate"],
        "random_daily_mean_pct": float(td.mean()),
        "random_daily_max_pct": float(td.max()),
        "random_win20_over_50pct_rate_mean": float(np.mean(trial_p50)),
        "random_win20_over_80pct_rate_mean": float(np.mean(trial_p80)),
        "trials_beating_signal": beat,
        "p_value": float((beat + 1) / 1001)}
print("  신호      일평균 %+.3f%%  P(+50%%) %.1f%%  P(+80%%) %.1f%%"
      % (sig["daily_mean_pct"], sig["win20_over_50pct_rate"], sig["win20_over_80pct_rate"]))
print("  무작위평균 일평균 %+.3f%%  P(+50%%) %.1f%%  P(+80%%) %.1f%%"
      % (td.mean(), np.mean(trial_p50), np.mean(trial_p80)))
print("  무작위최대 일평균 %+.3f%%   이긴 시행 %d/1000   p=%.3f"
      % (td.max(), beat, rand["p_value"]))

path = ROOT / "state" / "backtest_current.json"
out = json.loads(path.read_text(encoding="utf-8"))
out["volatility_sweep"] = {"generated_at": datetime.now().isoformat(timespec="seconds"),
                           "note": "top2 고정, 변동성 가중치만 변화. 공격 국면 상한 근거.",
                           "runs": sweep}
out["random_benchmark"] = dict(rand, generated_at=datetime.now().isoformat(timespec="seconds"),
                               note="같은 규정필터·경보필터 후보군에서 매일 2종목 무작위 선택. 비용 동일.")
path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print()
print("저장:", path)
