# -*- coding: utf-8 -*-
"""지정가 체결로 판명될 경우 오프셋을 얼마로 내려야 하는가.

1일차 관측이 '내 지정가로 체결'로 나오면 오프셋이 곧 비용이다. 그때는 체결률과
비용을 맞바꾸는 최적점을 찾아야 하는데, 대회 중 즉흥 판단은 금물이므로 지금 계산해
사전 명세로 남긴다.

기대값 = 체결률 x (알파 - 오프셋비용). 미체결은 포지션을 못 잡는 것이므로 그날
알파가 0 이고, 역선택(왕복 0.73%p 실측)까지 감안하면 더 나쁘다.
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

# 실측 체결률 (config._offset_fill_table). 매수/매도.
FILL = {0.0005: (0.621, 0.512), 0.0010: (0.645, 0.557), 0.0020: (0.759, 0.656),
        0.0030: (0.840, 0.739), 0.0050: (0.929, 0.866), 0.0100: (0.990, 0.982)}
ADVERSE = 0.0073   # 미체결 역선택 왕복 실측

rows = []
print("가정: 시뮬레이터가 **내 지정가로 체결**한다 (1일차 관측 결과가 그렇게 나온 경우)")
print("%-9s %8s %8s %9s %7s %8s %9s"
      % ("오프셋", "매수체결", "매도체결", "왕복비용", "일평균", "t", "기대일평균"))
for off, (fb, fs) in FILL.items():
    cost = 0.0022 + off * 2          # 매수·매도 모두 오프셋만큼 불리하게 체결
    r = bt.run(close, vol, dict(base), 2, cost=cost, fill="close")
    sd = r.std()
    daily = float(r.mean() * 100); t = float(r.mean() / (sd / len(r) ** 0.5))
    # 미체결분은 포지션을 못 잡아 알파 0, 게다가 역선택 손실이 붙는다
    eff = fb * daily - (1 - fb) * ADVERSE * 100
    rows.append({"offset": off, "fill_buy": fb, "fill_sell": fs,
                 "cost_round_trip": cost, "daily_mean_pct": daily, "t_stat": t,
                 "expected_daily_pct": eff})
    print("%-9s %7.1f%% %8.1f%% %8.2f%% %7.3f%% %8.2f %8.3f%%"
          % (f"{off:.2%}", fb * 100, fs * 100, cost * 100, daily, t, eff))

best = max(rows, key=lambda x: x["expected_daily_pct"])
print()
print("지정가 체결 판명 시 최적 오프셋: %.2f%%  (기대 일평균 %+.3f%%)"
      % (best["offset"] * 100, best["expected_daily_pct"]))
print("현재 설정 1.00%% 유지 시:        기대 일평균 %+.3f%%"
      % [r for r in rows if r["offset"] == 0.01][0]["expected_daily_pct"])

path = ROOT / "state" / "backtest_current.json"
j = json.loads(path.read_text(encoding="utf-8"))
j["offset_contingency"] = {"generated_at": datetime.now().isoformat(timespec="seconds"),
    "assumption": "시뮬레이터가 내 지정가로 체결하는 경우. 1일차 관측으로 판별한다.",
    "adverse_selection_round_trip": ADVERSE,
    "best_offset": best["offset"], "runs": rows}
path.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
print("\n저장:", path)
