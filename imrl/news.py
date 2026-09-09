# -*- coding: utf-8 -*-
"""보유·후보 종목의 뉴스를 모은다.

왜 필요한가
  알파는 가격과 거래량만 본다. 그래서 **왜** 그 종목이 움직였는지 모른다.
  실제로 1순위로 뽑힌 종목이 장관 후보자와 이름이 같다는 이유로 195% 뛰었다가
  무너지는 중이었다. 숫자만 보면 '변동성이 큰 좋은 후보'이고, 그건 전략 설계상
  틀린 판단이 아니다 - 다만 사람이 그 맥락을 보고 판단할 기회는 있어야 한다.

무엇을 하지 않는가
  **이 값은 매매 결정에 들어가지 않는다.** 뉴스를 점수로 바꾸려면 그 점수가
  수익을 예측한다는 것을 먼저 보여야 하는데, 그런 검증을 하지 않았다.
  검증 없이 결정에 꽂으면 잡음을 신호라고 부르는 것이다. 지금은 **보여주기만**
  한다. 판단은 사람이 한다.

출처는 Google 뉴스 RSS 다. 열쇠가 필요 없고 국내 매체를 폭넓게 긁는다.
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

RSS = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
TIMEOUT = 12

#: 제목에 있으면 눈에 띄게 표시할 말. 방향을 말하지 않는다 - **주의 신호**일 뿐이다.
FLAGS = ("급등", "급락", "상한가", "하한가", "테마", "관리종목", "거래정지",
         "유상증자", "무상증자", "감자", "횡령", "배임", "불성실공시",
         "상장폐지", "조회공시", "투자경고", "투자주의", "과열")


def _fetch(query: str) -> str:
    url = RSS.format(q=urllib.parse.quote(query))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def headlines(name: str, limit: int = 5) -> list[dict]:
    """한 종목의 최근 기사 제목. 실패하면 빈 목록 — 절대 예외를 올리지 않는다."""
    try:
        x = _fetch(f"{name} 주가")
    except (urllib.error.URLError, OSError, ValueError):
        return []
    out = []
    for block in re.findall(r"<item>(.*?)</item>", x, re.S)[:limit * 4]:
        t = re.search(r"<title>(.*?)</title>", block, re.S)
        d = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
        l = re.search(r"<link>(.*?)</link>", block, re.S)
        if not t:
            continue
        title = html.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip()
        # 제목에 종목명이 없으면 버린다.
        #
        # 검색어가 "이름 주가" 라 이름이 흔한 말이면 남의 기사가 섞인다. 실제로
        # 한 종목의 자리에 구글 알파벳 주가 기사가 들어왔다. 엉뚱한 기사는 없는
        # 것보다 나쁘다 - 사람이 그걸 보고 판단하기 때문이다.
        if name not in title:
            continue
        out.append({
            "title": title,
            # 전체 pubDate 를 둔다. 16자로 자르면 시각·시간대가 사라져 날짜를 못 읽고,
            # 못 읽으면 최근성 판정에서 그 기사가 통째로 빠진다. 표시는 보는 쪽이 자른다.
            "when": (d.group(1).strip() if d else ""),
            "url": (l.group(1).strip() if l else ""),
            "flags": [f for f in FLAGS if f in title],
        })
        if len(out) >= limit:
            break
    return out


def collect(names: list[str], per: int = 4) -> dict:
    """여러 종목을 한 번에. 이름이 짧으면 엉뚱한 기사가 섞이니 그대로 보여준다."""
    got, failed = {}, []
    for n in names:
        if not n:
            continue
        h = headlines(n, per)
        if h:
            got[n] = h
        else:
            failed.append(n)
    return {"at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "by_name": got, "failed": failed}
