# -*- coding: utf-8 -*-
"""HTS 세션이 얼마나 지속되는지 판정한다.

답하려는 질문 하나
  아침 로그인이 **매일** 필요한가, **재부팅할 때만** 필요한가.

  HTS 의 IdleTimeout 은 0 이고 Windows 업데이트도 대회 기간 동안 멈춰 있다.
  세션이 밤을 넘긴다면 사람이 하는 일이 20일에 한두 번으로 줄어든다. 그건
  추측할 게 아니라 관측하면 아는 것이고, 헬스체크가 매시간 돌면서 공짜로 쌓는다.

  부팅 시각을 함께 기록하는 것이 핵심이다. 그게 없으면 "세션이 만료됐다" 와
  "재부팅했다" 를 구분할 수 없다.

판정을 사람이 돌려봐야 나오면 아무도 안 본다. 그래서 16:00 리포트가 매일
현재 판정을 싣고, 결론이 처음 확정되는 날 한 번 알린다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "state" / "session_log.jsonl"
#: 판정에 필요한 최소 기록 수. 하루 7회씩 쌓이므로 이틀치다.
MIN_RECORDS = 14


@dataclass
class Verdict:
    determined: bool
    #: per_boot | per_day | unknown
    kind: str
    text: str
    records: int
    boots: int
    drops: int
    longest_hours: float


def _start_date() -> str:
    """대회 시작일. 읽지 못하면 빈 문자열 — 그때는 거르지 않는다."""
    try:
        cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        return str(cfg.get("contest", {}).get("start_date", ""))
    except Exception:
        return ""


def _rows() -> list[dict]:
    """대회 기간의 기록만.

    대회 전에는 HTS 를 켰다 껐다 한다. 그 껐다가 `true -> false` 로 남아
    "세션이 끊겼다" 로 읽히면, 실제로는 사람이 창을 닫은 것뿐인데 판정이
    "매일 로그인이 필요하다" 로 뒤집힌다. 답하려는 질문은 **대회 기간에**
    한 번의 로그인이 며칠 가느냐이므로, 그 전 기록은 세지 않는다.
    """
    if not LOG.exists():
        return []
    start = _start_date()
    out = []
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if start and str(r.get("at", ""))[:10] < start:
            continue
        out.append(r)
    return out


def verdict() -> Verdict:
    rows = _rows()
    if not rows:
        return Verdict(False, "unknown", "세션 기록이 아직 없다.", 0, 0, 0, 0.0)

    boots = {r.get("boot", "?") for r in rows}
    # 같은 부팅 안에서 로그인이 true -> false 로 바뀐 횟수.
    # 부팅이 바뀐 지점은 재부팅이므로 세지 않는다.
    drops = sum(1 for a, b in zip(rows, rows[1:])
                if a.get("boot") == b.get("boot")
                and a.get("logged_in") and not b.get("logged_in"))

    longest = 0.0
    for boot in boots:
        ins = [r for r in rows if r.get("boot") == boot and r.get("logged_in")]
        if len(ins) < 2:
            continue
        try:
            span = (datetime.fromisoformat(ins[-1]["at"])
                    - datetime.fromisoformat(ins[0]["at"])).total_seconds() / 3600
            longest = max(longest, span)
        except Exception:
            pass

    n = len(rows)
    if n < MIN_RECORDS:
        return Verdict(False, "unknown",
                       f"기록 {n}건 — 판정에 {MIN_RECORDS}건이 필요하다.",
                       n, len(boots), drops, longest)
    if drops == 0:
        return Verdict(True, "per_boot",
                       f"같은 부팅 안에서 세션이 끊긴 적이 없다 "
                       f"(최장 {longest:.1f}시간 유지). "
                       "로그인은 재부팅할 때만 하면 된다.",
                       n, len(boots), drops, longest)
    return Verdict(True, "per_day",
                   f"같은 부팅 안에서 {drops}회 끊겼다 (최장 {longest:.1f}시간). "
                   "세션이 만료되므로 매일 로그인이 필요하다.",
                   n, len(boots), drops, longest)


def report() -> str:
    """사람이 읽을 전체 보고."""
    rows = _rows()
    v = verdict()
    L = ["=" * 62, " HTS 세션 지속 관측", "=" * 62, "",
         f" 기록 {v.records}건 / 부팅 {v.boots}회", ""]
    if rows:
        from collections import OrderedDict
        by: "OrderedDict[str, list]" = OrderedDict()
        for r in rows:
            by.setdefault(r.get("boot", "?"), []).append(r)
        for boot, rs in by.items():
            ins = [r for r in rs if r.get("logged_in")]
            if not ins:
                L.append(f"  부팅 {boot[:16]}  로그인 관측 없음 ({len(rs)}회 점검)")
                continue
            try:
                h = (datetime.fromisoformat(ins[-1]["at"])
                     - datetime.fromisoformat(ins[0]["at"])).total_seconds() / 3600
            except Exception:
                h = 0.0
            d = sum(1 for a, b in zip(rs, rs[1:])
                    if a.get("logged_in") and not b.get("logged_in"))
            L.append(f"  부팅 {boot[:16]}  로그인 유지 {h:5.1f}시간 "
                     f"({ins[0]['at'][5:16]} ~ {ins[-1]['at'][5:16]})  끊김 {d}회")
    L += ["", " 판정", "  " + v.text, "=" * 62]
    return "\n".join(L)


def mark_announced(kind: str) -> bool:
    """결론을 이미 알렸는가. 처음이면 표시하고 True 를 돌려준다.

    같은 결론을 매일 알리면 알림이 배경 소음이 된다. 한 번만 보낸다.
    """
    p = ROOT / "state" / "session_verdict.txt"
    try:
        if p.exists() and p.read_text(encoding="utf-8").strip() == kind:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(kind, encoding="utf-8")
        return True
    except OSError:
        return False
