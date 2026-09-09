"""전략 백테스트.

목적은 두 가지다.
  1. 알파 가중치가 실제로 작동하는지 (동일가중 벤치마크 대비)
  2. **20영업일 구간 수익률의 분포** — 대회는 20영업일짜리 단판이므로
     평균 수익률보다 분포의 오른쪽 꼬리가 훨씬 중요하다.

알려진 한계 (결과 해석 시 반드시 감안할 것)
  - 생존 편향: 현재 상장 종목만 사용한다. 상장폐지 종목이 빠져 있어 결과가 낙관적이다.
  - 유니버스 편향: 과거 시점의 시장경보/관리종목 지정 이력을 구할 수 없어
    현재 기준 필터만 적용했다. 실제로는 급등주가 투자경고로 지정되어 매매 불가가 되는
    경우가 있으므로 실전 수익률은 이보다 낮다.
  - 체결 가정: 종가 체결을 가정한다. 실제로는 슬리피지가 추가된다.

따라서 절대 수익률 수치는 믿지 말고, **파라미터 간 상대 비교**와 **분포의 모양**만 본다.

사용법
  python backtest.py --years 2 --top-n 4
  python backtest.py --years 2 --top-n 4 --compare
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from imrl import data, universe

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "state" / "bars_cache.parquet"
CONTEST_DAYS = 20
ROUND_TRIP_COST = 0.0022  # 왕복 수수료(0.01%×2) + 매도 제세금(0.20%). 개별주식 기준.
#
# 이 값에는 **슬리피지가 들어 있지 않다.** 종가 단일가 경매로 집행하므로 스프레드
# 비용은 구조적으로 0 이지만(전 주문이 하나의 체결가로 체결된다), 우리 주문이
# 경매 체결가 자체를 밀어올리는 시장충격은 남는다. 그 크기를 실측한 적이 없으므로
# 0 으로 두는 대신 시나리오로 함께 본다 — 아래 COST_SCENARIOS.
#
# 비용 0.22% 만 가정한 결과를 단독으로 인용하면 안 된다. 실측 없이 낙관하는 것이다.
COST_SCENARIOS = {"규정비용만": 0.0022, "중간충격": 0.0050, "높은충격": 0.0080}


def audit_lookahead(close, vol, weights, samples: int = 12) -> list[str]:
    """미래 데이터 참조 검사.

    방법: i일 이후의 모든 값을 파괴한 패널로 score_at(i) 를 다시 계산해
    원본과 같은지 본다. 다르면 i 시점 계산이 미래를 봤다는 뜻이다.

    이 프로젝트는 이미 한 번 미래 정보로 당했다 — 종가 신호를 그 종가에 체결한다고
    가정해 알파의 2/3가 허구였다. 그건 look-ahead 가 아니라 집행 가정 오류였지만,
    같은 종류의 자기기만이라 자동 검사를 둔다.
    """
    import numpy as np

    problems: list[str] = []
    n = len(close)
    idxs = np.linspace(60, n - 3, samples).astype(int)
    for i in idxs:
        base = score_at(close, vol, int(i), weights)
        c2, v2 = close.copy(), vol.copy()
        # i 다음 날부터 전부 파괴한다. 미래를 참조하면 결과가 달라진다.
        c2.iloc[int(i) + 1:] = np.nan
        v2.iloc[int(i) + 1:] = np.nan
        test = score_at(c2, v2, int(i), weights)
        if len(base) != len(test) or not base.index.equals(test.index):
            problems.append(f"  {close.index[i]:%Y-%m-%d}: 종목 집합이 달라졌다 "
                            f"({len(base)} -> {len(test)})")
        elif not np.allclose(base.values, test.values, equal_nan=True):
            d = float(np.nanmax(np.abs(base.values - test.values)))
            problems.append(f"  {close.index[i]:%Y-%m-%d}: 점수가 달라졌다 (최대차 {d:.6f})")
    return problems



def load_panel(cfg: dict, years: float, n_symbols: int, refresh: bool = False) -> pd.DataFrame:
    """종목 × 날짜 종가 패널. 한 번 받으면 캐시한다."""
    if CACHE.exists() and not refresh:
        panel = pd.read_parquet(CACHE)
        print(f"캐시 사용: {panel.shape[1]}종목 × {panel.shape[0]}일  ({CACHE})")
        return panel

    uni = universe.build(cfg)
    codes = uni.sort_values("amount", ascending=False).head(n_symbols)["code"].tolist()
    print(f"일봉 수집 {len(codes)}종목 × 약 {years}년 ...")

    t0 = time.time()
    bars = data.fetch_daily_many(codes, days=int(365 * years), workers=8)
    print(f"  수집 완료 {len(bars)}종목, {time.time() - t0:.0f}초")

    close = {c: df.set_index("date")["close"] for c, df in bars.items() if len(df) > 60}
    vol = {c: df.set_index("date")["volume"] for c, df in bars.items() if len(df) > 60}
    opn = {c: df.set_index("date")["open"] for c, df in bars.items() if len(df) > 60}

    panel = pd.DataFrame(close).sort_index()
    vpanel = pd.DataFrame(vol).sort_index().reindex(panel.index)
    opanel = pd.DataFrame(opn).sort_index().reindex(panel.index)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(CACHE)
    vpanel.to_parquet(CACHE.with_name("bars_volume.parquet"))
    opanel.to_parquet(CACHE.with_name("bars_open.parquet"))
    print(f"캐시 저장: {panel.shape[1]}종목 × {panel.shape[0]}일")
    return panel


ALERT_MARGIN = 0.85


def _z(x: pd.Series) -> pd.Series:
    sd = x.std(ddof=0)
    return pd.Series(0.0, index=x.index) if not np.isfinite(sd) or sd == 0 else (x - x.mean()) / sd


def score_at(close: pd.DataFrame, vol: pd.DataFrame, i: int, weights: dict,
             avoid_alert: bool = True) -> pd.Series:
    """i번째 날 종가 기준 횡단면 점수. 미래 데이터는 쓰지 않는다.

    avoid_alert 는 실운영의 `alpha.score()` 와 같은 시장경보 사전 회피 필터다.
    운영에만 필터를 넣고 백테스트에 안 넣으면, 측정된 성과가 **실제로 배포된
    전략을 설명하지 못한다.** 필터가 성과를 깎는다면 그것도 알아야 하는 사실이다.
    """
    if i < 25:
        return pd.Series(dtype=float)

    win = close.iloc[i - 24: i + 1]
    valid = win.notna().all() & (win.iloc[-1] >= 1000)
    win = win.loc[:, valid]
    if win.shape[1] < 20:
        return pd.Series(dtype=float)

    px = win.iloc[-1]
    ret5 = px / win.iloc[-6] - 1
    ret20 = px / win.iloc[-21] - 1
    ma20 = win.iloc[-20:].mean()
    rets = win.pct_change().iloc[-20:]
    vola = rets.std(ddof=0)

    amt = (close * vol).iloc[i - 24: i + 1].loc[:, win.columns]
    amt20 = amt.iloc[-21:-1].mean()
    surge = (amt.iloc[-1] / amt20).replace([np.inf, -np.inf], np.nan).fillna(0)

    ok = (px > ma20) & (ret20 > 0) & (amt20 > 3e9)

    if avoid_alert:
        # KRX 시장경보 지정요건(3일 +100% / 5일 +60% / 15일 +100%) 근접 종목 제외.
        # 지정되면 매매제한이 걸려 팔지 못한 채 자본이 묶인다.
        ret3 = px / win.iloc[-4] - 1
        ret15 = px / win.iloc[-16] - 1
        alert = pd.concat([ret3 / 1.00, ret5 / 0.60, ret15 / 1.00], axis=1).max(axis=1)
        ok = ok & (alert < ALERT_MARGIN)

    if ok.sum() < 5:
        return pd.Series(dtype=float)

    s = (
        weights["ret20"] * _z(ret20[ok])
        + weights["ret5"] * _z(ret5[ok])
        + weights["amount_surge"] * _z(np.log1p(surge[ok].clip(lower=0)))
        + weights["volatility"] * _z(vola[ok])
    )
    return s.sort_values(ascending=False)


def run(close: pd.DataFrame, vol: pd.DataFrame, weights: dict, top_n: int,
        cost: float = ROUND_TRIP_COST, fill: str = "close",
        open_px: pd.DataFrame | None = None) -> pd.Series:
    """일간 리밸런싱 시뮬레이션. 반환값은 일별 전략 수익률.

    fill 인자가 결정적으로 중요하다.
      "close" — 종가에 신호를 만들고 **그 종가에** 체결한다고 가정.
                도달 불가능한 가격이다. 실제로는 신호를 만든 뒤에야 주문을 낸다.
      "open"  — 종가 신호로 **다음날 시가**에 체결하고 그 다음날 시가까지 보유.
                08:40 산출 → 09:05 집행이라는 실제 운영 루틴과 일치한다.

    둘의 차이가 오버나잇 갭이다. 고모멘텀 소형주에서 수익의 대부분이 오버나잇에
    발생한다는 것이 알려져 있으므로, close 기준 성과는 집행 불가능한 수익을
    포함할 수 있다. 알파가 실재하는지 판정하려면 open 기준을 봐야 한다.
    """
    if fill == "open" and open_px is None:
        raise ValueError("fill='open' 이면 open_px 패널이 필요하다")

    dates = close.index
    rets: list[float] = []
    idx: list = []
    prev: set[str] = set()

    last = len(dates) - (2 if fill == "open" else 1)
    for i in range(25, last):
        s = score_at(close, vol, i, weights)
        if s.empty:
            rets.append(0.0); idx.append(dates[i + 1]); prev = set(); continue

        picks = list(s.head(top_n).index)
        if fill == "open":
            entry = open_px.iloc[i + 1][picks]
            exit_ = open_px.iloc[i + 2][picks]
        else:
            entry = close.iloc[i][picks]
            exit_ = close.iloc[i + 1][picks]

        # 시가 데이터에 0 이 섞여 있다(약 0.1%). 그대로 나누면 inf 가 되어
        # 전체 통계가 오염된다. 유효한 종목만 남기고, 남은 게 없으면 그날은 건너뛴다.
        valid = entry.notna() & exit_.notna() & (entry > 0) & (exit_ > 0)
        if not valid.any():
            rets.append(0.0); idx.append(dates[i + 1]); prev = set(picks); continue
        nxt = exit_[valid] / entry[valid] - 1
        gross = float(nxt.mean())

        turned = len(set(picks) - prev) / max(len(picks), 1)
        rets.append(gross - turned * cost)
        idx.append(dates[i + 1])
        prev = set(picks)

    return pd.Series(rets, index=pd.DatetimeIndex(idx), name="strategy")


def summarize(r: pd.Series, label: str) -> dict:
    """20영업일 구간 수익률 분포 — 대회 성과의 직접적인 예측치."""
    cum = (1 + r).cumprod()
    win = CONTEST_DAYS
    if len(cum) <= win:
        return {}
    roll = (cum.shift(-win) / cum - 1).dropna()

    out = {
        "label": label,
        "days": len(r),
        "total_return_pct": (cum.iloc[-1] - 1) * 100,
        "daily_mean_pct": r.mean() * 100,
        "daily_std_pct": r.std() * 100,
        "sharpe_annual": (r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0,
        # 시작 자산 1 을 고점 후보에 넣는다. 빼면 첫날 -50% 의 낙폭이 0% 로 나온다.
        "max_drawdown_pct": float(((pd.concat([pd.Series([1.0]), cum.reset_index(drop=True)],
                                              ignore_index=True)
                                    / pd.concat([pd.Series([1.0]), cum.reset_index(drop=True)],
                                                ignore_index=True).cummax()) - 1).min() * 100),
        "win20_mean_pct": roll.mean() * 100,
        "win20_median_pct": roll.median() * 100,
        "win20_p10_pct": roll.quantile(0.10) * 100,
        "win20_p90_pct": roll.quantile(0.90) * 100,
        "win20_max_pct": roll.max() * 100,
        "win20_positive_rate": (roll > 0).mean() * 100,
        "win20_over_30pct_rate": (roll > 0.30).mean() * 100,
        # 대회 보상함수는 평균이 아니라 우측 꼬리 한 점이다.
        # 참가자 수백 명 규모에서 1등 수익률은 +40~70% 로 추정되므로
        # 그 임계값에서의 확률이 실제 목적함수에 가깝다.
        "win20_over_50pct_rate": (roll > 0.50).mean() * 100,
        "win20_over_60pct_rate": (roll > 0.60).mean() * 100,
        # 마이너스로 끝나면 순위와 무관하게 상금 0원이다. 제약조건으로 쓴다.
        "win20_negative_rate": (roll < 0).mean() * 100,
        # 비중첩 구간 수. 중첩 구간으로 계산한 확률의 표준오차를 가늠하는 데 쓴다.
        "independent_windows": len(cum) // win,
        "t_stat": (r.mean() / (r.std() / (len(r) ** 0.5))) if r.std() > 0 else 0.0,
    }
    return out


def print_summary(s: dict) -> None:
    if not s:
        print("  (구간이 부족해 통계를 낼 수 없다)")
        return
    print(f"\n[{s['label']}]  {s['days']}일")
    print(f"  누적수익률      {s['total_return_pct']:8.1f}%")
    print(f"  일평균/표준편차 {s['daily_mean_pct']:7.3f}% / {s['daily_std_pct']:.3f}%")
    print(f"  연환산 샤프     {s['sharpe_annual']:8.2f}")
    print(f"  최대낙폭        {s['max_drawdown_pct']:8.1f}%")
    print(f"  --- 20영업일 구간 수익률 분포 (대회 시뮬레이션) ---")
    print(f"  평균 {s['win20_mean_pct']:6.1f}%   중앙값 {s['win20_median_pct']:6.1f}%")
    print(f"  하위10% {s['win20_p10_pct']:6.1f}%   상위10% {s['win20_p90_pct']:6.1f}%   최대 {s['win20_max_pct']:6.1f}%")
    print(f"  플러스 확률 {s['win20_positive_rate']:.0f}%   마이너스 확률 {s['win20_negative_rate']:.0f}%")
    print(f"  +30% {s['win20_over_30pct_rate']:.0f}%   "
          f"+50% {s['win20_over_50pct_rate']:.1f}%   +60% {s['win20_over_60pct_rate']:.1f}%   <- 우승선")
    print(f"  t통계량 {s['t_stat']:.2f}  (비중첩 구간 {s['independent_windows']}개, "
          f"12개 조합 검정 시 임계값 약 2.9)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--symbols", type=int, default=500)
    ap.add_argument("--top-n", type=int, default=4)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--compare", action="store_true", help="종목수 및 가중치 조합 비교")
    ap.add_argument("--fill", default="open", choices=["open", "close", "both"],
                    help="체결 가정. 기본은 open (실제 집행과 일치)")
    args = ap.parse_args()

    cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    close = load_panel(cfg, args.years, args.symbols, args.refresh)
    vol = pd.read_parquet(CACHE.with_name("bars_volume.parquet")).reindex(close.index)
    open_path = CACHE.with_name("bars_open.parquet")
    if not open_path.exists():
        raise SystemExit(
            "시가 패널이 없다. --refresh 로 데이터를 다시 받을 것.\n"
            "종가 체결 가정은 실제 집행과 달라 알파를 과대평가한다."
        )
    open_px = pd.read_parquet(open_path).reindex(close.index)

    base = cfg["alpha"]["weights"]
    variants = {
        "모멘텀만": {"ret20": 1.0, "ret5": 0.6, "amount_surge": 0.0, "volatility": 0.0},
        "모멘텀+변동성": {"ret20": 1.0, "ret5": 0.6, "amount_surge": 0.0, "volatility": 1.0},
        "변동성강조": {"ret20": 0.9, "ret5": 0.6, "amount_surge": 0.8, "volatility": 1.0},
        "역방향ret5": {"ret20": 1.0, "ret5": -0.3, "amount_surge": 0.0, "volatility": 0.5},
    }
    fills = ["open", "close"] if args.fill == "both" else [args.fill]

    results = []
    if not args.compare:
        for f in fills:
            r = run(close, vol, base, args.top_n, fill=f, open_px=open_px)
            s_ = summarize(r, f"top{args.top_n} 설정가중치 [{f} 체결]")
            print_summary(s_)
            results.append(s_)
    else:
        for f in fills:
            for n in (2, 3, 4, 6):
                for name, w in variants.items():
                    r = run(close, vol, w, n, fill=f, open_px=open_px)
                    s_ = summarize(r, f"top{n} {name} [{f} 체결]")
                    print_summary(s_)
                    results.append(s_)

    out = ROOT / "state" / "backtest_results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(f"\n결과 저장: {out}")

    # 대회 목적함수 기준 순위표
    ok = [s_ for s_ in results if s_ and s_.get("win20_negative_rate", 100) <= 45]
    if ok:
        ok.sort(key=lambda x: -x["win20_over_50pct_rate"])
        print("\n=== 대회 목적함수 기준 상위 (P(+50%) 최대, 단 마이너스확률 ≤ 45%) ===")
        for s_ in ok[:8]:
            print(f"  {s_['label']:<34} P(+50%)={s_['win20_over_50pct_rate']:5.1f}%  "
                  f"P(<0)={s_['win20_negative_rate']:4.1f}%  t={s_['t_stat']:.2f}")

    print("\n주의: 생존 편향, 유니버스 전방선택, 과거 시장경보 미반영으로 낙관적이다.")
    print("      중첩 구간이라 확률의 표준오차가 크다. t통계량과 함께 볼 것.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
