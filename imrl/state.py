"""상태 저장과 수상 자격 요건 추적.

회전율 산정 공식은 대회 주최측이 공개하지 않았다("당사 모의투자시스템 산정 기준").
따라서 가능한 두 정의를 모두 계산해 보수적인 쪽(더 작은 값)을 기준으로 진척도를 관리한다.
정의는 대회 초반에 중계실 회전율 수치와 대조해 확정한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
POSITIONS_FILE = STATE_DIR / "positions.json"
TRADES_FILE = STATE_DIR / "trades.json"
LEADERBOARD_FILE = STATE_DIR / "leaderboard.json"


def _read(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, obj) -> None:
    """임시 파일에 쓰고 이름을 바꾼다.

    보유·원장·리더보드가 전부 이 함수를 지난다. 쓰는 도중에 프로세스가 죽으면
    (작업 스케줄러의 시간 제한, 재부팅, 전원) 반쯤 쓰인 JSON 이 남고, 다음 실행은
    보유를 못 읽어 빈 계좌로 계획한다 — 첫날 매수를 두 번 하는 종류의 사고다.
    replace 는 같은 볼륨 안에서 원자적이다. state/ 가 OneDrive 안이라 더 그렇다.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# 보유 종목
# --------------------------------------------------------------------------- #

def load_positions() -> dict[str, dict]:
    return _read(POSITIONS_FILE, {})


def save_positions(pos: dict[str, dict]) -> None:
    _write(POSITIONS_FILE, pos)


# --------------------------------------------------------------------------- #
# 체결 기록
# --------------------------------------------------------------------------- #

def load_trades() -> list[dict]:
    return _read(TRADES_FILE, [])


def trade_id(t: dict) -> str:
    """체결 한 건의 신원. 같은 체결을 두 번 넣어도 한 번으로 세기 위한 키.

    전송 원장의 조각 키(`SELL:005930#2`)가 있으면 그것이 신원이다. 분할 매도는
    같은 날·같은 종목·같은 수량·같은 지정가인 조각 셋을 만드는데, 값만으로 신원을
    삼으면 셋이 하나로 접혀 보유의 2/3 가 팔리지 않은 것으로 남는다 — 다음 날
    계획이 유령 보유를 팔려다 거부되고 현금 추정이 어긋난다. 사람이 `run.py fill`
    로 넣는 체결에는 키가 없으므로 예전 규칙(값 일치 = 중복)이 그대로 남는다.
    """
    # 날짜는 **날짜까지만** 본다. `run.py fill` 은 실행 시각을 초 단위로 붙이므로
    # 같은 체결을 3초 뒤 다시 넣으면 다른 신원이 됐다(외부 검토 재현: 2건 80주).
    base = "|".join([str(t.get("date", ""))[:10]] +
                    [str(t.get(k, "")) for k in ("side", "code", "qty", "price")])
    key = str(t.get("key", "") or "")
    return base + ("|" + key if key else "")


def record_trades(trades: list[dict]) -> tuple[list[dict], int]:
    """체결된 주문을 누적 기록한다. 회전율·매매일수·매매종목수의 원천 데이터.

    **같은 체결을 두 번 넣어도 한 번만 센다.**
      예전에는 무조건 append 였다. 그래서 사람이 `run.py fill` 을 두 번 실행하거나
      같은 날 체결을 다시 입력하면 매수·매도 누계가 두 배가 되고, 회전율이 실제의
      두 배로 보고된다. 요건 미달을 충족으로 착각하는 방향이라 가장 나쁜 오차다.
      보유 수량도 함께 두 배가 되어 다음 날 주문 수량이 어긋난다.

    반환값은 (새로 기록한 체결 목록, 중복이라 건너뛴 건수). 보유 반영은 **새로
    기록한 것만** 받아야 한다 - 중복까지 넘기면 수량이 두 배가 된다.
    """
    log = load_trades()
    seen = {trade_id(x) for x in log}
    added, dup = [], 0
    for t in trades:
        tid = trade_id(t)
        if tid in seen:
            dup += 1
            continue
        seen.add(tid)
        added.append(t)
    if added:
        log.extend(added)
        _write(TRADES_FILE, log)
    return added, dup


def apply_fills_to_positions(fills: list[dict]) -> dict[str, dict]:
    """체결 내역을 보유 종목에 반영한다.

    진입일(entry_date)을 함께 기록한다. 보유일수를 모르면 **시간 청산을 구현할
    수 없기** 때문이다. 국내 장중 실측에서 가격 스탑은 조일수록 성과가 나빠졌고
    (−2% < −3% < −5% < −8% < 스탑없음, 완전 단조) ATR 스탑은 발동률 88%에
    승률 11.6%로 최악이었던 반면, 시간 스탑만 뚜렷한 효과가 있었다.
    즉 이 전략에서 유일하게 근거 있는 청산 축이 보유일수인데 그 값이 없었다.

    추가 매수는 진입일을 갱신하지 않는다. 갱신하면 물타기로 시간 스탑을
    무한히 미룰 수 있어 규칙이 무력해진다.
    """
    pos = _apply_fills(load_positions(), fills)
    save_positions(pos)
    return pos


def rebuild_positions() -> dict[str, dict]:
    """체결 원장 전체를 처음부터 다시 재생해 보유를 만든다.

    원장이 진실이고 보유는 그 투영이다. 원장 저장과 보유 저장 사이에 프로세스가
    죽으면(외부 검토 재현: 거래 1건, 보유 빈 객체) 투영을 다시 만들면 된다.
    """
    pos = _apply_fills({}, load_trades())
    save_positions(pos)
    return pos


def heal_positions() -> bool:
    """원장이 보유보다 새로우면 보유를 원장에서 다시 만든다. 고쳤으면 True."""
    if not TRADES_FILE.exists():
        return False
    try:
        t_m = TRADES_FILE.stat().st_mtime
        p_m = POSITIONS_FILE.stat().st_mtime if POSITIONS_FILE.exists() else -1.0
    except OSError:
        return False
    if p_m + 2.0 < t_m:
        rebuild_positions()
        return True
    return False


def drop_assumed(day: str, pairs) -> int:
    """같은 날·방향·종목의 **추정** 체결을 원장에서 지운다.

    확정 체결(사람이 확인해 넣는 것)은 추정을 **대체**해야지 더해지면 안 된다.
    예전에는 추정 매수 100주 뒤 실제 40주를 넣으면 보유 140주가 됐다.
    지운 뒤 보유는 원장에서 다시 만든다. 지운 건수를 돌려준다.
    """
    want = {(str(s).upper(), str(c)) for s, c in pairs}
    keep, removed = [], 0
    for t in load_trades():
        if (t.get("assumed") and str(t.get("date", ""))[:10] == day
                and (str(t.get("side", "")).upper(), str(t.get("code", ""))) in want):
            removed += 1
            continue
        keep.append(t)
    if removed:
        _write(TRADES_FILE, keep)
        rebuild_positions()
    return removed


def _apply_fills(pos: dict[str, dict], fills: list[dict]) -> dict[str, dict]:
    pos = {c: dict(v) for c, v in (pos or {}).items()}
    for f in fills:
        code = f["code"]
        cur = pos.setdefault(code, {"name": f.get("name", code), "market": f.get("market", ""),
                                    "qty": 0, "avg_price": 0, "entry_date": None})
        qty, px = int(f["qty"]), int(f["price"])
        if f["side"] == "BUY":
            if cur["qty"] <= 0 or not cur.get("entry_date"):
                cur["entry_date"] = str(f.get("date", ""))[:10] or date.today().isoformat()
            total = cur["qty"] * cur["avg_price"] + qty * px
            cur["qty"] += qty
            cur["avg_price"] = int(total / cur["qty"]) if cur["qty"] else 0
        else:
            cur["qty"] -= qty
            if cur["qty"] <= 0:
                cur["qty"] = 0
                cur["avg_price"] = 0
                cur["entry_date"] = None
    return {c: v for c, v in pos.items() if v["qty"] > 0}


def days_held(pos_entry: dict, cal=None, today: date | None = None) -> int:
    """보유 영업일수. 진입일이 없으면 0.

    달력일이 아니라 영업일로 센다. 추석 연휴가 낀 주에 달력일로 세면 실제
    거래 기회 없이 보유일수만 늘어 시간 스탑이 엉뚱하게 발동한다.
    """
    raw = pos_entry.get("entry_date")
    if not raw:
        return 0
    try:
        start = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return 0
    today = today or date.today()
    if cal is not None and hasattr(cal, "sessions_between"):
        return cal.sessions_between(start, today)
    # 캘린더가 없으면 주말만 빼는 근사
    n, d = 0, start
    while d < today:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


# --------------------------------------------------------------------------- #
# 수상 자격 요건
# --------------------------------------------------------------------------- #

@dataclass
class ConstraintStatus:
    """자격 요건 진척도.

    두 종류의 숫자가 있다는 점이 중요하다.
      - 내부 추정치: 우리 체결 기록에서 계산. 주문이 전량 체결됐다고 가정하므로
        **실제보다 낙관적**이다. 미체결·부분체결·거부가 전부 체결로 잡힌다.
      - 공식 수치: 중계실에서 사람이 읽어 넣은 값. 주최측 집계 기준 그 자체다.

    공식 수치가 있으면 판정은 그걸로 한다. 내부 추정치는 참고와 괴리 감시용이다.
    """

    principal: int
    buy_amount: int = 0
    sell_amount: int = 0
    trading_days: int = 0
    distinct_symbols: int = 0
    days_left: int = 0
    symbol_concentration: dict[str, float] = field(default_factory=dict)
    official_turnover_pct: float | None = None
    official_trading_days: int | None = None
    official_distinct_symbols: int | None = None

    @property
    def turnover_a(self) -> float:
        """(누적매수 + 누적매도) / 투자원금 × 100"""
        return (self.buy_amount + self.sell_amount) / self.principal * 100

    @property
    def turnover_b(self) -> float:
        """누적매도 / 투자원금 × 100 (더 빡빡한 정의)"""
        return self.sell_amount / self.principal * 100

    @property
    def inferred_definition(self) -> str | None:
        """공식 수치가 A 와 B 중 어느 쪽에 가까운지 역산한다.

        중계실 값을 알면 추측할 필요가 없다. 둘 중 상대오차가 작은 쪽을 고른다.
        판별이 되면 `turnover_conservative` 가 min() 대신 실제 정의를 쓴다 —
        정의가 A 인데 min()(=B) 을 쓰면 회전율을 크게 과소평가해 파밍이 폭주한다.
        """
        o = self.official_turnover_pct
        if o is None or o <= 0:
            return None
        da = abs(self.turnover_a - o) / max(o, 1e-9)
        db = abs(self.turnover_b - o) / max(o, 1e-9)
        if min(da, db) > 0.35:      # 둘 다 안 맞으면 판별 보류
            return None
        return "A" if da <= db else "B"

    @property
    def turnover_conservative(self) -> float:
        """정의 미확정 시 보수적인 쪽. 확정되면 그 정의를 쓴다."""
        d = self.inferred_definition
        if d == "A":
            return self.turnover_a
        if d == "B":
            return self.turnover_b
        return min(self.turnover_a, self.turnover_b)

    # -- 판정에 쓰는 값: 공식 수치가 있으면 그걸 쓴다 ---------------------- #

    #: 체결을 확인할 수 없을 때 가정하는 체결률.
    #:
    #: 체결내역 화면은 격자가 전부 커스텀 렌더링(AfxWnd140)이라 메시지로 읽을 수
    #: 없다 — 수량·가격 칸과 같은 벽이다. 그래서 내부 추정치는 **주문 = 체결**로
    #: 계산하는데, 이건 낙관적이다. 미체결·부분체결·거부가 전부 체결로 잡힌다.
    #:
    #: 회전율 500%는 수상의 하드 요건이다. 과다 공급의 비용은 ETF 왕복 0.02% 지만
    #: 과소 공급의 비용은 **상금 전액**이다. 비대칭이 극단적이므로 낙관을 벌한다.
    #: 실측 동시호가 체결률이 매도 51.8%/매수 62.1% 였고 지정가를 ±1%로 고쳐
    #: 98.9%/99.4% 가 됐지만, 그건 백테스트 추정이지 실집행 관측이 아니다.
    #: 중계실 공식 수치가 들어오면 이 가정은 즉시 무시된다.
    assumed_fill_rate: float = 0.85

    @property
    def effective_turnover(self) -> float:
        """자격 판정에 쓰는 회전율.

        공식 수치가 있으면 그것을 쓴다. 없으면 내부 추정치에 체결률 가정을 곱해
        **의도적으로 낮게** 잡는다. 낮게 잡으면 파밍이 더 돌아 회전율이 남고,
        높게 잡으면 요건 미달을 충족으로 착각한 채 대회가 끝난다.
        """
        if self.official_turnover_pct is not None:
            return self.official_turnover_pct
        return self.turnover_conservative * self.assumed_fill_rate

    @property
    def effective_days(self) -> int:
        return (self.official_trading_days if self.official_trading_days is not None
                else self.trading_days)

    @property
    def effective_symbols(self) -> int:
        return (self.official_distinct_symbols if self.official_distinct_symbols is not None
                else self.distinct_symbols)

    @property
    def has_official(self) -> bool:
        return self.official_turnover_pct is not None

    def report(self, req: dict) -> str:
        need = req["min_turnover_pct"]
        src = ("중계실 공식" if self.has_official
               else f"내부 추정 x 체결률 {self.assumed_fill_rate:.0%} (보수적)")
        remain = max(0.0, need - self.effective_turnover)
        per_day = (remain / self.days_left) if self.days_left > 0 else 0.0
        daily_amt = int(per_day / 100 * self.principal)

        lines = [
            f"수상 자격 요건 진척도  [기준: {src}]",
            f"  회전율        {self.effective_turnover:8.1f}% / {need:.0f}%",
            f"  매매일수      {self.effective_days:8d}일 / {req['min_trading_days']}일",
            f"  매매종목수    {self.effective_symbols:8d}종목 / {req['min_distinct_symbols']}종목",
            f"  잔여 영업일   {self.days_left:8d}일",
        ]

        if self.has_official:
            d = self.inferred_definition
            gap = self.turnover_conservative - self.official_turnover_pct
            label = {"A": "정의A(매수+매도)", "B": "정의B(매도만)"}.get(d, "정의 미확정")
            lines.append(
                f"  내부 추정 {self.turnover_conservative:.1f}% "
                f"(공식 대비 {gap:+.1f}%p, {label})"
            )
            # 정의가 판별된 뒤에도 남는 괴리만 경고한다.
            # 예전에는 min(A,B) 와 공식값을 비교해서 **정의 차이만으로 매일 울렸다.**
            # 매일 울리는 경고는 없는 것보다 나쁘다 — 진짜 경고를 무시하게 된다.
            if d is None:
                lines.append(
                    "  정의 판별 보류. 매수만 발생한 초반에는 정상이다. "
                    "매도가 생긴 뒤 다시 본다."
                )
            elif abs(gap) > 30:
                lines.append(
                    "  ⚠ 정의를 맞춘 뒤에도 괴리가 크다. "
                    "체결 기록이 실제와 어긋나고 있으니 확인할 것."
                )
        else:
            lines.append(
                f"  ⚠ 중계실 공식 수치가 없다. 체결을 확인할 수 없어 체결률 "
                f"{self.assumed_fill_rate:.0%} 를 가정해 **낮춰 잡은** 값이다."
            )
            lines.append(
                "     과다 파밍 비용(ETF 왕복 0.02%)보다 요건 미달(상금 0원)이 훨씬 크다."
            )
            lines.append(
                "     `run.py board --turnover ...` 로 중계실 수치를 넣으면 가정을 버린다."
            )
            lines.append(f"     (정의A 매수+매도 {self.turnover_a:.1f}% / "
                         f"정의B 매도만 {self.turnover_b:.1f}%)")

        if remain > 0:
            lines.append(f"  → 남은 회전율 {remain:.0f}%p, 일평균 필요 거래대금 {daily_amt:,}원")
        else:
            lines.append("  → 회전율 요건 충족")

        # 페이스 점검: 남은 일수로 균등 배분했을 때 따라갈 수 있는가
        if self.days_left > 0 and remain > 0:
            pace_ok = per_day <= 60.0
            if not pace_ok:
                lines.append(
                    f"  ⚠ 하루 {per_day:.0f}%p 를 채워야 한다. 이 속도는 무리이고 "
                    "몰아치기 매매는 규정상 위험하다. 지금부터 회전을 늘릴 것."
                )

        if self.symbol_concentration:
            top_code, top_share = max(self.symbol_concentration.items(), key=lambda kv: kv[1])
            # 임계를 종목 수에 맞춘다. top2 운용에서는 각 종목이 구조적으로
            # 거래대금의 50%를 차지하므로 고정 30% 임계는 **매일 울린다.**
            # 균등 배분값(1/n)의 1.6배를 넘을 때만 이상 신호로 본다.
            n_sym = max(len(self.symbol_concentration), 1)
            threshold = min(0.60, max(0.30, 1.6 / n_sym))
            flag = ("  ⚠ 단일 종목 반복매매로 오인될 수 있음"
                    if top_share > threshold else "")
            lines.append(f"  회전 집중도 최대 {top_code} {top_share:.0%} "
                         f"(임계 {threshold:.0%}){flag}")
        return "\n".join(lines)

    def unmet(self, req: dict) -> list[str]:
        out = []
        if self.effective_turnover < req["min_turnover_pct"]:
            out.append("회전율")
        if self.effective_days < req["min_trading_days"]:
            out.append("매매일수")
        if self.effective_symbols < req["min_distinct_symbols"]:
            out.append("매매종목수")
        return out

    def pace_behind(self, req: dict) -> bool:
        """균등 페이스 대비 뒤처졌는가. 막판 몰아치기를 피하려면 매일 봐야 한다."""
        total = req["min_turnover_pct"]
        if self.days_left <= 0:
            return self.effective_turnover < total
        # 대회 전체 영업일을 20으로 보고 경과 비율만큼은 채워야 한다
        elapsed_ratio = max(0.0, 1.0 - self.days_left / 20.0)
        return self.effective_turnover < total * elapsed_ratio * 0.85


def compute_constraints(principal: int, days_left: int,
                        assumed_fill_rate: float | None = None) -> ConstraintStatus:
    trades = load_trades()
    buy = sum(t["qty"] * t["price"] for t in trades if t["side"] == "BUY")
    sell = sum(t["qty"] * t["price"] for t in trades if t["side"] == "SELL")
    days = {t["date"][:10] for t in trades}
    symbols = {t["code"] for t in trades}

    total_amt = buy + sell
    conc: dict[str, float] = {}
    if total_amt > 0:
        for t in trades:
            conc[t["code"]] = conc.get(t["code"], 0) + t["qty"] * t["price"]
        conc = {c: v / total_amt for c, v in conc.items()}

    board = load_leaderboard()
    if assumed_fill_rate is None:
        # 설정에서 읽는다. 없으면 클래스 기본값을 쓴다.
        try:
            cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
            assumed_fill_rate = float(cfg["requirements"].get(
                "assumed_fill_rate", ConstraintStatus.assumed_fill_rate))
        except (OSError, KeyError, ValueError):
            assumed_fill_rate = ConstraintStatus.assumed_fill_rate

    return ConstraintStatus(
        assumed_fill_rate=assumed_fill_rate,
        principal=principal,
        buy_amount=buy,
        sell_amount=sell,
        trading_days=len(days),
        distinct_symbols=len(symbols),
        days_left=days_left,
        symbol_concentration=conc,
        official_turnover_pct=board.get("official_turnover_pct"),
        official_trading_days=board.get("official_trading_days"),
        official_distinct_symbols=board.get("official_distinct_symbols"),
    )


def estimate_my_return(quotes: dict[str, dict], principal: int,
                       cost_rate: float = 0.0022) -> float | None:
    """내 수익률을 보유 포지션과 시세로 직접 추정한다. 판단 불가면 None.

    왜 필요한가
      국면 판정(`portfolio.choose_phase`)이 중계실 수동 입력에 걸려 있었다.
      저녁 입력을 하루 빠뜨리면 수익률을 0% 로 보고 국면을 고른다. 무인 운영이
      전제인 시스템에서 사람 손 하나가 국면 전체를 좌우하는 구조다.

      그런데 마이너스 판정에 필요한 것은 **내 수익률뿐이고, 그건 내가 안다.**
      보유 수량은 positions.json 에, 매수·매도 누계는 trades.json 에 있고,
      현재가는 매일 받는다. 남들 수익률(리더보드)만 웹에서만 얻을 수 있다.

    한계 — 정직하게
      체결을 확인할 수 없으므로 positions/trades 는 "주문이 체결됐다"는 가정 위에
      있다. 부분체결이 나면 이 추정도 같이 틀린다. 그래서 **중계실 공식 수치가
      있으면 언제나 그쪽이 우선**이고, 이 값은 공식 수치가 없을 때의 폴백이다.
      비용은 왕복 규정비용(0.22%)을 매수·매도 누계에 근사 적용한다.
    """
    pos = load_positions()
    trades = load_trades()
    if not pos and not trades:
        return None

    # 원장은 amount 를 저장하지 않는다 — qty 와 price 만 있다. 예전에는 여기서
    # amount 를 읽어 항상 0 이 나왔고, 그러면 매수액이 0 으로 잡혀 현금이 원금
    # 그대로 남는다. 보유 평가가 더해지면서 수익률이 **투자 비중만큼 통째로
    # 부풀었다**(90% 투자 시 +90%). 국면 판정이 그 값을 쓴다.
    def _amt(x: dict) -> int:
        a = int(x.get("amount", 0) or 0)
        return a if a else int(x.get("qty", 0) or 0) * int(x.get("price", 0) or 0)

    buy = sum(_amt(t) for t in trades if str(t.get("side", "")).upper() == "BUY")
    sell = sum(_amt(t) for t in trades if str(t.get("side", "")).upper() == "SELL")

    holdings = 0
    for code, p in pos.items():
        qty = int(p.get("qty", 0) or 0)
        if qty <= 0:
            continue
        px = int((quotes.get(code) or {}).get("price", 0) or 0)
        if px <= 0:
            # 시세를 못 받은 종목이 있으면 추정 전체를 포기한다. 일부만 시가
            # 평가하면 수익률이 실제보다 낮게 나와 국면을 잘못 고른다.
            return None
        holdings += qty * px

    cash = principal - buy + sell - int((buy + sell) * cost_rate / 2)
    equity = cash + holdings
    return (equity - principal) / principal * 100 if principal else None


# --------------------------------------------------------------------------- #
# 리더보드 (중계실에서 수동/자동 입력)
# --------------------------------------------------------------------------- #

def load_leaderboard() -> dict:
    return _read(LEADERBOARD_FILE, {})


def save_leaderboard(leader_return_pct: float, my_return_pct: float | None,
                     my_rank: int | None = None,
                     turnover_pct: float | None = None,
                     trading_days: int | None = None,
                     distinct_symbols: int | None = None,
                     n_field: int | None = None,
                     top_returns: list | None = None,
                     field_avg_pct: float | None = None) -> dict:
    """중계실에서 본 값을 저장한다.

    회전율·매매일수·매매종목수는 **주최측이 집계한 공식 수치**다. 내부 추정치는
    주문을 체결로 간주해 계산하므로 실제보다 낙관적으로 나온다. 공식 수치가
    들어오면 그쪽을 자격 판정의 기준으로 삼는다.

    미공개 회전율 공식 문제는 원리적으로 못 푸는 게 아니라 **관측으로 소거되는**
    문제다. 중계실이 매일 정답을 보여주므로 그걸 기록만 하면 된다.
    """
    obj = load_leaderboard()
    obj.update({
        "updated": datetime.now().isoformat(timespec="seconds"),
        "leader_return_pct": leader_return_pct,
        "my_rank": my_rank,
    })
    # **모르는 것을 0 으로 적지 않는다.**
    #
    # 자동 수집이 순위표에서 내 행을 못 찾았을 때 0.0 을 넣으면, 시스템은 그것을
    # 관측된 사실로 읽고 자체 추정을 버린다. "수익률을 모른다" 와 "수익률이 0%
    # 이다" 는 완전히 다른 말이고, 후자로 적으면 국면 판정이 통째로 틀어진다.
    # 값이 없으면 키를 건드리지 않아 기존 값이나 자체 추정이 살아 있게 둔다.
    if my_return_pct is not None:
        obj["my_return_pct"] = my_return_pct
    if turnover_pct is not None:
        obj["official_turnover_pct"] = turnover_pct
    if trading_days is not None:
        obj["official_trading_days"] = trading_days
    if distinct_symbols is not None:
        obj["official_distinct_symbols"] = distinct_symbols
    if n_field is not None:
        # 참가자수는 개막 전까지 늘어난다. 본 값이 있으면 설정 기본값보다 앞선다.
        obj["n_field"] = int(n_field)
    # 경쟁자의 **현재** 수익률(나 제외, 순위순). 결정 엔진이 상대의 출발선으로 쓴다.
    # 없으면 엔진은 전원 0% 에서 출발시키는데, 대회 중에는 그것이 추격·방어를 왜곡한다.
    if top_returns is not None:
        obj["top_returns"] = [float(x) for x in top_returns if x is not None]
    if field_avg_pct is not None:
        obj["field_avg_pct"] = float(field_avg_pct)
    _write(LEADERBOARD_FILE, obj)
    return obj


def turnover_evidence(cfg: dict) -> dict:
    """ETF 거래가 **공식 회전율**에 산입되는지 — 공식 수치와 내부 합계를 대조한다.

    매매종목수가 3 이라는 것은 ETF 가 종목수에 산입된다는 뜻이지 회전율 산입의
    증명이 아니다(외부 검토 지적). 증거는 회전율 자체에서 온다: ETF 거래금액이
    원금의 5% 이상 쌓인 뒤, 공식 회전율이 ETF 포함 합계에 맞는지 제외 합계에
    맞는지 본다. 둘이 뚜렷이 갈릴 때만 판정한다.
    """
    board = load_leaderboard()
    o = board.get("official_turnover_pct")
    principal = int(cfg.get("contest", {}).get("principal", 100_000_000)) or 1
    etf = {str(x) for x in (cfg.get("turnover_farm", {}).get("etf_pool") or [])}
    trades = load_trades()
    tot = sum(int(t.get("qty", 0)) * int(t.get("price", 0)) for t in trades)
    ex = sum(int(t.get("qty", 0)) * int(t.get("price", 0)) for t in trades
             if str(t.get("code")) not in etf)
    sell_tot = sum(int(t.get("qty", 0)) * int(t.get("price", 0)) for t in trades
                   if str(t.get("side", "")).upper() == "SELL")
    sell_ex = sum(int(t.get("qty", 0)) * int(t.get("price", 0)) for t in trades
                  if str(t.get("side", "")).upper() == "SELL" and str(t.get("code")) not in etf)
    out = {"official": o, "etf_amount": tot - ex,
           "incl_a": tot / principal * 100, "excl_a": ex / principal * 100,
           "incl_b": sell_tot / principal * 100, "excl_b": sell_ex / principal * 100,
           "verdict": None, "why": ""}
    if o is None or o <= 0:
        out["why"] = "공식 회전율 없음"
        return out
    if (tot - ex) < 0.05 * principal:
        out["why"] = "ETF 거래금액이 원금의 5% 미만 — 증거 부족"
        return out
    if abs(out["incl_a"] - out["excl_a"]) < 20:
        out["why"] = "포함/제외 차이가 작아 판별 불가"
        return out
    err_incl = min(abs(out["incl_a"] - o), abs(out["incl_b"] - o)) / o
    err_excl = min(abs(out["excl_a"] - o), abs(out["excl_b"] - o)) / o
    if err_incl < 0.15 and err_excl > 0.30:
        out["verdict"] = "confirmed"; out["why"] = "공식값이 ETF 포함 합계에 맞는다"
    elif err_excl < 0.15 and err_incl > 0.30:
        out["verdict"] = "refuted"; out["why"] = "공식값이 ETF 제외 합계에 맞는다"
    else:
        out["why"] = f"둘 다 애매 (포함 오차 {err_incl:.0%}, 제외 오차 {err_excl:.0%})"
    return out


FIELD_FILE = STATE_DIR / "field_history.json"


def load_field() -> dict | None:
    """가장 최근의 필드 분산 관측. 없으면 None.

    `run.py field` 가 중계실 상위권 수익률에서 추정해 기록한다.
    이 값이 없으면 `defend_threshold_pct` 는 하드코딩 기본값으로 돈다.
    """
    hist = _read(FIELD_FILE, [])
    return hist[-1] if hist else None
