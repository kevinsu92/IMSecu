# -*- coding: utf-8 -*-
"""전문가 제안을 결정 엔진의 후보로 올린다.

"전문가 의견이 즉각 매매에 반영되는 것" 의 유일하게 옳은 형태다.
  의견은 주문이 아니다. 의견은 **후보**다. 후보는 같은 경로 위에서 기준선과 나란히
  시뮬레이션되고, 확인용 경로에서 우승 확률 하한이 0 을 넘고 기준선보다 min_delta
  이상 나을 때만 선택된다. 그 관문(imrl/decision.py)은 이미 있다. 여기서는 제안을
  그 관문 앞에 세울 뿐이다.

  이게 전문가들 스스로 세운 원칙이다 — "7명 중 7명이 동의해도 데이터 검증 전에는
  hypothesis 다." 검증을 건너뛰고 주문으로 가는 통로는 만들지 않는다.

제안 파일: research/proposals/*.json
  {
    "id": "PROP-20260908-01",
    "source": "06_risk",                 # 전문가 폴더명 또는 "human"
    "hypothesis": "RSK-H003",            # 선택
    "weights": {"003010": 0.45, "092870": 0.45},
    "farm_per_day": 0.0,                 # 원금 대비 일일 왕복 비율 (선택)
    "qualification_mode": "NONE",        # NONE | PLANNED
    "note": "왜 이 배분인가",
    "expires": "20260910"                # 이 날짜 지나면 무시
  }

실행 불가능한 제안도 버리지 않는다. exclusion_reasons 를 달아 비교표에 남긴다 —
제안한 쪽이 왜 안 됐는지 봐야 다음 제안이 나아진다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .policies import Action

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "research" / "proposals"
MAX_EXPOSURE = 0.98


def _load_raw() -> list[tuple[Path, dict]]:
    out = []
    if not DIR.exists():
        return out
    for p in sorted(DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                out.append((p, d))
        except (OSError, ValueError):
            continue
    return out


def load_actions(universe_codes: set[str], cap_w: float,
                 today: date | None = None) -> list[Action]:
    """제안 파일을 Action 으로. 문제가 있으면 feasible=False 에 이유를 단다."""
    today = today or date.today()
    acts: list[Action] = []
    for path, d in _load_raw():
        pid = str(d.get("id") or path.stem)
        reasons: list[str] = []

        exp = str(d.get("expires", "") or "")
        if exp and exp < f"{today:%Y%m%d}":
            continue                        # 만료. 조용히 넘긴다.

        w_raw = d.get("weights") or {}
        weights: dict[str, float] = {}
        if not isinstance(w_raw, dict) or not w_raw:
            reasons.append("weights 없음")
        else:
            for code, w in w_raw.items():
                code = str(code).strip()
                try:
                    w = float(w)
                except (TypeError, ValueError):
                    reasons.append(f"{code} 비중이 숫자가 아님"); continue
                if w < 0:
                    reasons.append(f"{code} 음수 비중"); continue
                if code not in universe_codes:
                    reasons.append(f"{code} 유니버스 밖(매매제한·유동성 필터)"); continue
                if w > cap_w + 1e-9:
                    reasons.append(f"{code} 비중 {w:.2f} > 상한 {cap_w:.2f}(원금 50% 규정)")
                    w = cap_w
                weights[code] = w
            if sum(weights.values()) > MAX_EXPOSURE + 1e-9:
                reasons.append(f"총노출 {sum(weights.values()):.2f} > {MAX_EXPOSURE}")

        mode = str(d.get("qualification_mode", "NONE") or "NONE").upper()
        if mode not in ("NONE", "PLANNED"):
            reasons.append(f"qualification_mode {mode!r} 불명"); mode = "NONE"
        try:
            farm = max(0.0, float(d.get("farm_per_day", 0.0) or 0.0))
        except (TypeError, ValueError):
            farm = 0.0; reasons.append("farm_per_day 숫자 아님")

        acts.append(Action(
            action_id=f"PROP:{pid}",
            weights=weights,
            qualification_mode=mode,
            farm_per_day=farm,
            note=f"[{d.get('source', '?')}] {d.get('note', '')}".strip(),
            feasible=not reasons and bool(weights),
            exclusion_reasons=reasons or ([] if weights else ["비중 없음"]),
        ))
    return acts


def summary(today: date | None = None) -> list[dict]:
    """상황판용. 파일 그대로 + 만료 여부."""
    today = today or date.today()
    rows = []
    for path, d in _load_raw():
        exp = str(d.get("expires", "") or "")
        rows.append({
            "id": str(d.get("id") or path.stem),
            "source": d.get("source", "?"),
            "hypothesis": d.get("hypothesis", ""),
            "weights": d.get("weights", {}),
            "note": d.get("note", ""),
            "expires": exp,
            "expired": bool(exp and exp < f"{today:%Y%m%d}"),
        })
    return rows
