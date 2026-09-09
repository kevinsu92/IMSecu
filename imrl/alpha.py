"""신호 생성.

대회 기간이 21영업일뿐이라 펀더멘털은 반영될 시간이 없다.
단기 모멘텀 · 거래대금 유입 · 변동성 세 축만 쓴다.
변동성에 양(+)의 가중치를 주는 것은 의도된 선택이다 — 순위 대회에서 변동성은 비용이 아니라 자산이다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import data


def _zscore(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    sd = s.std(ddof=0)
    if not np.isfinite(sd) or sd == 0:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / sd


# KRX 시장경보(투자주의·경고·위험) 지정요건 근사.
#
# 이 전략은 단기 급등주를 고르는데, 그건 시장경보 지정요건과 정확히 같은 모집단이다.
# 지정되면 대회 규정상 **매매제한 종목**이 되어 살 수도 팔 수도 없게 될 수 있다.
# 이미 지정된 종목은 유니버스에서 빠지지만, **지정 직전 종목은 그대로 통과한다.**
# 그래서 요건 근처 종목을 미리 피한다. 안전마진을 곱해 여유를 둔다.
#
# 규정 회피가 목적이었는데 성과도 같이 올랐다. 421종목 935일 백테스트(top3, 종가체결):
#
#   margin  일평균    t   P(+50%)  P(<0)
#     0.50  0.273%  1.79    4.2%   39.3%
#     0.65  0.421%  2.72    6.9%   36.0%
#     0.75  0.511%  3.27    9.9%   37.9%
#     0.85  0.583%  3.66   11.4%   37.2%   <- 채택
#     0.95  0.566%  3.51   10.7%   38.5%
#     1.10  0.518%  3.14    9.4%   37.3%
#     1.50  0.420%  2.40    6.6%   40.7%
#     없음  0.439%  2.32    6.2%   40.6%
#
# 곡선이 뾰족하지 않고 완만한 단봉이라 파라미터 운이 아니다(0.75~0.95 구간이
# 전부 t 3.2~3.7). 너무 조이면(0.50) 멀쩡한 모멘텀 종목까지 버려 오히려 나빠진다.
# 기간을 갈라도 방향이 같다 — 2023년 이전 t 2.73 -> 4.18, 2024년 이후 t 1.20 -> 1.88.
#
# 경제적 해석: 요건 근처까지 간 종목은 이미 과열 소진 구간이다. ret5 에 음(-)
# 가중치를 준 것과 같은 힘의 더 강한 버전이다.
#
# 0.85 는 백테스트를 보기 전에 규정 여유만 보고 정한 값이다. 위 표는 그 선택이
# 운이 아니었는지 확인한 것이지, 표를 보고 고른 값이 아니다.
ALERT_MARGIN = 0.85
ALERT_RULES = [
    ("ret3", 1.00),    # 3일 +100%
    ("ret5", 0.60),    # 5일 +60%
    ("ret15", 1.00),   # 15일 +100%
]


def compute_features(bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """종목별 일봉에서 팩터 원값을 뽑는다.

    fetch_daily 는 OHLC 를 전부 돌려주는데 예전에는 close 와 volume 만 썼다.
    high/low/open 을 버리면 종가위치·고점거리·ATR·갭을 못 만든다. 추가 수집이나
    의존성 없이 계산되는 값들이라 버릴 이유가 없다. 특히 **종가에 매수하는 전략이
    그 종가가 당일 고가권인지 저가권인지 모르는 것**은 자기모순이다.
    """
    rows = []
    for code, df in bars.items():
        if len(df) < 25:
            continue
        close = df["close"].to_numpy(dtype=float)
        vol = df["volume"].to_numpy(dtype=float)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        opn = df["open"].to_numpy(dtype=float)
        amount = close * vol  # 일별 거래대금 근사

        ret3 = close[-1] / close[-4] - 1.0
        ret5 = close[-1] / close[-6] - 1.0
        ret15 = close[-1] / close[-16] - 1.0
        ret20 = close[-1] / close[-21] - 1.0
        ma20 = close[-20:].mean()
        daily_ret = np.diff(close[-21:]) / close[-21:-1]
        vola = float(np.std(daily_ret, ddof=0))
        amt20 = float(np.mean(amount[-21:-1]))
        amt_surge = float(amount[-1] / amt20) if amt20 > 0 else 0.0

        # 종가위치(CLV): 당일 저가 0, 고가 1. 종가에 사는 전략에서 특히 의미가 있다.
        rng = high[-1] - low[-1]
        clv = float((close[-1] - low[-1]) / rng) if rng > 0 else 0.5

        # 20일 신고가까지의 거리. 0 이면 신고가.
        hi20 = float(np.max(high[-20:]))
        dist_hi20 = float(hi20 / close[-1] - 1.0) if close[-1] > 0 else 1.0

        # ATR(14) 을 가격 대비 비율로. 손절폭·사이징의 기준이 된다.
        tr = np.maximum(
            high[-15:] - low[-15:],
            np.maximum(np.abs(high[-15:] - close[-16:-1]), np.abs(low[-15:] - close[-16:-1])),
        )
        atr = float(np.mean(tr)) / close[-1] if close[-1] > 0 else 0.0

        gap = float(opn[-1] / close[-2] - 1.0) if close[-2] > 0 else 0.0
        # 장중수익률: 시가 대비 종가. gap 과 합치면 하루 수익률이 오버나이트와
        # 장중으로 분해된다. 알파가 어느 쪽에 있는지 검증하려면 둘 다 필요하다.
        intraday = float(close[-1] / opn[-1] - 1.0) if opn[-1] > 0 else 0.0

        # 시장경보 지정요건 근접도. 1.0 을 넘으면 요건에 걸릴 수 있다.
        alert_ratio = 0.0
        for name, threshold in ALERT_RULES:
            val = {"ret3": ret3, "ret5": ret5, "ret15": ret15}[name]
            alert_ratio = max(alert_ratio, val / threshold)

        rows.append(
            {
                "code": code,
                "close": float(close[-1]),
                "ret3": ret3,
                "ret5": ret5,
                "ret15": ret15,
                "ret20": ret20,
                "ma20": ma20,
                "above_ma20": close[-1] > ma20,
                "volatility": vola,
                "amount_surge": amt_surge,
                "avg_amount20": amt20,
                "clv": clv,
                "dist_hi20": dist_hi20,
                "atr_pct": atr,
                "gap": gap,
                "intraday": intraday,
                "alert_ratio": alert_ratio,
            }
        )
    return pd.DataFrame(rows)


def score(cfg: dict, feats: pd.DataFrame, phase: str | None = None) -> pd.DataFrame:
    """팩터를 횡단면 z-score 로 합성해 최종 점수를 만든다.

    phase 를 주면 그 국면의 weight_overrides 를 기본 가중치 위에 덮는다.
    공격 국면에서 변동성 노출을 올리기 위한 것이다 — 순위 대회에서 필요한 것은
    기대수익이 아니라 상위 꼬리에 닿을 확률이고, 그건 변동성이 만든다.
    다만 무한정 올리면 안 된다. 실측(top3, 종가체결)에서 volatility 가중치별
    P(20일 +50%) 는 0.5 -> 11.4%, 1.0 -> 12.1%, 1.5 -> 7.8%, 3.0 -> 5.7% 로
    1.0 을 넘으면 오히려 떨어진다. 변동성만 높고 신호가 약한 종목이 섞이기 때문이다.
    """
    a = cfg["alpha"]
    df = feats.copy()
    if df.empty:
        return df

    if a.get("require_above_ma20"):
        df = df[df["above_ma20"]]
    if a.get("require_positive_ret20"):
        df = df[df["ret20"] > 0]

    # 시장경보 지정요건에 근접한 종목을 미리 제외한다.
    # 지정되면 매매제한이 걸려 **팔지 못한 채 자본이 묶인다.** 수익 기회를 놓치는
    # 것보다 나쁘다. 이미 지정된 종목은 universe 에서 빠지지만 지정 '직전' 종목은
    # 여기서만 걸러낼 수 있다.
    if a.get("avoid_market_alert", True) and "alert_ratio" in df.columns:
        df = df[df["alert_ratio"] < ALERT_MARGIN]

    if df.empty:
        return df

    w = dict(a["weights"])
    if phase:
        w.update(cfg.get("portfolio", {}).get(phase, {}).get("weight_overrides", {}))
    df["z_ret20"] = _zscore(df["ret20"])
    df["z_ret5"] = _zscore(df["ret5"])
    df["z_surge"] = _zscore(np.log1p(df["amount_surge"].clip(lower=0)))
    df["z_vola"] = _zscore(df["volatility"])

    df["score"] = (
        w["ret20"] * df["z_ret20"]
        + w["ret5"] * df["z_ret5"]
        + w["amount_surge"] * df["z_surge"]
        + w["volatility"] * df["z_vola"]
    )
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def rank_universe(cfg: dict, uni: pd.DataFrame, verbose: bool = True,
                  phase: str | None = None) -> pd.DataFrame:
    """유니버스 전체를 점수화한다. 거래대금 상위 N종목만 일봉을 받아 시간을 아낀다."""
    a = cfg["alpha"]
    pool = uni.sort_values("amount", ascending=False).head(a["max_candidates_scored"])
    codes = pool["code"].tolist()

    if verbose:
        print(f"  일봉 수집 대상 {len(codes)}종목 ...", flush=True)
    bars = data.fetch_daily_many(codes, days=a["history_days"])
    if verbose:
        print(f"  일봉 수집 완료 {len(bars)}종목", flush=True)
        # 일봉의 마지막 날짜를 기록한다(F-25). 15:05 에 오늘 봉이 빠져 있으면 신호가 하루 밀리고,
        # 16:30 전후에는 네이버가 당일 봉을 확정하며 값이 바뀐다 — 그날의 로그로 판정한다.
        try:
            from collections import Counter
            last = Counter(str(df["date"].iloc[-1])[:10] for df in bars.values() if len(df))
            top = ", ".join(f"{d} {n}종목" for d, n in last.most_common(3))
            print(f"  일봉 마지막 날짜: {top}", flush=True)
        except Exception:
            pass

    feats = compute_features(bars)
    scored = score(cfg, feats, phase)
    if scored.empty:
        return scored
    return scored.merge(uni[["code", "name", "market", "amount"]], on="code", how="left")
