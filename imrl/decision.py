# -*- coding: utf-8 -*-
"""후보 비교와 결정 기록 (명세 10·14절).

두 가지를 지킨다.

1. **같은 경로로 비교한다.** 후보 a 와 기준 b 를 같은 외생 충격에서 돌리고
   경로별 차이 D_i = Y_i(a) - Y_i(b) 의 평균을 본다. 후보마다 다른 난수를 쓰면
   차이의 대부분이 난수 오차가 된다.

2. **선택용과 확인용 경로를 나눈다.** 선택용으로 최선 후보를 고르고, 그 후보만
   확인용 경로에서 기준과 다시 비교한다. 확인용 결과가 나쁘다고 그 자료에서
   다음 후보를 고르면 확인용도 선택에 쓴 것이 되어 의미가 없다.

출력은 `scenario_only` 다. "당신의 우승확률은 12.37%" 같은 문장을 만들지 않는다.
가정한 모형과 후보 범위 안의 조건부 값이다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class CandidateResult:
    action_id: str
    feasible: bool
    exclusion_reasons: list[str]
    weights: dict[str, float]
    exposure: float
    qualification_mode: str
    #: 모형별 가정하 승률
    win_by_model: dict[str, float]
    aggregate_win: float
    worst_model_win: float
    qualification_rate: float
    mean_return: float
    p10_return: float
    mean_turnover: float
    mean_cost: int


@dataclass
class Decision:
    decision_at: str
    snapshot_id: str
    probability_semantics: str
    baseline_id: str
    continuation_policy_id: str
    selection_paths: int
    validation_paths: int
    master_seed: int
    candidates: list[CandidateResult]
    model_best_action: str
    execution_action: str
    paired_delta: float | None
    paired_se: float | None
    paired_lower_bound: float | None
    model_disagreement: bool
    execution_eligible: bool
    block_reasons: list[str] = field(default_factory=list)
    fallback_reason: str = ""
    assumptions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def aggregate(win_by_model: dict[str, np.ndarray], weights: dict[str, float] | None = None):
    """모형별 승률의 가중 평균과 최악 모형 값."""
    mids = sorted(win_by_model)
    w = weights or {m: 1.0 / len(mids) for m in mids}
    per = {m: float(win_by_model[m].mean()) for m in mids}
    agg = sum(w[m] * per[m] for m in mids)
    return per, agg, min(per.values())


def paired_delta(a_by_model: dict[str, np.ndarray], b_by_model: dict[str, np.ndarray],
                 weights: dict[str, float] | None = None, conf: float = 0.95):
    """층화 쌍별 차이와 그 표준오차, 보수적 하한.

    SE^2 = Σ w_m^2 · var(D_m) / n_m   (명세 10.2)

    희귀 승리에서 정규근사만 믿지 않는다. 표본이 퇴화했을 때 — 모든 경로의 차이가
    같은 값일 때 — 표본분산 0 은 참 불확실성 0 의 증거가 아니다. 그때는 SE 하한
    sqrt(0.25/n) 을 쓴다: 경로 하나가 달랐다면 생겼을 정도의 오차다. conf 는
    실제로 z 에 반영된다(예전에는 어떤 값을 줘도 1.96 이었다).
    """
    from statistics import NormalDist
    mids = sorted(a_by_model)
    w = weights or {m: 1.0 / len(mids) for m in mids}
    delta, var = 0.0, 0.0
    n_total = 0
    for m in mids:
        d = a_by_model[m].astype(float) - b_by_model[m].astype(float)
        n = len(d)
        n_total += n
        delta += w[m] * float(d.mean())
        if n > 1:
            var += (w[m] ** 2) * float(d.var(ddof=1)) / n
    if var > 0:
        se = math.sqrt(var)
    elif n_total > 0:
        se = math.sqrt(0.25 / n_total)
    else:
        se = 0.0
    conf = min(max(float(conf), 0.5), 0.9999)
    z = NormalDist().inv_cdf(0.5 + conf / 2.0)
    lower = delta - z * se
    return delta, se, lower


def choose(candidates: list[CandidateResult], baseline_id: str,
           delta: float | None, se: float | None, lower: float | None,
           min_delta: float, state_ok: bool,
           block_reasons: list[str]) -> tuple[str, bool, str]:
    """실행 행동을 고른다.

    개선이 확인되지 않으면 **기준 정책을 유지한다.** 추정 승률이 조금 높다는
    이유로 매일 후보를 갈아타면 비용만 늘고 추정 오차를 쫓는 것이 된다.
    """
    feas = [c for c in candidates if c.feasible]
    if not feas:
        return "NO_NEW_ORDERS", False, "실행 가능한 후보가 없다"
    best = max(feas, key=lambda c: c.aggregate_win)
    if not state_ok:
        return baseline_id, False, "계좌·주문 상태가 확정되지 않아 기준 정책을 유지한다"
    if best.action_id == baseline_id:
        return baseline_id, True, ""
    if lower is None or lower <= 0 or (delta or 0) < min_delta:
        return (baseline_id, True,
                f"개선이 확인되지 않았다 (차이 {(delta or 0):+.4f}, "
                f"하한 {(lower or 0):+.4f}, 기준 {min_delta:+.4f})")
    return best.action_id, True, ""


# --------------------------------------------------------------------------- #
# 실행 게이트 (명세 13절 execution, 18절 G4)
# --------------------------------------------------------------------------- #

@dataclass
class GateInput:
    """게이트가 보는 사실들. 전부 **확인된 것**이어야 한다."""
    mode: str                      # shadow | advisory | execute
    reconciled: bool               # 계좌·체결 원장이 대조됐는가
    unresolved_orders: list[str]   # 접수 불명·pending 인 주문 키
    unconfirmed_rules: list[str]   # 아직 확인 안 된 규정 id
    validation_ran: bool           # 확인용 경로 비교를 실제로 돌렸는가
    state_hash_now: str
    state_hash_at_decision: str
    action_uses_unconfirmed_rule: bool


def execution_gate(g: GateInput) -> tuple[bool, list[str]]:
    """결정을 실제 주문으로 흘려보내도 되는가.

    하나라도 걸리면 **신규 주문 없음**이 아니라 **기준 정책 유지**로 떨어진다.
    기준 정책은 이미 검증된 경로이기 때문이다. 다만 계좌·주문 상태가 불명이면
    기준 정책도 못 돌린다 — 그때는 신규 주문 자체를 막는다.

    플래그 하나로 전부 우회하는 구조를 만들지 않는다. 각 조건이 서로 다른
    사실을 본다.
    """
    reasons: list[str] = []
    if g.mode != "execute":
        reasons.append(f"MODE_{g.mode.upper()}")
    if not g.reconciled:
        reasons.append("ACCOUNT_NOT_RECONCILED")
    if g.unresolved_orders:
        reasons.append("UNRESOLVED_ORDERS:" + ",".join(g.unresolved_orders[:4]))
    if g.action_uses_unconfirmed_rule:
        reasons.append("ACTION_USES_UNCONFIRMED_RULE")
    if not g.validation_ran:
        reasons.append("NO_VALIDATION_MANIFEST")
    if g.state_hash_now != g.state_hash_at_decision:
        reasons.append("STATE_HASH_MISMATCH")
    return (not reasons), reasons


def gate_fallback(reasons: list[str]) -> str:
    """게이트가 막았을 때 무엇으로 떨어지는가."""
    hard = {"ACCOUNT_NOT_RECONCILED"} | {r for r in reasons
                                         if r.startswith("UNRESOLVED_ORDERS")}
    if hard & set(reasons):
        return "NO_NEW_ORDERS"
    return "BASELINE"
