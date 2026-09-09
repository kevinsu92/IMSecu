"""목표 포트폴리오 구성과 주문 생성.

핵심 설계 판단
  - 규정상 단일 종목 상한은 50% 지만, 시스템 하드 캡은 그보다 낮게 둔다.
    잔고 평가 시점 차이로 규정 상한을 넘기면 실격이므로 여유를 남긴다.
  - '매매종목 5종목 이상' 은 대회 기간 누적 기준이므로 동시 보유 종목 수와 무관하다.
    따라서 막판에는 2종목 집중이 가능하다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import pandas as pd

from .data import round_to_tick

# 상한가 근처. 국내 주식 상한가는 +30% 이고, 근처에서 이미 체결이 어렵다.
UPPER_LIMIT_PCT = 29.0


@dataclass
class Order:
    side: str          # "BUY" | "SELL"
    code: str
    name: str
    market: str
    qty: int
    limit_price: int
    reason: str

    @property
    def amount(self) -> int:
        return self.qty * self.limit_price

    def to_dict(self) -> dict:
        d = asdict(self)
        d["amount"] = self.amount
        return d


def defend_threshold_pct(days_left: int, n_participants: int = 300,
                         field_sigma_20d: float = 12.0) -> float:
    """'굳히기'가 정당화되는 최소 리드(%p).

    현재 1위라는 사실은 우승을 뜻하지 않는다. 최종 순위를 정하는 것은 마지막 날
    **전 참가자 수익률의 최댓값**이다. 참가자가 N명이면 그 최댓값은 평균보다
    대략 k*sigma 위에서 나오고, N=300 이면 k는 2.5~3.0 수준이다.

    즉 리드가 그 정도를 넘지 못하면, 현금을 쥐고 있어도 누군가가 뒤에서 뛰어넘는다.
    그때는 방어가 아니라 계속 공격하는 편이 1등 확률이 높다.

    5명짜리 대회의 직관(1위면 지킨다)을 수백 명 대회에 그대로 적용하면 틀린다.
    """
    import math

    k = 2.5 if n_participants < 500 else 3.0
    horizon = max(days_left, 1) / 20.0
    return k * field_sigma_20d * math.sqrt(horizon)


def _load_field_obs() -> dict | None:
    """최근 필드 관측. state 를 순환 import 하지 않도록 지연 로드한다."""
    try:
        from .state import load_field
        return load_field()
    except Exception:
        return None


def choose_phase(cfg: dict, days_left: int, my_return_pct: float, gap_to_leader_pct: float | None) -> str:
    """잔여일수·현재 수익률·1위와의 격차로 운용 국면을 정한다.

    gap_to_leader_pct 가 None 이면(리더보드 미확보) 보수적으로 표준 운용한다.
    """
    p = cfg["portfolio"]
    if days_left > p["final_phase_days_left"]:
        return "phase_early"

    # 마이너스 판정을 1위 판정보다 **먼저**, 그리고 리더보드 유무보다도 먼저 한다.
    #
    # 하락장에서 전 참가자가 마이너스면 "1위이면서 마이너스"가 나온다. 그때 상금은
    # 0원이므로 지킬 것이 없는데, 순서를 반대로 두면 방어로 빠져 회복 기회를 버린다.
    #
    # 리더보드보다 먼저 두는 이유: 마이너스면 **1위와의 격차와 무관하게** 수상
    # 대상에서 아예 제외된다. 남들이 뭘 하는지 몰라도 확실히 아는 사실이므로,
    # 중계실 수치를 못 읽었다는 이유로 이 판단을 미룰 이유가 없다. 예전에는
    # 리더보드 미확보 시 phase_early 로 빠져, 잔여 5일에 −3% 인 상황에서도
    # 표준 운용을 계속했다.
    if my_return_pct < 0:
        return "phase_final_attack"

    if gap_to_leader_pct is None:
        # 리더보드를 모른다. 예전에는 무조건 phase_early 로 빠졌는데, 그것이
        # **사람의 저녁 수동 입력 하나에 국면 전체를 걸어 놓는** 구조였다.
        # 중계실 입력을 하루 빠뜨리면 잔여 3일에 +5% 인 상황에서도 조용히
        # 표준 운용으로 돌고, 실패가 눈에 보이지도 않는다.
        #
        # 잔여 일수는 사람 입력과 무관하게 확실히 아는 값이다. 막판 구간이면
        # 리더보드를 몰라도 표준 운용은 틀린 선택이다 — 남은 며칠 안에 필드
        # 최댓값을 넘어야 하는데 표준 운용으로는 부족하다는 것이 push 국면의
        # 정의다. 그래서 막판에는 push 로 떨어뜨린다.
        if days_left <= int(p.get("final_phase_days_left", 7)):
            print("  경고: 리더보드 미확보 상태로 막판 구간에 진입했다 "
                  f"(잔여 {days_left}일). phase_final_push 로 진행한다. "
                  "중계실 수치를 입력하면 정확한 국면 판정이 가능하다.")
            return "phase_final_push"
        return "phase_early"

    if gap_to_leader_pct <= 0:
        # 내가 1위. 다만 '현재 1위'는 '이겼다'가 아니다 — 최종 순위를 정하는 것은
        # 마지막 날 전 참가자 수익률의 **최댓값**이다. 참가자가 수백 명이면 그
        # 최댓값은 현재 1위보다 훨씬 위에서 나온다. 따라서 리드가 충분히 클 때만
        # 방어가 의미를 갖는다. 그렇지 않으면 계속 공격하는 편이 낫다.
        # 관측된 필드 분산이 있으면 쓴다. 없으면 기본값(300명, 12%p).
        #
        # 예전에는 `defend_threshold_pct(days_left)` 로만 호출해 **관측값이 있어도
        # 무시했다.** `run.py field` 가 sigma_f 를 계산해 기록하는데 읽는 곳이
        # 없었다 — 측정에 소비자가 없는 상태였다.
        n_part, sig_f = 300, 12.0
        obs = _load_field_obs()
        if obs:
            n_part = int(obs.get("n_field", n_part))
            sig_f = float(obs.get("sigma_f_pct", sig_f))

        # 방어 국면은 기본 비활성이다.
        #
        # 바로 위 주석의 논리("현재 1위 != 최종 1위, 최종 순위는 마지막 날 필드
        # 최댓값이 정한다")를 끝까지 밀면 **방어는 존재해선 안 된다.** 리드를 지키는
        # 행동은 필드 최댓값이 나를 넘을 확률을 줄이지 못하고 내 우측 꼬리만 자른다.
        # 게다가 이 국면 전환 로직 자체가 백테스트에서 한 번도 실행된 적이 없다 —
        # 검증되지 않은 로직이 검증된 논리를 뒤집는 구조였다.
        #
        # config.portfolio.defend_enabled = true 로 되돌리면 다시 켜진다.
        if not p.get("defend_enabled", False):
            return "phase_final_push"

        if -gap_to_leader_pct >= defend_threshold_pct(days_left, n_part, sig_f):
            return "phase_final_defend"
        return "phase_final_push"

    if gap_to_leader_pct > 10:
        return "phase_final_attack"
    # 남은 며칠 안에 좁혀야 하는 격차 — 표준 운용으로는 부족하다
    return "phase_final_push"


def build_targets(cfg: dict, scored: pd.DataFrame, phase: str,
                  equity: int | None = None, principal: int | None = None) -> pd.DataFrame:
    """상위 점수 종목에 동일 비중을 배분한다.

    규정은 "**투자원금**의 최대 50%"다. 평가금액 기준 비중으로 검사하면
    수익이 났을 때 규정을 넘긴다 — 예를 들어 수익률 +40%면 평가금액의 45%가
    원금의 63%다. 그리고 이 버그는 **이기고 있을 때만** 발동한다. 1등을 0원으로
    만드는 종류의 버그이므로, 금액 기준으로 다시 한 번 조인다.
    """
    p = cfg["portfolio"][phase]
    rule_cap = cfg["requirements"]["max_single_weight_rule"]
    # 규정 50% 아래 운용 여유. 매뉴얼은 "투자원금의 최대 50%까지 단일 종목 편입 가능"이라
    # 매수 금액 기준으로 읽히고, 여유는 호가 단위·지정가 오프셋 반올림용이다(2026-09-07 0.05 -> 설정).
    margin = float(cfg["requirements"].get("single_weight_margin", 0.05))
    if scored.empty:
        return scored.head(0).copy()

    investable = 1.0 - p["cash_buffer"]

    # 뒤져 있으면 노출을 키운다.
    #
    # 토너먼트에서 지고 있을 때 조심하는 것은 지는 방법을 고르는 것이다. 다만
    # 이건 감이 아니라 실측이다 - 참가 약 100명 기준 P10 이 -21.0%에서 -19.7%로,
    # 음수 마감이 35.4%에서 35.0%로 **같이** 좋아졌고 1등 확률은 그대로였다.
    # 원금 기준 규정 상한은 아래 cap_w 계산이 그대로 지킨다.
    ro = cfg.get("risk_options", {})
    ret_pct = None
    if equity and principal:
        ret_pct = (equity / principal - 1.0) * 100.0

    # 익절 — 목표에 닿으면 전량 현금으로 가고 남은 기간 쉰다.
    #
    # **기본은 꺼져 있다.** 1등은 오른쪽 꼬리에서만 나오는데 익절이 정확히 그
    # 꼬리를 자르기 때문이다. 실측(참가 약 100명, 필드 시그마 25%p): +20% 익절은
    # 입상 확률을 46.3% -> 50.8% 로 올리는 대신 1등 확률을 2.62% -> 0.00% 로
    # 없앤다. 켤지 말지는 '1등이냐 입상이냐'를 고르는 일이지 위험 조절이 아니다.
    #
    # 빈 목표를 돌려주면 make_orders 가 보유 전체를 매도로 낸다.
    # 1e-9 는 부동소수점 경계 때문이다. (1.2 - 1.0) * 100 은 19.999999999999996 이라
    # 정확히 문턱에 닿은 날 발동하지 않는다. 문턱에 닿으면 발동해야 맞다.
    if (ro.get("take_profit_enabled") and ret_pct is not None
            and ret_pct >= float(ro.get("take_profit_pct", 20.0)) - 1e-9):
        print(f"  익절 발동: 수익률 {ret_pct:+.1f}% >= "
              f"{float(ro.get('take_profit_pct', 20.0)):+.1f}% — 전량 현금")
        return scored.head(0).copy()
    recovering = (ro.get("recover_enabled") and ret_pct is not None
                  and ret_pct <= float(ro.get("recover_below_pct", -20.0)) + 1e-9)
    if recovering:
        # 증거금이 없으니 총노출은 100%를 넘을 수 없다. 이미 90% 투자에
        # 종목당 45% 상한이라 **늘릴 여지가 8%p 뿐이다** - 실측에서 이 옵션의
        # 효과가 잡음 수준인 이유가 그것이다(P10 -21.0% -> -20.2%). 그래도
        # 방향은 맞고 비용이 없어 켜 둔다. 큰 것을 기대할 값이 아니다.
        investable = min(investable * float(ro.get("recover_exposure_mult", 1.5)),
                         1.0 - p["cash_buffer"] * 0.5)

    # 종목당 비중 상한. 원금 기준 규정을 평가금액 기준 비중으로 환산한다.
    cap_w = min(p["max_weight"], rule_cap - margin)
    if recovering:
        # 종목당 상한도 같이 올린다. 안 그러면 investable 만 키워도 cap_w 가
        # 막아 아무 일도 안 일어난다. **규정 상한은 넘지 않는다** - 원금 기준
        # 50% 를 평가금액 비중으로 환산한 값이 진짜 천장이다.
        head = (rule_cap - margin) * (principal / equity) if (equity and principal) else cap_w
        cap_w = min(p["max_weight"] * float(ro.get("recover_exposure_mult", 1.5)), head)
    capped_by_rule = False
    if equity and principal and equity > principal:
        cap_w = min(cap_w, (rule_cap - margin) * principal / equity)
        capped_by_rule = cap_w < min(p["max_weight"], rule_cap - margin)

    # 여기가 이 함수의 핵심이다.
    #
    # 원금 기준 상한은 **평가금액이 커질수록 비중 상한을 끌어내린다.** 계좌가
    # 1.5억이면 4,500만원은 30% 이고, 2억이면 22.5% 다. top_n 을 2 로 고정한 채
    # 상한만 내리면 투자율이 90% -> 60% -> 45% 로 떨어지고, 풀린 자본이 갈 곳이
    # 없어 **현금으로 눕는다.** 하필 이 경로는 계좌가 크게 불어난 경로, 즉
    # 상금이 걸린 우측 꼬리 경로에서만 발동한다. 이기고 있을 때만 켜지는
    # 디레버리징이고, 복리를 끊는다.
    #
    # 규정은 **종목당** 상한이지 총노출 상한이 아니다. 그러므로 상한에 걸려
    # 풀린 비중은 차순위 종목으로 채우는 것이 맞다. 이미 종목당 상한 때문에
    # 집중을 못 하는 상태이므로, 남는 자본을 3~5위에 넣는 쪽이 현금으로 두는
    # 것보다 우측 꼬리가 두껍다. config 의 _weight_note 가 이미 같은 말을 한다 --
    # "토너먼트에서 놀리는 자본은 순수 손실이므로".
    #
    # 확장은 상한에 실제로 걸렸을 때만 일어난다. 평가금액이 원금 근처인
    # 평시에는 cap_w 가 0.45 그대로라 n_need = 3 이 되지만 top_n(2)보다 크므로
    # 확장하지 않는다 -- 2종목 x 45% = 90% 라는 기존 설계가 그대로 유지된다.
    n = int(p["top_n"])
    if capped_by_rule and cap_w > 0:
        n_need = math.ceil(investable / cap_w - 1e-9)
        n_max = int(cfg.get("portfolio", {}).get("max_positions_when_capped", 6))
        n = max(n, min(n_need, n_max))
    n = min(n, len(scored))

    top = scored.head(n).copy()
    if top.empty:
        return top

    w = min(investable / len(top), cap_w)
    top["target_weight"] = w

    if n > int(p["top_n"]):
        print(f"  원금 기준 상한이 걸려 종목당 {cap_w:.1%} 까지만 가능하다. "
              f"차순위를 승격해 {n}종목 x {w:.1%} = {n * w:.0%} 투자로 채운다 "
              f"(2종목 고정이면 {2 * cap_w:.0%} 만 투자되고 나머지는 현금).")
    return top


# 종가 동시호가 지정가 오프셋.
#
# 왜 현재가 ±1틱이 아닌가
#   실측(2026-08-28~09-04, 160종목 분봉 365,528행)에서 ±1틱 지정가의 종가 동시호가
#   체결률은 **매도 51.8% / 매수 62.1%** 였다. 회전율 계획 650%가 실제 337%로 떨어져
#   500% 수상 하드요건에 미달한다. 게다가 미체결은 무작위가 아니라 역선택이다 —
#   미체결 매수 종목은 이후 평균 +0.34% 오르고 미체결 매도 종목은 −0.39% 내린다.
#   강한 것을 못 사고 약한 것을 못 파는 방향으로만 실패한다.
#
# 왜 공격적 지정가가 공짜인가
#   종가 동시호가는 단일가 경매다. 참여 주문 전부가 **하나의 체결가**로 체결된다.
#   따라서 매수 지정가를 1% 높게 넣어도 그 가격에 사는 것이 아니라 경매 체결가에
#   산다. 지정가는 "체결 여부"만 결정하고 "체결 가격"은 결정하지 않는다.
#   즉 오프셋은 체결확률만 올리고 가격 손해가 없다. 시장가와 달리 상한가 폭주에는
#   여전히 보호가 걸린다.
AUCTION_OFFSET_PCT = 0.01

# 매도 오프셋은 매수와 **비대칭이어야 한다.**
#
# 매도는 15:10 연속매매에서 나간다. 연속매매에서 매도 지정가를 낮추면 체결가는
# 내 지정가가 아니라 **상대 최우선 매수호가**다. 즉 오프셋을 키워도 가격 손해가
# 없고 체결확률만 오른다 — 공짜 옵션이다.
#
# 반면 매수는 15:21 종가 동시호가라 체결가가 내 지정가가 될 가능성이 남아 있다
# (매뉴얼이 체결 가격을 명시하지 않는다). 그래서 매수만 1% 로 제한한다.
#
# 그리고 매도 미체결은 단순한 기회 손실이 아니다 — 15:21 매수 자금이 사라져
# **그날 리밸런싱 전체가 무산된다.** 실패 비용이 비대칭이므로 오프셋도 비대칭이다.
SELL_OFFSET_PCT = 0.03


def auction_limit(px: int, market: str, side: str, offset_pct: float,
                  change_pct: float | None = None) -> int:
    """종가 단일가 경매용 지정가를 만든다.

    change_pct 를 주면 전일종가를 역산해 가격제한폭(±30%) 안쪽으로 자른다.
    제한폭을 넘는 주문은 접수 자체가 거부되므로 오프셋보다 우선한다.
    """
    factor = (1.0 + offset_pct) if side == "BUY" else (1.0 - offset_pct)
    want = px * factor

    if change_pct is not None:
        try:
            prev = px / (1.0 + float(change_pct) / 100.0)
            if prev > 0:
                want = min(want, prev * 1.30) if side == "BUY" else max(want, prev * 0.70)
        except (ZeroDivisionError, ValueError, TypeError):
            pass

    return round_to_tick(want, market, "up" if side == "BUY" else "down")


def make_orders(
    cfg: dict,
    targets: pd.DataFrame,
    positions: dict[str, dict],
    equity: int,
    quotes: dict[str, dict],
) -> list[Order]:
    """목표 비중과 현재 보유를 비교해 주문 리스트를 만든다.

    positions: {code: {"name":..., "market":..., "qty": int}}
    quotes:    {code: {"price": int, ...}}
    """
    offset = float(cfg.get("execution", {}).get("auction_offset_pct", AUCTION_OFFSET_PCT))
    sell_off = float(cfg.get("execution", {}).get("sell_offset_pct", SELL_OFFSET_PCT))
    orders: list[Order] = []
    skipped_upper: list[tuple] = []
    target_codes = set(targets["code"])

    # 1) 목표에 없는 보유 종목 전량 매도.
    #
    #    회전율 파밍 ETF 는 제외한다. 그 종목들은 `add_turnover_orders` 가 따로
    #    매도 주문을 만든다. 둘 다 만들면 **같은 종목에 매도가 두 건** 생기고,
    #    보유 수량의 두 배를 팔려 하게 된다.
    # etf_pool 은 코드 문자열 목록일 수도, {code: ...} 딕셔너리 목록일 수도 있다.
    raw_pool = cfg.get("turnover_farm", {}).get("etf_pool") or []
    farm_codes = {p["code"] if isinstance(p, dict) else str(p) for p in raw_pool}
    if not farm_codes:
        from .turnover import DEFAULT_POOL
        farm_codes = {p["code"] for p in DEFAULT_POOL}
    for code, pos in positions.items():
        if code in target_codes or pos.get("qty", 0) <= 0 or code in farm_codes:
            continue
        px = quotes.get(code, {}).get("price")
        if not px:
            continue
        orders.append(
            Order("SELL", code, pos.get("name", code), pos.get("market", ""), int(pos["qty"]),
                  auction_limit(px, pos.get("market", ""), "SELL", sell_off,
                                quotes.get(code, {}).get("change_pct"), ),
                  "목표 포트폴리오 이탈")
        )

    # 2) 상한가 종목을 먼저 걸러내고, 그 비중을 남은 종목에 재분배한다.
    #
    # 상한가 종목은 매수 주문을 넣어도 사실상 체결되지 않는다. 규정상 체결량 =
    # {(실제체결량/총호가잔량) × 주문수량} × 0.1 이라 예시상 3,000주 주문에 1.8주가
    # 체결된다. 그런데 그냥 건너뛰기만 하면 **그 비중이 통째로 현금이 된다.**
    # top_n 이 3이면 한 종목만 상한가여도 자본의 1/3이 놀고, 2였다면 절반이 논다.
    # 하필 그런 날은 시장이 가장 강한 날이다 — 가장 벌어야 할 날에 노출이 반토막 난다.
    #
    # 그래서 남은 종목에 비례 배분한다. 단 규정(단일 종목 원금의 50%)을 넘길 수는
    # 없으므로 안전여유를 둔 45%에서 자른다.
    live: list = []
    for row in targets.itertuples():
        q = quotes.get(row.code, {})
        if q.get("change_pct", 0) >= UPPER_LIMIT_PCT:
            skipped_upper.append((row.code, row.name, q.get("change_pct")))
        else:
            live.append(row)

    boost = 1.0
    if skipped_upper and live:
        kept = sum(float(r.target_weight) for r in live)
        total = kept + sum(
            float(r.target_weight) for r in targets.itertuples()
            if r.code in {c for c, _, _ in skipped_upper})
        if kept > 0:
            boost = total / kept
        principal = int(cfg.get("contest", {}).get("principal", equity)) or equity
        rule_cap = cfg["requirements"]["max_single_weight_rule"]
        margin = float(cfg["requirements"].get("single_weight_margin", 0.05))
        ceiling = (rule_cap - margin) * principal
        for r in live:
            w = float(r.target_weight) * boost
            if w * equity > ceiling:
                boost = min(boost, ceiling / (float(r.target_weight) * equity))
        print(f"  상한가 제외분 재분배: 비중 ×{boost:.3f}")

    for row in live:
        q = quotes.get(row.code, {})

        px = q.get("price") or int(row.close)
        target_amt = equity * float(row.target_weight) * boost
        held = int(positions.get(row.code, {}).get("qty", 0))

        # 수량은 **현재가가 아니라 매수 지정가** 기준으로 잡는다.
        # 지정가를 +1% 공격적으로 넣으므로 현재가로 수량을 잡으면 최악의 경우
        # 필요 예수금이 목표금액을 1% 넘고, 3종목이면 예수금 부족으로 주문이
        # 거부될 수 있다. 1% 덜 사는 것보다 포지션 자체를 못 잡는 쪽이 훨씬 나쁘다.
        # 실제 체결은 단일가 경매가라 보통 현재가 근처이므로 이 보수성의 실비용은
        # 체결가 대비 약 1% 미투자에 그친다.
        buy_limit = auction_limit(px, row.market, "BUY", offset, q.get("change_pct"))
        target_qty = int(target_amt // max(buy_limit, 1))

        diff = target_qty - held
        if diff == 0:
            continue
        side = "BUY" if diff > 0 else "SELL"
        limit = buy_limit if side == "BUY" else auction_limit(
            px, row.market, "SELL", sell_off, q.get("change_pct"))
        orders.append(
            Order(side, row.code, row.name, row.market, abs(diff), limit,
                  f"목표비중 {float(row.target_weight):.0%} (score {row.score:.2f})")
        )

    if skipped_upper:
        for code, name, pct in skipped_upper:
            print(f"  상한가 근접으로 제외: {name}({code}) {pct:+.1f}%")

    # 매도를 먼저 실행해야 매수 자금이 생긴다
    orders.sort(key=lambda o: 0 if o.side == "SELL" else 1)
    return orders


def add_turnover_orders(orders: list[Order], plan, quotes: dict,
                        positions: dict, cfg: dict | None = None) -> list[Order]:
    """회전율 보충용 ETF 주문을 붙인다.

    매도(어제 산 것)를 먼저, 매수를 나중에 둔다. 같은 날 사고파는 것이 아니라
    전날 산 것을 오늘 파는 구조라 "등락을 이용한 반복 거래"에 해당하지 않는다.
    """
    offset = float((cfg or {}).get("execution", {}).get(
        "auction_offset_pct", AUCTION_OFFSET_PCT))
    sell_off = float((cfg or {}).get("execution", {}).get(
        "sell_offset_pct", SELL_OFFSET_PCT))
    extra: list[Order] = []

    for s in plan.sells:
        px = quotes.get(s["code"], {}).get("price")
        if not px:
            continue
        extra.append(Order("SELL", s["code"], s["name"], "KOSPI", int(s["qty"]),
                           auction_limit(px, "KOSPI", "SELL", sell_off), s["reason"]))

    for b in plan.buys:
        px = quotes.get(b["code"], {}).get("price")
        if not px:
            continue
        # 수량을 **지정가 기준**으로 잡는다. 현재가로 잡으면 +1% 오프셋만큼
        # 실제 필요 금액이 목표를 넘는다(실측 12,954,545 목표에 13,067,800 주문).
        # 알파북에서 이미 고친 것과 같은 버그다.
        limit = auction_limit(px, "KOSPI", "BUY", offset)
        # 수량이 명시돼 있으면 그대로 쓴다(1일차 관측용 1주 등).
        # 예전에는 항상 amount 로 역산했는데, 관측 주문은 amount 를 만들 가격 정보가
        # 이 시점에 없어서 amount=0 -> qty=0 -> continue 로 **조용히 사라졌다.**
        # 로그에는 "1주만 매수한다"가 찍히는데 주문서는 비어 있었다.
        qty = int(b.get("qty") or 0) or int(b["amount"] // max(limit, 1))
        if qty <= 0:
            continue
        extra.append(Order("BUY", b["code"], b["name"], "KOSPI", qty,
                           limit, b["reason"]))

    merged = orders + extra
    merged.sort(key=lambda o: 0 if o.side == "SELL" else 1)
    return merged


def check_order_rules(cfg: dict, orders: list, equity: int | None = None,
                      principal: int | None = None) -> list[str]:
    """**최종 주문서**(알파북 + ETF 파밍)에 대한 규정 검사.

    왜 별도 함수인가
      `check_rule_violations` 는 `targets` 만 본다. 그런데 ETF 파밍 주문은
      targets 가 만들어진 **뒤에** 붙는다. 즉 비중 합계 검사가 파밍 레그를
      영영 보지 못했다. 알파북만으로 98% 를 쓰고 파밍이 30% 를 더 얹어도
      아무도 막지 않는 구조였다.

      `risk.py` 의 ORDER_TOO_LARGE 는 **주문 1건**의 금액만 본다. 같은 종목이
      두 건으로 쪼개져 들어오거나 합계가 자본을 넘는 경우는 잡지 못한다.
    """
    problems: list[str] = []
    if not orders:
        return problems
    cap = cfg["requirements"]["max_single_weight_rule"]

    def g(o, name, default=0):
        return getattr(o, name, default) if not isinstance(o, dict) else o.get(name, default)

    by_code: dict[str, int] = {}
    buy_total = 0
    for o in orders:
        if str(g(o, "side", "")).upper() != "BUY":
            continue
        amt = int(g(o, "qty", 0) or 0) * int(g(o, "limit_price", 0) or 0)
        buy_total += amt
        code = str(g(o, "code", ""))
        by_code[code] = by_code.get(code, 0) + amt

    if principal:
        for code, amt in by_code.items():
            ratio = amt / principal
            if ratio > cap - 0.02:
                problems.append(
                    f"{code} 매수 합계 {amt:,}원 = 원금의 {ratio:.1%} "
                    f"(규정 상한 {cap:.0%}). 같은 종목 주문이 쪼개져 있어도 합산된다."
                )
    if equity and buy_total > equity:
        problems.append(
            f"매수 총액 {buy_total:,}원 > 평가금액 {equity:,}원 (미수 발생, 규정 위반)"
        )
    return problems


def check_rule_violations(cfg: dict, targets: pd.DataFrame,
                          equity: int | None = None, principal: int | None = None) -> list[str]:
    """주문 전 규정 위반 사전 검증.

    비중뿐 아니라 **금액**도 본다. 규정 기준은 투자원금이지 평가금액이 아니다.
    """
    problems: list[str] = []
    cap = cfg["requirements"]["max_single_weight_rule"]
    if targets.empty:
        return problems

    mx = float(targets["target_weight"].max())
    if mx > cap:
        problems.append(f"단일 종목 비중 {mx:.1%} > 규정 상한 {cap:.0%}")

    total = float(targets["target_weight"].sum())
    if total > 1.0:
        problems.append(f"목표 비중 합계 {total:.1%} > 100% (미수 발생, 규정 위반)")

    if equity and principal:
        max_amt = mx * equity
        ratio = max_amt / principal
        if ratio > cap - 0.02:
            problems.append(
                f"단일 종목 금액 {max_amt:,.0f}원 = 원금의 {ratio:.1%} "
                f"(규정 상한 {cap:.0%}). 평가금액이 아니라 원금이 기준이다."
            )
    return problems
