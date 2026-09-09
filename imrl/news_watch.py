# -*- coding: utf-8 -*-
"""뉴스를 매일 기록하고, 위험 신호가 있으면 결정 엔진에 **제안**으로 올린다.

무엇이 바뀌었나
  뉴스는 상황판에 보여주기만 했고, 하루가 지나면 사라졌다. 이제 매일 15:05 전에
  보유·후보 종목의 기사를 파일로 남기고(추적), 제목에서 **경성 위험 신호**를
  찾고(검토), 그 종목이 오늘 기준 후보(top-2)에 들어 있으면 그 종목을 뺀 배분을
  제안 파일로 낸다(적용).

'적용' 의 형태
  제안은 주문이 아니다. 같은 경로 위에서 기준선과 나란히 시뮬레이션되고, 확인용
  경로에서 이겨야 실행된다(imrl/proposals.py, imrl/decision.py). 뉴스가 결정을
  **직접** 바꾸는 통로는 여기 없다 — 그건 뉴스가 수익을 예측한다는 검증이 없기
  때문이다. 여기서 뽑는 것은 예측이 아니라 **매매제한 위험**이다: 투자경고 지정
  예고, 거래정지, 관리종목, 상장폐지, 불성실공시, 횡령·배임. 이런 종목은 다음 날
  규정상 매매 자체가 막힐 수 있고, 시장경보 명단(imrl/data.py)은 **지정된 뒤에야**
  잡는다. 뉴스는 하루 먼저 안다.

파일
  state/news/YYYYMMDD.json           그날의 종목별 기사·표식 (추적)
  research/proposals/PROP-NEWS-*.json 위험 종목을 뺀 배분 제안 (오늘만 유효)
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import news

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "state" / "news"
PROP = ROOT / "research" / "proposals"

#: 규정상 매매제한으로 이어질 수 있는 말. 방향(호재/악재) 판단이 아니다.
HARD_RISK = ("거래정지", "매매거래정지", "관리종목", "상장폐지", "정리매매",
             "투자위험", "투자경고", "불성실공시", "횡령", "배임", "감자",
             "회생", "파산", "감사의견", "상장적격성")
#: 눈여겨볼 말. 제안을 내지는 않는다.
SOFT_RISK = ("투자주의", "급락", "하한가", "테마", "과열", "조회공시", "유상증자")


#: 이보다 오래된 기사는 표식에 쓰지 않는다.
#: 첫 라이브 실행에서 6월 20일자 "하루 거래정지" 기사가 9월 후보를 제외시켰다.
#: 검색 결과는 오래된 것도 돌려준다 — 신호는 최근 며칠 것만 신호다.
MAX_AGE_DAYS = 5


def age_days(when: str, today: date | None = None) -> int | None:
    """RSS pubDate → 며칠 전인가. 못 읽으면 None (표식에 안 쓴다)."""
    from email.utils import parsedate_to_datetime
    w = (when or "").strip()
    if not w:
        return None
    try:
        d = parsedate_to_datetime(w).date()
    except (TypeError, ValueError, IndexError):
        # 예전 기록은 "Mon, 07 Sep 2026" 까지만 남았다. 시각을 붙여 다시 읽는다.
        try:
            d = parsedate_to_datetime(w + " 00:00:00 +0000").date()
        except (TypeError, ValueError, IndexError):
            return None
    return ((today or date.today()) - d).days


def classify(headlines: list[dict], today: date | None = None) -> tuple[list[str], list[str]]:
    hard, soft = [], []
    for h in headlines:
        a = age_days(h.get("when", ""), today)
        if a is None or a > MAX_AGE_DAYS or a < -1:
            continue
        t = h.get("title", "")
        for k in HARD_RISK:
            if k in t and k not in hard:
                hard.append(k)
        for k in SOFT_RISK:
            if k in t and k not in soft:
                soft.append(k)
    return hard, soft


def collect(names_by_code: dict[str, str], per: int = 6, today: date | None = None) -> dict:
    """종목별 기사와 표식. 네트워크 실패는 그 종목만 빈 목록으로 남긴다.
    기사마다 age_days 를 붙여 기록한다 — 나중에 왜 표식이 붙었는지 볼 수 있게."""
    out = {}
    for code, name in names_by_code.items():
        hs = news.headlines(name, per) if name else []
        for h in hs:
            h["age_days"] = age_days(h.get("when", ""), today)
        hard, soft = classify(hs, today)
        out[code] = {"name": name, "headlines": hs, "hard": hard, "soft": soft}
    return out


def _dump(p: Path, obj: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def save(day: str, snap: dict, stamp: str | None = None) -> Path:
    """그날의 최신 한 장(YYYYMMDD.json). stamp(HHMM)가 있으면 회차별 사본도 남긴다 —
    하루 안에서 표식이 **언제** 붙었는지가 남아야 사후에 '뉴스가 먼저였나'를 볼 수 있다."""
    from datetime import datetime as _dt
    DIR.mkdir(parents=True, exist_ok=True)
    body = {"date": day, "at": _dt.now().isoformat(timespec="seconds"), "by_code": snap}
    p = DIR / f"{day}.json"
    _dump(p, body)
    if stamp:
        _dump(DIR / f"{day}_{stamp}.json", body)
    return p


def history(days: int = 7) -> list[dict]:
    """최근 며칠치(날짜 파일만 — 회차별 사본은 빼고). 상황판이 '이 종목 며칠째 표식' 을 그리는 데 쓴다."""
    if not DIR.exists():
        return []
    out = []
    for p in sorted(p for p in DIR.glob("*.json") if len(p.stem) == 8)[-days:]:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def intraday(day: str) -> list[dict]:
    """오늘 회차별 요약 — (시각, 종목수, 경성 표식 종목, 주의 종목수)."""
    if not DIR.exists():
        return []
    out = []
    for p in sorted(DIR.glob(f"{day}_*.json")):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        by = j.get("by_code") or {}
        out.append({"time": p.stem.split("_")[-1], "n": len(by),
                    "hard": sorted(c for c, v in by.items() if v.get("hard")),
                    "soft": sum(1 for v in by.values() if v.get("soft"))})
    return out


def propose_exclusions(day: str, snap: dict, ranked_codes: list[str], top_n: int,
                       cap_w: float, exposure: float = 0.90, scoring: str = "full") -> list[Path]:
    """경성 표식이 붙은 종목이 오늘 후보(top_n)에 있으면 그 종목을 뺀 배분을 제안한다.

    다음 순위를 올려 같은 노출을 유지한다. 규정 상한(cap_w)은 넘지 않는다.
    제안은 오늘만 유효하다 — 내일은 내일 뉴스로 다시 판단한다.

    scoring 은 이 순위가 어디서 왔는가다. 'full' 은 정밀 회차(유니버스 재계산),
    'cached' 는 시간별 회차(후보 캐시). cached 는 full 의 제안을 **덮지 않는다** —
    캐시 순위는 낡았을 수 있다. full 은 항상 덮는다. 그리고 표식이 사라지면 자기가 쓴
    제안은 지운다 — 아침 회차의 낡은 제안이 15:05 에 읽히면 안 된다.
    """
    PROP.mkdir(parents=True, exist_ok=True)
    pid = f"PROP-NEWS-{day}"
    p = PROP / f"{pid}.json"
    existing = None
    if p.exists():
        try:
            existing = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
    if scoring == "cached" and existing and str(existing.get("scoring", "full")) == "full":
        return []
    flagged = [c for c in ranked_codes[:top_n] if snap.get(c, {}).get("hard")]
    if not flagged:
        if existing is not None:
            p.unlink(missing_ok=True)
        return []
    # 승격은 **오늘 기사를 본 종목**만. 안 본 종목을 올리면 제외의 취지가 반쯤 사라진다.
    keep = [c for c in ranked_codes if c in snap and c not in flagged and not snap[c].get("hard")]
    take = keep[:top_n]
    if len(take) < 1:
        if existing is not None:
            p.unlink(missing_ok=True)
        return []
    w = min(exposure / len(take), cap_w)
    weights = {c: round(w, 4) for c in take}
    reasons = "; ".join(f"{c}({snap[c]['name']}): {', '.join(snap[c]['hard'])}" for c in flagged)
    rec = {"id": pid, "source": "news_watch", "hypothesis": "NEWS-RISK",
           "weights": weights,
           "note": f"매매제한 위험 신호로 제외 — {reasons}. 다음 순위 승격",
           "expires": day, "excluded": flagged, "scoring": scoring}
    _dump(p, rec)
    return [p]


def run(names_by_code: dict[str, str], ranked_codes: list[str], top_n: int, cap_w: float,
        day: str | None = None, scoring: str = "full", stamp: str | None = None) -> dict:
    day = day or f"{date.today():%Y%m%d}"
    snap = collect(names_by_code)
    path = save(day, snap, stamp=stamp)
    props = propose_exclusions(day, snap, ranked_codes, top_n, cap_w, scoring=scoring)
    return {"day": day, "file": str(path), "snapshot": snap,
            "hard": {c: v["hard"] for c, v in snap.items() if v["hard"]},
            "soft": {c: v["soft"] for c, v in snap.items() if v["soft"]},
            "proposals": [str(p) for p in props]}
