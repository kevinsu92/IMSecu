"""시세·종목 데이터 수집 계층.

모든 소스는 인증이 필요 없다.
  - FinanceDataReader : 종목 마스터(발행주식수, 시가총액, 소속부), ETF 목록
  - 네이버 금융       : 관리/투자주의/투자경고/투자위험 종목 리스트, 실시간 시세, 일봉
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd
from pathlib import Path
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://finance.naver.com/"}

_ALERT_URLS = {
    "관리종목": "https://finance.naver.com/sise/management.naver",
    "투자주의": "https://finance.naver.com/sise/investment_alert.naver?type=caution",
    "투자경고": "https://finance.naver.com/sise/investment_alert.naver?type=warning",
    "투자위험": "https://finance.naver.com/sise/investment_alert.naver?type=risk",
}


# --------------------------------------------------------------------------- #
# 종목 마스터
# --------------------------------------------------------------------------- #

LISTING_CACHE = Path(__file__).resolve().parent.parent / "state" / "listing_cache.csv"
_FDR_CACHE = ("https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/"
              "refs/heads/master/data/listing/krx/{date}.csv")


def _listing_from_fdr_cache(days_back: int = 8) -> pd.DataFrame | None:
    """FinanceDataReader 가 쓰는 GitHub 캐시 CSV 를 **있는 날짜**로 직접 읽는다.

    2026-09-08 1일차 사고: fdr.StockListing("KRX") 은 KRX 가 알려주는 '최근 영업일'
    (장중에는 오늘) 파일을 읽는데, 그 파일은 장 마감 뒤에야 생긴다. 14:45·15:05 에
    404 → 유니버스 실패 → 주문서 없음 → 매매 0건. 종목 필터(가격·주식수·거래대금·소속부)에는
    전일 스냅샷이면 충분하고 신호는 어차피 종목별 일봉을 따로 받는다.
    """
    from datetime import date, timedelta
    today = date.today()
    for i in range(days_back):
        d = today - timedelta(days=i)
        url = _FDR_CACHE.format(date=d.isoformat())
        try:
            r = requests.get(url, headers=UA, timeout=20)
        except Exception:
            continue
        if r.status_code != 200 or len(r.content) < 10_000:
            continue
        import io
        df = pd.read_csv(io.BytesIO(r.content), index_col=0,
                         dtype={"Code": str, "Dept": str, "ChangeCode": str, "MarketId": str})
        df.attrs["asof"] = d.isoformat(); df.attrs["source"] = "fdr-cache"
        return df
    return None


def _listing_from_naver() -> pd.DataFrame | None:
    """네이버 시가총액 목록(코스피·코스닥). 소속부는 없다 — 시장경보 명단이 따로 걸러준다."""
    rows = []
    for market, key in (("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ")):
        for page in range(1, 40):
            url = f"https://m.stock.naver.com/api/stocks/marketValue/{key}?page={page}&pageSize=100"
            try:
                r = requests.get(url, headers={**UA, "Referer": "https://m.stock.naver.com/"}, timeout=15)
                items = r.json().get("stocks", []) if r.status_code == 200 else []
            except Exception:
                items = []
            if not items:
                break
            for it in items:
                if str(it.get("stockEndType", "stock")) != "stock":
                    continue                      # ETF·ETN 은 알파 유니버스에 넣지 않는다 (FDR 목록과 동일)
                try:
                    close = int(str(it.get("closePrice", "0")).replace(",", "") or 0)
                    # marketValue·accumulatedTradingValue 는 **억원** 단위다 (삼성전자 "15,755,721" = 1,575조).
                    mv = int(str(it.get("marketValue", "0")).replace(",", "") or 0) * 100_000_000
                    amt = int(str(it.get("accumulatedTradingValue", "0")).replace(",", "") or 0) * 100_000_000
                except ValueError:
                    continue
                rows.append({"Code": str(it.get("itemCode", "")).zfill(6), "Name": it.get("stockName", ""),
                             "Market": market, "Dept": "", "Close": close, "Amount": amt,
                             "Marcap": mv, "Stocks": int(mv / close) if close else 0})
            if len(items) < 100:
                break
    if len(rows) < 1000:
        return None
    df = pd.DataFrame(rows)
    df = df[~df["Name"].str.contains(r"ETN|ETF|KODEX|TIGER|KBSTAR|RISE|ACE |SOL |HANARO|ARIRANG|KOSEF|PLUS |1Q |KIWOOM |선물|인버스|레버리지|\(합성\)|\(H\)", regex=True, na=False)].copy()
    df.attrs["asof"] = pd.Timestamp.today().strftime("%Y-%m-%d"); df.attrs["source"] = "naver-marketvalue"
    return df


def load_listing() -> pd.DataFrame:
    """KRX 전체 상장 종목 스냅샷. 종가/발행주식수/거래대금/소속부 포함.

    출처 순서: (1) FDR GitHub 캐시의 가장 최근 날짜 파일 (2) FinanceDataReader 자체
    (3) 네이버 시가총액 목록 (4) 마지막 성공분 로컬 캐시. 어느 것이든 성공하면 로컬 캐시를 갱신한다.
    """
    df = None
    try:
        df = _listing_from_fdr_cache()
    except Exception as exc:
        print(f"  종목목록: FDR 캐시 실패 ({type(exc).__name__}: {str(exc)[:80]})")
    if df is None:
        try:
            import FinanceDataReader as fdr
            df = fdr.StockListing("KRX"); df.attrs["asof"] = "?"; df.attrs["source"] = "fdr"
        except Exception as exc:
            print(f"  종목목록: FinanceDataReader 실패 ({type(exc).__name__}: {str(exc)[:80]})")
    if df is None:
        try:
            df = _listing_from_naver()
        except Exception as exc:
            print(f"  종목목록: 네이버 목록 실패 ({type(exc).__name__}: {str(exc)[:80]})")
    if df is None:
        if LISTING_CACHE.exists():
            df = pd.read_csv(LISTING_CACHE, dtype={"Code": str, "Dept": str})
            df.attrs["asof"] = str(df["_asof"].iloc[0]) if "_asof" in df.columns else "?"
            df.attrs["source"] = "local-cache"
            print(f"  종목목록: 온라인 출처 전부 실패 — 로컬 캐시 사용 (기준 {df.attrs['asof']})")
        else:
            raise RuntimeError("종목목록을 어디서도 받지 못했다 (FDR 캐시·FDR·네이버·로컬 캐시 전부 실패)")
    asof, source = df.attrs.get("asof", "?"), df.attrs.get("source", "?")
    df = df.rename(columns={"Code": "code", "Name": "name", "Market": "market"})
    df["code"] = df["code"].astype(str).str.zfill(6)
    if source != "local-cache":
        try:
            LISTING_CACHE.parent.mkdir(parents=True, exist_ok=True)
            out = df.copy(); out["_asof"] = asof; out["_source"] = source
            out.to_csv(LISTING_CACHE, index=False, encoding="utf-8")
        except OSError:
            pass
    df.attrs["asof"], df.attrs["source"] = asof, source
    print(f"  종목목록: {len(df)}종목, 기준일 {asof}, 출처 {source}")
    return df


def load_etf_listing() -> pd.DataFrame:
    """국내 상장 ETF 목록. 회전율 파밍 대상 선별에 사용."""
    import FinanceDataReader as fdr

    df = fdr.StockListing("ETF/KR")
    df = df.rename(columns={"Symbol": "code", "Name": "name"})
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


# --------------------------------------------------------------------------- #
# 규정상 매매제한 종목 리스트
# --------------------------------------------------------------------------- #

def fetch_alert_codes() -> dict[str, set[str]]:
    """관리종목 / 투자주의 / 투자경고 / 투자위험 종목코드 집합.

    대회 규정 1.2절의 매매제한 종목 중 시장경보 관련 항목을 커버한다.
    """
    out: dict[str, set[str]] = {}
    for label, url in _ALERT_URLS.items():
        try:
            r = requests.get(url, headers=UA, timeout=15)
            r.encoding = "euc-kr"
            out[label] = set(re.findall(r"code=(\d{6})", r.text))
        except Exception as exc:  # 네트워크 실패 시 빈 집합이 아니라 예외를 올린다.
            raise RuntimeError(f"{label} 리스트 수집 실패: {exc}") from exc
    return out


def fetch_trade_status(code: str) -> dict:
    """개별 종목의 거래정지 여부 등 실시간 상태."""
    url = f"https://m.stock.naver.com/api/stock/{code}/basic"
    r = requests.get(url, headers={**UA, "Referer": "https://m.stock.naver.com/"}, timeout=10)
    r.raise_for_status()
    j = r.json()
    return {
        "code": code,
        "name": j.get("stockName"),
        "close": _to_int(j.get("closePrice")),
        "tradable": j.get("tradableStatus") == "tradable",
        "trade_stop": (j.get("tradeStopType") or {}).get("name") != "TRADING",
        "newly_listed": bool(j.get("newlyListed")),
    }


# --------------------------------------------------------------------------- #
# 시세
# --------------------------------------------------------------------------- #

def fetch_quote(code: str) -> dict:
    """실시간(지연 없음) 현재가 스냅샷."""
    url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
    r = requests.get(url, headers={**UA, "Referer": "https://m.stock.naver.com/"}, timeout=10)
    r.raise_for_status()
    d = r.json()["datas"][0]
    return {
        "code": code,
        "name": d.get("stockName"),
        "price": _to_int(d.get("closePrice")),
        "change_pct": float(d.get("fluctuationsRatio") or 0),
        "open": _to_int(d.get("openPrice")),
        "high": _to_int(d.get("highPrice")),
        "low": _to_int(d.get("lowPrice")),
        "volume": _to_int(d.get("accumulatedTradingVolume")),
        "tradable": d.get("tradableStatus") == "tradable",
        # 아래 세 개는 예전에 버리던 값이다. 시세가 언제 것인지 모르면
        # **어제 가격으로 오늘 주문을 낼 수 있다.** 응답에 이미 들어 있으므로
        # 추가 요청 없이 그대로 싣는다.
        "traded_at": d.get("localTradedAt"),          # 예: 2026-09-04T15:30:00+09:00
        "market_status": d.get("marketStatus"),        # OPEN | CLOSE | ...
        "amount": _to_int(d.get("accumulatedTradingValueRaw")),
    }


def fetch_daily(code: str, days: int = 90) -> pd.DataFrame:
    """네이버 일봉. 컬럼 = date, open, high, low, close, volume, foreign_ratio."""
    end = date.today()
    start = end - timedelta(days=int(days * 1.9) + 10)
    url = (
        "https://api.finance.naver.com/siseJson.naver"
        f"?symbol={code}&requestType=1"
        f"&startTime={start:%Y%m%d}&endTime={end:%Y%m%d}&timeframe=day"
    )
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    rows = json.loads(r.text.replace("'", '"'))
    if len(rows) < 2:
        return pd.DataFrame()
    df = pd.DataFrame(rows[1:], columns=["date", "open", "high", "low", "close", "volume", "foreign_ratio"])
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close"]).reset_index(drop=True)


def fetch_daily_many(codes: list[str], days: int = 90, workers: int = 8) -> dict[str, pd.DataFrame]:
    """다수 종목 일봉 병렬 수집. 실패한 종목은 결과에서 제외한다."""
    result: dict[str, pd.DataFrame] = {}

    def one(c: str):
        for attempt in range(2):
            try:
                return c, fetch_daily(c, days)
            except Exception:
                time.sleep(0.4 * (attempt + 1))
        return c, pd.DataFrame()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for code, df in ex.map(one, codes):
            if not df.empty:
                result[code] = df
    return result


# --------------------------------------------------------------------------- #
# 유틸
# --------------------------------------------------------------------------- #

def _to_int(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).replace(",", "").strip()
    return int(s) if s.lstrip("-").isdigit() else None


def tick_size(price: int, market: str = "") -> int:
    """KRX 호가단위. 지정가 주문 가격을 유효 호가로 맞추는 데 사용.

    **코스피·코스닥이 동일하다.** KRX 가 2023-01-25 부로 두 시장의 호가단위를
    통합했다. 그 전에는 코스닥이 5만원 이상 전부 100원이었고, 이 함수도 그
    옛 규칙(`if kosdaq: return 100`)을 갖고 있었다.

    그대로 두면 코스닥 20만원 이상 종목에서 **거래소가 받지 않는 가격**이 나온다.
    예: 레인보우로보틱스 459,000원에 +1% 오프셋 -> 옛 규칙 463,600원(100원 틱)이지만
    올바른 값은 464,000원(500원 틱)이다. 463,600 은 호가 그리드에 없는 값이라
    거부되거나 조용히 스냅되고, 스냅되면 얼마에 주문했는지 알 수 없게 된다.
    2026-09-05 기준 해당 조건에 걸리는 코스닥 종목이 5개 있고 전부 모멘텀 성격이라
    이 전략이 실제로 고를 수 있는 이름들이다.

    market 인자는 호환성을 위해 남긴다. 판정에는 쓰지 않는다.
    """
    if price < 2000:
        return 1
    if price < 5000:
        return 5
    if price < 20000:
        return 10
    if price < 50000:
        return 50
    if price < 200000:
        return 100
    if price < 500000:
        return 500
    return 1000


def round_to_tick(price: float, market: str, mode: str = "down") -> int:
    """가격을 유효 호가 단위로 정렬."""
    p = int(price)
    t = tick_size(p, market)
    return (p // t) * t if mode == "down" else -(-p // t) * t


@dataclass(frozen=True)
class TradingCalendar:
    """대회 영업일 계산. 주말 + 설정 파일의 휴장일 목록을 제외한다."""

    start: date
    end: date
    holidays: frozenset[date]

    @classmethod
    def from_config(cls, contest: dict) -> "TradingCalendar":
        return cls(
            start=datetime.strptime(contest["start_date"], "%Y-%m-%d").date(),
            end=datetime.strptime(contest["end_date"], "%Y-%m-%d").date(),
            holidays=frozenset(
                datetime.strptime(h, "%Y-%m-%d").date() for h in contest.get("holidays", [])
            ),
        )

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def trading_days(self) -> list[date]:
        days, cur = [], self.start
        while cur <= self.end:
            if self.is_trading_day(cur):
                days.append(cur)
            cur += timedelta(days=1)
        return days

    def days_left(self, today: date | None = None) -> int:
        today = today or date.today()
        return sum(1 for d in self.trading_days() if d >= today)

    def total_days(self) -> int:
        return len(self.trading_days())
