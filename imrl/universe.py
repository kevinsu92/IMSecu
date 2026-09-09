"""대회 규정에 맞는 매매 가능 유니버스 구성.

매뉴얼 p10 '투자 및 제한 종목' 을 그대로 코드로 옮긴 것이 핵심이다.
하나라도 누락되면 주문 거부 또는 실격으로 이어지므로 필터는 보수적으로 건다.
"""

from __future__ import annotations

import pandas as pd

from . import data


EXCLUDED_MARKETS = {"KONEX"}


def build(cfg: dict, listing: pd.DataFrame | None = None) -> pd.DataFrame:
    """매매 가능 종목 데이터프레임을 반환한다.

    반환 컬럼: code, name, market, close, shares, amount, marcap
    """
    u = cfg["universe"]
    df = listing if listing is not None else data.load_listing()

    df = df.rename(columns={"Stocks": "shares", "Close": "close", "Amount": "amount", "Marcap": "marcap"})
    keep = ["code", "name", "market", "close", "shares", "amount", "marcap", "Dept"]
    df = df[[c for c in keep if c in df.columns]].copy()
    df["Dept"] = df.get("Dept", pd.Series(dtype=object)).fillna("")

    n0 = len(df)
    steps: list[tuple[str, int]] = []

    def step(label: str, mask: pd.Series) -> None:
        nonlocal df
        df = df[mask].copy()
        steps.append((label, len(df)))

    # 1) 시장: 거래소·코스닥만. 코넥스 제외 (규정 명시)
    step("시장(코스피/코스닥)", df["market"].isin(u["markets"]) & ~df["market"].isin(EXCLUDED_MARKETS))

    # 2) 현재가 1,000원 미만 제외
    step("현재가>=1000", pd.to_numeric(df["close"], errors="coerce").fillna(0) >= u["min_price"])

    # 3) 총 발행주식수 100,000주 미만 제외
    step("발행주식수>=100k", pd.to_numeric(df["shares"], errors="coerce").fillna(0) >= u["min_shares"])

    # 4) 소속부 기준 관리종목 / 투자주의환기종목 제외
    step("소속부 정상", ~df["Dept"].str.contains("관리종목|투자주의환기", na=False))

    # 5) 스팩·우선주·리츠 등 제외 (유동성이 낮고 모멘텀 신호가 무의미)
    pat = "|".join(u["exclude_name_patterns"])
    step("스팩/리츠 제외", ~df["name"].str.contains(pat, na=False, case=False))
    if u.get("exclude_preferred"):
        # 우선주는 종목코드 끝자리가 0이 아니다 (예: 005935 삼성전자우)
        step("우선주 제외", df["code"].str.endswith("0"))

    # 6) 시장경보 종목 제외 — 규정의 투자주의/경고/위험 + 관리종목
    alerts = data.fetch_alert_codes()
    banned = set().union(*alerts.values())
    step(f"시장경보 제외({len(banned)}종목)", ~df["code"].isin(banned))

    # 7) 유동성 하한 — 규정은 아니지만 체결 안정성을 위한 자체 기준
    step("거래대금 하한", pd.to_numeric(df["amount"], errors="coerce").fillna(0) >= u["min_avg_amount_krw"])

    df.attrs["filter_steps"] = [("원본", n0)] + steps
    df.attrs["alert_counts"] = {k: len(v) for k, v in alerts.items()}
    return df.reset_index(drop=True)


def describe(df: pd.DataFrame) -> str:
    lines = ["유니버스 필터 단계별 잔존 종목수"]
    for label, n in df.attrs.get("filter_steps", []):
        lines.append(f"  {label:<24} {n:>5}")
    ac = df.attrs.get("alert_counts", {})
    if ac:
        lines.append("  시장경보 원천: " + ", ".join(f"{k} {v}" for k, v in ac.items()))
    return "\n".join(lines)


def verify_tradable(codes: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """주문 직전 개별 종목의 거래정지·신규상장 여부를 최종 확인한다.

    유니버스 스냅샷은 전일 기준이므로, 실제 주문 대상에 대해서만 한 번 더 검증한다.
    """
    ok: list[str] = []
    rejected: list[tuple[str, str]] = []
    for c in codes:
        try:
            st = data.fetch_trade_status(c)
        except Exception as exc:
            rejected.append((c, f"상태조회 실패: {exc}"))
            continue
        if st["trade_stop"] or not st["tradable"]:
            rejected.append((c, "거래정지"))
        elif st["newly_listed"]:
            rejected.append((c, "신규상장(동시호가 주문 불가)"))
        else:
            ok.append(c)
    return ok, rejected
