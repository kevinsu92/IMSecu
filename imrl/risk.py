"""주문 직전 독립 리스크 검사.

왜 별도 모듈인가
  전략(alpha/portfolio)은 "무엇을 얼마나 살 것인가"를 정한다. 이 모듈은
  **그 답이 맞는지 묻지 않는다.** 전략이 무슨 근거로 그런 주문을 만들었든,
  나가면 안 되는 주문인지만 본다. 두 판단이 같은 코드 안에 있으면 전략의
  버그가 곧 리스크 검사의 버그가 된다 — 잘못된 비중을 계산한 코드가
  그 비중이 정상이라고 스스로 확인해 주는 구조가 된다.

  그래서 입력을 최종 주문 리스트로만 받는다. 점수도, 국면도, 왜 골랐는지도
  보지 않는다. 규정·한도·데이터 신선도만 본다.

검사 결과의 등급
  BLOCK  하나라도 있으면 **전량 전송 중단**. 부분 전송은 하지 않는다.
         일부만 나가면 포트폴리오가 의도한 어느 상태도 아니게 된다.
  WARN   기록하고 진행. 사람이 나중에 볼 수 있게 남긴다.

한도 값에 대하여
  `config/settings.json` 의 `risk` 절을 읽는다. 값이 없으면 보수적인 기본값을
  쓰되 **그 사실을 WARN 으로 남긴다.** 조용히 기본값으로 도는 한도는 한도가 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

#: 한도 기본값. 실거래 전에 사람이 정해야 하는 값에는 주석으로 표시했다.
DEFAULTS = {
    # 시세가 몇 분까지 오래된 것을 허용하는가.
    # 15:05 에 신호를 만들고 15:10~15:27 에 전송하므로 최대 간격이 22분이다.
    # 여유를 두되, 하루 전 시세(=1440분)는 절대 통과하지 않도록 한다.
    "max_quote_age_minutes": 45,
    # 주문 1건의 최대 금액 (원금 대비 비율). 규정 단일종목 50% 안쪽.
    "max_order_ratio_of_principal": 0.45,
    # 하루 총 매수 금액 (원금 대비). 1.0 을 넘으면 현금 이상으로 주문한 것이다.
    "max_daily_buy_ratio_of_principal": 1.05,
    # 하루 주문 건수 상한. 폭주 루프를 잡는 안전망이지 전략 파라미터가 아니다.
    "max_orders_per_day": 20,
    # 누적 손실이 이 비율을 넘으면 신규 **매수**를 막는다. 매도는 항상 허용한다.
    # 0 이면 비활성. 대회 규정상 마이너스면 수상 제외라 손실 한도는 양날의 검이다 —
    # 막으면 회복 기회도 사라진다. 그래서 기본값은 끈 상태다.
    "max_drawdown_pct": 0.0,
    # 지정가가 직전가 대비 이 비율을 넘게 벗어나면 오입력으로 본다.
    "max_limit_deviation_pct": 0.35,
}


@dataclass
class Violation:
    level: str      # BLOCK | WARN
    code: str       # 기계가 읽는 식별자
    message: str


@dataclass
class RiskReport:
    violations: list[Violation] = field(default_factory=list)
    checked: int = 0

    @property
    def blocked(self) -> bool:
        return any(v.level == "BLOCK" for v in self.violations)

    @property
    def blocks(self) -> list[Violation]:
        return [v for v in self.violations if v.level == "BLOCK"]

    @property
    def warns(self) -> list[Violation]:
        return [v for v in self.violations if v.level == "WARN"]

    def add(self, level: str, code: str, message: str) -> None:
        self.violations.append(Violation(level, code, message))

    def text(self) -> str:
        if not self.violations:
            return f"리스크 검사 통과 (주문 {self.checked}건)"
        lines = [f"리스크 검사: 주문 {self.checked}건, "
                 f"차단 {len(self.blocks)}건 / 경고 {len(self.warns)}건"]
        for v in self.violations:
            lines.append(f"  [{v.level}] {v.code}: {v.message}")
        return "\n".join(lines)


def _limits(cfg: dict, report: RiskReport) -> dict:
    r = dict(DEFAULTS)
    given = cfg.get("risk") or {}
    if not given:
        report.add("WARN", "RISK_CONFIG_MISSING",
                   "config 에 risk 절이 없어 기본 한도로 검사한다. "
                   "실거래 전에 값을 확정할 것.")
    for k in DEFAULTS:
        if k in given:
            r[k] = given[k]
    return r


def _quote_age_minutes(quote: dict, now: datetime) -> float | None:
    """시세가 몇 분 전 것인지. 판단 불가면 None."""
    raw = quote.get("traded_at")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=KST)
    return (now - ts).total_seconds() / 60.0


def check_orders(cfg: dict, orders: list, quotes: dict[str, dict],
                 equity: int, principal: int,
                 now: datetime | None = None,
                 market_open: bool = True,
                 cash: int | None = None) -> RiskReport:
    """최종 주문 리스트를 검사한다.

    orders 는 `portfolio.Order` 또는 같은 필드를 가진 dict 리스트를 받는다.
    전송 직전에 호출하고, `report.blocked` 이면 **한 건도 보내지 않는다.**
    """
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)

    rep = RiskReport(checked=len(orders))
    lim = _limits(cfg, rep)

    def g(o, name, default=None):
        return getattr(o, name, None) if not isinstance(o, dict) else o.get(name, default)

    if not orders:
        rep.add("WARN", "NO_ORDERS", "주문이 없다.")
        return rep

    if len(orders) > lim["max_orders_per_day"]:
        rep.add("BLOCK", "TOO_MANY_ORDERS",
                f"주문 {len(orders)}건이 상한 {lim['max_orders_per_day']}건을 넘는다. "
                "정상 운용에서 나올 수 없는 수라 폭주로 본다.")

    # --- 손실 한도: 넘으면 신규 매수만 막는다. 매도는 언제나 허용한다 ---------- #
    dd_limit = float(lim["max_drawdown_pct"])
    drawdown = (principal - equity) / principal * 100 if principal else 0.0
    block_buys = False
    if dd_limit > 0 and drawdown >= dd_limit:
        block_buys = True
        # **여기서 BLOCK 을 걸면 안 된다.** 호출자는 차단이 하나라도 있으면 전량
        # 중단하므로, 전역 BLOCK 을 추가하는 순간 "매도는 허용"이라는 설명과
        # 정반대로 매도까지 막힌다. 실제로 평가액 6,000만원에서 정상 매도 1건만
        # 넣어도 차단됐다. 손실 한도의 목적은 노출 확대를 멈추는 것이지 청산
        # 경로를 잠그는 것이 아니다 — 잠그면 회복도 손절도 불가능해진다.
        #
        # 실제 차단은 아래 매수 루프에서 BUY 주문에만 건다.
        rep.add("WARN", "DRAWDOWN_LIMIT",
                f"누적 손실 {drawdown:.1f}% 가 한도 {dd_limit:.1f}% 를 넘었다. "
                "신규 매수만 막고 매도는 통과시킨다.")

    seen: set[tuple] = set()
    buy_total = 0
    by_symbol: dict[str, int] = {}
    order_cap = lim["max_order_ratio_of_principal"] * principal

    for o in orders:
        side = str(g(o, "side", "")).upper()
        code = str(g(o, "code", ""))
        qty = int(g(o, "qty", 0) or 0)
        limit = int(g(o, "limit_price", 0) or 0)
        name = g(o, "name", code)
        amount = qty * limit
        q = quotes.get(code, {})

        # 1) 같은 종목·방향이 두 번 들어 있는가.
        #    실행 시점의 중복 방지(submitted 기록)와 별개로, 주문서 자체가
        #    중복을 담고 있으면 그건 생성 단계의 버그다.
        # 분할 매도는 같은 (side, code) 가 여러 건 나오는 것이 정상이다.
        # 조각 인덱스까지 넣어야 "쪼갠 주문"과 "정말 중복된 주문"이 구분된다.
        # 분할이 리스크 검사보다 앞으로 오면서 이 구분이 필요해졌다.
        key = (side, code, g(o, "_split", None))
        if key in seen:
            rep.add("BLOCK", "DUPLICATE_ORDER",
                    f"{name}({code}) {side} 주문이 주문서에 두 번 있다.")
        seen.add(key)

        # 2) 수량·가격이 말이 되는가
        if qty <= 0:
            rep.add("BLOCK", "BAD_QTY", f"{name}({code}) 수량이 {qty} 다.")
        if limit <= 0:
            rep.add("BLOCK", "BAD_PRICE", f"{name}({code}) 지정가가 {limit} 이다.")

        # 3) 시세 자체가 있는가.
        #
        #    시세 딕셔너리가 통째로 비어도 예전에는 "시각 불명" 경고만 남고
        #    가격 괴리·거래가능 검사가 전부 건너뛰어져 **매수가 통과했다.**
        #    데이터 장애와 "정상인데 값이 없음"을 구분하지 않은 것이다.
        #    가격을 모르는 채로 사는 것은 어떤 경우에도 정당화되지 않는다.
        #    매도는 다르다 — 보유를 줄이는 방향이라 시세를 몰라도 허용한다.
        px_known = bool(q) and bool(q.get("price"))
        if side == "BUY" and not px_known:
            rep.add("BLOCK", "NO_QUOTE",
                    f"{name}({code}) 시세를 받지 못했다. 가격을 모르는 채로 "
                    "매수하지 않는다(데이터 장애일 수 있다).")

        # 4) 시세가 언제 것인가.
        #    장중이 아니면(주말·장외) 마지막 체결이 오래된 것이 정상이므로 검사하지 않는다.
        if market_open:
            age = _quote_age_minutes(q, now)
            if age is None:
                rep.add("BLOCK" if side == "BUY" else "WARN", "QUOTE_TIME_UNKNOWN",
                        f"{name}({code}) 시세 시각을 알 수 없다."
                        + (" 신규 매수는 막는다." if side == "BUY" else ""))
            elif age > lim["max_quote_age_minutes"]:
                rep.add("BLOCK", "STALE_QUOTE",
                        f"{name}({code}) 시세가 {age:.0f}분 전 것이다 "
                        f"(한도 {lim['max_quote_age_minutes']}분). "
                        "오래된 가격으로 주문하지 않는다.")
            elif age < -5.0:
                # 미래 시각의 시세는 시계 오류나 데이터 오류다. 예전에는 나이가
                # 음수라 "오래됨" 검사만 조용히 통과했다(외부 검토 재현).
                rep.add("BLOCK", "QUOTE_FROM_FUTURE",
                        f"{name}({code}) 시세 시각이 {-age:.0f}분 뒤의 미래다. "
                        "시계·데이터 오류로 보고 주문하지 않는다.")

        # 4) 지정가가 현재가에서 터무니없이 벗어나 있는가 (오입력·좌표사고 탐지)
        px = q.get("price")
        if px and limit > 0:
            dev = abs(limit / px - 1.0)
            if dev > lim["max_limit_deviation_pct"]:
                rep.add("BLOCK", "LIMIT_DEVIATION",
                        f"{name}({code}) 지정가 {limit:,} 이 현재가 {px:,} 에서 "
                        f"{dev:.0%} 벗어났다.")

        # 5) 거래 가능한 종목인가
        if q and q.get("tradable") is False:
            rep.add("BLOCK", "NOT_TRADABLE", f"{name}({code}) 가 거래 불가 상태다.")

        # 6) 주문 1건 금액 상한
        if amount > order_cap:
            rep.add("BLOCK", "ORDER_TOO_LARGE",
                    f"{name}({code}) 주문금액 {amount:,}원이 상한 {int(order_cap):,}원"
                    f"(원금의 {lim['max_order_ratio_of_principal']:.0%})을 넘는다.")

        if side == "BUY":
            if block_buys:
                rep.add("BLOCK", "BUY_BLOCKED",
                        f"{name}({code}) 손실 한도로 매수가 막혀 있다.")
            buy_total += amount
            by_symbol[code] = by_symbol.get(code, 0) + amount

    # 7) 하루 총 매수 금액
    buy_cap = lim["max_daily_buy_ratio_of_principal"] * principal
    if buy_total > buy_cap:
        rep.add("BLOCK", "DAILY_BUY_LIMIT",
                f"총 매수 {buy_total:,}원이 상한 {int(buy_cap):,}원을 넘는다.")

    # 8) 매수 총액이 평가금액을 넘는가.
    #    이것만으로는 부족하다 — 평가금액의 90% 가 주식에 묶여 있어도 통과한다.
    if buy_total > equity:
        rep.add("BLOCK", "INSUFFICIENT_EQUITY",
                f"총 매수 {buy_total:,}원이 평가금액 {equity:,}원을 넘는다.")

    # 9) 실제로 낼 수 있는 현금이 있는가.
    #
    #    8) 이 "보수적"이라고 주석에 적혀 있었지만 보수적이지 않았다. 현금이
    #    10% 뿐인데 평가금액의 100% 까지 매수를 허용한다. 전략 버그를 잡으라고
    #    분리해 둔 층에 현금 제약이 아예 없어서, run.py 와 **같은 방식으로**
    #    틀리고 있었다. 두 층이 같은 방식으로 틀리면 층을 나눈 의미가 없다.
    #
    #    한계는 정직하게 적어 둔다: HTS 에서 주문가능금액을 읽는 경로가 없어
    #    이 값은 계획 단계 추정치를 넘겨받은 것이다. 완전히 독립적이지 않다.
    #    그래도 "현금을 아예 안 본다" 보다는 낫고, 추정 자체가 보수적(과소)이다.
    if cash is not None:
        sell_total = 0
        for o in orders:
            if str(g(o, "side", "")).upper() == "SELL":
                sell_total += int(g(o, "qty", 0) or 0) * int(g(o, "limit_price", 0) or 0)
        if buy_total > cash + sell_total:
            rep.add("BLOCK", "INSUFFICIENT_CASH",
                    f"총 매수 {buy_total:,}원이 예수금 {cash:,}원 + 매도 대금 "
                    f"{sell_total:,}원을 넘는다. 예수금 부족으로 거부될 주문이다.")
    elif buy_total > 0:
        # 현금을 모르는 채로 사지 않는다. 예전에는 경고만 남기고 통과시켰다 —
        # 3천만원 신규 매수가 cash=None 에서 그대로 나갔다(외부 검토 재현).
        # 매도만 있으면 현금이 필요 없으므로 경고로 둔다.
        rep.add("BLOCK", "CASH_UNKNOWN",
                "예수금 추정치가 없다(계획 meta 파일 부재). 현금을 모르는 채로 "
                "신규 매수를 내지 않는다.")
    else:
        rep.add("WARN", "CASH_UNKNOWN",
                "예수금 추정치를 받지 못했다. 매도만 있어 진행한다.")

    return rep
