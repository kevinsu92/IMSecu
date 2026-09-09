"""왕복비용 민감도를 현재 설정(top2)에서 다시 잰다.

문서 §7 의 비용 표(0.583% / t 3.66 / 11.4%)는 top3 시절 수치다.
배포 설정이 top2 로 바뀐 뒤 재측정하지 않았다.
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
import pandas as pd
import backtest as bt

cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
close = bt.load_panel(cfg, 4.0, 600)
vol = pd.read_parquet(bt.CACHE.with_name("bars_volume.parquet")).reindex(close.index)
base = cfg["alpha"]["weights"]

rows = []
print("%-10s %8s %7s %8s %8s" % ("왕복비용", "일평균", "t", "P(+50%)", "P(+80%)"))
for c in (0.0022, 0.0050, 0.0080):
    r = bt.run(close, vol, dict(base), 2, cost=c, fill="close")
    cum = (1 + r).cumprod(); roll = (cum.shift(-20) / cum - 1).dropna(); sd = r.std()
    s = {"cost_round_trip": c, "top_n": 2,
         "daily_mean_pct": float(r.mean() * 100),
         "t_stat": float(r.mean() / (sd / len(r) ** 0.5)),
         "win20_over_50pct_rate": float((roll > 0.50).mean() * 100),
         "win20_over_80pct_rate": float((roll > 0.80).mean() * 100)}
    rows.append(s)
    print("%-10s %7.3f%% %6.2f %7.1f%% %7.1f%%"
          % (f"{c:.2%}", s["daily_mean_pct"], s["t_stat"],
             s["win20_over_50pct_rate"], s["win20_over_80pct_rate"]))

path = ROOT / "state" / "backtest_current.json"
d = json.loads(path.read_text(encoding="utf-8"))
d["cost_sensitivity"] = {"generated_at": datetime.now().isoformat(timespec="seconds"),
    "note": "top2 기준. 시장충격은 1:1 미러링이라 구조적으로 0이고, 이 값은 미체결 역선택(왕복 0.73%p 실측)을 반영한 시나리오다.",
    "runs": rows}
path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print("\n저장:", path)
