# -*- coding: utf-8 -*-
"""비교할 행동 후보 생성 (명세 6절).

후보는 **목표 이름이 아니라 구체적인 목표 비중**으로 정의한다. 시뮬레이션에서
2종목 50%씩으로 평가하고 실제로는 45%씩 주문하면 같은 승률을 인용할 수 없다.

첫 버전의 고정 목록이다. 승률을 본 뒤 후보를 즉흥적으로 추가하지 않는다 —
그러면 선택용 자료로 후보를 고른 것이 되어 비교가 오염된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Action:
    action_id: str
    #: code -> 목표 비중(평가금액 대비 비율)
    weights: dict[str, float]
    #: 자격 확보 거래를 붙였는가. NONE | PLANNED
    qualification_mode: str = "NONE"
    #: 원금 대비 일일 왕복 금액(비율). PLANNED 일 때만 > 0
    farm_per_day: float = 0.0
    note: str = ""
    feasible: bool = True
    exclusion_reasons: list[str] = field(default_factory=list)


def _weights(codes: list[str], n: int, exposure: float, cap: float) -> dict[str, float]:
    """상위 n 종목에 균등 배분하되 종목당 상한을 지킨다.

    상한 때문에 목표 노출을 못 채우면 **남는 것은 현금으로 둔다.** 보이지 않게
    n+1 번째 종목을 끼워 넣지 않는다(명세 6.1). 차순위 편입은 별도 후보다.
    """
    take = codes[:n]
    if not take:
        return {}
    w = min(exposure / len(take), cap)
    return {c: w for c in take}


def generate_actions(base_codes: list[str], vol_codes: list[str],
                     held: dict[str, float], cap: float,
                     exposure: float = 0.90,
                     turnover_deficit: float = 0.0,
                     days_left: int = 1,
                     farm_available: bool = False) -> list[Action]:
    """고정된 후보 목록을 만든다.

    base_codes: 기본 신호 상위 순서. vol_codes: 변동성 가중치만 올린 신호 상위 순서.
    held:       현재 보유 비중(평가금액 대비). HOLD/REDUCE 의 출발점.
    cap:        종목당 상한(평가금액 대비 비율로 환산된 값).
    """
    hold = Action("HOLD", dict(held), note="현재 주수 유지. 신규 주문 0건")
    # 가격이 올라 보유 비중이 종목당 상한을 넘을 수 있다. HOLD 는 새로 사지 않으므로
    # "편입" 기준이면 위반이 아니고 "보유" 기준이면 위반이다 — 규정 원문이 양쪽으로
    # 읽혀 아직 미확정이다. 자동으로 유리한 해석을 고르지 않고 표시만 한다.
    over = [c for c, w in held.items() if w > cap + 1e-9]
    if over:
        hold.note += f"  (보유 비중이 상한을 넘는 종목: {', '.join(over)} — 규정 해석 미확정)"

    acts: list[Action] = [
        hold,
        Action("BASE", _weights(base_codes, 2, exposure, cap),
               note="기준 정책 baseline_v1 의 오늘 행동"),
        Action("CONCENTRATE_BASE", _weights(base_codes, 2, exposure, cap),
               note="기본 신호 상위 2"),
        Action("CONCENTRATE_HIGH_VOL", _weights(vol_codes, 2, exposure, cap),
               note="변동성 가중치 1.0 신호 상위 2"),
        Action("DIVERSIFY_3", _weights(base_codes, 3, exposure, cap)),
        Action("DIVERSIFY_4", _weights(base_codes, 4, exposure, cap)),
        Action("REDUCE_60", {c: w * (0.60 / exposure) for c, w in
                             _weights(base_codes, 2, exposure, cap).items()},
               note="주식 노출 약 60%. 매도만 발생"),
        Action("REDUCE_30", {c: w * (0.30 / exposure) for c, w in
                             _weights(base_codes, 2, exposure, cap).items()},
               note="주식 노출 약 30%"),
        Action("CASH", {}, note="검증된 보유를 청산. 보충 매수는 붙이지 않는다"),
    ]

    # QUALIFY_MIN: 보유를 유지하면서 부족분만큼의 자격 확보 거래를 명시적으로 붙인다.
    #
    # 다른 후보에 몰래 붙이지 않는다(명세 5.2). 붙인 후보만 PLANNED 로 표시한다.
    if turnover_deficit > 0 and days_left > 0:
        per_day = turnover_deficit / max(days_left, 1) / 2.0   # 왕복이라 절반씩
        q = Action("QUALIFY_MIN", _weights(base_codes, 2, exposure, cap),
                   qualification_mode="PLANNED", farm_per_day=per_day,
                   note=f"회전율 부족분 {turnover_deficit:.0%} 를 {days_left}일에 분산")
        if not farm_available:
            # 공식 산입이 확인되지 않은 상품으로 자격을 채운다고 가정하지 않는다.
            q.feasible = False
            q.exclusion_reasons.append("UNCONFIRMED_QUALIFICATION_RULE")
        acts.append(q)

    # 실제 주문이 같아지는 후보는 합친다.
    seen, out = {}, []
    for a in acts:
        key = (tuple(sorted((c, round(w, 6)) for c, w in a.weights.items())),
               a.qualification_mode, round(a.farm_per_day, 6))
        if key in seen:
            seen[key].note += f" (= {a.action_id})"
            continue
        seen[key] = a
        out.append(a)
    return out
