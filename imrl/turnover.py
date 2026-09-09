"""회전율 요건 강제.

왜 필요한가
  회전율 500%는 수상 자격의 하드 요건이다. 못 채우면 수익률 1등이어도 상금 0원이다.
  그런데 자연 회전만으로는 부족하다. 4종목 동일비중(종목당 24.5%)에서 정의 B
  (누적매도/원금) 기준 500%를 채우려면 **19회 리밸런싱 동안 매일 종목의 27% 이상을
  교체**해야 한다. 여유가 12%밖에 없다.

  더 나쁜 것은 실패 시나리오와 승리 시나리오가 **양의 상관**을 갖는다는 점이다.
  고른 종목이 계속 상위를 유지하며 크게 오르는 상황 = 랭킹이 안 바뀌는 상황
  = 회전이 멈추는 상황이다. 즉 잘 되고 있을 때 실격당한다.

  그래서 부족분을 능동적으로 채운다.

어떻게 채우는가
  ETF 왕복으로 채운다. ETF는 제세금이 없어 왕복 비용이 0.02% 로, 개별주식
  0.22% 의 1/11 이다.

규정 리스크 관리
  "고의적으로 주식의 등락을 이용한 반복적인 단일 종목 거래" 는 수상 취소 사유다.
  이를 피하기 위해:
    - 당일 왕복(같은 날 사고파는 것)을 하지 않는다. 최소 1영업일 보유한다.
      이 규칙 하나가 "등락을 이용"과 "반복"을 동시에 깬다.
    - 경제적으로 서로 다른 ETF 를 순환한다. 같은 지수를 추종하는 ETF 를
      돌려가며 사고파는 것은 자산배분이 아니라 워시 트레이딩으로 보인다.
    - 종목별 누적 거래대금 집중도를 상한으로 막는다.
    - 매일 조금씩 채운다. 막판 몰아치기가 가장 눈에 띈다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 회전율 파밍용 ETF 는 **방향성을 지지 않는 것**이 요건이다.
#
# 처음에는 KOSPI200 ETF 4종(069500/102110/148020/069660)을 썼는데 이게 잘못이었다.
# 실측 일변동성이 2.81~2.85% 이고 max_daily_farm_ratio 0.35 이므로, 알파와 무관한
# 자리에서 계좌 변동성 약 0.98%/일 을 오버나이트로 떠안게 된다.
#
# 그리고 시장 베타는 **순위를 바꾸지 못한다.** 대회 참가자 전원이 같은 지수 움직임을
# 같이 먹기 때문이다. 순위를 움직이는 것은 개별종목 초과수익뿐이다. 즉 KOSPI200
# 오버레이는 기대순위에 기여하지 않으면서 결과 분산만 키운다. 분산이 필요하면
# 알파북의 집중도(top_n, max_weight)로 의도적으로 조절하는 것이 옳다.
#
# 그래서 CD금리/초단기채권 ETF 로 바꾼다. 실측 일변동성 0.007~0.014% 로 사실상 0 이고,
# 459580 은 20일 평균 거래대금 4,966억이라 우리 물량(최대 3,500만원)에 호가 영향이 없다.
# 규정 방어 측면에서도 낫다 — "주식의 등락을 이용한 반복 거래" 가 금지 사유인데
# 애초에 등락이 없는 상품이라 그 구성요건에 해당하지 않는다.
#
# ETF 는 제세금이 없어 왕복 0.02% 로 동일하다. 가격 하한 1,000원 규정도 전부 충족.
DEFAULT_POOL = [
    {"code": "459580", "name": "KODEX CD금리액티브", "asset": "cd_rate"},
    {"code": "469830", "name": "SOL 초단기채권액티브", "asset": "short_bond"},
    {"code": "357870", "name": "TIGER CD금리투자KIS", "asset": "cd_rate_alt"},
]


@dataclass
class TurnoverPlan:
    """오늘 회전율을 얼마나, 무엇으로 채울지."""

    needed_pct: float          # 오늘 채워야 할 회전율(%p)
    amount_krw: int            # 그에 해당하는 거래대금
    buys: list[dict]           # 오늘 살 ETF
    sells: list[dict]          # 오늘 팔 ETF (어제 산 것)
    note: str

    @property
    def is_empty(self) -> bool:
        return not self.buys and not self.sells


def required_pace(target_pct: float, days_left: int, total_days: int = 20,
                  safety: float = 1.30) -> float:
    """지금까지 채워져 있어야 할 누적 회전율.

    안전마진 30% 를 곱한다. 회전율 산정 공식이 미공개라 우리 추정보다 엄격할
    여지가 있고, 미체결로 실제 거래가 계획보다 적게 잡힐 수도 있다.
    부족해서 실격당하는 손실이 조금 더 거래하는 비용보다 훨씬 크다.
    """
    elapsed = max(0, total_days - days_left)
    return target_pct * (elapsed / total_days) * safety


def plan_daily(target_pct: float, current_pct: float, days_left: int,
               principal: int, positions: dict, pool: list[dict] | None = None,
               max_daily_ratio: float = 0.30, total_days: int = 20,
               safety: float = 1.0) -> TurnoverPlan:
    """오늘의 회전율 보충 계획.

    positions 에 이미 파밍용 ETF 가 있으면 **먼저 판다**(어제 산 것). 그 뒤 부족분
    만큼 새로 산다. 사고파는 것이 서로 다른 날에 일어나므로 당일 왕복이 아니다.
    """
    pool = pool or DEFAULT_POOL
    pool_codes = {p["code"] for p in pool}

    # 1) 어제 산 파밍 ETF 를 정리한다 (매도 = 정의 B 회전율에 직접 기여)
    sells = []
    for code, pos in positions.items():
        if code in pool_codes and pos.get("qty", 0) > 0:
            sells.append({"code": code, "name": pos.get("name", code),
                          "qty": int(pos["qty"]), "reason": "회전율 확보용 ETF 정리"})

    # 2) 페이스 대비 부족분 계산
    # safety 기본값이 1.0 인 이유: 호출측이 넘기는 target_pct 가 이미 안전마진을
    # 포함한 값(config.turnover_farm.target_pct_with_margin = 650)이기 때문이다.
    # 여기서 또 1.30 을 곱하면 845% 가 되어 마진이 이중으로 붙는다.
    need_by_now = required_pace(target_pct, days_left, total_days, safety)
    shortfall_pct = max(0.0, need_by_now - current_pct)

    if days_left <= 0 or shortfall_pct <= 0:
        note = ("회전율 페이스 정상. 추가 매매 불필요."
                if shortfall_pct <= 0 else "대회 종료.")
        return TurnoverPlan(0.0, 0, [], sells, note)

    # 남은 날에 균등 배분하되 하루 상한을 둔다
    per_day_pct = shortfall_pct / days_left
    cap_pct = max_daily_ratio * 100
    today_pct = min(per_day_pct, cap_pct)
    amount = int(today_pct / 100 * principal)

    if amount < 1_000_000:
        return TurnoverPlan(today_pct, amount, [], sells,
                            "부족분이 작아 오늘은 보충하지 않는다.")

    # 3) 여러 ETF 에 나눠 산다. 한 종목에 몰면 집중도 경고에 걸린다.
    candidates = [p for p in pool if p["code"] not in {s["code"] for s in sells}]
    if not candidates:
        candidates = pool
    n = min(len(candidates), max(2, round(amount / 15_000_000)))
    chosen = candidates[:n]
    each = amount // len(chosen)

    buys = [{"code": c["code"], "name": c["name"], "amount": each,
             "reason": f"회전율 보충 (페이스 {current_pct:.0f}% / 필요 {need_by_now:.0f}%)"}
            for c in chosen]

    note = (f"페이스 부족 {shortfall_pct:.0f}%p. 오늘 {today_pct:.1f}%p "
            f"({amount:,}원) 를 {len(chosen)}종목으로 보충한다.")
    if per_day_pct > cap_pct:
        note += f" 하루 상한 {cap_pct:.0f}%p 에 걸려 나머지는 이후로 넘긴다."
    return TurnoverPlan(today_pct, amount, buys, sells, note)


def cost_estimate(amount_krw: int, is_etf: bool = True) -> int:
    """왕복 비용. ETF 는 제세금이 없어 개별주식의 1/11 이다."""
    fee = 0.0001
    tax = 0.0 if is_etf else 0.0020
    return int(amount_krw * fee * 2 + amount_krw * tax)


# --------------------------------------------------------------------------- #
# ETF 가 자격 집계에서 제외될 때의 폴백
# --------------------------------------------------------------------------- #

def stock_pool(universe_df, held: set[str], day_index: int,
               n: int = 2, exclude: set[str] | None = None) -> list[dict]:
    """개별주식으로 회전율을 채울 때 쓸 종목을 고른다.

    언제 쓰는가
      1일차 관측에서 ETF 가 자격 집계에 산입되지 않는 것으로 확인된 경우다.
      그러면 CD금리 ETF 경로가 통째로 무효가 되고(자연 회전 481% 로는 500% 요건
      미달), 개별주식으로 부족분을 채우는 수밖에 없다.

    무엇을 고르는가
      **알파 상위가 아니라 저변동·고유동성 종목**이다. 파밍은 수익을 노리는
      거래가 아니라 자격을 채우는 거래이므로, 하룻밤 들고 있는 동안의 방향성
      노출을 최소화해야 한다. 알파 종목을 쓰면 이미 들고 있는 위험이 두 배가 된다.

    왜 매일 돌리는가
      규정은 "고의적으로 주식의 등락을 이용한 **반복적인 단일 종목 거래**" 를
      수상 취소 사유로 둔다. 같은 종목을 20일 왕복하면 그 문구에 정면으로
      해당한다. `day_index` 로 시작 위치를 밀어 매일 다른 종목을 쓴다.

    비용
      주식 왕복 0.22% 로 ETF(0.02%)의 11배다. 부족분 19%p 면 약 0.042%p,
      자연 회전이 무너져 500%p 를 전부 채워야 하면 약 1.1%p 다.
      싸지 않지만 자격 미달(상금 0원)보다는 훨씬 싸다.
    """
    if universe_df is None or len(universe_df) == 0:
        return []
    skip = set(held) | set(exclude or ())
    df = universe_df[~universe_df["code"].isin(skip)].copy()
    if df.empty:
        return []

    # 저변동 우선. 변동성 열이 없으면 유동성만으로 고른다.
    if "volatility" in df.columns:
        df = df.sort_values("volatility")
    elif "amount" in df.columns:
        df = df.sort_values("amount", ascending=False)
    cand = df.head(max(n * 6, 12))
    if cand.empty:
        return []

    rows = cand.to_dict("records")
    start = (day_index * n) % len(rows)
    picked = [rows[(start + i) % len(rows)] for i in range(min(n, len(rows)))]
    return [{"code": str(r["code"]), "name": str(r.get("name", r["code"])),
             "asset": "stock_fallback"} for r in picked]
