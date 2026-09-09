# -*- coding: utf-8 -*-
"""대회 종료까지의 계좌 전개와 최종 보상 판정.

목적함수는 **자격을 충족한 최종 1등**이다(명세 2.1).

    Y_T = 1  if 내 자격 Q(T) 참 AND 자격 있는 참가자 중 최종 순위 1위
          0  otherwise

자격과 순위를 곱하지 않는다. 같은 경로에서 함께 판정한다 — 두 사건은 독립이
아니기 때문이다. 잘 오른 종목을 계속 들고 있으면 수익률은 좋아지지만 교체가
줄어 회전율이 모자란다. 곱셈으로 근사하면 그 상관이 사라진다.

회계 불변식 (2026-09-07 외부 검토로 고침)
  현금과 주수는 **같은 체결 사건**으로만 움직인다. 예전에는 현금에서 체결률만큼만
  빼고 주식은 목표 전량을 받은 것으로 계산해, 가격이 그대로여도 부분체결만으로
  +13.5% 가 생겼다(원금 1억, 2종목 45%, 체결률 85%). 지금은 체결된 주수만큼만
  현금·보유·비용이 함께 바뀌고, 미체결 매도 잔량은 계속 보유한다. 가격 고정·
  비용 0 이면 어떤 체결률에서도 총자산이 보존된다.

  시작 계좌의 누적 회전금액·매매일수·매매종목은 **보존**된다. 이미 500%·5일·
  5종목을 채운 계좌가 현금을 들고만 있어도 자격을 잃지 않는다.

이 시뮬레이터가 모델링하는 것과 하지 않는 것
  한다:   정수 주수 사이징(0일차), 수수료·제세금, 회전율·매매일수·매매종목수
          누적, 원금 기준 단일종목 상한, 체결률에 따른 미체결, 마이너스 실격,
          동률 시 회전율 우선.
  안 한다: 호가창·부분체결의 순차 전개, 장중 사건, 거래정지·상한가 지정,
          경쟁자의 내 순위에 대한 반응.
  1일차 이후 재조정은 `continuation_v1` — **0일차 목표 비중을 유지**하는 고정
  정책이다. 경로 안에서 알파를 다시 계산하지 않는다. 그러려면 전체 유니버스의
  미래 특징이 필요한데 그것은 생성하지 않았다. 이 한계는 결과에 표시한다.
  교체율로 유입되는 새 종목 수는 **배치 안에 있는 종목 수를 넘지 못한다** —
  없는 종목을 산 것으로 자격을 채우지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Rules:
    principal: int
    min_turnover: float          # 비율. 500% = 5.0
    min_trading_days: int
    min_distinct_symbols: int
    single_limit: float          # 원금 대비. 0.50
    cost_stock: float            # 왕복이 아니라 **편도** 비율
    cost_etf: float
    fill_rate: float             # 지정가 체결률 가정


@dataclass
class Account:
    cash: int
    #: code -> 주수
    shares: dict[str, int]
    turnover_amount: int         # 누적 매수+매도 체결금액
    trading_days: int
    symbols: frozenset


@dataclass
class Outcomes:
    """경로별 결과. 후보 비교는 이 배열들로 한다."""
    win: np.ndarray              # (P,) 0/1  자격 충족 AND 1위
    qualified: np.ndarray        # (P,) 0/1  참고값. 목적함수가 아니다
    final_return: np.ndarray     # (P,) 비율
    turnover: np.ndarray         # (P,) 비율
    traded_amount: np.ndarray    # (P,) 원
    cost: np.ndarray             # (P,) 원


def _cost_rate(code: str, etf_codes: frozenset, rules: Rules) -> float:
    return rules.cost_etf if code in etf_codes else rules.cost_stock


def _fill_qty(diff: int, f: float) -> int:
    """체결된 주수. 0 쪽으로 절사한다 — 미체결을 체결로 세지 않는다."""
    if diff >= 0:
        return int(diff * f)
    return -int((-diff) * f)


def simulate(action_weights: dict[str, float],
             start: Account, prices: dict[str, int],
             batch, rules: Rules, etf_codes: frozenset,
             my_return_now: float, field_return_now: np.ndarray | None,
             farm_per_day: float = 0.0,
             rebalance_band: float = 0.02,
             daily_replacement: float = 0.0,
             universe_n: int | None = None) -> Outcomes:
    """한 후보 행동을 경로 묶음 전체에서 전개한다.

    action_weights: code -> 목표 비중(평가금액 대비 비율). 합이 1 이하.
    my_return_now:  관측시점까지의 내 수익률(비율).
    field_return_now: (n_field,) 관측시점까지의 경쟁자 수익률. None 이면 0.
    farm_per_day:   자격 확보용 일일 회전 금액(원금 대비 비율). QUALIFY_MIN 만 > 0.
    daily_replacement: 하루에 교체되는 보유 비율. 실제 전략은 매일 점수를 다시 매겨
        상위 N 이 바뀌면 종목을 갈아탄다. 경로 안에서 알파를 재계산하지 않으므로
        그 교체를 **실측 교체율**로 대신 넣는다(top2 일평균 24.5%). 이것이 없으면
        회전율이 드리프트 재조정분만 남아 실제의 1/10 수준이 되고, 모든 후보가
        자격 미달로 떨어져 승률이 전부 0 이 된다. 노출이 0 인 후보(CASH)는 교체할
        것이 없으므로 자동으로 0 이 된다.
    universe_n: 실제로 살 수 있는 종목 수(유니버스 크기). 교체로 유입되는 새 종목의
        상한이다. 주지 않으면 배치 안의 종목 수를 상한으로 쓴다 — 합성 시험처럼
        종목이 둘뿐인 세계에서 다섯 종목을 발명하지 않는다.
    """
    codes = batch.codes
    P, D, C = batch.returns.shape
    principal = rules.principal
    f = float(rules.fill_rate)

    equity0 = start.cash + sum(q * prices[c] for c, q in start.shares.items() if c in prices)

    # --- 0일차: 정수 주수. 체결된 주수만큼만 현금·보유·비용이 함께 움직인다 ------ #
    shares: dict[str, int] = {c: int(q) for c, q in start.shares.items()}
    plan: list[tuple[str, int]] = []                  # (code, 체결 주수. 음수 = 매도)
    for c in codes:
        px = prices.get(c, 0)
        if px <= 0:
            continue
        want_amt = equity0 * action_weights.get(c, 0.0)
        # 원금 기준 단일종목 상한. 평가금액이 아니라 원금이 기준이다.
        want_amt = min(want_amt, rules.single_limit * principal)
        want_q = int(want_amt // px)
        fq = _fill_qty(want_q - shares.get(c, 0), f)
        if fq:
            plan.append((c, fq))
    # 목표에 없는 보유는 전량 매도를 시도한다 (체결률만큼만 팔린다)
    for c, q in start.shares.items():
        if c in codes or q <= 0 or c not in prices:
            continue
        fq = _fill_qty(-q, f)
        if fq:
            plan.append((c, fq))

    cash = int(start.cash)
    buy_f = sell_f = cost0 = 0
    traded_codes: set[str] = set()
    # 매도가 먼저(15:10), 매수가 나중(15:21). 매도 대금은 같은 날 쓴다.
    for c, fq in plan:
        if fq >= 0:
            continue
        amt = -fq * prices[c]
        fee = int(amt * _cost_rate(c, etf_codes, rules))
        cash += amt - fee
        sell_f += amt
        cost0 += fee
        shares[c] = shares.get(c, 0) + fq
        traded_codes.add(c)
    # 현금이 모자라면 매수를 줄인다 — 신용은 없다.
    for c, fq in plan:
        if fq <= 0:
            continue
        px = prices[c]
        rate = _cost_rate(c, etf_codes, rules)
        afford = int(cash // (px * (1.0 + rate))) if cash > 0 else 0
        q = min(fq, afford)
        if q <= 0:
            continue
        amt = q * px
        fee = int(amt * rate)
        cash -= amt + fee
        buy_f += amt
        cost0 += fee
        shares[c] = shares.get(c, 0) + q
        traded_codes.add(c)

    cash0 = cash
    invested0 = sum(q * prices[c] for c, q in shares.items() if c in prices and q > 0)
    eq0 = cash0 + invested0
    if eq0 <= 0:
        eq0 = max(equity0, 1)
    w_actual = np.array([shares.get(c, 0) * prices.get(c, 0) / eq0 for c in codes], dtype=float)
    traded0 = buy_f + sell_f

    # --- 1일차 이후: 목표 비중 유지 (continuation_v1) ------------------------- #
    eq = np.full(P, float(eq0))
    wt = np.tile(w_actual, (P, 1))
    traded = np.full(P, float(start.turnover_amount + traded0))
    cost = np.full(P, float(cost0))
    # 편도 비용의 가중평균. 종목별로 다르지만 재조정 단계에서는 대표값을 쓴다.
    rate = np.array([_cost_rate(c, etf_codes, rules) for c in codes], dtype=float)
    rate_mean = float(rate.mean()) if len(rate) else rules.cost_stock

    for d in range(D):
        r = batch.returns[:, d, :]                       # (P, C)
        grow = 1.0 + (wt * r).sum(axis=1)                # 현금은 0%
        eq = eq * grow
        # 드리프트 후 비중
        denom = np.where(grow == 0, 1.0, grow)
        wt = wt * (1.0 + r) / denom[:, None]
        # 밴드를 넘은 종목만 되돌린다. 매일 전량 재조정하면 비용이 비현실적으로 커진다.
        # 체결률만큼만 되돌아온다 — 거래금액·비용·비중이 같은 사건이다.
        drift = wt - w_actual
        need = np.abs(drift) > rebalance_band
        move = np.where(need, drift * f, 0.0)            # (P, C)
        amt = np.abs(move) * eq[:, None]
        traded += amt.sum(axis=1)
        fee = (amt * rate).sum(axis=1)
        cost += fee
        eq -= fee
        wt = wt - move
        # 종목 교체. 판 만큼 다시 사므로 회전율에 2배로 기여한다.
        if daily_replacement > 0:
            exposure = wt.sum(axis=1)
            ra = daily_replacement * exposure * eq * f
            traded += 2.0 * ra
            fee = 2.0 * ra * rate_mean
            eq -= fee
            cost += fee

        # 자격 확보용 회전(있으면 매일 같은 금액을 왕복)
        if farm_per_day > 0:
            fa = farm_per_day * principal * f
            traded += 2.0 * fa
            fee = 2.0 * fa * rules.cost_etf
            cost += fee
            eq -= fee

    # --- 최종 판정 ----------------------------------------------------------- #
    final_mult = eq / max(equity0, 1)
    my_final = (1.0 + my_return_now) * final_mult - 1.0

    turnover = traded / principal
    traded_today = traded0 > 0 or farm_per_day > 0
    held_n = sum(1 for c in codes if shares.get(c, 0) > 0 and action_weights.get(c, 0.0) > 0)
    # 교체는 보유가 있어야 일어난다. 현금 후보는 교체할 것이 없다.
    keeps_trading = (daily_replacement > 0 and held_n > 0) or farm_per_day > 0
    days = start.trading_days + (D if keeps_trading else (1 if traded_today else 0))

    # 매매 종목수.
    #
    # 시작 시점의 종목 집합 + 오늘 실제 체결된 종목. 실제 전략은 매일 점수를 다시
    # 매겨 상위 N 이 바뀌면 갈아타므로 교체로 들어오는 새 종목을 근사해 더하되,
    # **배치 안에 있는 종목**을 넘지 못한다 — 없는 종목을 산 것으로 치지 않는다.
    syms_set = set(start.symbols) | traded_codes
    syms = len(syms_set)
    if daily_replacement > 0 and held_n:
        avail = int(universe_n) if universe_n else len(codes)
        pool = max(0, avail - len(syms_set))
        syms += min(int(daily_replacement * held_n * D), pool)
    if farm_per_day > 0:
        syms += len(set(etf_codes) - syms_set)

    qualified = ((turnover >= rules.min_turnover)
                 & (days >= rules.min_trading_days)
                 & (syms >= rules.min_distinct_symbols)
                 & (my_final > 0.0))                     # 마이너스는 수상 제외

    n_comp = batch.field_multiplier.shape[1]
    if field_return_now is None:
        base_field = np.zeros(n_comp)
    else:
        fr = np.asarray(field_return_now, dtype=float).ravel()
        # 관측 길이가 다르면 앞에서부터 채우고 나머지는 0. 차원 오류로 죽지 않는다.
        base_field = np.zeros(n_comp)
        k = min(len(fr), n_comp)
        base_field[:k] = fr[:k]
    field_final = (1.0 + base_field)[None, :] * batch.field_multiplier - 1.0
    best_other = field_final.max(axis=1) if n_comp else np.full(P, -np.inf)

    # 동률은 회전율 우선이라 우리가 유리한 쪽으로 가정하지 않는다 — 엄격한 우위만 승리.
    win = (qualified & (my_final > best_other)).astype(np.int8)

    return Outcomes(win=win, qualified=qualified.astype(np.int8),
                    final_return=my_final, turnover=turnover,
                    traded_amount=traded, cost=cost)
