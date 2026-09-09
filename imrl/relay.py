# -*- coding: utf-8 -*-
"""중계실을 직접 읽는다.

웹 중계실 화면이 그리는 순위표 JSON 을 프로그램으로 읽어 기록한다. 공개 순위표에
보이는 정보(순위·필명·수익률·회전율·매매일수)만 쓰고, 아이디·계좌번호 컬럼은
저장 전에 버린다(`scrub`).

그래서 매일 저녁 사람이 숫자를 옮겨 적을 이유가 없어졌다. 이 값들은 **소급 취득이
불가능하고**(대회가 끝나면 중간 순위표가 남지 않는다) 우승 확률 추정을 좌우한다.
사람 손에 맡기면 언젠가 하루가 빈다.

응답 키는 한국어다. 대회 시작 전에는 목록이 비어 있어 **행의 실제 컬럼명을 아직
본 적이 없다.** 그래서 키를 못박지 않고 후보 목록으로 찾고, 원본을 항상 통째로
남긴다. 1일차에 예상과 다른 이름이 와도 데이터를 잃지 않기 위해서다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://vts.imfnsec.com/apis/vts/hi/"
TIMEOUT = 20

#: 행에서 값을 꺼낼 때 볼 키 후보. 앞에서부터 먼저 맞는 것을 쓴다.
KEYS = {
    "rank":     ("순위", "등수", "랭킹"),
    "name":     ("필명", "닉네임", "성명", "이름"),
    "ret":      ("수익률", "누적수익률", "총수익률", "손익률"),
    "turnover": ("회전율", "매매회전율", "누적회전율"),
    "days":     ("매매일수", "거래일수", "매매일"),
    "equity":   ("추정예탁자산", "평가예탁총액", "예탁자산", "총평가금액"),
    "symbols":  ("매매종목수", "종목수", "매매종목"),
}


#: 저장하기 전에 지우는 키.
#:
#: 순위표는 남의 **아이디와 계좌번호**까지 같이 준다. 우리가 필요한 것은 순위와
#: 수익률과 회전율뿐이고, 남의 계좌번호를 디스크에 남길 이유가 없다. 원본을
#: 보존하는 것보다 이걸 지우는 편이 낫다.
DROP_KEYS = ("아이디", "계좌번호", "계좌번호구분", "ID", "userid")


def scrub(items: list[dict]) -> list[dict]:
    """남의 식별정보를 뺀 사본. 필명은 순위표에 공개되는 이름이라 남긴다."""
    return [{k: v for k, v in it.items() if k not in DROP_KEYS} for it in items]


class RelayError(RuntimeError):
    """중계실을 읽지 못했다. 부르는 쪽은 이걸 잡아 조용히 넘어가야 한다 —
    순위 수집 실패로 그날 매매가 멈추면 안 된다."""


def _post(midpath: str, **kw) -> object:
    body = json.dumps(dict(midpath=midpath, **kw), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + midpath, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Origin": "https://vts.imfnsec.com",
                 "Referer": "https://vts.imfnsec.com/hi/vts/",
                 "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            out = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RelayError(f"{midpath}: {type(exc).__name__}: {exc}") from exc
    if isinstance(out, dict) and out.get("statusCode") and out.get("error"):
        raise RelayError(f"{midpath}: {out.get('message', out['error'])}")
    return out


def _pick(row: dict, field: str):
    for k in KEYS[field]:
        if k in row:
            return row[k]
    return None


def _num(v, default=None):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def summary(league: str) -> dict:
    """전체현황. 참가자수와 최고·최저·평균 수익률.

    구분이 '국내주식' 인 행만 쓴다. 같은 대회에 선물옵션 행이 함께 오는데
    참가자수 0 인 그 행을 잡으면 필드가 통째로 사라진다.
    """
    rows = _post("common/summary/statistic", league=str(league)) or []
    stock = next((r for r in rows if str(r.get("구분", "")).strip() == "국내주식"), None)
    if stock is None:
        stock = rows[0] if rows else {}
    return {
        "date": str(stock.get("일자", "")),
        "name": str(stock.get("대회명", "")),
        "n_field": int(_num(stock.get("참가자수"), 0) or 0),
        "n_traded": int(_num(stock.get("매매자수"), 0) or 0),
        "max_pct": _num(stock.get("최고수익률"), 0.0),
        "min_pct": _num(stock.get("최저수익률"), 0.0),
        "avg_pct": _num(stock.get("평균수익률"), 0.0),
        "raw": rows,
    }


def ranking(league: str, limit: int = 300, userid: str = "GUEST") -> list[dict]:
    """순위중계 전체. 순위·필명·수익률·회전율·매매일수·추정예탁자산.

    userid 는 인증이 아니라 '내 행 표시' 용이라 아무 값이나 받는다 —
    개인 로그인 ID 를 파일에 남기지 않으려고 자리표시자를 쓴다.
    """
    out: list[dict] = []
    page = 1
    while len(out) < limit and page <= 40:
        d = _post("stocks/ranking/rank", panname="_ALL_", league=str(league),
                  period="1", userid=userid, _page=page, _limit=100)
        items = (d or {}).get("items") or []
        out.extend(items)
        if not items or not (d or {}).get("nextpage"):
            break
        page += 1
    return out[:limit]


def trade_top(league: str) -> list[dict]:
    """오늘 참가자들이 가장 많이 매매한 종목 (중계실 '매매상위', 실시간, 상위 5).

    2026-09-08 발견: 순위표는 D+1 이지만 이 표는 **당일** 이다. 군중이 지금 무엇을
    사고파는지가 그대로 보인다 — 필드 모형(테마 군집)의 유일한 직접 관측이다.
    """
    rows = _post("stocks/ranking/high/sale", league=str(league), memegb="1") or []
    out = []
    for r in rows:
        out.append({"date": str(r.get("일자", "")), "rank": int(_num(r.get("순위"), 0) or 0),
                    "code": str(r.get("종목코드", "")).zfill(6), "name": str(r.get("종목명", "")),
                    "qty": int(_num(r.get("매매수량"), 0) or 0), "amount": int(_num(r.get("매매금액"), 0) or 0)})
    return out


def league_info(league: str) -> dict:
    """대회 통계 한 장 (기준일 D-1): 거래자·이익/손실실현·무거래자·총거래금액·평균회전율·평균수익률."""
    rows = _post("report/stock/league/info", league=str(league)) or []
    return dict(rows[0]) if rows else {}


def memetop10(league: str) -> list[dict]:
    """대회 기간 누적 매매 상위 10 (기준일 D-1). 아이디·계좌는 지운다."""
    rows = _post("report/stock/league/memetop10", league=str(league)) or []
    return scrub(list(rows))


def untraded_count(league: str) -> int | None:
    """아직 한 번도 매매하지 않은 참가자 수. 명단은 개인 아이디라 **세기만** 한다."""
    try:
        rows = _post("report/stock/league/notmememember", league=str(league)) or []
    except RelayError:
        return None
    return len(rows)


def index_rates(league: str) -> dict:
    """대회 기간 코스피·코스닥 등락률(리포트). 비어 있으면 빈 목록."""
    out = {}
    for key, mid in (("kospi", "report/stock/league/kospijisurate"), ("kosdaq", "report/stock/league/kosdaqjisurate")):
        try:
            out[key] = _post(mid, league=str(league)) or []
        except RelayError:
            out[key] = []
    return out


def full_ranking(league: str, limit: int = 10000) -> list[dict]:
    """리포트용 전체 순위 (한 번에 최대 limit). 순위중계와 같은 행 형식으로 보인다."""
    d = _post("report/stock/league/ranking", league=str(league), period="1", _page=1, _limit=limit)
    return list((d or {}).get("items") or []) if isinstance(d, dict) else list(d or [])


def extras(league: str) -> dict:
    """순위표 밖의 중계실 정보 전부. 하나가 막혀도 나머지는 남긴다."""
    out: dict = {}
    for key, fn in (("trade_top", trade_top), ("info", league_info), ("memetop10", memetop10),
                    ("untraded", untraded_count), ("index_rates", index_rates)):
        try:
            out[key] = fn(league)
        except Exception as exc:          # RelayError 포함. 부가 정보라 조용히 넘긴다
            out[key] = None
            out.setdefault("errors", {})[key] = f"{type(exc).__name__}: {str(exc)[:80]}"
    return out


def parse_rows(items: list[dict]) -> list[dict]:
    """한국어 키를 우리 이름으로. 못 찾은 값은 None 으로 남긴다."""
    rows = []
    for it in items:
        rows.append({
            "rank": int(_num(_pick(it, "rank"), 0) or 0),
            "name": _pick(it, "name"),
            "ret_pct": _num(_pick(it, "ret")),
            "turnover_pct": _num(_pick(it, "turnover")),
            "days": _num(_pick(it, "days")),
            "equity": _num(_pick(it, "equity")),
            "symbols": _num(_pick(it, "symbols")),
        })
    return rows


def find_me(items: list[dict], account: str = "", pen_name: str = "") -> int:
    """순위표에서 내 행의 위치. 없으면 -1.

    **계좌번호로 찾는다.** 필명은 HTS 주문창에서 본 이름이라 순위표 표기와 같다는
    보장이 없고, 남이 같은 필명을 쓸 수도 있다. 계좌번호는 우리가 확실히 아는
    값이고 응답에 그대로 들어 있다 - 지우기 **전에** 여기서 쓴다.

    필명은 계좌번호가 안 맞을 때의 예비 수단으로만 둔다.
    """
    want = "".join(ch for ch in str(account) if ch.isdigit())
    if want:
        for i, it in enumerate(items):
            got = "".join(ch for ch in str(it.get("계좌번호", "")) if ch.isdigit())
            if got and got == want:
                return i
    pen = str(pen_name).strip()
    if pen:
        for i, it in enumerate(items):
            if str(_pick(it, "name") or "").strip() == pen:
                return i
    return -1


def snapshot(league: str, limit: int = 300, account: str = "",
             pen_name: str = "") -> dict:
    """오늘의 중계실 한 장. 원본을 함께 담아 파일로 남길 수 있게 한다."""
    s = summary(league)
    s.pop("raw", None)          # 요약 원본도 굳이 두 번 저장하지 않는다
    items = ranking(league, limit)
    if not items:
        # 순위중계가 비면 리포트용 전체 순위를 한 번 더 본다 (같은 행 형식). 둘 다 비면 개장 전이다.
        try:
            items = full_ranking(league)[:limit]
        except RelayError:
            items = []
    ext = extras(league)
    # 계좌번호를 지우기 **전에** 내 행을 찾아 둔다.
    me = find_me(items, account, pen_name)
    rows = parse_rows(items)
    return {
        "me_index": me,
        "at": f"{date.today():%Y%m%d}",
        "league": str(league),
        "summary": s,
        "rows": rows,
        "raw_items": scrub(items),
        # 행의 실제 컬럼명은 1일차에 처음 본다. 무엇이 왔는지 남겨 둔다.
        "raw_keys": sorted(scrub(items)[0].keys()) if items else [],
        # 순위표 밖의 중계실 정보 — 오늘 매매상위·대회 통계·누적 매매상위·미거래자 수·지수 등락
        "extra": ext,
    }


def save(snap: dict) -> Path:
    """그날의 '최신' 한 장(relay_YYYYMMDD.json)과 시각별 스냅샷(relay/YYYYMMDD_HHMM.json).

    매시간 긁으면 하루치 안에서 순위·수익률이 어떻게 움직였는지가 남는다. 결정 엔진은
    최신 한 장만 읽고, 시각별 파일은 상황판과 사후 분석이 읽는다.
    """
    from datetime import datetime as _dt
    p = ROOT / "state" / f"relay_{snap['at']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(snap, ensure_ascii=False, indent=1)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(p)
    hourly = ROOT / "state" / "relay" / f"{snap['at']}_{_dt.now():%H%M}.json"
    hourly.parent.mkdir(parents=True, exist_ok=True)
    tmp2 = hourly.with_suffix(".tmp")
    tmp2.write_text(body, encoding="utf-8")
    tmp2.replace(hourly)
    return p


def intraday(day: str) -> list[dict]:
    """오늘 시각별 스냅샷 요약 — (시각, 참가자수, 최고, 평균, 내 순위, 내 수익률)."""
    d = ROOT / "state" / "relay"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob(f"{day}_*.json")):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        su = s.get("summary", {}) or {}
        rows = s.get("rows", []) or []
        mi = s.get("me_index", -1)
        me = rows[mi] if 0 <= mi < len(rows) else {}
        out.append({"time": p.stem.split("_")[-1], "n_field": su.get("n_field"),
                    "max_pct": su.get("max_pct"), "avg_pct": su.get("avg_pct"),
                    "rank": me.get("rank"), "ret_pct": me.get("ret_pct"),
                    "turnover_pct": me.get("turnover_pct"), "rows": len(rows)})
    return out


OBS = ROOT / "research" / "observations.jsonl"


def record_observation(kind: str, **fields) -> None:
    """대회 기간에 실제로 잰 값을 한 줄로 남긴다.

    전문가 가설 중 몇 개는 대회가 직접 답한다 — 필드 분산 σ_f, 참가자 수, ETF 산입,
    체결가 규칙, 내 순위. 문서에 적힌 채로 두면 답이 와도 아무도 대조하지 않는다.
    여기 쌓이는 줄이 그 대조의 재료이고, 상황판이 이것을 그대로 보여준다.
    """
    import json as _j
    from datetime import datetime as _dt
    try:
        OBS.parent.mkdir(parents=True, exist_ok=True)
        with OBS.open("a", encoding="utf-8") as f:
            f.write(_j.dumps({"at": _dt.now().isoformat(timespec="seconds"), "kind": kind, **fields},
                             ensure_ascii=False) + chr(10))
    except OSError:
        pass


def observations(limit: int = 60) -> list[dict]:
    import json as _j
    if not OBS.exists():
        return []
    out = []
    for line in OBS.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            out.append(_j.loads(line))
        except ValueError:
            continue
    return out
