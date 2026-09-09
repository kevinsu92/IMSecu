"""iM Rookie League 자동 모의투자 시스템 — 실행 진입점.

사용법
  python run.py check        환경·데이터·대회 일정 점검
  python run.py universe     오늘의 매매 가능 유니버스 산출
  python run.py signal       종목 점수 상위 랭킹 출력
  python run.py plan         오늘의 목표 포트폴리오 + 주문 리스트 생성
  python run.py status       수상 자격 요건 진척도
  python run.py fill         체결 결과 입력 (보유·회전율 갱신)
  python run.py board        중계실에서 본 순위/수익률 입력
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from imrl import alpha, data, notify, portfolio, state, turnover, universe

# Windows 콘솔 기본 코드페이지는 cp949 라 '⚠' 같은 문자를 만나면 print 가
# UnicodeEncodeError 로 죽는다. 실제로 15:20 집행 직전 리포트 출력에서 터졌다.
# 주문은 이미 저장된 뒤라 조용히 실패하는 것이 아니라 요란하게 실패하지만,
# 스케줄러 실행에서는 그 뒤 단계가 통째로 날아간다. 출력 인코딩을 UTF-8 로 고정한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass



ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "settings.json"



class _Tee:
    """표준출력을 파일에도 남긴다.

    스케줄러로 실행하면 콘솔이 없어 출력이 사라진다. `execute.py` 는 이미
    같은 장치를 갖고 있는데 `run.py` 에는 없었다 — 15:05 신호 산출이 실패해도
    **기록도 알림도 없이 조용히 사라지는** 상태였다. 무인 운영에서 가장 나쁜 형태다.
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a", encoding="utf-8")
        self.stdout = sys.stdout
        head = "=" * 60
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.file.write("\n" + head + "\n" + stamp + "  " + " ".join(sys.argv[1:]) + "\n")

    def write(self, s):
        self.stdout.write(s)
        self.file.write(s)
        self.file.flush()

    def flush(self):
        self.stdout.flush()
        self.file.flush()


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _calendar(cfg: dict) -> data.TradingCalendar:
    return data.TradingCalendar.from_config(cfg["contest"])


# --------------------------------------------------------------------------- #

def cmd_check(cfg: dict) -> None:
    cal = _calendar(cfg)
    days = cal.trading_days()
    today = date.today()
    print("=" * 62)
    print(f"대회: {cfg['contest']['name']}")
    print(f"기간: {cal.start} ~ {cal.end}   총 {cal.total_days()}영업일")
    print(f"오늘: {today}   잔여 {cal.days_left()}영업일")
    print(f"투자원금: {cfg['contest']['principal']:,}원")
    print(f"휴장일 설정: {', '.join(cfg['contest']['holidays'])}  (KRX 공지로 검증 필요)")
    print("=" * 62)
    print("영업일 목록:", ", ".join(d.strftime("%m/%d") for d in days))
    print()

    print("데이터 소스 점검")
    try:
        lst = data.load_listing()
        print(f"  [OK] 종목 마스터 {len(lst):,}종목")
    except Exception as exc:
        print(f"  [실패] 종목 마스터: {exc}")
    try:
        alerts = data.fetch_alert_codes()
        print("  [OK] 시장경보 " + ", ".join(f"{k} {len(v)}" for k, v in alerts.items()))
    except Exception as exc:
        print(f"  [실패] 시장경보: {exc}")
    try:
        q = data.fetch_quote("005930")
        print(f"  [OK] 실시간 시세 삼성전자 {q['price']:,}원 ({q['change_pct']:+.2f}%)")
    except Exception as exc:
        print(f"  [실패] 실시간 시세: {exc}")
    try:
        d = data.fetch_daily("005930", 40)
        print(f"  [OK] 일봉 {len(d)}봉, 최종 {d['date'].iloc[-1]:%Y-%m-%d}")
    except Exception as exc:
        print(f"  [실패] 일봉: {exc}")


def cmd_universe(cfg: dict) -> pd.DataFrame:
    uni = universe.build(cfg)
    print(universe.describe(uni))
    print(f"\n최종 매매 가능 종목: {len(uni):,}개")
    print(uni.nlargest(10, "amount")[["code", "name", "market", "close", "amount"]].to_string(index=False))
    return uni


def cmd_signal(cfg: dict) -> pd.DataFrame:
    uni = universe.build(cfg)
    print(f"유니버스 {len(uni):,}종목")
    scored = alpha.rank_universe(cfg, uni)
    if scored.empty:
        print("조건을 만족하는 종목이 없다. (상승 추세 종목 부재)")
        return scored
    cols = ["code", "name", "market", "close", "ret5", "ret20", "amount_surge", "volatility", "score"]
    view = scored.head(20)[cols].copy()
    for c in ["ret5", "ret20"]:
        view[c] = (view[c] * 100).round(1)
    view["volatility"] = (view["volatility"] * 100).round(2)
    view["amount_surge"] = view["amount_surge"].round(2)
    view["score"] = view["score"].round(2)
    print("\n상위 20종목")
    print(view.to_string(index=False))
    return scored


def _farm_pool(cfg: dict, uni, positions: dict, days_left: int, total_days: int):
    """회전율 파밍에 쓸 종목 풀. ETF 산입이 확인되기 전/후로 갈린다."""
    rs = cfg.get("rules_status", {})
    if rs.get("etf_counts_for_turnover") is False and "etf_counts_for_turnover" in rs             and rs.get("_confirmed_at"):
        # 관측으로 **제외가 확정된** 경우에만 주식 폴백을 쓴다.
        # 아직 확인 전(기본 false)에는 ETF 로 진행한다 - 1일차 관측 자체가
        # ETF 1주 매수로 이루어지기 때문이다.
        day_index = max(total_days - days_left, 0)
        pool = turnover.stock_pool(uni, set(positions), day_index, n=2)
        if pool:
            print(f"  ETF 제외 확정 - 개별주식 폴백: "
                  f"{', '.join(p['name'] for p in pool)}")
            return pool
        print("  ETF 제외 확정인데 폴백 종목을 고르지 못했다. 파밍을 건너뛴다.")
        return []
    return None      # None 이면 turnover 가 기본 ETF 풀을 쓴다


def _mark_step(name: str, rc: int) -> None:
    """단계 완료 표시. 감시자와 모니터가 이 파일을 본다.

    예전에는 감시자가 자기 자식 프로세스의 종료코드로만 표시를 남겼다. 그래서
    사람이 콘솔에서 직접 돌리면 실제로 끝났는데도 모니터가 계속 '대기' 로
    보여줬다. 어느 경로로 돌든 같은 사실이 남아야 한다.
    """
    if rc == 0:
        try:
            (ROOT / "state" / f"{name}_{date.today():%Y%m%d}.done").touch()
        except OSError:
            pass


def _unresolved_orders(lookback_days: int = 7) -> list[str]:
    """접수 여부가 확정되지 않은 주문 키.

    실행 게이트가 본다. 불명 주문이 하나라도 있으면 계좌 상태를 신뢰할 수 없다.

    **오늘 것만 보면 안 된다.** 계획은 15:05 에 도는데 그날 주문은 15:10 부터
    나가므로 오늘 원장은 항상 비어 있다. 정작 계좌를 불확실하게 만드는 것은
    어제 15:21 에 접수 불명으로 끝난 주문이다. 그래서 최근 며칠을 함께 본다.
    """
    out: list[str] = []
    for back in range(lookback_days):
        day = date.today() - timedelta(days=back)
        p = ROOT / "state" / f"submitted_{day:%Y%m%d}.json"
        if not p.exists():
            continue
        try:
            rows = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            out.append(f"LEDGER_UNREADABLE:{day:%Y%m%d}")
            continue
        last: dict[str, str] = {}
        for r in rows:
            last[str(r.get("key"))] = str(r.get("status", "submitted"))
        out += [f"{day:%m%d}/{k}" for k, s in last.items()
                if s in ("unknown", "pending_send", "modal")]
    return out


def cmd_plan(cfg: dict) -> None:
    cal = _calendar(cfg)
    days_left = cal.days_left()
    principal = cfg["contest"]["principal"]

    board = state.load_leaderboard()

    # 원장이 보유보다 새로우면(기록 도중 끊긴 흔적) 보유를 원장에서 다시 만든다.
    if state.heal_positions():
        print("보유를 원장에서 다시 만들었다 (이전 기록이 도중에 끊긴 흔적).")

    # 내 수익률: 중계실 공식 수치가 **오늘 것이면** 그것을 쓰고, 없거나 오래됐으면
    # 보유 포지션과 시세로 직접 추정한다.
    #
    # 예전에는 없으면 0.0% 로 뒀는데, 그건 "손실이 없다"고 단정하는 것과 같다.
    # 저녁 입력을 하루 빠뜨리면 마이너스인데도 phase_early 로 돌아간다. 무인 운영이
    # 전제인 시스템에서 사람 손 하나가 국면 전체를 뒤집는 구조였다.
    # 남들 수익률(리더보드)은 웹에서만 얻지만, **내 수익률은 내가 안다.**
    my_ret = None
    src = "중계실"
    stamp = str(board.get("updated", ""))[:10]
    if board.get("my_return_pct") is not None and stamp == f"{date.today():%Y-%m-%d}":
        my_ret = float(board["my_return_pct"])
    else:
        held = state.load_positions()
        if held:
            pq = {}
            for c in held:
                try:
                    pq[c] = data.fetch_quote(c)
                except Exception:
                    pass
            my_ret = state.estimate_my_return(pq, principal)
            src = "자체 추정"
        if my_ret is None:
            my_ret = float(board.get("my_return_pct", 0.0) or 0.0)
            src = "중계실(오래됨)" if board.get("my_return_pct") is not None else "기본 0%"

    gap = None
    if board.get("leader_return_pct") is not None and stamp == f"{date.today():%Y-%m-%d}":
        gap = float(board["leader_return_pct"]) - my_ret

    phase = portfolio.choose_phase(cfg, days_left, my_ret, gap)
    print(f"잔여 {days_left}영업일 / 내 수익률 {my_ret:+.2f}% ({src}) / 1위와 격차 "
          f"{'미확보' if gap is None else f'{gap:+.2f}%p'}  →  국면: {phase}")

    uni = universe.build(cfg)
    scored = alpha.rank_universe(cfg, uni, phase=phase)
    if scored.empty:
        print("매수 후보 없음. 현금 보유 유지.")
        return

    # 주문 직전 거래정지·신규상장 최종 검증
    top_codes = scored.head(cfg["portfolio"][phase]["top_n"] * 3)["code"].tolist()
    ok, rejected = universe.verify_tradable(top_codes)
    if rejected:
        print("최종 검증 탈락: " + ", ".join(f"{c}({r})" for c, r in rejected))
    scored = scored[scored["code"].isin(ok)].reset_index(drop=True)

    # 상한가 종목을 **목표 선정 전에** 걸러낸다.
    #
    # 예전에는 목표를 정한 뒤 make_orders 에서 상한가를 건너뛰고 남은 종목에
    # 비중을 재분배했다. 그런데 top2 x 45% 는 이미 규정 상한(단일종목 원금 45%)에
    # 붙어 있어 **재분배할 여유가 0 이다** — 실측에서 배수가 정확히 x1.000 이 나오고
    # 45% 가 현금으로 방치됐다. 하필 시장이 가장 강한 날에 노출이 절반이 된다.
    #
    # 여기서 미리 빼면 차순위 종목이 자연스럽게 올라온다. 재분배가 아니라 승격이다.
    lim_quotes = {}
    for c in scored.head(cfg["portfolio"][phase]["top_n"] * 3)["code"].tolist():
        try:
            lim_quotes[c] = data.fetch_quote(c)
        except Exception:
            pass
    hot = [c for c, q in lim_quotes.items()
           if q.get("change_pct", 0) >= portfolio.UPPER_LIMIT_PCT]
    if hot:
        names = {r.code: r.name for r in scored.itertuples()}
        print("상한가 근접으로 사전 제외(차순위 승격): "
              + ", ".join(f"{names.get(c, c)}({c})" for c in hot))
        scored = scored[~scored["code"].isin(hot)].reset_index(drop=True)
        if scored.empty:
            print("상한가 제외 후 후보가 없다. 현금 보유 유지.")
            return

    # 15:05 의 최종 후보 목록을 남긴다. 시간별 사이클이 이 목록으로 뉴스를 본다.
    try:
        from imrl import cycle as _cycle
        _cycle.save_candidates(scored, cfg["portfolio"][phase]["top_n"], phase)
    except Exception as exc:
        print(f"  후보 목록 기록 실패 ({type(exc).__name__}) — 계획은 계속한다")

    equity = principal + int(principal * my_ret / 100)
    positions = state.load_positions()

    # 회전율 계획을 **알파북 사이징보다 먼저** 세운다.
    #
    # 예전에는 목표비중(자본의 98%)을 먼저 정하고 그 뒤에 ETF 회전 주문을 덧붙였다.
    # 그러면 ETF 를 살 현금이 남아 있지 않아 계획은 세워지지만 주문은 거부된다.
    # 회전율 500%는 수상의 하드 요건이라 못 채우면 수익률 1등이어도 상금이 0원이다.
    # 따라서 파밍 몫을 먼저 떼고 남은 돈으로 알파북을 짠다.
    #
    # 떼는 금액은 크지 않다. 실측(421종목 935일, 현 가중치 기준) 상위 3종목의
    # 일평균 교체율이 23.0% 라 자연 매도만으로 20일 누적 450% 가 나온다.
    # 부족분만 채우면 되므로 스리브는 하루치 파밍액 한 번이면 충분하고,
    # 그 이후로는 어제 판 대금으로 오늘 사는 회전이 된다.
    st_now = state.compute_constraints(principal, days_left)
    # 목표는 규정 하한(500%)이 아니라 안전마진을 얹은 값이다.
    # 예전에는 config 의 target_pct_with_margin(650) 을 아무도 안 읽고
    # turnover.required_pace 의 하드코딩 safety=1.30 이 우연히 같은 값을 만들었다.
    # 대회 중 config 를 올려도 동작이 안 바뀌는 상태였다.
    tplan = turnover.plan_daily(
        cfg["turnover_farm"].get("target_pct_with_margin",
                                 cfg["requirements"]["min_turnover_pct"]),
        st_now.effective_turnover,
        # 회전율 마감을 **마지막 2영업일 앞으로 당긴다.**
        #
        # 중계실 회전율이 D+1 반영이면 최종일 매매는 산입되지 않는다. 마지막 날
        # 파밍으로 요건을 채우려다 반영이 안 되면 그대로 실격이다.
        # 남은 일수를 2일 적게 보면 페이스가 앞당겨지고 마지막 2일이 여유분이 된다.
        #
        # 주의: total_days 를 줄이면 안 된다. elapsed = total_days - days_left 라
        # 총일수를 줄이면 경과일도 같이 줄어 요구 페이스가 오히려 **낮아진다.**
        max(days_left - 2, 0),
        principal,
        positions,
        # ETF 가 자격 집계에 산입되지 않는 것으로 확인되면 개별주식으로 채운다.
        #
        # 1일차 관측(`run.py etfcheck`)이 2 를 돌려주면 CD금리 ETF 경로가 통째로
        # 무효다. 자연 회전 481% 로는 요건 500% 를 못 넘으므로 부족분을 주식으로
        # 채우는 수밖에 없다. 알파 상위가 아니라 **저변동 종목을 매일 돌려가며**
        # 쓴다 - 파밍은 수익이 아니라 자격을 위한 거래라 방향성 노출을 최소화해야
        # 하고, 같은 종목을 반복 왕복하면 규정의 '반복적 단일 종목 거래' 에 걸린다.
        pool=_farm_pool(cfg, uni, positions, days_left, cal.total_days()),
        max_daily_ratio=cfg["turnover_farm"].get("max_daily_farm_ratio", 0.30),
        total_days=cal.total_days(),
    )

    # 1일차는 **관측일**이다.
    #
    # 회전율 마감을 D-2 로 당긴 결과 1일차에 elapsed=2 가 되어 650 x 2/20 = 65%p
    # 어치 파밍이 즉시 요구된다. 그런데 ETF 가 회전율·매매종목수에 산입되는지
    # 자체가 아직 미확인이다. 산입되지 않는 것으로 판명되면 그 매수는 순수
    # 낭비이고, 그 전에 65%p 를 사 버리면 **관측 자체가 오염된다** — 중계실
    # 회전율이 움직인 것이 알파북 매수 때문인지 ETF 때문인지 구분이 안 된다.
    #
    # 그래서 1일차에는 ETF 를 1주만 왕복시켜 산입 여부만 본다. 2일차부터는 남은
    # 18일에 페이스가 자동으로 재분배되므로 총량 손실이 없다.
    elapsed = cal.total_days() - days_left
    # ETF 산입 여부가 이미 확정됐으면 관측할 것이 없다.
    _rs = cfg.get("rules_status", {})
    _need_probe = not _rs.get("_confirmed_at")
    if (cfg["turnover_farm"].get("probe_first_day", True) and _need_probe
            and elapsed <= 0 and tplan.buys):
        # 수량만 지정한다. 이 시점에는 ETF 시세가 없어 금액을 만들 수 없고,
        # 금액으로 역산하게 두면 amount=0 -> qty=0 으로 주문이 사라진다.
        first = dict(tplan.buys[0])
        first.update(qty=1, amount=0, reason="1일차 ETF 산입 여부 관측용 1주")
        tplan.buys = [first]
        tplan.note = ("1일차 관측 모드 — ETF 1주만 매수한다. 회전율·매매종목수에 "
                      "산입되는지 확인한 뒤 2일차부터 정상 파밍한다.")
    print(f"\n회전율: {tplan.note}")

    farm_buy = sum(int(b.get("amount", 0)) for b in tplan.buys)
    farm_sell_value = 0
    for sell in tplan.sells:
        held = positions.get(sell["code"], {})
        farm_sell_value += int(sell.get("qty", 0)) * int(held.get("avg_price", 0) or 0)
    # 어제 산 ETF 를 오늘 팔면 그 대금이 오늘 매수 재원이 된다. 순증분만 뗀다.
    farm_reserve = max(0, farm_buy - farm_sell_value)

    # 파밍 예약금에 상한을 둔다.
    #
    # farm_reserve 는 equity 에서 **먼저** 빠지고 남은 돈으로 알파북을 짠다.
    # 상한이 없으면 파밍 수요가 큰 날 알파 노출이 90% -> 58% 까지 내려간다.
    # 자격요건은 채워야 하지만 그 대가로 수익률 경쟁에서 빠지면 순서가 뒤바뀐다.
    #
    # 막판 5일은 상한을 푼다. 그 시점에는 자격 미달이 확정적 0원이라 알파보다
    # 우선한다. 그전에는 부족분을 다음 날로 이월하는 편이 낫다 — 페이스 계산이
    # 남은 일수로 다시 나누므로 자동으로 만회된다.
    farm_cap_ratio = float(cfg["turnover_farm"].get("max_reserve_ratio", 0.20))
    if days_left > 5 and equity and farm_reserve > farm_cap_ratio * equity:
        capped = int(farm_cap_ratio * equity)
        print(f"  파밍 예약금 {farm_reserve:,}원이 알파 노출을 과도하게 줄인다 "
              f"({farm_cap_ratio:.0%} 상한 적용 -> {capped:,}원). 부족분은 이월된다.")
        farm_reserve = capped

    alpha_equity = max(0, equity - farm_reserve)
    if farm_reserve:
        print(f"  회전율 파밍용 현금 {farm_reserve:,}원을 먼저 확보한다 "
              f"(알파북 가용 {alpha_equity:,}원)")

    targets = portfolio.build_targets(cfg, scored, phase, alpha_equity, principal)

    # 결정 엔진 실행 연결 (명세 18절 G4).
    #
    # 엔진은 **목표 비중만** 바꾼다. 주문 생성(make_orders), 규정 검사, 리스크
    # 게이트, 중복 방지, 원장은 전부 기존 경로가 그대로 한다. 새 엔진이 자기
    # 주문 경로를 따로 가지면 지금까지 만든 방어가 통째로 우회된다.
    #
    # 게이트가 하나라도 걸리면 기준 정책(위에서 만든 targets)을 그대로 쓴다.
    # 계좌·주문 상태가 불명이면 기준 정책도 못 돌리므로 주문 자체를 만들지 않는다.
    if cfg.get("decision_engine", {}).get("mode") == "execute":
        from imrl import decision_cmd
        unresolved = _unresolved_orders()
        w, dsn, reasons, fallback = decision_cmd.decide_for_execution(
            cfg, positions, unresolved, scored=scored, uni=uni)
        if w is not None:
            rows = []
            for _, r in scored.iterrows():
                if r["code"] in w and w[r["code"]] > 0:
                    rows.append({"code": r["code"], "name": r["name"],
                                 "market": r["market"], "close": r["close"],
                                 "score": r["score"], "target_weight": w[r["code"]]})
            if rows:
                targets = pd.DataFrame(rows)
                print(f"  결정 엔진 실행 연결: {dsn.execution_action} "
                      f"({len(rows)}종목, 노출 {sum(w.values()):.0%})")
            elif len(w) == 0:
                # CASH: 빈 비중은 "결정 없음"이 아니라 **전량 현금화**다. 예전에는
                # `if w:` 라 빈 딕셔너리가 거짓으로 떨어져 기준 매수 목표가 그대로 남았다.
                targets = targets.iloc[0:0]
                print(f"  결정 엔진 실행 연결: {dsn.execution_action} "
                      "(현금화 — 보유 매도만 만들고 신규 매수는 없다)")
            else:
                print("  결정 엔진이 고른 종목이 점수표에 없다 — 기준 정책을 유지한다.")
        else:
            print("  결정 엔진 실행 게이트 차단: " + ", ".join(reasons))
            if fallback == "NO_NEW_ORDERS":
                print("  계좌·주문 상태가 확정되지 않아 신규 주문을 만들지 않는다.")
                notify.send("[IMRL] 계좌·주문 상태 미확정으로 주문 생성 중단: "
                            + ", ".join(reasons))
                return
            print("  기준 정책으로 진행한다.")

    problems = portfolio.check_rule_violations(cfg, targets, alpha_equity, principal)
    if problems:
        print("규정 위반 감지 — 주문 생성 중단")
        for p in problems:
            print("  " + p)
        return

    codes = sorted(set(targets["code"]) | set(positions))
    quotes = {c: v for c, v in lim_quotes.items() if c in codes}
    for c in [x for x in codes if x not in quotes]:
        try:
            quotes[c] = data.fetch_quote(c)
        except Exception:
            pass

    orders = portfolio.make_orders(cfg, targets, positions, alpha_equity, quotes)

    if not tplan.is_empty:
        for c in [b["code"] for b in tplan.buys] + [s["code"] for s in tplan.sells]:
            if c not in quotes:
                try:
                    quotes[c] = data.fetch_quote(c)
                except Exception:
                    pass
        orders = portfolio.add_turnover_orders(orders, tplan, quotes, positions, cfg)

    # 최종 현금 검증. 규정 검사는 알파북만 보므로, ETF 파밍 주문까지 합친
    # 실제 필요 예수금은 여기서만 확인된다. 매도 대금이 먼저 들어오는 것을
    # 감안하되, 매도가 미체결일 수 있으므로 **매도 대금을 세지 않은** 보수적
    # 기준도 함께 본다. 예수금 부족으로 거부되면 그날 포지션이 통째로 빈다.
    buy_total = sum(o.qty * o.limit_price for o in orders if o.side == "BUY")
    sell_total = sum(o.qty * o.limit_price for o in orders if o.side == "SELL")
    # 예수금 추정: equity 에서 보유 **평가액**을 뺀다.
    #
    # 예전에는 취득원가(avg_price)로 뺐다. 그러면
    #     cash = equity - 취득원가합 = 예수금 + 평가손익
    # 이 되어 **평가익만큼 유령 현금이 잡힌다.** 계좌가 +40% 면 없는 돈 3,600만원이
    # 잡히고, 그 상태로 예수금 부족 주문이 경고도 없이 나간다. 이 오류도 다른
    # 것들과 마찬가지로 이기고 있을 때만 발동한다.
    #
    # 현재가를 쓰되 취득원가보다 낮으면 취득원가를 쓴다 — 보유 평가액을 과대
    # 평가하는 방향이라 예수금 추정이 보수적(과소)으로 나온다.
    held_value = 0
    for _code, _pos in positions.items():
        _q = int(_pos.get("qty", 0) or 0)
        if _q <= 0:
            continue
        _avg = int(_pos.get("avg_price", 0) or 0)
        _cur = int((quotes.get(_code) or {}).get("price", 0) or 0)
        held_value += _q * max(_avg, _cur)
    cash_now = max(0, equity - held_value)
    print(f"\n주문 합계  매수 {buy_total:,}원 / 매도 {sell_total:,}원 "
          f"(추정 예수금 {cash_now:,}원)")
    # 매도 대금까지 합쳐도 매수를 못 대면 **보내지 않는다.**
    #
    # 예전에는 경고만 찍고 그대로 전송했다. HTS 가 예수금 부족으로 거부하는데
    # 체결 확인 수단이 없어 아무도 모른 채 하루가 지나간다. 더 나쁜 것은 그
    # 주문서를 사람이 `run.py fill` 로 체결 기록하면 회전율이 **과대** 계상된다는
    # 점이다 — 요건 미달을 충족으로 착각하는 정확히 그 실패다.
    if buy_total > cash_now + sell_total:
        msg = (f"예수금 부족: 매수 {buy_total:,}원 > 추정 예수금 {cash_now:,}원 "
               f"+ 매도 {sell_total:,}원. 주문 생성을 중단한다.")
        print("  " + msg)
        notify.send("[IMRL] " + msg)
        return
    if buy_total > equity:
        print("  주문 총액이 자본을 초과한다 — 주문 생성 중단")
        return

    # 규정 검사를 **최종 주문서**에 다시 건다.
    #
    # 위쪽 check_rule_violations 는 targets 만 보므로 ETF 파밍 레그가 붙기 전에
    # 돈다. 비중 합계 검사가 파밍분을 영영 못 보는 구조였다.
    order_problems = portfolio.check_order_rules(cfg, orders, equity, principal)
    if order_problems:
        print("최종 주문서 규정 위반 — 주문 생성 중단")
        for _p in order_problems:
            print("  " + _p)
        notify.send("[IMRL] 최종 주문서 규정 위반으로 중단: " + " / ".join(order_problems))
        return

    print("\n목표 포트폴리오")
    print(targets[["code", "name", "market", "close", "score", "target_weight"]].to_string(index=False))

    text = notify.format_orders(orders, equity)
    print()
    notify.send(text)

    out = ROOT / "state" / f"orders_{date.today():%Y%m%d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([o.to_dict() for o in orders], ensure_ascii=False, indent=2), encoding="utf-8")
    # 리스크 게이트가 쓸 예수금 추정치를 옆에 남긴다. 주문 배열 형식을 바꾸면
    # execute.py 의 기존 파서가 깨지므로 별도 파일로 둔다.
    (out.parent / f"meta_{date.today():%Y%m%d}.json").write_text(
        json.dumps({"cash_estimate": int(cash_now), "equity": int(equity),
                    "principal": int(principal), "phase": phase,
                    "buy_total": int(buy_total), "sell_total": int(sell_total)},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n주문서 저장: {out}")

    st = state.compute_constraints(principal, days_left)
    print()
    print(st.report(cfg["requirements"]))


def cmd_status(cfg: dict, push: bool = False) -> None:
    # 중계실을 먼저 읽는다. 주최측 공식 수치가 있으면 내부 추정을 버릴 수 있고,
    # 무엇보다 **소급 취득이 불가능한** 필드 관측이 매일 자동으로 쌓인다.
    # 실패해도 리포트는 그대로 나간다 - 순위 수집은 매매에 필요한 게 아니다.
    if cfg.get("contest", {}).get("league_code"):
        try:
            _relay_auto(cfg, argparse.Namespace(
                league=None, pen=None, mine=None, rank=None,
                turnover=None, days=None, symbols=None))
            print()
        except Exception as exc:
            print(f"중계실 수집 실패 ({type(exc).__name__}) — 리포트는 계속한다.")
            print()

    cal = _calendar(cfg)
    st = state.compute_constraints(cfg["contest"]["principal"], cal.days_left())
    report = st.report(cfg["requirements"])
    print(report)
    pos = state.load_positions()
    if pos:
        print("\n보유 종목")
        for c, p in pos.items():
            print(f"  {p.get('name', c)} ({c})  {p['qty']:,}주  평단 {p.get('avg_price', 0):,}원")
    else:
        print("\n보유 종목 없음")

    # 상황판을 다시 그린다. 사람이 보는 유일한 화면이라 리포트마다 갱신한다.
    # 뉴스까지 긁으면 수십 초가 걸리지만 16:00 에는 급한 것이 없다.
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import dashboard as _dash
        print(f"상황판 갱신: {_dash.build(with_news=True).name}")
    except Exception as exc:
        print(f"상황판 갱신 실패 ({type(exc).__name__}: {exc}) — 리포트는 계속한다.")

    # HTS 세션이 얼마나 지속되는지 — 매일 현재 판정을 함께 싣는다.
    #
    # 사람이 `monitor.py --session` 을 돌려봐야 나오는 결론은 아무도 안 본다.
    # 답이 필요한 질문("아침 로그인이 매일 필요한가")이라 리포트에 붙인다.
    from imrl import session as _sess
    v = _sess.verdict()
    print()
    print(f"HTS 세션: {v.text}")

    if push:
        board = state.load_leaderboard()
        body = notify.format_status(report, pos, board.get("my_return_pct"))
        notify.send(body + f"{chr(10)}{chr(10)}HTS 세션: {v.text}")

        # 결론이 **처음 확정되는 날** 한 번만 따로 알린다. 매일 같은 문장을
        # 보내면 알림이 배경 소음이 되고, 정작 봐야 할 경고가 묻힌다.
        if v.determined and _sess.mark_announced(v.kind):
            if v.kind == "per_boot":
                notify.alert("로그인 주기 확정 — 재부팅할 때만",
                             v.text + f"{chr(10)}PC 를 켜두면 아침마다 로그인하지 "
                                      "않아도 된다.")
            else:
                notify.alert("로그인 주기 확정 — 매일 필요",
                             v.text + f"{chr(10)}매일 아침 HTS 로그인이 필요하다.")

    # 대회 막바지에 요건이 비면 실격이므로, 남은 날이 적을수록 강하게 경보한다.
    unmet = st.unmet(cfg["requirements"])
    if unmet and cal.days_left() <= 5:
        notify.alert("수상 자격 요건 미달",
                     f"잔여 {cal.days_left()}영업일인데 미충족: {', '.join(unmet)}")


def cmd_fill(cfg: dict, args) -> None:
    """체결 결과를 기록한다.

    예) python run.py fill --side BUY --code 005930 --qty 100 --price 255500
        python run.py fill --from-orders            (오늘 주문서를 전량 체결로 간주)
    """
    fills: list[dict] = []
    today = datetime.now().isoformat(timespec="seconds")

    if args.from_orders:
        path = ROOT / "state" / f"orders_{date.today():%Y%m%d}.json"
        if not path.exists():
            print(f"주문서가 없다: {path}")
            return
        for o in json.loads(path.read_text(encoding="utf-8")):
            fills.append({"date": today, "side": o["side"], "code": o["code"], "name": o["name"],
                          "market": o["market"], "qty": o["qty"], "price": o["limit_price"]})
    else:
        if not all([args.side, args.code, args.qty, args.price]):
            print("--side --code --qty --price 를 모두 지정하거나 --from-orders 를 쓸 것")
            return
        fills.append({"date": today, "side": args.side.upper(), "code": args.code,
                      "name": args.name or args.code, "market": args.market or "",
                      "qty": int(args.qty), "price": int(args.price)})

    # 확정 체결은 같은 날·방향·종목의 **추정** 체결(접수 기준 assumed)을 대체한다.
    # 더하면 보유가 두 배가 된다 — 추정 매수 100주 뒤 실제 40주를 넣으면 140주였다.
    day = today[:10]
    removed = state.drop_assumed(day, [(f["side"], f["code"]) for f in fills])
    if removed:
        print(f"같은 날 추정 체결 {removed}건을 지우고 확정 체결로 대체한다.")

    # 없는 것을 팔 수 없다. 초과 매도를 조용히 0 으로 자르면 잘못된 입력이
    # 정상 잔고로 둔갑한다. 기록하지 않고 거부한다.
    held_now = state.load_positions()
    for f in fills:
        if str(f["side"]).upper() == "SELL":
            have = int(held_now.get(f["code"], {}).get("qty", 0) or 0)
            if int(f["qty"]) > have:
                print(f"거부: {f.get('name', f['code'])}({f['code']}) 매도 {int(f['qty']):,}주 > "
                      f"보유 {have:,}주. 보유가 틀렸으면 먼저 원장을 고칠 것 (기록하지 않았다).")
                return

    # 보유 반영은 **새로 기록된 체결만** 받는다. 같은 체결을 두 번 입력하면
    # 회전율과 보유 수량이 함께 두 배가 되는데, 회전율 과대계상은 요건 미달을
    # 충족으로 착각하게 만드는 방향이라 특히 나쁘다.
    added, dup = state.record_trades(fills)
    pos = state.apply_fills_to_positions(added)
    print(f"{len(added)}건 기록 완료" + (f" (중복 {dup}건 무시)" if dup else ""))
    for c, p in pos.items():
        print(f"  {p.get('name', c)} ({c})  {p['qty']:,}주  평단 {p.get('avg_price', 0):,}원")


def cmd_reconcile(cfg: dict, args) -> None:
    """주문서 · 전송기록 · 체결기록 · 보유를 서로 대조한다.

    왜 필요한가
      이 시스템에는 네 종류의 "사실"이 있고 서로 어긋날 수 있다.
        1) orders_YYYYMMDD.json  — 내려고 한 주문
        2) submitted_YYYYMMDD.json — 실제로 전송한 주문
        3) trades.json            — 체결됐다고 기록된 것
        4) positions.json         — 그 결과로 들고 있다고 믿는 것
      HTS 가 재시작되거나 전송 도중 끊기면 이 넷이 갈라진다. 그 상태로 다음 날
      주문을 만들면 **없는 포지션을 팔고 있는 포지션을 또 산다.**

      부분체결도 여기서 드러난다. 전송 수량과 체결 수량의 차이가 곧 미체결 잔량이다.

    이 명령은 자동으로 고치지 않는다. **차이를 보여주고 사람이 확인하게 한다.**
    HTS 잔고 화면을 자동으로 읽지 못하는 한, 조용한 자동 보정은 틀린 값을
    사실로 굳히는 쪽으로만 작동한다.
    """
    day = args.date or f"{date.today():%Y%m%d}"
    sd = ROOT / "state"

    def _load(path, default):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    if state.heal_positions():
        print("보유를 원장에서 다시 만들었다 (이전 기록이 도중에 끊긴 흔적).")
    orders = _load(sd / f"orders_{day}.json", [])
    submitted = _load(sd / f"submitted_{day}.json", [])
    trades = [t for t in state.load_trades() if str(t.get("date", ""))[:10].replace("-", "") == day]
    positions = state.load_positions()

    print(f"대조 기준일 {day}")
    print(f"  주문서 {len(orders)}건 / 전송기록 {len(submitted)}건 / "
          f"체결기록 {len(trades)}건 / 보유 {len(positions)}종목")
    print()

    # 원장은 시도마다 한 줄이다. **키별 마지막 상태**만이 현재 상태다.
    # 예전에는 전체 이력에서 pending 을 찾아, 정상적으로 pending_send → submitted 가
    # 된 주문도 "접수 불명"으로 계속 보고했다(외부 검토 재현).
    last: dict[str, dict] = {}
    for r in submitted:
        last[str(r.get("key", ""))] = r

    # 분할 매도는 원장 키가 `SELL:종목#1/3` 형태다. 주문서 쪽 키(`SELL:종목`)로
    # 그냥 찾으면 세 조각이 모두 접수됐어도 "전송 안 됨"으로 나온다.
    # 부모 키로 정규화해 대조하고, 접수 확정된 것만 전송으로 센다.
    sub_keys = set()
    for k, r in last.items():
        if r.get("status", "submitted") != "submitted":
            continue
        sub_keys.add(k.split("#", 1)[0])
    filled: dict[tuple, int] = {}
    assumed_only: dict[tuple, bool] = {}
    for t in trades:
        k = (t["side"].upper(), t["code"])
        filled[k] = filled.get(k, 0) + int(t.get("qty", 0))
        assumed_only[k] = assumed_only.get(k, True) and bool(t.get("assumed"))
    n_assumed = sum(1 for t in trades if t.get("assumed"))

    issues = 0
    print(f"{'방향':<5}{'종목':<10}{'주문':>8}{'전송':>6}{'체결':>8}{'미체결':>8}  상태")
    for o in orders:
        k = (o["side"].upper(), o["code"])
        key = f"{o['side']}:{o['code']}"
        was_sent = key in sub_keys
        got = filled.get(k, 0)
        want = int(o["qty"])
        rest = want - got
        if not was_sent:
            stt, issues = "전송 안 됨", issues + 1
        elif got == 0:
            stt, issues = "전량 미체결", issues + 1
        elif rest > 0:
            stt, issues = f"부분체결 {got/want:.0%}", issues + 1
        elif rest < 0:
            stt, issues = "체결이 주문보다 많다(기록 오류)", issues + 1
        elif assumed_only.get(k):
            # 접수를 근거로 **가정한** 체결이다. 체결 확인 수단이 없는 한 사실이 아니다.
            stt = "추정 체결(접수 기준, 미확인)"
        else:
            stt = "완전체결"
        print(f"{o['side']:<5}{o['name'][:8]:<10}{want:>8,}{'O' if was_sent else 'X':>6}"
              f"{got:>8,}{max(rest,0):>8,}  {stt}")

    # 접수 여부가 불명인 주문 — 사람이 HTS 주문내역과 대조해야 풀린다.
    #
    # 15:40 대조는 그날의 마지막 점검이다. 여기서 안 보이면 불명 주문이 그대로
    # 묻히고, 다음 날 재시도가 계속 건너뛴다(다시 누르지 않는 것이 안전 정책이라
    # 조용히 영영 안 나간다). 그래서 별도로, 눈에 띄게 보고한다.
    pending = [r for r in last.values()
               if r.get("status") in ("unknown", "pending_send", "modal")]
    if pending:
        print()
        print("★ 접수 여부 불명 — HTS 주문내역과 대조가 필요하다")
        for r in pending:
            print(f"  {r.get('key')}  {r.get('name', r.get('code'))} "
                  f"{int(r.get('qty') or 0):,}주 @ {int(r.get('price') or 0):,}원 "
                  f"— {r.get('status')}")
        print(f"  확인 후 state/submitted_{day}.json 의 status 를 "
              "'submitted' 또는 'not_accepted' 로 고칠 것.")
        issues += len(pending)
        try:
            notify.alert("접수 불명 주문 확인 필요",
                         chr(10).join(
                             [f"{r.get('key')} {r.get('status')}" for r in pending]
                             + ["HTS 주문내역과 대조해 status 를 고칠 것."]))
        except Exception as exc:
            print(f"  알림 실패: {exc}")

    # 주문서에 없는데 체결된 것 — 수동 매매나 기록 오류
    order_keys = {(o["side"].upper(), o["code"]) for o in orders}
    for k in filled:
        if k not in order_keys:
            print(f"{k[0]:<5}{k[1]:<10}{'-':>8}{'-':>6}{filled[k]:>8,}{'-':>8}  주문서에 없는 체결")
            issues += 1

    print()
    if positions:
        print("보유 종목")
        for c, pv in positions.items():
            held = state.days_held(pv)
            print(f"  {pv.get('name', c)}({c})  {pv['qty']:,}주  평단 {pv.get('avg_price', 0):,}원  "
                  f"진입 {pv.get('entry_date') or '미상'} (보유 {held}영업일)")

    print()
    if issues:
        print(f"불일치 {issues}건. HTS 잔고·체결내역 화면과 대조해 "
              "`run.py fill` 로 실제 체결을 입력할 것.")
        print("자동 보정하지 않는다 — 확인되지 않은 값을 사실로 굳히는 쪽으로만 작동한다.")
    else:
        print("불일치 없음.")

    # 대조 **결과**를 파일로 남긴다. "대조를 돌렸다"(done 표식)와 "계좌가 맞다"(PASS)는
    # 다른 사실이다. 체결 확인 수단이 없는 날은 추정 체결이 남으므로 PASS 가 아니라
    # UNKNOWN 이다. 상황판·전문가 점검이 이 파일을 읽는다.
    status = "FAIL" if issues else ("UNKNOWN" if n_assumed else "PASS")
    rec = {"date": day, "at": datetime.now().isoformat(timespec="seconds"), "status": status,
           "issues": int(issues), "pending": [str(r.get("key")) for r in pending],
           "assumed_trades": int(n_assumed), "orders": len(orders),
           "note": ("추정 체결이 남아 있다 — 접수를 근거로 가정한 보유" if status == "UNKNOWN" else
                    ("불일치가 있다 — 사람이 확인할 것" if status == "FAIL" else "주문·전송·체결·보유가 맞는다"))}
    try:
        p = sd / f"reconcile_{day}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError as exc:
        print(f"대조 결과 기록 실패: {exc}")
    print(f"대조 결과: {status} — {rec['note']}")
    return 0


def cmd_field(cfg: dict, args) -> None:
    """중계실 상위권 수익률을 기록하고 필드 분산(sigma_f)을 추정한다.

    왜 이게 매매보다 중요한가
      우리 우승 확률 p1 은 **필드 분산 하나에 100배 달라진다.**
      sigma_f = 15%p 면 현재 설정으로 p1 이 1/N 의 약 10배이고,
      sigma_f = 30%p 면 사실상 0 이다. 즉 "지금 설정이 충분히 공격적인가"라는
      질문의 답이 전적으로 이 값에 달려 있는데, 아직 한 번도 측정되지 않았다.

      그리고 이 데이터는 **소급 취득이 불가능하다.** 대회가 끝난 뒤에는
      중간 순위표가 남지 않는다. 첫날부터 매일 적어야 한다.

    추정 방법
      상위 K명만 알아도 필드 전체 분산을 추정할 수 있다. 정규분포 가정 하에서
      상위 i번째 값의 기대 위치는 표준정규의 (1 - i/(N+1)) 분위수이므로,
      관측된 상위 수익률을 그 분위수에 회귀하면 기울기가 곧 sigma_f 다.
      절편은 필드 평균이다. 상위권만 쓰므로 꼬리 왜곡에 취약하다 —
      그래서 추정치를 하나로 믿지 말고 매일 갱신해 추세를 본다.

    예) python run.py field --date 20260908 --n 300 --returns 42.1,38.7,35.2,33.9,31.0
    """
    import math

    rets = sorted((float(x) for x in args.returns.split(",")), reverse=True)
    if len(rets) < 3:
        print("최소 3개 이상의 수익률이 필요하다.")
        return
    n_field = args.n or int(cfg.get("contest", {}).get("n_field", 97))

    def norm_ppf(p: float) -> float:
        """표준정규 분위수. scipy 없이 이분법으로 구한다."""
        lo, hi = -8.0, 8.0
        for _ in range(120):
            mid = (lo + hi) / 2
            if 0.5 * (1 + math.erf(mid / math.sqrt(2))) < p:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    # **로그공간에서 회귀한다.** 원공간 정규회귀를 쓰면 안 된다.
    #
    # 필드 수익률 분포는 우측 꼬리가 두껍다(shifted-lognormal). 그 분포를 정규
    # 분위수 축에 놓으면 상위 꼬리가 **위로 휘므로**, 꼬리만 잘라 직선을 맞추면
    # 기울기가 모집단 표준편차보다 가파르게 나온다.
    #
    # 시뮬레이션 실측 (N=300, 600회 평균):
    #   참 sigma_f=25%p -> 정규회귀 34.5~48.8%p (K=100~10),  로그회귀 25.5~26.2%p
    #   참 sigma_f=35%p -> 정규회귀 53.3~82.3%p,             로그회귀 35.9~36.4%p
    #
    # 관측을 늘려도 안 고쳐진다(K=100 에서도 34.5 vs 25). 그리고 **과대추정은
    # 방향이 나쁘다** — 필드가 실제보다 흩어져 보이면 "이길 수 없다"고 잘못
    # 판단하게 된다. 로그회귀는 K=10 에서도 오차 5% 안쪽이다.
    xs = [norm_ppf(1 - (i + 1) / (n_field + 1)) for i in range(len(rets))]
    g = [math.log1p(r / 100.0) for r in rets]
    mx = sum(xs) / len(xs)
    mg = sum(g) / len(g)
    den = sum((x - mx) ** 2 for x in xs)
    s_log = (sum((x - mx) * (y - mg) for x, y in zip(xs, g)) / den) if den else 0.0
    m_log = mg - s_log * mx

    if s_log <= 0:
        print("추정 실패 — 상위 수익률이 단조 감소하는지 확인할 것.")
        return

    # 로그정규 모수 -> 원공간 통계
    sigma_f = math.sqrt((math.exp(s_log ** 2) - 1)
                        * math.exp(2 * m_log + s_log ** 2)) * 100
    mu_f = (math.exp(m_log + s_log ** 2 / 2) - 1) * 100
    z_max = norm_ppf(1 - 1 / (n_field + 1))
    expected_max = (math.exp(m_log + s_log * z_max) - 1) * 100

    try:
        from imrl import relay as _rl
        _rl.record_observation("field_sigma", date=args.date, n_field=n_field,
                               sigma_f_pct=round(sigma_f, 2), mu_f_pct=round(mu_f, 2),
                               hypothesis="RSK-H001")
    except Exception:
        pass
    rec = {"date": args.date, "n_field": n_field, "returns": rets,
           "sigma_f_pct": round(sigma_f, 2), "mu_f_pct": round(mu_f, 2),
           "expected_max_pct": round(expected_max, 2),
           "method": "lognormal_logspace_regression"}
    path = ROOT / "state" / "field_history.json"
    hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    hist = [h for h in hist if h.get("date") != args.date] + [rec]
    hist.sort(key=lambda h: h["date"])
    path.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[{args.date}] 관측 상위 {len(rets)}명 / 참가자 {n_field}명")
    print(f"  상위 수익률: {', '.join(f'{r:+.1f}%' for r in rets[:8])}"
          + (" ..." if len(rets) > 8 else ""))
    print(f"  필드 평균 추정   mu_f    = {mu_f:+.1f}%")
    print(f"  필드 분산 추정   sigma_f = {sigma_f:.1f}%p")
    print(f"  1위 기대 수익률          = {expected_max:+.1f}%")
    print()
    if sigma_f <= 0:
        print("  추정 실패 — 입력값을 확인할 것.")
    elif sigma_f < 18:
        print("  → 필드 분산이 낮다. 현재 설정(top2)으로 충분히 경쟁력이 있다.")
    elif sigma_f < 28:
        print("  → 중간. 현재 설정 유지하되 막판 추격 시 공격 국면을 적극 쓸 것.")
    else:
        print("  → 필드 분산이 높다. 우리 분산이 필드에 못 미치면 구조적으로 이길 수 없다.")
        print("     집중도를 더 올리거나(top2 유지+변동성 가중치 상향) 전략 재검토가 필요하다.")
    print()
    print(f"  기록: {path}  (총 {len(hist)}일)")


def cmd_etfcheck(cfg: dict, args) -> None:
    """1일차 매매종목수로 ETF 산입 여부를 확정하고 설정에 기록한다.

    왜 매매종목수인가
      회전율(%)은 1주(원금의 약 1%)로는 표시 반올림에 묻히고, 매도 기준이면
      매수만으로는 움직이지 않으며, D+1 반영일 수도 있다. 매매종목수는 정수
      카운트라 매수만으로 당일 확정된다. 하루 만에 반올림 없이 답이 나온다.

    1일차 주문은 주식 2종목 + ETF 1주다. 따라서
      3 -> ETF 산입,  2 -> ETF 제외.
    """
    n = int(args.symbols)
    cfg_path = ROOT / "config" / "settings.json"
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    pool = raw.get("turnover_farm", {}).get("etf_pool", [])

    print(f"중계실 매매종목수: {n}")
    print("1일차 주문: 주식 2종목 + ETF 1주 (총 3종목 체결 가정)")
    try:
        from imrl import relay as _rl
        _rl.record_observation("etf_rule", symbols=n, etf_counts=(n >= 3), hypothesis="HYP-002")
    except Exception:
        pass
    print()

    if n >= 3:
        rs = raw.setdefault("rules_status", {})
        rs["etf_counts_for_symbols"] = True
        # 회전율 산입은 종목수를 근거로 **가정**한다. 종목수 3 은 ETF 가 종목수에
        # 산입된다는 증거이지 회전율 산입의 증명이 아니다(외부 검토 지적). 확정은
        # ETF 거래가 원금의 5% 이상 쌓인 뒤 공식 회전율과 대조해서 한다
        # (state.turnover_evidence, 중계실 수집마다 검사).
        rs["etf_counts_for_turnover"] = True
        rs["_turnover_evidence"] = "assumed_from_symbols"
        rs["_confirmed_at"] = datetime.now().isoformat(timespec="seconds")
        rs["_evidence"] = (
            f"1일차에 주식 2종목 + ETF 1주를 체결했고 중계실 매매종목수가 {n} 였다. "
            "ETF 가 매매종목수에 산입된다(확인). 회전율 산입은 이를 근거로 가정하며 "
            "ETF 거래가 쌓이면 공식 회전율과 대조해 확정/반증한다.")
        print("→ ETF 가 매매종목수에 산입된다(확인). 회전율 산입은 가정 — 공식 회전율로 검증한다.")
        print(f"   ETF 풀 {pool} 로 2일차부터 파밍. 반증되면 개별주식 파밍으로 바뀐다.")
        print("   QUALIFY_MIN 후보도 실행 가능으로 바뀐다.")
    elif n == 2:
        rs = raw.setdefault("rules_status", {})
        rs["etf_counts_for_symbols"] = False
        rs["etf_counts_for_turnover"] = False
        rs["_turnover_evidence"] = "refuted_by_symbols"
        rs["_confirmed_at"] = datetime.now().isoformat(timespec="seconds")
        rs["_evidence"] = (
            f"1일차에 주식 2종목 + ETF 1주를 체결했는데 중계실 매매종목수가 {n} 였다. "
            "ETF 는 자격 집계에서 제외된다.")
        raw.setdefault("turnover_farm", {})["enabled"] = False
        print("→ ETF 가 제외된다. **파밍 설계가 무효다.**")
        print()
        print("   결과:")
        print("     - 자연 회전율만 남는다 (실측 481%). 요건 500% 에 19%p 부족")
        print("     - 체결 5종목 요건도 주식만으로 채워야 한다")
        print("   turnover_farm.enabled 를 false 로 껐다. ETF 를 더 사지 않는다.")
        print()
        print("   폴백을 정해야 한다 — 상위권 종목을 순환시키며 소량 왕복하는 방식.")
        print("   비용이 11배(0.02% -> 0.22%)이고 방향성 변동성이 붙으므로")
        print("   부족분 19%p 기준 약 0.042%p 다. 절대값은 작지만 규정 5.3")
        print("   ('등락을 이용한 반복적 단일 종목 거래')에 걸리지 않게 종목을")
        print("   돌려야 한다.")
    else:
        print(f"→ 예상 밖의 값이다 ({n}). 1일차 체결이 일부만 됐을 수 있다.")
        print("   `run.py reconcile` 로 실제 체결을 먼저 확인할 것.")
        print("   설정은 바꾸지 않았다.")
        return

    cfg_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"설정 기록: rules_status.etf_counts_for_turnover = "
          f"{raw['rules_status']['etf_counts_for_turnover']}")


def con_start(cfg: dict) -> str:
    """대회 시작일. 없으면 빈 문자열."""
    return str(cfg.get("contest", {}).get("start_date", ""))


def _relay_auto(cfg: dict, args) -> None:
    """중계실을 직접 읽어 기록한다. 사람 입력 없음.

    실패해도 던지지 않는다. 순위 수집은 매매에 필요한 것이 아니라 결정을 더
    좋게 만드는 관측이고, 여기서 예외를 올리면 그날 리포트가 통째로 죽는다.
    """
    from imrl import relay
    con = cfg.get("contest", {})
    league = str(getattr(args, "league", None) or con.get("league_code", ""))
    if not league:
        print("contest.league_code 가 없다.")
        return
    try:
        snap = relay.snapshot(league,
                              account=str(con.get("mock_account", "")),
                              pen_name=str(con.get("pen_name", "")))
    except relay.RelayError as exc:
        print(f"중계실을 읽지 못했다: {exc}")
        return
    path = relay.save(snap)
    su, rows = snap["summary"], snap["rows"]
    print(f"중계실 수집 ({su['date']} 기준) — {su['name'][:40]}")
    relay.record_observation("relay", date=su.get("date"), n_field=su.get("n_field"),
                             n_traded=su.get("n_traded"), max_pct=su.get("max_pct"),
                             avg_pct=su.get("avg_pct"), rows=len(rows))
    print(f"  참가 {su['n_field']}명 / 매매자 {su['n_traded']}명 / "
          f"최고 {su['max_pct']:+.2f}% 평균 {su['avg_pct']:+.2f}%")
    print(f"  원본: {path.name}")
    # 순위표 밖의 중계실 정보. 매매상위는 **당일** 실시간이라 군중이 지금 무엇을 사는지 보인다.
    ex = snap.get("extra") or {}
    tt = ex.get("trade_top") or []
    if tt:
        print("  오늘 매매상위: " + ", ".join(f"{t['name']}({t['code']}) {t['amount'] / 1e8:.1f}억" for t in tt[:5]))
        relay.record_observation("relay_flow", date=tt[0].get("date"),
                                 top=[{"code": t["code"], "name": t["name"], "amount": t["amount"]} for t in tt[:5]])
    info = ex.get("info") or {}
    if info:
        print(f"  대회 통계({info.get('기준일자')}): 거래자 {info.get('거래자')} / 이익실현 {info.get('이익실현')} / "
              f"손실실현 {info.get('손실실현')} / 무거래 {info.get('무거래자')} / 평균회전율 {info.get('평균회전율')}% / "
              f"평균수익률 {info.get('평균수익률')}%")
        relay.record_observation("league_info", date=str(info.get("기준일자")),
                                 traders=info.get("거래자"), gain=info.get("이익실현"), loss=info.get("손실실현"),
                                 idle=info.get("무거래자"), avg_turnover=info.get("평균회전율"),
                                 avg_ret=info.get("평균수익률"), total_amount=info.get("총거래금액"))

    if not rows:
        # 매매자가 있는데 순위표가 비면 개장 전이 아니다. 순위표
        # 출처가 바뀌었거나 형식이 바뀐 것이니, 그날 관측을 잃기 전에 알린다.
        if su["n_traded"] > 0:
            msg = (f"중계실 요약은 매매자 {su['n_traded']}명이라는데 순위표가 비어 있다. "
                   "자동 수집이 막혔을 수 있다. 오늘은 `run.py relay --top ... --mine ...` "
                   "로 손으로 넣을 것 — 이 관측은 나중에 다시 못 구한다.")
            print(f"  ⚠ {msg}")
            try:
                notify.alert("중계실 자동 수집 실패", msg)
            except Exception:
                pass
        else:
            print("  순위표가 아직 비어 있다 (개장 전이면 정상).")
        state.save_leaderboard(su["max_pct"], None, None, None, None, None,
                               su["n_field"])
        return

    # 컬럼명을 처음 보는 날이다. 매핑이 빗나갔으면 조용히 0 을 쓰는 대신 알린다.
    missing = [k for k in ("ret_pct", "turnover_pct") if rows[0].get(k) is None]
    if missing:
        print(f"  ⚠ 컬럼 매핑 실패 {missing} — 실제 키: {snap['raw_keys']}")
        print("     원본은 저장했다. imrl/relay.py 의 KEYS 에 이름을 추가할 것.")

    pen = str(getattr(args, "pen", None) or con.get("pen_name", "")).strip()
    mi = snap.get("me_index", -1)
    me = rows[mi] if 0 <= mi < len(rows) else None
    mine = args.mine if args.mine is not None else (me or {}).get("ret_pct")
    rank = args.rank or (me or {}).get("rank")
    turn = args.turnover if args.turnover is not None else (me or {}).get("turnover_pct")
    days = args.days if args.days is not None else (me or {}).get("days")
    syms = args.symbols if args.symbols is not None else (me or {}).get("symbols")

    if me:
        print(f"  내 행: {rank}위 / 수익률 {mine:+.2f}% / 회전율 {turn or 0:.0f}% "
              f"/ 매매일 {days or 0:.0f} / 종목 {syms or 0:.0f}")
        relay.record_observation("me", date=su.get("date"), rank=rank, ret_pct=mine,
                                 turnover_pct=turn, days=days, symbols=syms)
    else:
        print(f"  ⚠ 내 행을 찾지 못했다 (계좌 {con.get('mock_account','')} / "
              f"필명 {pen!r}). 아직 매매 전이면 정상이다.")

    # 경쟁자의 현재 수익률(나 제외, 순위순)을 결정 엔진의 출발선으로 넘긴다.
    others = [r.get("ret_pct") for i, r in enumerate(rows) if i != mi and r.get("ret_pct") is not None]
    state.save_leaderboard(rows[0].get("ret_pct") or su["max_pct"],
                           float(mine) if mine is not None else None,
                           int(rank) if rank else None,
                           float(turn) if turn is not None else None,
                           int(days) if days is not None else None,
                           int(syms) if syms is not None else None,
                           su["n_field"],
                           top_returns=others[:200], field_avg_pct=su.get("avg_pct"))

    # ETF 산입 여부를 사람 없이 판별한다.
    #
    # 이 판별은 **1일차에만 가능하다** - 그날은 주식 2종목 + ETF 1주라 매매종목수
    # 3 이면 산입, 2 면 제외로 갈린다. 둘째 날부터는 종목이 섞여 못 가린다.
    # 판별 전제는 세 주문이 **모두 접수됐다는 원장 기록**이다 — ETF 만 미접수인데
    # 종목수 2 를 보고 "ETF 제외"로 확정하면 파밍 설계를 통째로 잘못 버린다.
    try:
        confirmed = cfg.get("rules_status", {}).get("etf_counts_for_turnover")
        if (confirmed is None or confirmed is False) and me and syms and int(syms) in (2, 3):
            already = (ROOT / "config" / "settings.json")
            raw_cfg = json.loads(already.read_text(encoding="utf-8"))
            if "_confirmed_at" not in raw_cfg.get("rules_status", {}):
                ok_probe, why_probe = _day1_probe_evidence(cfg)
                print()
                print(f"  ETF 산입 판별: 중계실 매매종목수 {int(syms)} — {why_probe}")
                if ok_probe:
                    cmd_etfcheck(cfg, argparse.Namespace(symbols=int(syms)))
                    notify.alert("ETF 산입 여부 판별",
                                 f"중계실 매매종목수 {int(syms)} - "
                                 + ("ETF 종목수 산입 확인, 회전율 산입은 가정(공식 회전율로 검증 예정). 파밍 진행"
                                    if int(syms) >= 3 else "ETF 제외, 주식 파밍으로 전환"))
                else:
                    print("  판별 보류 — 설정을 바꾸지 않았다.")
    except Exception as exc:
        print(f"  ETF 판별 생략 ({type(exc).__name__}: {exc})")

    # 회전율 산입의 **증거** — 공식 회전율과 내부 합계(ETF 포함/제외)를 매일 대조한다.
    try:
        ev = state.turnover_evidence(cfg)
        print(f"  ETF 회전율 증거: {ev['why']}"
              + (f" (공식 {ev['official']:.0f}% / 포함 {ev['incl_a']:.0f}% / 제외 {ev['excl_a']:.0f}%)"
                 if ev.get("official") else ""))
        if ev.get("verdict"):
            relay.record_observation("etf_turnover_evidence", verdict=ev["verdict"],
                                     official=ev["official"], incl=round(ev["incl_a"], 1),
                                     excl=round(ev["excl_a"], 1))
            rs = cfg.get("rules_status", {})
            if ev["verdict"] == "refuted" and rs.get("_turnover_evidence") != "refuted_by_turnover":
                _set_rules_status(etf_counts_for_turnover=False, _turnover_evidence="refuted_by_turnover",
                                  _turnover_evidence_at=datetime.now().isoformat(timespec="seconds"))
                notify.alert("ETF 회전율 산입 반증",
                             f"공식 회전율 {ev['official']:.0f}% 가 ETF 제외 합계({ev['excl_a']:.0f}%)에 맞는다. "
                             "ETF 파밍을 멈추고 개별주식 파밍으로 바꾼다.")
            elif ev["verdict"] == "confirmed" and rs.get("_turnover_evidence") != "confirmed_by_turnover":
                _set_rules_status(etf_counts_for_turnover=True, _turnover_evidence="confirmed_by_turnover",
                                  _turnover_evidence_at=datetime.now().isoformat(timespec="seconds"))
                notify.alert("ETF 회전율 산입 확인",
                             f"공식 회전율 {ev['official']:.0f}% 가 ETF 포함 합계({ev['incl_a']:.0f}%)에 맞는다.")
    except Exception as exc:
        print(f"  회전율 증거 검사 생략 ({type(exc).__name__}: {exc})")

    tops = [r["ret_pct"] for r in rows[:12] if r.get("ret_pct") is not None]
    if len(tops) >= 3:
        cmd_field(cfg, argparse.Namespace(
            returns=",".join(f"{t}" for t in tops), n=su["n_field"],
            date=su["date"] or f"{date.today():%Y%m%d}"))
    else:
        print("  분산 추정은 상위 3개 이상이 필요하다.")


def _set_rules_status(**kv) -> None:
    """config 의 rules_status 항목을 원자적으로 바꾼다."""
    p = ROOT / "config" / "settings.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw.setdefault("rules_status", {}).update(kv)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def _day1_probe_evidence(cfg: dict) -> tuple[bool, str]:
    """1일차 ETF 판별의 전제가 성립하는가 — 1일차(또는 그 다음 날 아침)이고, 그날
    주식 2종목 + ETF 1주 매수가 **모두 접수**된 원장 기록이 있어야 한다."""
    try:
        cal = _calendar(cfg)
        tds = cal.trading_days()
        today = date.today()
        past = [d for d in tds if d <= today]
        if not past:
            return False, "대회 시작 전"
        if len(past) > 2:
            return False, f"1일차가 아니다 (경과 {len(past)}일) — 종목수로는 판별 불가"
        day1 = past[0]
        rows = json.loads((ROOT / "state" / f"submitted_{day1:%Y%m%d}.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, "1일차 전송 원장이 없다 — 접수 근거 없음"
    except Exception as exc:
        return False, f"원장 확인 실패 {type(exc).__name__}"
    last: dict[str, str] = {}
    for r in rows:
        last[str(r.get("key", ""))] = str(r.get("status", "submitted"))
    etf = {str(x) for x in cfg.get("turnover_farm", {}).get("etf_pool", [])}
    buys = [k for k, s in last.items() if k.startswith("BUY:") and s == "submitted"]
    stocks = [k for k in buys if k.split(":", 1)[1].split("#")[0] not in etf]
    etfs = [k for k in buys if k.split(":", 1)[1].split("#")[0] in etf]
    if len(stocks) >= 2 and len(etfs) >= 1:
        return True, f"1일차 접수 원장: 주식 {len(stocks)}건 + ETF {len(etfs)}건 접수됨"
    return False, f"1일차 접수 원장 불충분 (주식 {len(stocks)}건, ETF {len(etfs)}건 접수) — 종목수 {'2' if not etfs else '?'} 로 판별하면 오판"


def cmd_newswatch(cfg: dict, args) -> None:
    """15:05 전에 뉴스를 기록·검토하고 위험 종목 제외 제안을 낸다.

    보는 종목: 보유 + 오늘 점수 상위(top_n x 3). 후보를 알아야 제안이 의미가 있다.
    실패해도 던지지 않는다 — 뉴스 수집이 안 됐다고 계획을 멈추면 안 된다.
    """
    from imrl import news_watch
    phase = "phase_early"
    try:
        uni = universe.build(cfg)
        scored = alpha.rank_universe(cfg, uni, phase=phase)
    except Exception as exc:
        print(f"유니버스/점수 실패 ({type(exc).__name__}: {exc}) — 보유 종목만 본다")
        scored = None
    top_n = int(cfg["portfolio"][phase]["top_n"])
    ranked = [] if scored is None or scored.empty else scored.head(top_n * 3)["code"].astype(str).tolist()
    names = {}
    if scored is not None and not scored.empty:
        for r in scored.head(top_n * 3).itertuples():
            names[str(r.code)] = str(r.name)
    for c, p in state.load_positions().items():
        names.setdefault(str(c), str(p.get("name") or c))
    if not names:
        print("볼 종목이 없다."); return
    principal = int(cfg["contest"]["principal"])
    cap_w = (float(cfg["requirements"]["max_single_weight_rule"])
             - float(cfg["requirements"].get("single_weight_margin", 0.05)))
    res = news_watch.run(names, ranked, top_n, cap_w)
    print(f"뉴스 기록: {Path(res['file']).name}  종목 {len(names)}개")
    for c, hs in res["hard"].items():
        print(f"  ! 경성 위험 {names.get(c, c)}({c}): {', '.join(hs)}")
    for c, ss in res["soft"].items():
        print(f"  · 주의 {names.get(c, c)}({c}): {', '.join(ss)}")
    if res["proposals"]:
        print(f"  제외 제안 생성: {[Path(x).name for x in res['proposals']]} — 15:05 계획에서 기준선과 비교된다")
        try:
            notify.alert("뉴스 위험 신호 — 제외 제안",
                         "; ".join(f"{names.get(c, c)}: {', '.join(hs)}" for c, hs in res["hard"].items())
                         + chr(10) + "15:05 계획에서 이 종목을 뺀 배분이 후보로 올라간다. 관문을 통과해야 실행된다.")
        except Exception:
            pass
    elif not res["hard"]:
        print("  경성 위험 신호 없음")

    # 정밀 회차의 후보 목록을 남기고, 같은 스냅샷으로 전문가 점검까지 돌린다.
    # 시간별 회차(run.py cycle)는 이 후보 목록을 재사용한다 — 유니버스 재계산은 여기서만.
    try:
        from imrl import cycle as _cycle
        _cycle.save_candidates(scored, top_n, phase)
        out = _cycle.run(cfg, source="newswatch", full=res)
        c = out.get("opinions", {})
        print(f"  사이클(정밀): 전문가 의견 {c.get('info', 0)}/{c.get('warn', 0)}/{c.get('alert', 0)}"
              f" (info/warn/alert) · 경보 전송 {len(out.get('alerts_sent', []))}건")
    except Exception as exc:
        print(f"  사이클 기록 실패 ({type(exc).__name__}: {exc}) — 뉴스 기록·제안은 위에서 끝났다")


def cmd_cycle(cfg: dict, args) -> int:
    """시간별 사이클 한 회차: 뉴스 → 전문가 점검 → 제외 제안 → 기록 → 상황판.

    실패해도 텔레그램을 울리지 않는다 — 매시간 도는 것이 경보를 내면 경보가 배경
    소음이 된다. 실패는 기록(run.log·cycle_log)으로 남기고 종료코드로 알린다.
    """
    from imrl import cycle as _cycle
    try:
        out = _cycle.run(cfg, source=str(getattr(args, "source", "manual") or "manual"))
    except Exception as exc:
        print(f"사이클 실패: {type(exc).__name__}: {exc}")
        return 1
    if out.get("skipped"):
        print(f"사이클 건너뜀: {out['skipped']}")
        return 0
    c = out.get("opinions", {})
    print(f"사이클({out.get('mode')}) {str(out.get('at', ''))[11:16]} 종목 {out.get('names')}개"
          f" · 경성 {len(out.get('hard', []))} · 제안 {len(out.get('proposals', []))}"
          f" · 의견 {c.get('info', 0)}/{c.get('warn', 0)}/{c.get('alert', 0)}"
          f" · 경보 {len(out.get('alerts_sent', []))}건 · {out.get('elapsed_s')}초")
    for code, hs in (out.get("hard_detail") or {}).items():
        print(f"  ! 경성 위험 {code}: {', '.join(hs)}")
    for o in out.get("ops", []):
        if o["level"] != "info":
            print(f"  [{o['level']}] {o['expert']}: {o['text']}")
    # 상황판을 바로 다시 그린다 — 사람이 보는 화면이 회차와 같이 움직여야 한다.
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import dashboard as _dash
        _dash.build(with_news=False)
    except Exception as exc:
        print(f"  상황판 갱신 실패 ({type(exc).__name__}: {exc})")
    return 0


def cmd_relay(cfg: dict, args) -> None:
    """중계실 화면 하나를 한 번에 받아 넣는다.

    이 값들은 **소급 취득이 불가능하다.** 대회가 끝나면 중간 순위표는 남지 않는다.
    그런데 지금까지는 같은 화면을 보고 `board` 와 `field` 를 따로 쳐야 했다.
    입력이 두 번이면 한 번은 빠진다 — 그러면 그날 관측이 통째로 사라진다.

    중계실은 앱·웹에만 있고 로그인이 필요하다. HTS 메뉴 전체를 뒤졌지만 같은
    화면이 없어서(중계·대회·리그·랭킹 전부 0건) 화면 캡처로는 자동화되지 않는다.
    그래서 남은 사람 손을 없애는 대신 **한 줄로 줄인다.**
    """
    if getattr(args, "auto", False):
        return _relay_auto(cfg, args)
    if not args.top or args.mine is None:
        print("--auto 를 쓰거나 --top 과 --mine 을 함께 줄 것.")
        return

    # 대회 전 손입력을 막는다.
    #
    # 이 명령의 사용법 예시에는 그럴듯한 숫자가 들어간다. 그 예시를 그대로
    # 실행하면 관측하지 않은 값이 **관측된 사실로** 기록되고, 거기서 나온
    # 필드 분산이 1일차 결정에 그대로 들어간다. 예전에 테스트가 남긴 가짜
    # 보유가 하루를 날릴 뻔한 것과 같은 종류의 사고다.
    #
    # 개장 전에는 순위표에 넣을 것이 존재하지 않는다. 그러니 막는 것이 맞다.
    start = str(con_start(cfg))
    obs = str(getattr(args, "date", "") or f"{date.today():%Y%m%d}")
    if start and obs < start.replace("-", ""):
        print(f"관측일 {obs} 이 대회 시작({start}) 전이다.")
        print("  개장 전에는 순위표에 기록할 것이 없다. 넣지 않는다.")
        print("  실제로 중계실을 보고 넣는 것이라면 --date 로 그날을 지정할 것.")
        return
    n = args.n_field or int(cfg.get("contest", {}).get("n_field", 97))
    tops = [float(x) for x in args.top.split(",") if x.strip()]
    if not tops:
        print("--top 에 최소 1개의 수익률이 필요하다.")
        return

    state.save_leaderboard(max(tops), args.mine, args.rank,
                           args.turnover, args.days, args.symbols, n)
    print(f"순위 기록: 1위 {max(tops):+.2f}% / 나 {args.mine:+.2f}%"
          + (f" / {args.rank}위" if args.rank else "") + f" / 참가 {n}명")

    if len(tops) < 3:
        print("  분산 추정은 상위 3개 이상이 필요하다 — 순위 기록만 했다.")
    else:
        fa = argparse.Namespace(returns=args.top, n=n, date=args.date)
        cmd_field(cfg, fa)

    rec = state.load_field()
    if rec and rec.get("sigma_f_pct"):
        sig = float(rec["sigma_f_pct"])
        print()
        print(f"  이 관측이 결정에 주는 뜻: 필드 σ {sig:.1f}%p, 참가 {n}명")
        print("  다음 계획부터 시나리오의 필드 분산이 이 값으로 맞춰진다.")


def cmd_board(cfg: dict, args) -> None:
    """중계실에서 본 값을 기록한다.

    회전율·매매일수·매매종목수는 주최측 공식 집계다. 내부 추정치는 주문을 체결로
    간주해 계산하므로 낙관적이다. 공식 수치를 넣으면 자격 판정이 그걸로 바뀐다.
    """
    obj = state.save_leaderboard(args.leader, args.mine, args.rank,
                                 args.turnover, args.days, args.symbols,
                                 getattr(args, "n_field", None))
    print(f"리더보드 갱신: 1위 {obj['leader_return_pct']:+.2f}% / 나 {obj['my_return_pct']:+.2f}% "
          f"/ 순위 {obj['my_rank']}")
    if args.turnover is not None:
        print(f"  공식 회전율 {args.turnover:.1f}%  매매일수 {args.days}  종목수 {args.symbols}")
    else:
        print("  ⚠ 공식 회전율 미입력. --turnover 로 중계실 수치를 넣어야 "
              "자격 판정이 정확해진다.")


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description="iM Rookie League 자동 모의투자 시스템")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="환경·데이터·일정 점검")
    sub.add_parser("universe", help="매매 가능 유니버스")
    sub.add_parser("signal", help="종목 점수 랭킹")
    sub.add_parser("plan", help="오늘의 주문 생성")
    st_p = sub.add_parser("status", help="자격 요건 진척도")
    st_p.add_argument("--push", action="store_true", help="텔레그램으로도 전송")

    f = sub.add_parser("fill", help="체결 기록")
    f.add_argument("--from-orders", action="store_true")
    f.add_argument("--side"); f.add_argument("--code"); f.add_argument("--qty")
    f.add_argument("--price"); f.add_argument("--name"); f.add_argument("--market")

    rc = sub.add_parser("reconcile", help="주문·전송·체결·보유 대조")
    rc.add_argument("--date", help="대상일 YYYYMMDD (기본 오늘)")

    fd = sub.add_parser("field", help="중계실 상위권 수익률 기록 + 필드 분산 추정")
    fd.add_argument("--returns", required=True,
                    help="상위 수익률 쉼표 구분, 예: 42.1,38.7,35.2")
    fd.add_argument("--n", type=int, default=None,
                    help="총 참가자 수 (기본: 설정의 contest.n_field)")
    fd.add_argument("--date", default=f"{date.today():%Y%m%d}", help="관측일")

    dp = sub.add_parser("decision",
                        help="후보 행동별 가정하 우승 확률 비교 (shadow - 주문 안 냄)")
    dp.add_argument("--mode", choices=["shadow", "advisory"], default="shadow")
    dp.add_argument("--paths", type=int, default=None, help="선택용 경로 수")

    ec = sub.add_parser("etfcheck",
                        help="1일차 관측: 중계실 매매종목수로 ETF 산입 여부를 확정한다")
    ec.add_argument("--symbols", type=int, required=True,
                    help="중계실에 표시된 매매종목수 (ETF 1주만 산 1일차 기준)")

    b = sub.add_parser("board", help="중계실 순위 입력")
    b.add_argument("--leader", type=float, required=True, help="1위 수익률 %%")
    b.add_argument("--mine", type=float, required=True, help="내 수익률 %%")
    b.add_argument("--rank", type=int, default=None, help="내 순위")
    b.add_argument("--turnover", type=float, default=None, help="중계실 공식 회전율 %%")
    b.add_argument("--days", type=int, default=None, help="중계실 공식 매매일수")
    b.add_argument("--symbols", type=int, default=None, help="중계실 공식 매매종목수")
    b.add_argument("--n-field", type=int, default=None, dest="n_field",
                   help="중계실 전체현황의 참가자수")

    sub.add_parser("newswatch", help="뉴스 기록·검토 + 위험 종목 제외 제안 (15:05 전, 정밀 회차)")
    cy = sub.add_parser("cycle", help="시간별 사이클: 뉴스 → 전문가 점검 → 제외 제안 → 기록 (24시간)")
    cy.add_argument("--source", default="manual", help="누가 돌렸나 (watchdog/tracker/manual)")
    rl = sub.add_parser("relay",
                        help="중계실 화면을 한 줄로 입력 (순위+분산+자격 한꺼번에)")
    rl.add_argument("--auto", action="store_true",
                    help="중계실 API 에서 직접 가져온다 (로그인 불필요)")
    rl.add_argument("--top", default=None,
                    help="상위권 수익률 쉼표 구분 (순위중계 화면 위에서부터). "
                         "예: 12.1,10.4,9.8,9.1,8.6")
    rl.add_argument("--mine", type=float, default=None, help="내 수익률 %%")
    rl.add_argument("--rank", type=int, default=None, help="내 순위")
    rl.add_argument("--n-field", type=int, default=None, dest="n_field",
                    help="전체현황의 참가자수 (기본: 설정값)")
    rl.add_argument("--turnover", type=float, default=None, help="내 회전율 %%")
    rl.add_argument("--days", type=int, default=None, help="내 매매일수")
    rl.add_argument("--symbols", type=int, default=None, help="내 매매종목수")
    rl.add_argument("--date", default=f"{date.today():%Y%m%d}", help="관측일")

    args = ap.parse_args()

    # 로그를 먼저 건다. 이 아래 어디서 죽어도 기록이 남아야 한다.
    _tee = _Tee(ROOT / "state" / "run.log")
    sys.stdout = _tee
    sys.stderr = _tee
    cfg = load_config()

    if args.cmd == "check":
        cmd_check(cfg)
    elif args.cmd == "universe":
        cmd_universe(cfg)
    elif args.cmd == "signal":
        cmd_signal(cfg)
    elif args.cmd == "plan":
        # 스케줄러 무인 실행 경로다. 예외가 조용히 사라지면 15:10/15:21 집행이
        # 어제 주문서를 읽거나 아무것도 못 한다. 실패를 즉시 알린다.
        return _guarded(lambda: cmd_plan(cfg), "주문서 생성(15:05)")
    elif args.cmd == "status":
        rc = _guarded(lambda: cmd_status(cfg, push=args.push), "자격요건 리포트(16:00)")
        _mark_step("status", rc)
        return rc
    elif args.cmd == "fill":
        cmd_fill(cfg, args)
    elif args.cmd == "reconcile":
        rc = _guarded(lambda: cmd_reconcile(cfg, args), "정합성 대조(15:40)")
        _mark_step("reconcile", rc)
        return rc
    elif args.cmd == "field":
        cmd_field(cfg, args)
    elif args.cmd == "board":
        cmd_board(cfg, args)
    elif args.cmd == "relay":
        cmd_relay(cfg, args)
    elif args.cmd == "newswatch":
        cmd_newswatch(cfg, args)
    elif args.cmd == "cycle":
        return cmd_cycle(cfg, args)
    elif args.cmd == "etfcheck":
        cmd_etfcheck(cfg, args)
    elif args.cmd == "decision":
        from imrl import decision_cmd
        return _guarded(lambda: decision_cmd.run(cfg, args), "decision")
    return 0


def _guarded(fn, label: str) -> int:
    """스케줄러 실행에서 예외가 조용히 사라지지 않게 감싼다.

    15:05 신호 산출이 실패하면 15:10/15:21 집행은 **어제 주문서**를 읽거나
    아무것도 못 한다. 실패 사실이 그때까지 아무에게도 전달되지 않는 것이
    가장 위험하다. 텔레그램으로 즉시 알린다.
    """
    import traceback

    try:
        rc = fn()
        return int(rc) if isinstance(rc, int) else 0
    except Exception as exc:
        print(f"{label} 실패: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        try:
            notify.alert(f"{label} 실패", "\n".join([
                f"{type(exc).__name__}: {exc}",
                "",
                "이후 집행이 어제 주문서를 쓰거나 중단될 수 있다. 확인할 것.",
            ]))
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
