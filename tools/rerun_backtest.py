"""문서에 인용하는 백테스트를 재실행하고 결과를 파일로 남긴다.

왜 필요한가
  `state/backtest_results.json` 은 2026-09-05 13:13 저장분인데, 그 뒤에
  시장경보 필터(alert_ratio)와 top2 전환이 들어갔다. 즉 **저장된 유일한
  백테스트가 현재 설정을 설명하지 못한다.** 문서의 표는 재실행 결과인데
  저장이 안 돼 있어 검수자가 재현할 수 없었다.

  검수 문서에서 헤드라인 표가 재현 불가면 나머지 숫자의 신뢰도까지 같이 떨어진다.
  그래서 문서가 인용하는 모든 조합을 한 번에 돌려 타임스탬프와 함께 저장한다.
"""
import io
import json
import sys
from datetime import datetime
from pathlib import Path

sys.argv = ["x"]
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for st in (sys.stdout, sys.stderr):
    try:
        st.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
import pandas as pd

import backtest as bt

cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
close = bt.load_panel(cfg, 4.0, 600)
vol = pd.read_parquet(bt.CACHE.with_name("bars_volume.parquet")).reindex(close.index)
base = cfg["alpha"]["weights"]

CUT = pd.Timestamp("2024-01-01")


def stats(r: pd.Series) -> dict:
    cum = (1 + r).cumprod()
    roll = (cum.shift(-20) / cum - 1).dropna()
    sd = r.std()
    return {
        "days": int(len(r)),
        "daily_mean_pct": float(r.mean() * 100),
        "t_stat": float(r.mean() / (sd / len(r) ** 0.5)) if sd > 0 else 0.0,
        "win20_median_pct": float(roll.median() * 100),
        "win20_over_50pct_rate": float((roll > 0.50).mean() * 100),
        "win20_over_80pct_rate": float((roll > 0.80).mean() * 100),
        "win20_over_100pct_rate": float((roll > 1.00).mean() * 100),
        "win20_negative_rate": float((roll < 0).mean() * 100),
        "win20_p10_pct": float(roll.quantile(0.10) * 100),
        "win20_max_pct": float(roll.max() * 100),
        "independent_windows": int(len(cum) // 20),
    }


out = {
    "generated_at": datetime.now().isoformat(timespec="seconds"),
    "panel": {"symbols": int(close.shape[1]), "days": int(close.shape[0]),
              "first": str(close.index[0].date()), "last": str(close.index[-1].date())},
    "config": {
        "weights": base,
        "alert_filter": True,
        "alert_margin": bt.ALERT_MARGIN,
        "cost_round_trip": bt.ROUND_TRIP_COST,
        "fill": "close",
    },
    "note": ("TRADING_RULES.md §12 가 인용하는 표의 원본. "
             "경보필터 적용 후 재실행분이다. 이전 파일(backtest_results.json, 13:13)은 "
             "경보필터·top2 전환 이전 데이터라 현재 설정을 설명하지 못한다."),
    "runs": [],
}

print(f"패널 {close.shape[1]}종목 x {close.shape[0]}일")
print()
print("%-22s %7s %8s %8s %9s %8s %6s"
      % ("설정", "중앙값", "P(+50%)", "P(+80%)", "P(+100%)", "P(<0)", "t"))

for top_n in (2, 3, 4, 6):
    for vw in (0.5, 1.0):
        w = dict(base)
        w["volatility"] = vw
        r = bt.run(close, vol, w, top_n, fill="close")
        s = stats(r)
        s.update({"top_n": top_n, "volatility_weight": vw, "period": "full"})
        out["runs"].append(s)
        print("%-22s %6.1f%% %7.1f%% %7.1f%% %8.1f%% %7.1f%% %6.2f"
              % (f"top{top_n} vol{vw}", s["win20_median_pct"], s["win20_over_50pct_rate"],
                 s["win20_over_80pct_rate"], s["win20_over_100pct_rate"],
                 s["win20_negative_rate"], s["t_stat"]))

# 기간 분할 — 문서 §7 의 "알파가 2023년에서 나온다" 주장을 같은 파일로 검증 가능하게
print()
print("기간 분할 (top2 vol0.5)")
w = dict(base)
r = bt.run(close, vol, w, 2, fill="close")
for label, rr in (("2023_and_before", r[r.index < CUT]), ("2024_onward", r[r.index >= CUT])):
    s = stats(rr)
    s.update({"top_n": 2, "volatility_weight": 0.5, "period": label})
    out["runs"].append(s)
    print("  %-18s n=%3d  일평균 %+.3f%%  중앙값 %+.1f%%  t=%5.2f  P(<0) %.1f%%"
          % (label, s["days"], s["daily_mean_pct"], s["win20_median_pct"],
             s["t_stat"], s["win20_negative_rate"]))

path = ROOT / "state" / "backtest_current.json"
path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print()
print(f"저장: {path}  ({len(out['runs'])} runs)")
