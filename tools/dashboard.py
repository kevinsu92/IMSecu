# -*- coding: utf-8 -*-
"""대회 내내 무슨 일이 일어나는지 한 장으로 본다.

무인 운영에서 사람이 잃는 것은 통제가 아니라 **가시성**이다. 프로그램이 알아서
돌수록 "지금 정상인가"와 "왜 저 종목을 샀나"를 답할 방법이 없어진다. 로그 파일
넷과 상태 파일 여러 개에 흩어져 있으면 아무도 안 본다.

여기서 한 장으로 모아 `state/dashboard.html` 로 쓴다. 파일 하나에 데이터까지
박아 넣어 브라우저로 열기만 하면 되고, 서버도 인터넷도 필요 없다.

    python tools/dashboard.py            뉴스까지 포함
    python tools/dashboard.py --no-news  빠르게
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S = ROOT / "state"
sys.path.insert(0, str(ROOT))
for st in (sys.stdout, sys.stderr):
    try:
        st.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def rd(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def tail(p: Path, n: int) -> list[str]:
    try:
        return [l.rstrip() for l in p.read_text(encoding="utf-8", errors="replace")
                .splitlines() if l.strip()][-n:]
    except Exception:
        return []


def gather(with_news: bool = True) -> dict:
    cfg = rd(ROOT / "config" / "settings.json", {}) or {}
    con = cfg.get("contest", {})
    today = date.today()
    day = f"{today:%Y%m%d}"

    # 달력
    try:
        from imrl import data as _d
        cal = _d.TradingCalendar.from_config(con)
        days = cal.trading_days()
        left = cal.days_left()
        total = cal.total_days()
    except Exception:
        days, left, total = [], 0, 20

    # HTS / 감시자
    hts = {"ok": False, "text": "확인 못 함"}
    try:
        from imrl import hts_exec
        pid = hts_exec.axis_pid()
        if not pid:
            hts = {"ok": False, "text": "HTS 가 꺼져 있다"}
        else:
            try:
                hts_exec.find_order_window(pid)
                hts = {"ok": True, "text": f"준비됨 (pid {pid}, 주문 화면 열림)"}
            except Exception:
                hts = {"ok": False, "text": f"실행 중(pid {pid}) — 로그인 전이거나 화면 1200 미개방"}
    except Exception as exc:
        hts = {"ok": False, "text": f"모듈 로드 실패 {exc}"}

    # 오늘 단계
    steps, rest = [], None
    try:
        import watchdog as w
        rest = w.rest_day(today)
        now = datetime.now().time()
        has_sheet = (S / f"orders_{day}.json").exists()
        for name, t0, t1, _cmd, _need in w.STEPS:
            done = w.step_done(name)
            if name in ("sell", "buy") and not has_sheet:
                stt = "wait"; note = "주문서 생성 전"
            elif done:
                stt = "ok"; note = "완료"
            elif now < t0:
                stt = "wait"; note = f"{t0:%H:%M} 시작"
            elif now >= t1:
                stt = "bad"; note = f"놓침 (마감 {t1:%H:%M})"
            else:
                stt = "wait"; note = f"실행 창 {t0:%H:%M}~{t1:%H:%M}"
            steps.append({"name": name, "state": stt, "note": note,
                          "t0": f"{t0:%H:%M}", "t1": f"{t1:%H:%M}"})
    except Exception as exc:
        steps = [{"name": "?", "state": "bad", "note": str(exc), "t0": "", "t1": ""}]

    # 자격 요건
    qual = {}
    try:
        from imrl import state as st
        c = st.compute_constraints(int(con.get("principal", 100000000)), left)
        req = cfg.get("requirements", {})
        # 세 지표 모두 **공식 우선**(effective_*)으로 읽는다. 예전에는 회전율만 공식이고
        # 일수·종목수는 내부 원장이라, 공식 6일/7종목이 화면에 0/0 으로 나올 수 있었다.
        _lb = rd(S / "leaderboard.json", {}) or {}
        qual = {
            "turnover": [float(c.effective_turnover), float(req.get("min_turnover_pct", 500))],
            "days": [float(c.effective_days), float(req.get("min_trading_days", 5))],
            "symbols": [float(c.effective_symbols), float(req.get("min_distinct_symbols", 5))],
            "source": "공식(중계실)" if c.has_official else f"내부 추정 × 체결률 {c.assumed_fill_rate:.0%}",
            "official": {"turnover": c.official_turnover_pct, "days": c.official_trading_days,
                         "symbols": c.official_distinct_symbols, "at": _lb.get("updated", "")},
            "internal": {"turnover": float(c.turnover_conservative), "days": int(c.trading_days),
                         "symbols": int(c.distinct_symbols)},
            "etf_rule": {"symbols": cfg.get("rules_status", {}).get("etf_counts_for_symbols"),
                         "turnover": cfg.get("rules_status", {}).get("etf_counts_for_turnover"),
                         "evidence": cfg.get("rules_status", {}).get("_turnover_evidence", "")},
        }
    except Exception:
        qual = {}

    # 매매 이력 — 날짜별 주문서 + 전송원장
    history = []
    for op in sorted(S.glob("orders_*.json")):
        d = op.stem.split("_")[-1]
        orders = rd(op, []) or []
        led = {str(r.get("key", "")): str(r.get("status", "")) for r in (rd(S / f"submitted_{d}.json", []) or [])}
        rows = []
        for o in orders:
            side, code = str(o.get("side", "")).upper(), str(o.get("code", ""))
            pre = f"{side}:{code}"
            hits = {k: v for k, v in led.items() if k == pre or k.startswith(pre + "#")}
            vals = list(hits.values())
            # 원장은 키별 마지막 상태(led 는 뒤에 쓴 줄이 이긴다). 거절(not_accepted)은
            # 접수가 아니다 — 예전에는 '접수 1건'으로 보였다(외부 검토 재현).
            if not hits:
                stt = "미전송"
            elif any(v in ("unknown", "pending_send", "modal") for v in vals):
                stt = "접수 불명"
            elif all(v == "not_accepted" for v in vals):
                stt = "미접수(거절)"
            elif any(v == "not_accepted" for v in vals):
                stt = f"부분 접수 {sum(1 for v in vals if v == 'submitted')}/{len(vals)}"
            else:
                stt = f"접수 {len(hits)}건 · 체결 미확인"
            rows.append({"side": side, "code": code, "name": o.get("name", code),
                         "qty": int(o.get("qty", 0)), "price": int(o.get("limit_price", 0)),
                         "amount": int(o.get("amount", 0)), "reason": o.get("reason", ""),
                         "status": stt})
        history.append({"date": d, "orders": rows})
    history.sort(key=lambda x: x["date"], reverse=True)

    # 중계실 추이
    series = []
    for rp in sorted(S.glob("relay_*.json")):
        snap = rd(rp, {}) or {}
        su = snap.get("summary", {}) or {}
        mi = snap.get("me_index", -1)
        rows_ = snap.get("rows", []) or []
        me = rows_[mi] if 0 <= mi < len(rows_) else None
        series.append({
            "date": str(su.get("date") or snap.get("at", "")),
            "n_field": su.get("n_field"),
            "max": su.get("max_pct"), "avg": su.get("avg_pct"), "min": su.get("min_pct"),
            "mine": (me or {}).get("ret_pct"),
            "rank": (me or {}).get("rank"),
            "turnover": (me or {}).get("turnover_pct"),
        })

    board = rd(S / "leaderboard.json", {}) or {}
    fieldh = rd(S / "field_history.json", []) or []
    positions = rd(S / "positions.json", {}) or {}

    # 세션 판정
    try:
        from imrl import session as _sess
        v = _sess.verdict()
        sess = {"text": v.text, "determined": v.determined, "records": v.records}
    except Exception:
        sess = {"text": "확인 못 함", "determined": False, "records": 0}

    # 뉴스 — 보유 + 오늘 주문 종목
    names = [str(p.get("name") or c) for c, p in positions.items()]
    if history:
        names += [o["name"] for o in history[0]["orders"]]
    names = [n for n in dict.fromkeys(names) if n]
    # 사이클 기록이 있으면 그것을 보인다 — 매시간 새로 긁은 것이고, 여기서 다시 긁으면
    # 같은 출처를 두 번 두드리는 것뿐이다. 기록이 없을 때만(첫 회차 전) 직접 긁는다.
    newsd = {"by_name": {}, "failed": [], "at": "", "source": ""}
    snap_files = (sorted(p for p in (S / "news").glob("*.json") if len(p.stem) == 8)
                  if (S / "news").exists() else [])
    if snap_files:
        j = rd(snap_files[-1], {}) or {}
        by, miss = {}, []
        for v in (j.get("by_code") or {}).values():
            if v.get("headlines"):
                by[str(v.get("name"))] = v["headlines"]
            else:
                miss.append(str(v.get("name")))
        newsd = {"by_name": by, "failed": miss, "at": j.get("at", ""),
                 "source": f"사이클 기록 {j.get('date', '')} {str(j.get('at', ''))[11:16]}"}
    elif with_news and names:
        try:
            from imrl import news
            newsd = news.collect(names[:6], per=4)
            newsd["source"] = "실시간 수집"
        except Exception as exc:
            newsd = {"by_name": {}, "failed": [f"{type(exc).__name__}"], "at": "", "source": ""}

    # 유니버스 요약 (설정만 — 매번 다시 만들지 않는다)
    uni = cfg.get("universe", {})

    # 대회 규정 대조표 — 주최측 안내문 7장을 코드가 어떻게 지키는지.
    # "안다"고 적지 않는다. 어디서 지키는지 파일을 적는다.
    rules = [
        ("투자대상: 거래소·코스닥 개별종목 (ETF 포함, 단일종목레버리지 ETF 제외)", "ok",
         "universe.markets=KOSPI/KOSDAQ; 유니버스는 개별주식만 (ETF 는 유니버스에 없음). 파밍 ETF 3종은 CD금리·초단기채권 — 레버리지 아님"),
        ("현재가 1,000원 미만 제외", "ok", "universe.min_price=1000 (imrl/universe.py 단계 3)"),
        ("총발행주식수 100,000주 미만 제외", "ok", "universe.min_shares=100000 (단계 4)"),
        ("관리종목·정리매매·투자주의·투자경고·투자위험 제외", "ok",
         "소속부 필터 + 계획 때마다 시장경보 명단 수집 (imrl/data.py fetch_alert_codes). 수집 실패 시 예외 — 조용히 통과하지 않음. 제외 종목 수는 매일 다르다"),
        ("거래정지·코넥스·K-OTC 제외", "ok", "시장 필터(코스피/코스닥만) + 주문 직전 거래정지 재확인 (imrl/universe.py:81)"),
        ("ETN/ELW 제외", "ok", "유니버스 명칭 검사 잔존 0건"),
        ("수수료 0.01% + 제세금 0.20% (ETF 제세금 없음)", "ok",
         "backtest.ROUND_TRIP_COST=0.0022, contest_sim cost_stock=0.0011/측 cost_etf=0.0001"),
        ("주문유형: 지정가·시장가만 / 현금매수·매도만 (미수·신용·공매도 불가)", "ok",
         "보통가(지정가) 현금 주문만 냄; 시장가 체크는 매번 해제 확인 (_ensure_auto_price_off, reset_form)"),
        ("매매시간: 정규 09:00~15:20, 동시호가 15:20~15:30", "ok",
         "매도 15:10 (정규), 매수 15:21 (종가 동시호가). 감시자 창 sell 15:10~15:19 / buy 15:21~15:28"),
        ("시간외거래(NXT/KRX) 미지원", "warn",
         "주문창 매매구분이 '스마트'(KRX/NXT 자동). 모의 시스템이 NXT 를 안 받으므로 KRX 로만 갈 것으로 보이나 1일차 체결내역에서 확인"),
        ("15:20~15:30 = 동시호가이자 예약주문 구간", "warn",
         "15:21 주문이 당일 동시호가가 아니라 익일 예약주문으로 잡힐 가능성. 02_execution 이 15:17 권장. 1일차 15:40 대조에서 당일 체결 여부로 확정"),
        ("체결: 실제시장 1:1 대응, 현재가 또는 유리한 호가에 실제 체결량만큼", "warn",
         "체결 '가격'이 지정가인지 시장가인지 명시 없음. 오프셋 1% 의 비용이 0 인지 1% 인지가 여기 걸림 — 1일차 수익률로 판별 (config _offset_risk_note)"),
        ("상한가 매수·하한가 매도: 체결량 = 실제체결량/총잔량 × 주문량 × 0.1", "ok",
         "상한가 근접 종목은 목표 선정 전에 제외하고 차순위 승격 (run.py:257, imrl/portfolio.py:347). 하한가 매도는 막을 방법이 없음 — 체결 안 되면 대조에서 잔존 보유로 드러남"),
        ("단일종목 최대 투자원금의 50%", "ok",
         "max_single_weight_rule 0.50, 실운용 상한 49%(여유 1%p, 매뉴얼 p17 '투자원금의 최대 50%까지 편입') · 현금 5% 완충 — 손실복구 옵션 포함 어떤 수익률에서도 원금대비 49% 초과 없음 (selftest_options 스윕)"),
        ("최소 거래: 회전율 500% / 체결 5종목 / 매매일수 5일", "ok",
         "회전율 목표 650% (마진), 파밍 ETF 로 종목·일수 확보. 중계실 공식 수치가 매일 자동 수집돼 내부 추정을 대체 (imrl/relay.py)"),
        ("동률 시 회전율 高 → 거래일수 多", "ok", "state.compute_constraints 가 둘 다 추적; 회전율은 요건보다 여유 있게"),
        ("마이너스 수익률 포상 제외", "ok",
         "결정 엔진과 하방 분석 모두 P(수익률>0) 를 자격 조건으로 계산 (tools/downside.py)"),
        ("3일 결제 / 권리락·액면분할 시 수량·현금 보정", "warn",
         "매도대금 당일 재사용 가정 (run.py:478, 총평가금액 산식과 일치). 권리락 보정은 대조(reconcile)에서 보유 수량 불일치로 감지 — 자동 보정 없음"),
    ]

    experts = []
    reg = rd(ROOT / "research" / "hypothesis_status.json", {}) or {}
    hyps = reg.get("hypotheses", {}) or {}
    labels = {"01_momentum": "주도주·모멘텀", "02_execution": "장중 진입·체결",
              "03_swing": "스윙·오버나이트", "04_quant": "퀀트 검증", "05_regime": "시장 레짐",
              "06_risk": "리스크·토너먼트", "07_redteam": "레드팀"}
    for d in sorted((ROOT / "research" / "experts").glob("0*_*")):
        mine = {k: v for k, v in hyps.items() if v.get("expert") == d.name}
        fin = d / "FINDINGS.md"
        handoff = []
        if fin.exists():
            txt = fin.read_text(encoding="utf-8", errors="replace")
            i = txt.find("메인 개발 Claude")
            if i >= 0:
                for line in txt[i:].splitlines()[1:60]:
                    t = line.strip()
                    if not t or t.startswith("#") or t.startswith("|--") or t.startswith("| #") or t.startswith("| 위치") or t.startswith("| 순위"):
                        continue
                    if t.startswith("|"):
                        cells = [c.strip() for c in t.strip("|").split("|") if c.strip()]
                        if len(cells) >= 2:
                            handoff.append(cells[1][:140] if cells[0][:1].isdigit() or cells[0].startswith("**") else cells[0][:140])
                    elif t[0].isdigit() or t.startswith(("-", "①", "②", "③", "④", "**")):
                        handoff.append(t.lstrip("-*① ②③④ ").strip("* ")[:140])
                    if len(handoff) >= 5:
                        break
        by_status = {}
        for v in mine.values():
            st_ = v.get("status", "IDEA"); by_status[st_] = by_status.get(st_, 0) + 1
        experts.append({
            "id": d.name, "label": labels.get(d.name, d.name),
            "n_hyp": len(mine), "by_status": by_status,
            "live": [{"id": k, "title": v.get("title", ""), "evidence": v.get("evidence", "")}
                     for k, v in mine.items() if v.get("status") not in ("IDEA", "REJECTED")],
            # 마크다운 표식은 화면에서 잡음이다. 굵게·코드 표시만 벗긴다.
            "handoff": [h.replace("**", "").replace("`", "").strip()
                        for h in handoff if h.strip(" *`-")],
        })
    measured = reg.get("measured_options", {}) or {}

    try:
        from imrl import proposals as _pp
        props = _pp.summary()
    except Exception:
        props = []
    last_dec = None
    decs = sorted(S.glob("decision_*_execute.json"))
    if decs:
        dj = rd(decs[-1], {}) or {}
        dd = dj.get("decision", {}) or {}
        # 후보 **전부**를 실제 스키마(aggregate_win)로 읽는다. 예전에는 PROP 만 추리고
        # 있지도 않은 키 이름을 찾아 12.34% 가 화면에서 사라졌다(외부 검토 재현).
        def _num(v):
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        cands = []
        for c in (dd.get("candidates", []) or []):
            cands.append({"id": str(c.get("action_id", "")), "feasible": bool(c.get("feasible")),
                          "reasons": c.get("exclusion_reasons", []) or [],
                          "win": _num(c.get("aggregate_win")), "worst": _num(c.get("worst_model_win")),
                          "qual": _num(c.get("qualification_rate")), "mean": _num(c.get("mean_return")),
                          "p10": _num(c.get("p10_return")), "exposure": _num(c.get("exposure")),
                          "cost": _num(c.get("mean_cost")), "turnover": _num(c.get("mean_turnover"))})
        last_dec = {"file": decs[-1].name, "at": dd.get("decision_at", ""),
                    "executed": dd.get("execution_action", ""),
                    "model_best": dd.get("model_best_action", ""),
                    "adopted": dd.get("adopted_action"),
                    "eligible": dd.get("execution_eligible"),
                    "block_reasons": dd.get("block_reasons", []) or [],
                    "fallback": dd.get("fallback", "") or dd.get("fallback_reason", ""),
                    "gate_at": dd.get("gate_evaluated_at", ""),
                    "lower_bound": dd.get("paired_lower_bound"),
                    "delta": dd.get("paired_delta"), "se": dd.get("paired_se"),
                    "paths": [dd.get("selection_paths"), dd.get("validation_paths")],
                    "assumptions": dd.get("assumptions", []) or [],
                    "candidates": cands,
                    "proposals": [c for c in cands if c["id"].startswith("PROP:")]}

    # 뉴스 이력 — 종목별 표식이 며칠째인가
    try:
        from imrl import news_watch as _nw
        nh = _nw.history(7)
    except Exception:
        nh = []
    news_hist = []
    seen = {}
    for dayrec in nh:
        for code, v in (dayrec.get("by_code") or {}).items():
            e = seen.setdefault(code, {"code": code, "name": v.get("name", code), "days": []})
            e["days"].append({"date": dayrec.get("date", ""), "hard": v.get("hard", []),
                              "soft": v.get("soft", []), "n": len(v.get("headlines", []))})
    news_hist = sorted(seen.values(), key=lambda e: (-len([d for d in e["days"] if d["hard"]]), e["name"]))

    try:
        from imrl import relay as _rl
        obs = _rl.observations(40)
        intraday = _rl.intraday(day)
        # 순위표 밖의 중계실 정보(최신 파일 한 장): 오늘 매매상위·대회 통계·누적 매매상위·미거래자·지수
        relay_extra, relay_rows, relay_me = {}, [], None
        try:
            _rfs = sorted(S.glob("relay_*.json"))
            if _rfs:
                _rj = json.loads(_rfs[-1].read_text(encoding="utf-8"))
                relay_extra = _rj.get("extra") or {}
                relay_rows = (_rj.get("rows") or [])[:15]
                _mi = _rj.get("me_index", -1)
                relay_me = (_rj.get("rows") or [])[_mi] if 0 <= _mi < len(_rj.get("rows") or []) else None
        except Exception:
            pass
    except Exception:
        obs, intraday = [], []

    # 사이클 — 뉴스·전문가·제안이 매시간 도는 기록. "지금 무엇을 보고 있나" 가 여기 있다.
    try:
        from imrl import cycle as _cy, news_watch as _nw2
        import watchdog as _w2
        cycles = _cy.recent(24)
        opinions = _cy.latest_opinions()
        cand = _cy.load_candidates()
        news_intraday = _nw2.intraday(day)
        try:
            mark = float(_w2.CYCLE_MARK.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            mark = 0.0
        cyc = {"next_due": (datetime.fromtimestamp(mark + _w2.CYCLE_GAP_SEC).isoformat(timespec="seconds")
                            if mark else ""),
               "blackout": _cy.blackout(), "active": list(_cy.active(cfg, today))}
    except Exception as exc:
        cycles, opinions, cand, news_intraday = [], {}, {}, []
        cyc = {"next_due": "", "blackout": False, "active": [True, ""], "error": str(exc)}

    # 신선도 — 원천마다 따로. "서버 연결됨"은 연결일 뿐 자료의 최신성이 아니다.
    hb = rd(S / "control_heartbeat.json", {}) or {}
    now_dt = datetime.now()

    def _age_iso(ts):
        try:
            return round((now_dt - datetime.fromisoformat(str(ts))).total_seconds() / 60.0, 1) if ts else None
        except ValueError:
            return None

    def _age_file(p: Path):
        try:
            return round((now_dt.timestamp() - p.stat().st_mtime) / 60.0, 1)
        except OSError:
            return None

    news_files = sorted(p for p in (S / "news").glob("*.json") if len(p.stem) == 8) if (S / "news").exists() else []
    fresh = {
        "heartbeat": _age_iso(hb.get("at")),
        "relay": _age_file(S / "relay_last_pull.txt"),
        "cycle": _age_file(S / "cycle_last_run.txt"),
        "news": _age_file(news_files[-1]) if news_files else None,
        "leaderboard": _age_iso(board.get("updated")),
        "positions": _age_file(S / "positions.json"),
        "decision": _age_iso(last_dec["at"]) if last_dec else None,
    }

    # 실행 판정 — 행동과 연결된 말로. HTTP 200 이나 주문창 존재 하나로 '정상'이 되지 않는다.
    kill_any = (S / "KILL").exists() or Path("C:/imrl_state/KILL").exists()
    reconcile = rd(S / f"reconcile_{day}.json", {}) or {}
    try:
        from imrl import experts as _ex
        unresolved = _ex._unresolved(today)
    except Exception:
        unresolved = []
    reasons = []
    if rest and rest[0]:
        status = "시작 전 · 휴장"
        reasons.append(rest[1])
    else:
        if kill_any:
            reasons.append("신규 주문 전송 중지 파일 있음")
        if (S / f"SKIP_{day}").exists():
            reasons.append("오늘 매도·매수 건너뛰기")
        if not hts.get("ok"):
            reasons.append("HTS 미준비 — 로그인 필요")
        if unresolved:
            reasons.append(f"접수 불명 주문 {len(unresolved)}건 — 대조 필요 (신규 주문 차단)")
        if reconcile.get("status") == "FAIL":
            reasons.append(f"오늘 대조 FAIL — 불일치 {reconcile.get('issues', 0)}건")
        status = ("신규 주문 중지" if kill_any else ("확인 필요" if reasons else "실행 준비"))
    readiness = {"status": status, "reasons": reasons, "unresolved": len(unresolved),
                 "reconcile": reconcile.get("status", "미실행"),
                 "mode": cfg.get("decision_engine", {}).get("mode", ""),
                 "kill_paths": [str(p) for p in (S / "KILL", Path("C:/imrl_state/KILL")) if p.exists()]}

    # 1위 격차 — %p 와, 선두가 멈춘다는 가정하의 필요 추가 수익률. 예측이 아니다.
    gap = None
    try:
        L, R = board.get("leader_return_pct"), board.get("my_return_pct")
        if L is not None and R is not None:
            need = (1 + float(L) / 100) / (1 + float(R) / 100) - 1
            gap = {"leader": float(L), "mine": float(R), "pp": float(L) - float(R),
                   "need_pct": need * 100,
                   "per_day_pct": ((1 + need) ** (1 / max(left, 1)) - 1) * 100 if need > 0 else 0.0,
                   "days_left": left}
    except (TypeError, ValueError):
        gap = None

    return {
        "heartbeat": hb, "fresh": fresh, "readiness": readiness, "gap": gap,
        "reconcile": reconcile,
        "cycles": cycles, "opinions": opinions, "candidates": cand,
        "news_intraday": news_intraday, "cycle": cyc,
        "news_history": news_hist, "observations": obs, "intraday": intraday,
        "control_url": "http://127.0.0.1:8765/",
        "rules": rules, "experts": experts, "measured": measured,
        "proposals": props, "last_decision": last_dec,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "contest": {"name": con.get("name", ""), "start": con.get("start_date", ""),
                    "end": con.get("end_date", ""), "principal": con.get("principal", 0),
                    "n_field": board.get("n_field") or con.get("n_field"),
                    "prize_places": con.get("prize_places"),
                    "days_left": left, "days_total": total,
                    "holidays": con.get("holidays", [])},
        "hts": hts, "steps": steps,
        "rest": {"is": bool(rest and rest[0]), "why": (rest[1] if rest else "")},
        "qual": qual, "history": history, "series": series,
        "board": board, "field": fieldh[-5:], "positions": positions,
        "relay_extra": relay_extra, "relay_rows": relay_rows, "relay_me": relay_me,
        "session": sess, "news": newsd, "universe": uni,
        "risk": cfg.get("risk_options", {}),
        "portfolio": cfg.get("portfolio", {}),
        "watchdog_log": tail(S / "watchdog.log", 12),
        "kill": kill_any,
        "skip_today": (S / f"SKIP_{day}").exists(),
    }


def build(with_news: bool = True) -> Path:
    from dashboard_view import HTML
    d = gather(with_news)
    out = S / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    # </script> 가 데이터 안에 있으면 문서가 거기서 끊긴다.
    payload = json.dumps(d, ensure_ascii=False).replace("</", "<\\/")
    body = HTML.replace("__DATA__", payload)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(out)
    return out


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    p = build(with_news="--no-news" not in sys.argv)
    print(f"상황판: {p}")
    print("  브라우저로 열면 된다. 서버도 인터넷도 필요 없다.")
