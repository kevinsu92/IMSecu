# -*- coding: utf-8 -*-
"""한 시간에 한 번 도는 사이클 — 뉴스 수집 → 전문가 점검 → 제외 제안 → 기록.

왜 필요한가
  뉴스는 14:45 에 한 번만 봤다. 그 한 번이 실패하면 15:05 계획은 아무 제안도 못 받고,
  하루 사이에 무엇이 언제 나왔는지는 아무 데도 남지 않았다. 전문가는 문서였고
  아무도 다시 읽지 않았다. 이제 낮에는 감시자가, 밤과 재부팅 직후에는 제어 서버
  추적기가 같은 마커(state/cycle_last_run.txt)를 보고 한 시간에 한 번 이것을 돌린다.

두 종류의 회차
  정밀(full)   14:45 — 유니버스를 다시 만들고 점수를 다시 매겨 후보를 새로 뽑는다.
               시세 출처(KRX)를 두드리므로 하루 한 번만. 후보 목록을 캐시로 남긴다.
  시간별(cached) 그 후보 캐시 + 보유 종목으로 뉴스만 새로 긁는다(RSS, 종목당 1회).
               유니버스를 다시 만들지 않는다 — 시세 출처를 매시간 두드리다 15:05 직전에
               막히는 것이 가장 나쁜 실패다.

매매에 닿는 통로 (바뀌지 않았다)
  1. 경성 위험 종목의 제외 **제안** → 15:05 계획의 관문(imrl/proposals.py, decision.py)
     시간별 회차의 제안은 정밀 회차의 제안을 덮지 않는다. 정밀 결과가 없을 때의 대비다.
     14:30~15:35 에는 시간별 회차가 쉰다 — 그 창은 정밀 회차와 계획의 것이다.
  2. 중계실 σ_f → 결정 엔진의 필드 분산 (imrl/relay.py, 별도 마커로 매시간)
  전문가 의견은 기록·상황판·텔레그램이다. 주문이 아니다.

파일
  state/candidates.json          정밀 회차(또는 15:05 계획)가 남긴 후보 목록
  state/news/YYYYMMDD_HHMM.json  회차별 뉴스 스냅샷 (YYYYMMDD.json 은 그날 최신)
  state/experts/YYYYMMDD_HHMM.json 회차별 전문가 의견 (YYYYMMDD.json 은 그날 최신)
  state/experts/alerts_YYYYMMDD.json 오늘 이미 보낸 경보 키
  state/cycle_log.jsonl          회차 한 줄씩 — 상황판의 "사이클" 표
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from . import experts, news_watch

ROOT = Path(__file__).resolve().parent.parent
S = ROOT / "state"
CAND = S / "candidates.json"
EXP_DIR = S / "experts"
LOG = S / "cycle_log.jsonl"
BLACKOUT = (dtime(14, 30), dtime(15, 35))
GAP_SEC = 3600


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _rd(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def active(cfg: dict, today: date | None = None) -> tuple[bool, str]:
    """대회 마지막 날 +1일까지 돈다. 그 뒤는 긁을 것이 없다."""
    today = today or date.today()
    end = str(cfg.get("contest", {}).get("end_date", "") or "")
    if not end:
        return True, ""
    try:
        last = datetime.strptime(end, "%Y-%m-%d").date() + timedelta(days=1)
    except ValueError:
        return True, ""
    if today > last:
        return False, f"대회 종료 +1일({last}) 지남 — 사이클 중지"
    return True, ""


def blackout(now: datetime | None = None) -> bool:
    """정밀 회차와 15:05 계획의 시간. 시간별 회차는 쉰다."""
    t = (now or datetime.now()).time()
    return BLACKOUT[0] <= t < BLACKOUT[1]


# ------------------------------------------------------------------ 후보 캐시

def save_candidates(scored, top_n: int, phase: str, day: str | None = None) -> Path | None:
    """정밀 회차·15:05 계획이 뽑은 후보를 남긴다. 시간별 회차가 이것으로 뉴스를 본다."""
    if scored is None or getattr(scored, "empty", True):
        return None
    day = day or f"{date.today():%Y%m%d}"
    head = scored.head(int(top_n) * 3)
    codes = [str(c) for c in head["code"].tolist()]
    names = {str(r.code): str(r.name) for r in head.itertuples()}
    old = load_candidates()
    previous = None
    if old.get("codes"):
        previous = ({"date": old.get("date"), "codes": old.get("codes")}
                    if old.get("date") != day else old.get("previous"))
    _write(CAND, {"date": day, "at": datetime.now().isoformat(timespec="seconds"),
                  "phase": phase, "top_n": int(top_n), "codes": codes, "names": names,
                  "previous": previous})
    return CAND


def load_candidates() -> dict:
    d = _rd(CAND, {}) or {}
    return d if isinstance(d, dict) else {}


def watch_names(cand: dict) -> tuple[dict[str, str], list[str]]:
    """감시 대상: 후보 캐시 전체(top_n×3) + 보유 종목. (이름표, 순위 목록)"""
    names: dict[str, str] = {}
    ranked: list[str] = []
    if cand.get("codes"):
        ranked = [str(c) for c in cand["codes"]]
        nm = cand.get("names") or {}
        for c in ranked:
            names[c] = str(nm.get(c) or c)
    try:
        from . import state as _st
        for c, p in _st.load_positions().items():
            names.setdefault(str(c), str(p.get("name") or c))
    except Exception:
        pass
    return names, ranked


# ------------------------------------------------------------------ 경보 중복 방지

def _alert_sent(day: str, key: str) -> bool:
    """오늘 이미 **전달된** 경보인가."""
    return key in (_rd(EXP_DIR / f"alerts_{day}.json", []) or [])


def _mark_alert(day: str, key: str) -> None:
    """전달 성공 뒤에만 부른다. 보내기 전에 표시하면 실패한 경보가 그날 다시 안 나간다."""
    p = EXP_DIR / f"alerts_{day}.json"
    sent = _rd(p, []) or []
    if key not in sent:
        sent.append(key)
        try:
            _write(p, sent)
        except OSError:
            pass


# ------------------------------------------------------------------ 한 회차

def run(cfg: dict, source: str = "manual", now: datetime | None = None,
        full: dict | None = None) -> dict:
    """한 회차. `full` 이 있으면 정밀 회차의 결과(news_watch.run 반환값)를 그대로 쓴다."""
    now = now or datetime.now()
    today = now.date(); day = f"{today:%Y%m%d}"; stamp = f"{now:%H%M}"
    t0 = time.time()
    ok, why = active(cfg, today)
    if not ok:
        return {"skipped": why, "day": day}

    cand = load_candidates()
    names, ranked = watch_names(cand)
    top_n = int(cand.get("top_n") or cfg.get("portfolio", {}).get("phase_early", {}).get("top_n", 2))
    cap_w = float(cfg.get("requirements", {}).get("max_single_weight_rule", 0.5)) - 0.05

    if full is not None:
        mode = "full"
        snap = dict(full.get("snapshot") or {})
        props = [str(p) for p in (full.get("proposals") or [])]
        news_watch.save(day, snap, stamp=stamp)
    else:
        mode = "cached"
        snap = news_watch.collect(names, today=today) if names else {}
        news_watch.save(day, snap, stamp=stamp)
        props = ([] if blackout(now) else
                 [str(p) for p in news_watch.propose_exclusions(day, snap, ranked, top_n, cap_w,
                                                                scoring="cached")])

    ctx = experts.context(cfg, day, now, snap, cand)
    ops = experts.evaluate(ctx)
    rec = {"date": day, "at": now.isoformat(timespec="seconds"), "source": source,
           "mode": mode, "opinions": ops}
    _write(EXP_DIR / f"{day}.json", rec)
    _write(EXP_DIR / f"{day}_{stamp}.json", rec)

    sent: list[str] = []
    failed: list[str] = []
    for o in ops:
        if o["level"] != "alert" or _alert_sent(day, o["key"]):
            continue
        try:
            from . import notify
            ok_send = notify.alert(f"전문가 경보 — {o['expert']}", o["text"])
        except Exception:
            ok_send = False
        if ok_send:
            _mark_alert(day, o["key"])          # 전달 성공 뒤에만 중복 방지 표시
            sent.append(o["key"])
        else:
            failed.append(o["key"])             # 다음 회차에 다시 시도한다

    hard = {c: v.get("hard", []) for c, v in snap.items() if v.get("hard")}
    counts = {lv: sum(1 for o in ops if o["level"] == lv) for lv in experts.LEVELS}
    line = {"at": rec["at"], "source": source, "mode": mode, "names": len(names),
            "hard": sorted(hard), "soft": sum(1 for v in snap.values() if v.get("soft")),
            "proposals": [Path(p).name for p in props], "opinions": counts,
            "alerts_sent": sent, "alerts_failed": failed,
            "elapsed_s": round(time.time() - t0, 1)}
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return {**line, "day": day, "hard_detail": hard, "ops": ops}


# ------------------------------------------------------------------ 상황판용

def recent(limit: int = 24) -> list[dict]:
    """최근 회차, 최신이 앞."""
    if not LOG.exists():
        return []
    out = []
    for ln in LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    out.reverse()
    return out


def latest_opinions() -> dict:
    if not EXP_DIR.exists():
        return {}
    files = sorted(p for p in EXP_DIR.glob("*.json") if len(p.stem) == 8)
    return (_rd(files[-1], {}) or {}) if files else {}
