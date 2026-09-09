"""경보필터의 성과 효과를 현재 설정(top2)에서 다시 잰다.

문서가 인용하는 "t 2.32 -> 3.66" 은 top3 시절 측정치다. top3 -> top2 전환 뒤
다시 재지 않았다. 현재 배포 설정을 설명하지 못하는 숫자를 문서에 두면 안 된다.
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
orig = bt.score_at

def stats(r):
    cum = (1 + r).cumprod(); roll = (cum.shift(-20) / cum - 1).dropna(); sd = r.std()
    return {"days": int(len(r)), "daily_mean_pct": float(r.mean() * 100),
            "t_stat": float(r.mean() / (sd / len(r) ** 0.5)) if sd > 0 else 0.0,
            "win20_median_pct": float(roll.median() * 100),
            "win20_over_50pct_rate": float((roll > 0.50).mean() * 100),
            "win20_over_80pct_rate": float((roll > 0.80).mean() * 100),
            "win20_negative_rate": float((roll < 0).mean() * 100)}

out = {}
print("%-6s %-10s %8s %8s %8s %7s" % ("top_n", "경보필터", "일평균", "P(+50%)", "P(+80%)", "t"))
for top_n in (2, 3):
    for on in (True, False):
        bt.score_at = (orig if on else
                       (lambda c, v, i, w, avoid_alert=False: orig(c, v, i, w, False)))
        s = stats(bt.run(close, vol, dict(base), top_n, fill="close"))
        bt.score_at = orig
        s.update({"top_n": top_n, "alert_filter": on})
        out[f"top{top_n}_alert_{'on' if on else 'off'}"] = s
        print("%-6d %-10s %7.3f%% %7.1f%% %7.1f%% %6.2f"
              % (top_n, "적용" if on else "미적용", s["daily_mean_pct"],
                 s["win20_over_50pct_rate"], s["win20_over_80pct_rate"], s["t_stat"]))

path = ROOT / "state" / "backtest_current.json"
d = json.loads(path.read_text(encoding="utf-8"))
d["alert_filter_effect"] = dict(out, generated_at=datetime.now().isoformat(timespec="seconds"),
    note="시장경보 사전회피 필터 적용/미적용 비교. top3 는 문서의 과거 수치(t 2.32->3.66) 대조용.")
path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print("\n저장:", path)
