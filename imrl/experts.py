# -*- coding: utf-8 -*-
"""전문가 7명의 **점검표**를 매 사이클 돌린다.

무엇인가
  research/experts/0N_* 의 전문가들은 문서다. 문서는 시간이 지나도 같은 말을 한다.
  대회 중에 필요한 것은 "지금 상태를 그 전문가의 잣대로 보면 어떤가" 다. 그래서
  각 전문가의 FINDINGS·DEV_REQUIREMENTS 에서 **지금 계산할 수 있는 점검 항목**만
  뽑아 코드로 옮겼다. 매시간 같은 잣대로 재고 결과를 남긴다(state/experts/).

무엇이 아닌가
  이 의견은 주문이 아니다. 여기서 나오는 것은 세 등급뿐이다.
    info   기록 (상황판)
    warn   기록 + 상황판 강조
    alert  기록 + 텔레그램 (같은 건은 하루 한 번)
  매매에 닿는 통로는 여전히 둘이다 — 뉴스 경성 위험의 제외 **제안**(15:05 관문 통과
  필요, imrl/news_watch.py)과 중계실 σ_f(imrl/decision_cmd.py). 전문가 의견이 검증
  없이 주문이 되는 경로는 만들지 않는다. "7명 중 7명이 동의해도 데이터 검증 전에는
  hypothesis 다" — 전문가들 스스로 세운 원칙이다.

  LLM 을 부르지 않는다. 여기 있는 것은 전문가 문서에서 옮긴 **결정론적 규칙**이다.
  같은 입력이면 같은 의견이 나오고, 왜 그 의견이 나왔는지 코드로 추적된다.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S = ROOT / "state"

EXPERTS = [("01_momentum", "주도주·모멘텀"), ("02_execution", "장중 진입·체결"),
           ("03_swing", "스윙·오버나이트"), ("04_quant", "퀀트 검증"),
           ("05_regime", "시장 레짐"), ("06_risk", "리스크·토너먼트"),
           ("07_redteam", "레드팀")]
LEVELS = ("info", "warn", "alert")


def _rd(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def op(expert: str, level: str, key: str, text: str) -> dict:
    if level not in LEVELS:
        raise ValueError(f"level {level!r}")
    return {"expert": expert, "level": level, "key": key, "text": text}


def _age_min(path: Path, now: datetime) -> float | None:
    """파일이 몇 분 전 것인가. 없으면 None."""
    try:
        return max(0.0, (now.timestamp() - path.stat().st_mtime) / 60.0)
    except OSError:
        return None


def _age_iso(stamp: str | None, now: datetime) -> float | None:
    if not stamp:
        return None
    try:
        return max(0.0, (now - datetime.fromisoformat(str(stamp))).total_seconds() / 60.0)
    except ValueError:
        return None


def _unresolved(today: date, lookback_days: int = 7) -> list[str]:
    """접수 여부가 확정되지 않은 주문 키 (run.py 의 것과 같은 규칙)."""
    out: list[str] = []
    for back in range(lookback_days):
        day = today - timedelta(days=back)
        p = S / f"submitted_{day:%Y%m%d}.json"
        if not p.exists():
            continue
        rows = _rd(p, None)
        if rows is None:
            out.append(f"LEDGER_UNREADABLE:{day:%Y%m%d}")
            continue
        last: dict[str, str] = {}
        for r in rows:
            last[str(r.get("key"))] = str(r.get("status", "submitted"))
        out += [f"{day:%m%d}/{k}" for k, s in last.items()
                if s in ("unknown", "pending_send", "modal")]
    return out


def context(cfg: dict, day: str, now: datetime, snap: dict | None, cand: dict | None) -> dict:
    """전문가들이 보는 세계. 전부 파일에서 읽는다 — 네트워크 없음."""
    today = now.date()
    con = cfg.get("contest", {})
    ctx: dict = {
        "cfg": cfg, "day": day, "now": now, "today": today,
        "news": snap or {}, "candidates": cand or {},
        "positions": _rd(S / "positions.json", {}) or {},
        "board": _rd(S / "leaderboard.json", {}) or {},
        "heartbeat": _rd(S / "control_heartbeat.json", {}) or {},
        "proposals": [], "relay": {}, "field": {}, "hyps": {}, "constraints": None,
        "kill": (S / "KILL").exists() or Path("C:/imrl_state/KILL").exists(),
        "skip": (S / f"SKIP_{day}").exists(),
        "plan_done": (S / f"orders_{day}.json").exists(),
        "reconcile_done": (S / f"reconcile_{day}.done").exists(),
        # "돌렸다"(done) 와 "맞다"(PASS) 는 다른 사실이다 — 결과 파일을 따로 읽는다.
        "reconcile": _rd(S / f"reconcile_{day}.json", {}) or {},
        "unresolved": _unresolved(today),
        "relay_age_min": _age_min(S / "relay_last_pull.txt", now),
        "trading_day": False, "days_left": 0, "total": 20, "elapsed": 0, "future_days": [],
    }
    try:
        from .data import TradingCalendar
        cal = TradingCalendar.from_config(con)
        tds = cal.trading_days()
        ctx["trading_day"] = today in set(tds)
        ctx["days_left"] = cal.days_left(today)
        ctx["total"] = cal.total_days()
        ctx["elapsed"] = ctx["total"] - ctx["days_left"]
        ctx["future_days"] = [d for d in tds if d >= today][:6]
    except Exception:
        pass
    files = sorted(S.glob("relay_*.json"))
    if files:
        ctx["relay"] = _rd(files[-1], {}) or {}
    fh = _rd(S / "field_history.json", []) or []
    if fh:
        ctx["field"] = fh[-1]
    reg = _rd(ROOT / "research" / "hypothesis_status.json", {}) or {}
    ctx["hyps"] = reg.get("hypotheses", {}) or {}
    try:
        from . import proposals as _pp
        ctx["proposals"] = _pp.summary(today)
    except Exception:
        pass
    try:
        from . import state as _st
        ctx["constraints"] = _st.compute_constraints(int(con.get("principal", 100000000)),
                                                     ctx["days_left"])
    except Exception:
        pass
    return ctx


# ------------------------------------------------------------------ 전문가들

def _e01(ctx: dict) -> list[dict]:
    """주도주·모멘텀 — 후보가 최신인가, 후보에 붙은 표식은 무엇인가."""
    E = "01_momentum"; out = []
    c = ctx["candidates"]; news = ctx["news"]; now = ctx["now"]
    if not c or not c.get("codes"):
        out.append(op(E, "warn", "mom.nocand",
                      "후보 목록이 없다 — 14:45 정밀 회차(전체 점수 재계산) 전이거나 실패. "
                      "뉴스 감시는 보유 종목만 본다"))
        return out
    top_n = int(c.get("top_n", 2)); codes = list(c["codes"]); names = c.get("names", {}) or {}
    try:
        age = (ctx["today"] - datetime.strptime(str(c.get("date")), "%Y%m%d").date()).days
    except ValueError:
        age = 0
    top = ", ".join(str(names.get(x, x)) for x in codes[:top_n])
    if age >= 1 and ctx["trading_day"] and now.time() >= dtime(14, 50):
        out.append(op(E, "warn", "mom.stale",
                      f"후보 목록이 {age}일 전 것({c.get('date')}) — 오늘 14:45 재계산이 아직 없다. "
                      "15:05 계획은 자체 점수로 다시 뽑으므로 매매에는 영향 없음"))
    else:
        out.append(op(E, "info", "mom.top",
                      f"후보 top-{top_n}: {top} (기준 {c.get('date')} {c.get('phase', '')}, "
                      f"감시 {len(codes)}종목)"))
    prev = c.get("previous") or {}
    if prev.get("codes") and prev.get("date") != c.get("date"):
        kept = len(set(codes[:top_n]) & set(list(prev["codes"])[:top_n]))
        out.append(op(E, "info", "mom.churn",
                      f"전 회차({prev.get('date')}) 대비 top-{top_n} 유지 {kept}/{top_n} — "
                      "교체는 설계된 회전(실측 일평균 23%)의 재료다"))
    for x in codes[:top_n]:
        soft = (news.get(x) or {}).get("soft") or []
        hot = [s for s in soft if s in ("급등", "과열", "테마", "상한가")]
        if hot:
            out.append(op(E, "info", f"mom.hot.{x}",
                          f"{names.get(x, x)} 표식 {'·'.join(hot)} — 변동성은 이 대회에서 비용이 아니라 "
                          "자산(vol 가중 +0.5). 제외 사유 아님"))
    return out


def _e02(ctx: dict) -> list[dict]:
    """장중 진입·체결 — 오늘 집행이 나갈 수 있는 상태인가."""
    E = "02_execution"; out = []
    t = ctx["now"].time(); hb = ctx["heartbeat"]
    if not ctx["trading_day"]:
        nxt = ctx["future_days"][0] if ctx["future_days"] else None
        out.append(op(E, "info", "exec.rest",
                      "휴장 — 집행 없음" + (f". 다음 거래일 {nxt:%m/%d}" if nxt else "")))
        return out
    hts_ok = bool(hb.get("hts_ok"))
    if dtime(13, 30) <= t < dtime(15, 28) and not hts_ok:
        out.append(op(E, "alert", "exec.hts",
                      f"HTS 미준비 ({hb.get('hts', '상태 미상')}) — 15:10 매도·15:21 매수가 나가지 "
                      "못한다. 로그인이 필요하다"))
    elif t < dtime(15, 5):
        out.append(op(E, "info", "exec.plan",
                      f"HTS {'준비됨' if hts_ok else '미준비'} · 오늘 집행: 15:05 계획 → 15:10 매도(3분할) "
                      "→ 15:21 매수(종가 동시호가 +1%) → 15:40 대조"))
    if t >= dtime(15, 20) and not ctx["plan_done"] and not ctx["skip"] and not ctx["kill"]:
        out.append(op(E, "alert", "exec.noplan",
                      "15:05 주문서가 없다 — 오늘 매매가 나가지 않는다. state/watchdog.log 와 "
                      "state/run.log 를 확인할 것"))
    un = ctx["unresolved"]
    if un:
        out.append(op(E, "alert", "exec.unresolved",
                      f"접수 불명 주문 {len(un)}건 ({', '.join(un[:3])}) — 계좌 상태 미확정. 결정 엔진은 "
                      "신규 주문을 만들지 않는다. HTS 체결내역과 대조할 것"))
    else:
        out.append(op(E, "info", "exec.ledger", "접수 불명 주문 없음"))
    if ctx["elapsed"] == 0:
        out.append(op(E, "info", "exec.day1",
                      "1일차 관측일: 15:21 주문의 접수 유형(동시호가/예약)·NXT 라우팅은 15:40 대조로, "
                      "체결가 규칙(종가/지정가)은 종가 후 수익률로 확정한다"))
    return out


def _e03(ctx: dict) -> list[dict]:
    """스윙·오버나이트 — 갭 구간과 보유 기간."""
    E = "03_swing"; out = []
    fd = ctx["future_days"]; pos = ctx["positions"]; today = ctx["today"]
    for i in range(len(fd) - 1):
        gap = (fd[i + 1] - fd[i]).days
        if gap >= 3:
            lead = (fd[i] - today).days
            out.append(op(E, "warn" if lead <= 1 else "info", "swing.gap",
                          f"{fd[i]:%m/%d} 종가 후 {gap - 1}일 휴장(→ {fd[i + 1]:%m/%d}) — 오버나이트 갭 구간 "
                          f"D-{lead}. CONFLICT-012 미해결: 규칙상 노출 축소 없음, 갭은 필드 전원이 같이 맞는다"))
            break
    if not pos:
        out.append(op(E, "info", "swing.flat", "보유 없음 — 오버나이트 노출 0"))
        return out
    parts = []
    for code, p in list(pos.items())[:4]:
        raw = str(p.get("entry_date", ""))[:10]
        held = None
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                held = (today - datetime.strptime(raw[:8] if fmt == "%Y%m%d" else raw, fmt).date()).days
                break
            except ValueError:
                continue
        parts.append(f"{p.get('name', code)} {held}일" if held is not None else str(p.get("name", code)))
    out.append(op(E, "info", "swing.held",
                  f"보유 {len(pos)}종목 ({', '.join(parts)}) — 청산 축은 가격 스탑이 아니라 순위 교체(시간)다"))
    return out


def _e04(ctx: dict) -> list[dict]:
    """퀀트 검증 — 배포 전략의 검증 상태와 실현 대 기대."""
    E = "04_quant"; out = []
    cnt: dict[str, int] = {}
    for v in ctx["hyps"].values():
        s = str(v.get("status", "IDEA")); cnt[s] = cnt.get(s, 0) + 1
    out.append(op(E, "warn", "quant.validation",
                  "배포 전략 검증 상태: walk-forward 5창 검정 FAIL(검정력 부족) — 시간제약 예외로 배포. "
                  f"가설 IDEA {cnt.get('IDEA', 0)} / READY_FOR_TEST {cnt.get('READY_FOR_TEST', 0)}. "
                  "대회 중 파라미터 변경 금지(사후 검증만)"))
    board = ctx["board"]; my = board.get("my_return_pct"); el = int(ctx["elapsed"])
    if my is None:
        out.append(op(E, "info", "quant.realized", "실현 수익률 관측 전 — 1일차 종가 후부터 기대치와 대조한다"))
        return out
    m = float(my); n = max(el, 1)
    out.append(op(E, "info", "quant.realized",
                  f"실현 {m:+.2f}% vs 종가체결 기대 {0.35 * n:+.2f}% ({n}일, 일평균 +0.350%) — "
                  "20일 분포 P10 -32% / 중앙값 +11.3%"))
    if el == 1 or (el == 0 and ctx["now"].time() >= dtime(16, 0)):
        if m >= -0.10:
            out.append(op(E, "info", "quant.day1",
                          f"1일차 체결가 판별: {m:+.2f}% ≥ -0.10% → 종가 체결. 오프셋 1.0% 유지"))
        elif m <= -0.70:
            out.append(op(E, "alert", "quant.day1",
                          f"1일차 체결가 판별: {m:+.2f}% ≤ -0.70% → 내 지정가 체결. execution.auction_offset_pct·"
                          "sell_offset_pct 를 0.003 으로 즉시 내릴 것 (안 내리면 기대수익 부호가 음수)"))
        else:
            out.append(op(E, "warn", "quant.day1",
                          f"1일차 체결가 판별: {m:+.2f}% (-0.70 ~ -0.10) → 부분 지정가 체결. 오프셋 0.005 로 "
                          "내리고 2일차 재관측"))
    return out


def _e05(ctx: dict) -> list[dict]:
    """시장 레짐 — 필드가 어떻게 움직이고 얼마나 흩어졌나."""
    E = "05_regime"; out = []
    su = (ctx["relay"].get("summary") or {}); f = ctx["field"]
    nf = su.get("n_field"); nt = su.get("n_traded") or 0
    if not su:
        out.append(op(E, "info", "regime.field", "중계실 관측 없음"))
    elif nt == 0:
        out.append(op(E, "info", "regime.field", f"필드 매매 전 (참가 {nf}명) — 분산 관측은 1일차 종가 후"))
    else:
        out.append(op(E, "info", "regime.field",
                      f"필드 {nf}명 / 매매 {nt}명 · 최고 {float(su.get('max_pct') or 0):+.2f}% "
                      f"평균 {float(su.get('avg_pct') or 0):+.2f}% 최저 {float(su.get('min_pct') or 0):+.2f}% "
                      f"({su.get('date')})"))
    # 군중 매매상위(당일 실시간). 우리 후보·보유와 겹치면 알린다 — 판단이 아니라 관측이다.
    ex = ctx["relay"].get("extra") or {}
    tt = ex.get("trade_top") or []
    if tt:
        cand_codes = set(str(c) for c in ((ctx.get("candidates") or {}).get("codes") or []))
        held_codes = set(str(c) for c in (ctx.get("positions") or {}).keys())
        names = ", ".join(f"{t.get('name')}({int(t.get('amount', 0) or 0) / 1e8:.1f}억)" for t in tt[:5])
        hit = [t.get("name") for t in tt[:5] if str(t.get("code")) in cand_codes | held_codes]
        out.append(op(E, "warn" if hit else "info", "regime.crowd",
                      f"군중 매매상위({tt[0].get('date')}): {names}"
                      + (f" — 우리 후보·보유와 겹침: {', '.join(hit)}" if hit else " — 우리 후보와 겹치지 않음")))
    sig = f.get("sigma_f_pct")
    if sig:
        sig = float(sig)
        if sig >= 28:
            out.append(op(E, "warn", "regime.sigma",
                          f"σ_f {sig:.1f}%p ({f.get('date')}) — 높음. 우리 분산이 필드에 못 미치면 구조적으로 "
                          "못 이긴다: 집중(top-2·45%) 유지가 유일한 경로"))
        elif sig >= 18:
            out.append(op(E, "info", "regime.sigma",
                          f"σ_f {sig:.1f}%p ({f.get('date')}) — 중간. 현 설정 유지, 막판 추격 시 공격 국면"))
        else:
            out.append(op(E, "info", "regime.sigma",
                          f"σ_f {sig:.1f}%p ({f.get('date')}) — 낮음. top-2 로 충분히 경쟁력 있음"))
    else:
        out.append(op(E, "info", "regime.sigma", "σ_f 미관측 — 결정 엔진은 설정의 필드 모델 5개를 그대로 쓴다"))
    return out


def _e06(ctx: dict) -> list[dict]:
    """리스크·토너먼트 — 순위, 마이너스, 자격 페이스, 집중도."""
    E = "06_risk"; out = []
    rl = ctx["relay"]; rows = rl.get("rows") or []; mi = rl.get("me_index", -1)
    me = rows[mi] if isinstance(mi, int) and 0 <= mi < len(rows) else None
    prize = int(ctx["cfg"].get("contest", {}).get("prize_places", 25))
    cut = rows[prize - 1].get("ret_pct") if len(rows) >= prize else None
    if me and me.get("ret_pct") is not None:
        top = float(rows[0].get("ret_pct") or 0.0); r = float(me["ret_pct"])
        txt = f"내 {me.get('rank')}위 / {r:+.2f}% · 1위 {top:+.2f}% (갭 {top - r:+.2f}%p)"
        if cut is not None:
            txt += f" · {prize}위 컷 {float(cut):+.2f}%"
        out.append(op(E, "info", "risk.rank", txt))
        if r < 0:
            out.append(op(E, "warn", "risk.negative",
                          f"수익률 마이너스({r:+.2f}%) — 이대로 끝나면 순위와 무관하게 수상 제외. 규칙상 방어가 "
                          "아니라 공격 유지(마이너스는 지킬 가치가 없다)"))
    else:
        out.append(op(E, "info", "risk.rank", "내 순위 행 미확인 (매매 전이면 정상)"))
    c = ctx["constraints"]; req = ctx["cfg"].get("requirements", {})
    dl = int(ctx["days_left"]); el = int(ctx["elapsed"]); tot = int(ctx["total"])
    if c is not None:
        got = float(c.effective_turnover)
        tgt = float(ctx["cfg"].get("turnover_farm", {}).get("target_pct_with_margin", 650))
        need = tgt * el / max(tot - 2, 1)
        unmet = c.unmet(req)
        days = c.official_trading_days if c.official_trading_days is not None else c.trading_days
        syms = c.official_distinct_symbols if c.official_distinct_symbols is not None else c.distinct_symbols
        if dl <= 5 and unmet:
            out.append(op(E, "alert", "risk.unmet",
                          f"잔여 {dl}영업일, 미충족 {', '.join(unmet)} — 파밍 상한 해제 구간. 채우지 못하면 "
                          "순위 무관 0원"))
        elif el >= 2 and got < need - 30:
            out.append(op(E, "warn", "risk.pace",
                          f"회전율 {got:.0f}% < 페이스 {need:.0f}% — 부족분은 다음 날로 이월(5일 이하에서 상한 해제)"))
        else:
            out.append(op(E, "info", "risk.qual",
                          f"회전율 {got:.0f}% (요건 500·목표 650) · 매매일 {days}/5 · 종목 {syms}/5 · 잔여 {dl}일"))
    P = int(ctx["cfg"].get("contest", {}).get("principal", 100000000)) or 1
    for code, p in ctx["positions"].items():
        v = int(p.get("qty", 0) or 0) * int(p.get("avg_price", 0) or 0) / P
        if v > 0.45:
            out.append(op(E, "alert", f"risk.conc.{code}",
                          f"{p.get('name', code)} 원금 대비 {v:.0%}(취득가 기준) — 규정 50% 근접. 재매수 금지"))
        elif v > 0.40:
            out.append(op(E, "info", f"risk.conc.{code}",
                          f"{p.get('name', code)} 원금 대비 {v:.0%} — 설계 상한 45% 안"))
    return out


def _e07(ctx: dict) -> list[dict]:
    """레드팀 — 데이터가 신선한가, 위험 종목이 제안 없이 후보에 남았나, 놓친 단계는 없나."""
    E = "07_redteam"; out = []
    hb = ctx["heartbeat"]; now = ctx["now"]
    hb_age = _age_iso(hb.get("at"), now); rl_age = ctx["relay_age_min"]
    fresh = []
    # "기록이 없다"는 "신선하다"가 아니다. 예전에는 둘 다 없을 때 '정상'이 나왔다(외부 검토 재현).
    if hb_age is None:
        out.append(op(E, "warn", "rt.hb", "추적기 심장박동 기록이 없다 — 제어 서버가 한 번도 돌지 않았거나 파일이 없다"))
    elif hb_age > 15:
        out.append(op(E, "warn", "rt.hb",
                      f"추적기 심장박동 {hb_age:.0f}분 전 — 제어 서버가 죽었을 수 있다 (로그온 작업/감시자가 다시 띄운다)"))
    else:
        fresh.append(f"심장박동 {hb_age:.0f}분")
    if rl_age is None:
        out.append(op(E, "warn", "rt.relay", "중계실 수집 기록이 없다 — 순위·필드 분산 관측이 아직 없다"))
    elif rl_age > 150:
        out.append(op(E, "warn", "rt.relay", f"중계실 마지막 수집 {rl_age:.0f}분 전 — 시간별 수집이 멈췄다"))
    else:
        fresh.append(f"중계실 {rl_age:.0f}분")
    if fresh and not out:
        out.append(op(E, "info", "rt.fresh", "데이터 신선도 정상 (" + ", ".join(fresh) + ")"))
    rc = ctx.get("reconcile") or {}
    if rc.get("status") == "FAIL":
        out.append(op(E, "warn", "rt.reconcile_fail",
                      f"오늘 대조 결과 FAIL — 불일치 {rc.get('issues', 0)}건, 접수 불명 {len(rc.get('pending', []))}건. 계좌 사실이 미확정"))
    elif rc.get("status") == "UNKNOWN":
        out.append(op(E, "info", "rt.reconcile_unknown",
                      f"오늘 대조 결과 UNKNOWN — 추정 체결 {rc.get('assumed_trades', 0)}건(접수 기준). 체결 확인 수단이 없어 사실로 승격하지 않는다"))
    news = ctx["news"]; c = ctx["candidates"] or {}
    top_n = int(c.get("top_n", 2)) if c else 2
    prop_ids = {str(p.get("id")) for p in ctx["proposals"] if not p.get("expired")}
    for code, p in ctx["positions"].items():
        hard = (news.get(code) or {}).get("hard") or []
        if hard:
            out.append(op(E, "alert", f"rt.hold.{code}",
                          f"보유 {p.get('name', code)} 경성 위험 {'·'.join(hard)} — 자동 매도 없음. 내일 매매제한 "
                          "가능성: 사람이 판단(긴급정지·제안·수동 매도)"))
    for code in list(c.get("codes") or [])[:top_n]:
        hard = (news.get(code) or {}).get("hard") or []
        if not hard:
            continue
        nm = (c.get("names") or {}).get(code, code)
        if f"PROP-NEWS-{ctx['day']}" in prop_ids:
            out.append(op(E, "info", f"rt.cand.{code}",
                          f"후보 {nm} 경성 위험 {'·'.join(hard)} → 제외 제안 PROP-NEWS-{ctx['day']} 대기 (15:05 관문)"))
        else:
            out.append(op(E, "alert", f"rt.noprop.{code}",
                          f"후보 {nm} 경성 위험 {'·'.join(hard)}인데 제외 제안이 없다 — 정밀 회차(14:45) 결과 "
                          "대기 중이거나 실패"))
    if ctx["kill"]:
        out.append(op(E, "warn", "rt.kill", "긴급정지 파일 있음 — 주문이 나가지 않는다"))
    if ctx["skip"]:
        out.append(op(E, "warn", "rt.skip", "오늘 건너뛰기 표식 — 매도·매수 없음"))
    if ctx["trading_day"] and now.time() >= dtime(16, 6) and ctx["plan_done"] and not ctx["reconcile_done"]:
        out.append(op(E, "warn", "rt.reconcile", "15:40 대조가 끝나지 않았다 — 체결·보유 상태가 미확정"))
    if ctx["trading_day"] and int(ctx["days_left"]) <= 2:
        out.append(op(E, "info", "rt.tail", "회전율 D+1 반영 가정 — 마지막 2일 매매는 요건에 산입되지 않는 것으로 계획됐다"))
    return out


_FUNCS = (_e01, _e02, _e03, _e04, _e05, _e06, _e07)


def evaluate(ctx: dict) -> list[dict]:
    """전문가 7명을 차례로. 한 명이 죽어도 나머지는 말한다."""
    out: list[dict] = []
    for fn, (eid, _label) in zip(_FUNCS, EXPERTS):
        try:
            out += fn(ctx)
        except Exception as exc:
            out.append(op(eid, "warn", f"{eid}.error", f"점검 실패 {type(exc).__name__}: {exc}"))
    return out
