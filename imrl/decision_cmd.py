# -*- coding: utf-8 -*-
"""`run.py decision` 의 본체 (명세 12.3).

**주문을 만들지 않는다.** shadow 는 보고서만, advisory 는 사람이 검토할 주문
의도를 남긴다. 어느 쪽도 `orders_*.json` 을 쓰지 않으므로 `execute.py` 가
이 결과를 소비할 경로가 없다.

여기서 하는 일
  1. 확정된 계좌·자격 상태를 모은다
  2. 고정된 후보 목록을 만든다 (policies)
  3. 같은 외생 경로에서 모든 후보를 대회 종료까지 전개한다 (scenarios + contest_sim)
  4. 자격을 충족한 최종 1등 비율을 비교하고, 최선 후보만 확인용 경로에서 다시 잰다
  5. 개선이 확인되지 않으면 기준 정책을 유지한다
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import alpha, contest_sim, data, decision as dec, policies, scenarios, state, universe

ROOT = Path(__file__).resolve().parent.parent


def _observed_field_sigma(cal, days_left: int) -> float | None:
    """중계실에서 잰 필드 분산을 **잔여 구간** 기준으로 환산한다.

    `run.py field` 가 상위권 수익률에서 sigma_f 를 추정해 남긴다. 그런데 그 값은
    대회 시작부터 관측일까지의 **누적** 수익률 분산이고, 시뮬레이터가 필요로 하는
    것은 관측 시점 이후 남은 날들의 분산이다. 둘을 그대로 맞바꾸면 대회 후반에
    갈수록 필드를 크게 과대평가한다 — 이틀 남은 시점에 18일치 분산을 쓰게 된다.

    독립 증분 가정에서 분산은 날짜에 비례하므로 sqrt(잔여/경과) 로 환산한다.
    거친 가정이지만, 하드코딩된 추측값보다는 관측에 붙어 있는 편이 낫다.

    관측이 없으면 None 을 돌려주고 모형 기본값을 그대로 쓴다. 여기서 잘못
    추측하느니 원래 모수로 도는 편이 낫다.
    """
    rec = state.load_field()
    if not rec:
        return None
    try:
        sig = float(rec["sigma_f_pct"]) / 100.0
        obs_day = datetime.strptime(str(rec["date"]), "%Y%m%d").date()
    except (KeyError, TypeError, ValueError):
        return None
    if sig <= 0:
        return None
    elapsed = sum(1 for d in cal.trading_days() if d <= obs_day)
    if elapsed <= 0:
        return None
    return sig * (days_left / elapsed) ** 0.5


def _history(codes: list[str], min_days: int = 120) -> tuple[np.ndarray, list[str]] | None:
    """과거 일별 수익률 패널. 캐시에 없는 종목은 제외한다."""
    p = ROOT / "state" / "bars_cache.parquet"
    if not p.exists():
        return None
    close = pd.read_parquet(p)
    have = [c for c in codes if c in close.columns]
    if len(have) < 3:
        return None
    sub = close[have].dropna()
    if len(sub) < min_days:
        return None
    rets = sub.pct_change().dropna().values
    # 극단값은 가격제한폭 안으로 자른다. 데이터 오류가 경로를 지배하지 않게 한다.
    return np.clip(rets, -0.31, 0.31), have


def run(cfg: dict, args, scored=None, uni=None):
    de = cfg.get("decision_engine", {})
    if not de.get("enabled", False):
        print("decision_engine 이 꺼져 있다. config 의 decision_engine.enabled 를 켤 것.")
        return

    sim_cfg = de.get("simulation", {})
    n_sel = int(getattr(args, "paths", None) or sim_cfg.get("selection_paths", 2000))
    n_val = int(sim_cfg.get("validation_paths", 4000))
    seed = int(sim_cfg.get("master_seed", 20260906))
    mode = getattr(args, "mode", "shadow")

    cal = data.TradingCalendar.from_config(cfg["contest"])
    days_left = max(cal.days_left(), 1)
    principal = int(cfg["contest"]["principal"])

    # --- 상태 ---------------------------------------------------------------- #
    board = state.load_leaderboard()
    positions = state.load_positions()

    # 유니버스·점수는 **호출자가 이미 만든 것을 재사용한다.**
    # 후보마다 다시 수집하면 같은 snapshot 이 아니게 되고(명세 15절), 콜드
    # 캐시에서는 429종목 일봉을 두 번 더 받아 plan 이 작업 제한을 넘길 수 있다.
    if uni is None:
        uni = universe.build(cfg)
    base_scored = scored if scored is not None else alpha.rank_universe(
        cfg, uni, phase="phase_early")
    if base_scored.empty:
        print("후보 종목이 없다.")
        return
    vol_cfg = json.loads(json.dumps(cfg))
    vol_cfg["alpha"]["weights"]["volatility"] = 1.0
    vol_scored = alpha.rank_universe(vol_cfg, uni, phase="phase_early")

    base_codes = list(base_scored["code"])[:6]
    vol_codes = list(vol_scored["code"])[:6] if not vol_scored.empty else base_codes
    codes = sorted(set(base_codes) | set(vol_codes) | set(positions))

    quotes = {}
    for c in codes:
        try:
            quotes[c] = data.fetch_quote(c)
        except Exception:
            pass
    prices = {c: int(q.get("price") or 0) for c, q in quotes.items() if q.get("price")}

    hp = _history([c for c in codes if c in prices])
    if hp is None:
        print("과거 수익률 패널을 만들지 못했다 (state/bars_cache.parquet 확인).")
        return
    hist, codes = hp
    prices = {c: prices[c] for c in codes}
    base_codes = [c for c in base_codes if c in codes]
    vol_codes = [c for c in vol_codes if c in codes]

    my_ret = state.estimate_my_return(quotes, principal)
    ret_src = "체결원장 추정"
    if my_ret is None:
        my_ret = float(board.get("my_return_pct", 0.0) or 0.0)
        ret_src = "중계실/기본"
    my_ret /= 100.0

    st_now = state.compute_constraints(principal, days_left)
    turnover_now = st_now.effective_turnover / 100.0
    deficit = max(0.0, cfg["requirements"]["min_turnover_pct"] / 100.0 - turnover_now)

    equity = principal * (1.0 + my_ret)
    cap_base = (float(cfg["requirements"]["max_single_weight_rule"])
                - float(cfg["requirements"].get("single_weight_margin", 0.05)))
    cap_amt = cap_base * principal
    cap_w = min(cap_base, cap_amt / equity) if equity > 0 else cap_base
    held_w = ({c: positions[c]["qty"] * prices[c] / equity
               for c in positions if c in prices} if equity > 0 else {})

    # ETF 산입이 공식 확인되지 않았으면 QUALIFY_MIN 을 실행 가능으로 표시하지 않는다.
    farm_ok = bool(cfg.get("rules_status", {}).get("etf_counts_for_turnover", False))
    actions = policies.generate_actions(base_codes, vol_codes, held_w, cap_w,
                                        exposure=1.0 - float(cfg["portfolio"]["phase_early"].get("cash_buffer", 0.10)),
                                        turnover_deficit=deficit,
                                        days_left=max(days_left - 2, 1),
                                        farm_available=farm_ok)

    # 전문가 제안을 같은 관문 앞에 세운다. 제안은 후보일 뿐이고, 선택은 아래
    # 시뮬레이션과 확인용 경로가 한다. 제안 파일이 없으면 아무 일도 없다.
    from . import proposals as _prop
    extra = _prop.load_actions(set(codes), cap_w)
    if extra:
        n_ok = sum(1 for a in extra if a.feasible)
        print(f"전문가 제안 {len(extra)}건 (실행 가능 {n_ok}건) — 후보에 추가")
        for a in extra:
            if not a.feasible:
                print(f"  - {a.action_id}: {'; '.join(a.exclusion_reasons)}")
        actions = list(actions) + extra

    rules = contest_sim.Rules(
        principal=principal,
        min_turnover=cfg["requirements"]["min_turnover_pct"] / 100.0,
        min_trading_days=int(cfg["requirements"]["min_trading_days"]),
        min_distinct_symbols=int(cfg["requirements"]["min_distinct_symbols"]),
        single_limit=float(cfg["requirements"]["max_single_weight_rule"]),
        cost_stock=0.0011, cost_etf=0.0001,
        fill_rate=float(cfg["requirements"].get("assumed_fill_rate", 0.85)))
    etf = frozenset(str(x) for x in cfg["turnover_farm"].get("etf_pool", []))
    invested = sum(positions[c]["qty"] * prices[c] for c in positions if c in prices)
    # 시작 계좌는 **지금까지의 사실**을 전부 담는다 — 누적 회전금액, 공식 우선 매매일수,
    # 실제로 체결된 종목 집합. 이것을 비우면 이미 자격을 채운 계좌가 현금을 들고만
    # 있어도 시뮬레이션 안에서 자격을 잃는다(외부 검토 재현: 500%·5일·5종목 → 0%).
    traded_syms = frozenset(str(t.get("code")) for t in state.load_trades() if t.get("code"))
    start = contest_sim.Account(
        cash=int(equity - invested), shares={c: int(positions[c]["qty"]) for c in positions},
        turnover_amount=int(turnover_now * principal),
        trading_days=int(getattr(st_now, "effective_days", 0) or 0),
        symbols=traded_syms)

    # 참가자수는 추정하지 말고 본 것을 쓴다. 중계실 전체현황에 숫자가 있다.
    # 300 으로 잡았던 예전 값은 근거가 없었고, 97명과의 차이는 1등 확률을
    # 2.4배 바꾼다 - 모형 안의 어떤 모수보다 크게 움직인다.
    n_field = int(board.get("n_field")
                  or cfg.get("contest", {}).get("n_field", 97) or 97)
    # 경쟁자는 나를 뺀 수다. 관측된 상위권 수익률(중계실, 나 제외)을 앞에서부터
    # 채우고, 관측되지 않은 나머지는 필드 평균으로 둔다. 길이가 달라도 죽지 않는다.
    n_comp = max(int(n_field) - 1, 1)
    tr = [float(x) for x in (board.get("top_returns") or []) if x is not None][:n_comp]
    if tr:
        field_now = np.full(n_comp, float(board.get("field_avg_pct") or 0.0) / 100.0)
        field_now[:len(tr)] = np.array(tr, dtype=float) / 100.0
    else:
        field_now = None

    print(f"경로 {n_sel}(선택)+{n_val}(확인) / 잔여 {days_left}일 / 종목 {len(codes)}개 "
          f"/ 경쟁자 {n_comp}명 (참가 {n_field}명, 관측 {len(tr)}명)")
    print(f"내 수익률 {my_ret:+.2%} ({ret_src}) / 회전율 {turnover_now:.0%} "
          f"/ 부족분 {deficit:.0%}")

    # 실측 종목 교체율(top2 일평균). 경로 안에서 알파를 다시 계산하지 않으므로
    # 이 값으로 교체를 대신 넣는다. 자연 회전 481%/20일의 근거값이다.
    repl = float(de.get("daily_replacement", 0.245))

    sigma_obs = _observed_field_sigma(cal, days_left)
    if sigma_obs is None:
        print("필드 분산: 관측 없음 — 모형 기본값을 쓴다")
    else:
        print(f"필드 분산: 관측 반영 잔여구간 {sigma_obs:.1%}")

    def batches(n, purpose):
        return scenarios.make_batches(hist, codes, n, days_left, n_comp, seed,
                                      purpose, sigma_obs=sigma_obs)

    # 교체로 들어올 수 있는 새 종목의 상한 = 실제 유니버스 크기. 배치(가격 경로용
    # 종목 몇 개)로 상한을 잡으면 5종목 요건을 어떤 후보도 못 채운다.
    uni_n = int(len(uni)) if uni is not None else None

    sel = batches(n_sel, "selection")
    results, wins = [], {}
    for a in actions:
        by_model, qr, rr, tt, cc = {}, [], [], [], []
        for bt in sel:
            o = contest_sim.simulate(a.weights, start, prices, bt, rules, etf,
                                     my_ret, field_now, a.farm_per_day,
                                     daily_replacement=repl, universe_n=uni_n)
            by_model[bt.model_id] = o.win
            qr.append(o.qualified.mean()); rr.append(o.final_return)
            tt.append(o.turnover.mean()); cc.append(o.cost.mean())
        per, agg, worst = dec.aggregate(by_model)
        allr = np.concatenate(rr)
        results.append(dec.CandidateResult(
            action_id=a.action_id, feasible=a.feasible,
            exclusion_reasons=list(a.exclusion_reasons), weights=dict(a.weights),
            exposure=float(sum(a.weights.values())),
            qualification_mode=a.qualification_mode, win_by_model=per,
            aggregate_win=agg, worst_model_win=worst,
            qualification_rate=float(np.mean(qr)), mean_return=float(allr.mean()),
            p10_return=float(np.quantile(allr, 0.10)),
            mean_turnover=float(np.mean(tt)), mean_cost=int(np.mean(cc))))
        wins[a.action_id] = by_model

    feas = [r for r in results if r.feasible]
    best = max(feas, key=lambda r: r.aggregate_win) if feas else None
    baseline = "BASE" if any(r.action_id == "BASE" for r in feas) else "HOLD"

    delta = se = lower = None
    if best is not None and best.action_id != baseline:
        val = batches(n_val, "validation")
        wv = {}
        for aid in (best.action_id, baseline):
            act = next(a for a in actions if a.action_id == aid)
            wv[aid] = {bt.model_id: contest_sim.simulate(
                act.weights, start, prices, bt, rules, etf, my_ret, field_now,
                act.farm_per_day, daily_replacement=repl, universe_n=uni_n).win for bt in val}
        delta, se, lower = dec.paired_delta(wv[best.action_id], wv[baseline])

    disagree = bool(best and feas and
                    max(feas, key=lambda r: r.worst_model_win).action_id != best.action_id)
    exec_action, ok, reason = dec.choose(
        results, baseline, delta, se, lower,
        float(de.get("switching", {}).get("minimum_actionable_delta", 0.005)),
        state_ok=True, block_reasons=[])

    print()
    print("%-23s %9s %9s %8s %9s %7s" %
          ("후보", "가정승률", "최악모형", "자격률", "평균수익", "노출"))
    for r in sorted(results, key=lambda x: -x.aggregate_win):
        mark = "*" if best and r.action_id == best.action_id else (" " if r.feasible else "x")
        print("%s%-22s %8.2f%% %8.2f%% %7.1f%% %8.1f%% %6.0f%%"
              % (mark, r.action_id, r.aggregate_win * 100, r.worst_model_win * 100,
                 r.qualification_rate * 100, r.mean_return * 100, r.exposure * 100))
        if not r.feasible:
            print("       제외: " + ", ".join(r.exclusion_reasons))
    print()
    print(f"모형상 최선: {best.action_id if best else '-'}    기준: {baseline}")
    if delta is not None:
        print(f"확인용 쌍별 차이 {delta:+.4f}  SE {se:.4f}  95% 하한 {lower:+.4f}")
    print(f"실행 행동: {exec_action}" + (f"   ({reason})" if reason else ""))
    print(f"모형 간 불일치: {'있음' if disagree else '없음'}")
    print()
    print("가정하 시나리오 승률이다(scenario_only). 실제 대회 우승 확률이 아니다.")

    d = dec.Decision(
        decision_at=datetime.now().isoformat(timespec="seconds"),
        snapshot_id=hashlib.sha256(json.dumps(
            {"codes": codes, "prices": prices, "ret": my_ret,
             "turnover": turnover_now, "days": days_left},
            sort_keys=True).encode()).hexdigest()[:16],
        probability_semantics="scenario_only", baseline_id=baseline,
        continuation_policy_id="continuation_v1", selection_paths=n_sel,
        validation_paths=n_val, master_seed=seed, candidates=results,
        model_best_action=best.action_id if best else "-", execution_action=exec_action,
        paired_delta=delta, paired_se=se, paired_lower_bound=lower,
        model_disagreement=disagree, execution_eligible=False,
        block_reasons=[f"MODE_{mode.upper()}"], fallback_reason=reason,
        assumptions=[f"경쟁자 모형 {len(scenarios.FIELD_MODELS)}종 균등가중",
                     f"참가자 {n_field}명 (경쟁자 {n_comp}명, 관측 {len(tr)}명)",
                     "블록 부트스트랩 5일 블록",
                     f"체결률 {rules.fill_rate:.0%}",
                     "관측 리더보드 없으면 경쟁자 시작 수익률 0%, 있으면 미관측 상대는 필드 평균"],
        limitations=["경로 안에서 알파를 재계산하지 않는다 (continuation_v1 = 0일차 목표 유지)",
                     "호가창·부분체결의 순차 전개 없음",
                     "유니버스가 오늘 기준 선택이라 생존 편향이 남는다",
                     "ETF 회전율 산입 미확인 시 QUALIFY_MIN 은 실행 불가로 표시된다"])

    out = ROOT / "state" / f"decision_{date.today():%Y%m%d}_{mode}.json"
    payload = dict(vars(d))
    payload["candidates"] = [vars(c) for c in results]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"mode": mode, "decision": payload},
                              ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    print(f"결정 기록 저장: {out}")
    if mode != "execute":
        print("주문 파일은 만들지 않았다 — execute.py 가 이 파일을 소비하지 않는다.")

    chosen = next((a for a in actions if a.action_id == exec_action), None)
    return {"decision": d, "action": chosen, "actions": actions,
            "validation_ran": delta is not None,
            "snapshot_id": d.snapshot_id, "prices": prices,
            "path": str(out)}


def decide_for_execution(cfg: dict, positions: dict, unresolved: list[str],
                         scored=None, uni=None):
    """실행 연결용. 결정을 내고 **게이트를 통과했을 때만** 행동을 돌려준다.

    돌려주는 것은 목표 비중이다. 주문 생성·규정 검사·리스크 게이트는 기존
    경로(`portfolio.make_orders`, `risk.check_orders`)가 그대로 한다. 새 엔진이
    자기만의 주문 경로를 갖지 않는다 — 그러면 검증된 방어가 통째로 우회된다.

    반환: (weights | None, decision, gate_reasons, fallback)
    """
    from . import decision as dec

    de = cfg.get("decision_engine", {})
    mode = de.get("mode", "shadow")

    class _A:
        pass
    a = _A()
    a.mode = mode
    a.paths = None
    res = run(cfg, a, scored=scored, uni=uni)
    if not res:
        return None, None, ["ENGINE_NO_RESULT"], "BASELINE"

    d = res["decision"]
    act = res["action"]
    rules_status = cfg.get("rules_status", {})
    unconfirmed = [k for k, v in rules_status.items()
                   if not k.startswith("_") and v is False]

    g = dec.GateInput(
        mode=mode,
        # 대조 완료 판정: 접수 불명 주문이 없고, 오늘 원장과 보유가 어긋나지 않음.
        reconciled=not unresolved,
        unresolved_orders=list(unresolved),
        unconfirmed_rules=unconfirmed,
        validation_ran=bool(res["validation_ran"]),
        state_hash_now=res["snapshot_id"],
        state_hash_at_decision=d.snapshot_id,
        action_uses_unconfirmed_rule=bool(
            act is not None and act.qualification_mode == "PLANNED"
            and not rules_status.get("etf_counts_for_turnover", False)),
    )
    ok, reasons = dec.execution_gate(g)
    fallback = "" if ok else dec.gate_fallback(reasons)

    # 관문 결과를 결정 파일에 **다시 적는다.** 예전에는 저장 시점의 초기값
    # (execution_eligible=False, MODE_EXECUTE)만 남아 화면이 실제 판정과 달랐다.
    # 채택 행동(adopted_action)은 모형 최선·실행 후보와 다른 필드다 — 관문이 막으면
    # 기준선 또는 신규 주문 없음이 채택된다.
    if ok and act is not None:
        adopted = act.action_id
    elif fallback == "NO_NEW_ORDERS":
        adopted = "NO_NEW_ORDERS"
    else:
        adopted = d.baseline_id
    try:
        p = Path(res["path"])
        dj = json.loads(p.read_text(encoding="utf-8"))
        dj.setdefault("decision", {}).update({
            "execution_eligible": bool(ok),
            "block_reasons": list(reasons),
            "fallback": fallback,
            "adopted_action": adopted,
            "gate_evaluated_at": datetime.now().isoformat(timespec="seconds"),
        })
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(dj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(p)
    except Exception as exc:
        print(f"  관문 결과 기록 실패 ({type(exc).__name__}: {exc})")

    if not ok or act is None:
        return None, d, reasons, fallback
    return dict(act.weights), d, [], ""
