# -*- coding: utf-8 -*-
"""시간별 사이클 자가 점검 — 뉴스 → 전문가 점검 → 제외 제안 → 기록.

못박는 것
  시간별 회차는 유니버스를 다시 만들지 않는다(후보 캐시만 쓴다).
  시간별 회차의 제안은 정밀 회차의 제안을 덮지 않는다. 정밀 창(14:30~15:35)에는 제안을 내지 않는다.
  표식이 사라지면 자기가 쓴 제안은 지운다. 대회 종료 +1일 뒤에는 멈춘다.
  경보는 같은 건 하루 한 번. 전문가 7명이 항상 말하고, 한 명이 죽어도 나머지는 산다.
전부 임시 폴더에서 돈다 — 운영 기록(state/news, state/experts, research/proposals)을 건드리지 않는다.
"""
import json, shutil, sys, tempfile
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from datetime import date, datetime
from pathlib import Path
import pandas as pd
from imrl import cycle as CY, experts as EX, news_watch as NW, news, notify, state as ST

fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)

cfg = json.loads(Path('config/settings.json').read_text(encoding='utf-8'))
tmp = Path(tempfile.mkdtemp(prefix="imrl_cycle_"))
bak = (NW.DIR, NW.PROP, CY.CAND, CY.EXP_DIR, CY.LOG, EX.S, news.headlines, notify.alert, ST.load_positions, EX._FUNCS)
NW.DIR = tmp / "news"; NW.PROP = tmp / "proposals"
CY.CAND = tmp / "candidates.json"; CY.EXP_DIR = tmp / "experts"; CY.LOG = tmp / "cycle_log.jsonl"
EX.S = tmp / "state"; EX.S.mkdir(parents=True)
sent = []
notify.alert = lambda title, detail: sent.append((title, detail)) or True
ST.load_positions = lambda: {}
W = "Mon, 07 Sep 2026 01:00:00 GMT"
def fake_news(bad):
    def h(name, limit=5):
        if name in bad:
            return [{"title": f"{name} 거래정지 예고", "when": W, "url": "", "flags": ["거래정지"]}]
        return [{"title": f"{name} 실적 개선 기대", "when": W, "url": "", "flags": []}]
    return h

try:
    print("[1] 언제까지 도나")
    ck("대회 전에도 돈다", CY.active(cfg, date(2026, 9, 7))[0])
    ck("마지막 날 +1일(10/09)까지 돈다", CY.active(cfg, date(2026, 10, 9))[0])
    off = CY.active(cfg, date(2026, 10, 10))
    ck("10/10 부터는 멈춘다", not off[0] and "종료" in off[1], off[1])
    ck("정밀 창: 14:29 아님 / 14:30 / 15:34 / 15:35 아님",
       not CY.blackout(datetime(2026, 9, 8, 14, 29)) and CY.blackout(datetime(2026, 9, 8, 14, 30))
       and CY.blackout(datetime(2026, 9, 8, 15, 34)) and not CY.blackout(datetime(2026, 9, 8, 15, 35)))

    print()
    print("[2] 후보 캐시 — 정밀 회차가 남기고 시간별 회차가 쓴다")
    sc = pd.DataFrame({"code": ["A", "B", "C", "D"], "name": ["가", "나", "다", "라"], "score": [9, 8, 7, 6]})
    ck("빈 점수표는 안 남긴다", CY.save_candidates(None, 2, "phase_early") is None
       and CY.save_candidates(pd.DataFrame(), 2, "phase_early") is None)
    CY.save_candidates(sc, 2, "phase_early", day="20260906")
    CY.save_candidates(sc.iloc[[1, 0, 2, 3]], 2, "phase_early", day="20260907")
    c = CY.load_candidates()
    ck("후보 4종목 + 이름표", c["codes"] == ["B", "A", "C", "D"] and c["names"]["A"] == "가", str(c["codes"]))
    ck("전 회차가 남는다", c["previous"]["date"] == "20260906" and c["previous"]["codes"][:2] == ["A", "B"])
    names, ranked = CY.watch_names(c)
    ck("감시 대상 = 후보 전체 (+보유)", set(names) == {"A", "B", "C", "D"} and ranked == c["codes"])

    print()
    print("[3] 시간별 회차 — 뉴스만 긁고, 제안은 정밀 창 밖에서만")
    news.headlines = fake_news({"나"})           # B(1위 후보)에 경성 표식
    out = CY.run(cfg, source="test", now=datetime(2026, 9, 7, 10, 0))
    ck("후보 4종목을 봤다", out["names"] == 4 and out["mode"] == "cached", str(out.get("names")))
    ck("경성 표식 B", out["hard"] == ["B"], str(out["hard"]))
    p = NW.PROP / "PROP-NEWS-20260907.json"
    ck("제외 제안 생성 (cached 표시)", p.exists() and json.loads(p.read_text(encoding="utf-8")).get("scoring") == "cached")
    ck("회차별 뉴스 사본 + 그날 최신", (NW.DIR / "20260907_1000.json").exists() and (NW.DIR / "20260907.json").exists())
    ck("회차별 전문가 의견 + 그날 최신", (CY.EXP_DIR / "20260907_1000.json").exists() and (CY.EXP_DIR / "20260907.json").exists())
    ck("사이클 로그 한 줄", CY.LOG.exists() and len(CY.LOG.read_text(encoding="utf-8").splitlines()) == 1)
    ck("정밀 회차만 있는 history 에 회차 사본이 섞이지 않는다", [h["date"] for h in NW.history(7)] == ["20260907"])
    it = NW.intraday("20260907")
    ck("오늘 회차 요약", len(it) == 1 and it[0]["time"] == "1000" and it[0]["hard"] == ["B"], str(it))
    p.unlink()
    out2 = CY.run(cfg, source="test", now=datetime(2026, 9, 7, 14, 45))
    ck("정밀 창에서는 제안을 내지 않는다", out2["proposals"] == [] and not p.exists())

    print()
    print("[4] 제안 우선순위 — 시간별은 정밀을 덮지 않고, 정밀은 낡은 것을 지운다")
    snap_full = {"B": {"name": "나", "hard": ["관리종목"], "soft": []}, "A": {"name": "가", "hard": [], "soft": []},
                 "C": {"name": "다", "hard": [], "soft": []}}
    NW.propose_exclusions("20260907", snap_full, ["B", "A", "C"], 2, 0.45, scoring="full")
    ck("정밀 제안 생성", p.exists() and json.loads(p.read_text(encoding="utf-8"))["scoring"] == "full")
    CY.run(cfg, source="test", now=datetime(2026, 9, 7, 11, 0))
    ck("시간별 회차가 정밀 제안을 덮지 않는다", json.loads(p.read_text(encoding="utf-8"))["scoring"] == "full"
       and json.loads(p.read_text(encoding="utf-8"))["excluded"] == ["B"])
    clean = {k: {"name": v["name"], "hard": [], "soft": []} for k, v in snap_full.items()}
    ck("정밀 회차에 표식이 없으면 자기 제안을 지운다",
       NW.propose_exclusions("20260907", clean, ["B", "A", "C"], 2, 0.45, scoring="full") == [] and not p.exists())
    NW.propose_exclusions("20260907", snap_full, ["B", "A", "C"], 2, 0.45, scoring="cached")
    ck("시간별 제안은 표식이 사라지면 시간별 회차가 지운다",
       p.exists() and NW.propose_exclusions("20260907", clean, ["B", "A", "C"], 2, 0.45, scoring="cached") == []
       and not p.exists())

    print()
    print("[5] 전문가 7명 — 항상 말하고, 한 명이 죽어도 나머지는 산다")
    ops = out["ops"]
    ids = {o["expert"] for o in ops}
    ck("7명 전원 의견", ids == {e for e, _ in EX.EXPERTS}, str(sorted(ids)))
    ck("등급은 info/warn/alert 뿐", all(o["level"] in EX.LEVELS for o in ops))
    ck("의견마다 키·문장", all(o.get("key") and o.get("text") for o in ops))
    ck("검증 상태 경고(04_quant)가 항상 있다", any(o["key"] == "quant.validation" and o["level"] == "warn" for o in ops))
    def boom(ctx): raise RuntimeError("죽음")
    EX._FUNCS = (boom,) + EX._FUNCS[1:]
    ctx = EX.context(cfg, "20260907", datetime(2026, 9, 7, 10, 0), {}, {})
    ops2 = EX.evaluate(ctx)
    ck("한 명이 죽으면 그 자리에 실패 의견, 나머지 6명은 정상",
       any(o["key"].endswith(".error") and "죽음" in o["text"] for o in ops2)
       and {o["expert"] for o in ops2} == {e for e, _ in EX.EXPERTS})
    EX._FUNCS = bak[9]

    print()
    print("[6] 경보 — 보유 종목 경성 위험은 텔레그램, 같은 건 하루 한 번")
    ST.load_positions = lambda: {"X": {"name": "엑스", "qty": 10, "avg_price": 1000, "entry_date": "2026-09-04"}}
    (EX.S / "positions.json").write_text(json.dumps({"X": {"name": "엑스", "qty": 10, "avg_price": 1000, "entry_date": "2026-09-04"}}), encoding="utf-8")
    news.headlines = fake_news({"엑스"})
    sent.clear()
    r1 = CY.run(cfg, source="test", now=datetime(2026, 9, 8, 9, 0))
    ck("보유 경성 위험 → 경보 1건 전송", r1["alerts_sent"] == ["rt.hold.X"] and len(sent) == 1 and "엑스" in sent[0][1], str(r1["alerts_sent"]))
    ck("경보 문구에 '자동 매도 없음'", "자동 매도 없음" in sent[0][1])
    r2 = CY.run(cfg, source="test", now=datetime(2026, 9, 8, 10, 0))
    ck("같은 날 같은 건은 다시 안 보낸다", r2["alerts_sent"] == [] and len(sent) == 1)
    ck("의견에는 매번 남는다", any(o["key"] == "rt.hold.X" and o["level"] == "alert" for o in r2["ops"]))
    ST.load_positions = lambda: {}
    (EX.S / "positions.json").unlink()

    print()
    print("[7] 시각·상태에 따른 점검 (02_execution / 04_quant / 03_swing / 06_risk)")
    (EX.S / "control_heartbeat.json").write_text(json.dumps({"hts_ok": False, "hts": "HTS 꺼짐", "at": "2026-09-08T14:00:00"}), encoding="utf-8")
    ctx = EX.context(cfg, "20260908", datetime(2026, 9, 8, 14, 0), {}, {})
    ck("거래일로 본다 (09/08)", ctx["trading_day"] and ctx["elapsed"] == 0, f"left={ctx['days_left']}")
    o = {x["key"]: x for x in EX.evaluate(ctx)}
    ck("14:00 HTS 미준비 → 경보", o.get("exec.hts", {}).get("level") == "alert")
    ck("1일차 관측 안내", "exec.day1" in o)
    ctx = EX.context(cfg, "20260908", datetime(2026, 9, 8, 15, 30), {}, {})
    o = {x["key"]: x for x in EX.evaluate(ctx)}
    ck("15:30 주문서 없음 → 경보", o.get("exec.noplan", {}).get("level") == "alert")
    ctx = EX.context(cfg, "20260906", datetime(2026, 9, 6, 12, 0), {}, {})
    o = {x["key"]: x for x in EX.evaluate(ctx)}
    ck("일요일은 휴장 안내만", "exec.rest" in o and "exec.hts" not in o and "exec.noplan" not in o)
    ctx = EX.context(cfg, "20260922", datetime(2026, 9, 22, 12, 0), {}, {})
    o = {x["key"]: x for x in EX.evaluate(ctx)}
    ck("추석 갭: 09/23 종가 후 4일 휴장 → 09/28, D-1 경고",
       o.get("swing.gap", {}).get("level") == "warn" and "09/23" in o["swing.gap"]["text"] and "09/28" in o["swing.gap"]["text"], o.get("swing.gap", {}).get("text", ""))
    for ret, lvl, frag in ((-0.05, "info", "종가 체결"), (-0.40, "warn", "0.005"), (-0.90, "alert", "0.003")):
        (EX.S / "leaderboard.json").write_text(json.dumps({"updated": "2026-09-08T16:10:00", "my_return_pct": ret}), encoding="utf-8")
        ctx = EX.context(cfg, "20260909", datetime(2026, 9, 9, 9, 0), {}, {})
        o = {x["key"]: x for x in EX.evaluate(ctx)}
        ck(f"1일차 체결가 판별 {ret:+.2f}% → {lvl}", o.get("quant.day1", {}).get("level") == lvl and frag in o.get("quant.day1", {}).get("text", ""), o.get("quant.day1", {}).get("text", "")[:60])
    rows = [{"rank": i + 1, "ret_pct": round(12.0 - i * 0.4, 2), "turnover_pct": 100.0} for i in range(30)]
    (EX.S / "relay_20260909.json").write_text(json.dumps({"at": "20260909", "summary": {"date": "20260909", "n_field": 112, "n_traded": 80, "max_pct": 12.0, "avg_pct": 3.0, "min_pct": -5.0}, "rows": rows, "me_index": 2}), encoding="utf-8")
    ctx = EX.context(cfg, "20260909", datetime(2026, 9, 9, 9, 0), {}, {})
    o = {x["key"]: x for x in EX.evaluate(ctx)}
    ck("순위·갭·25위 컷", "3위" in o["risk.rank"]["text"] and "25위 컷" in o["risk.rank"]["text"] and "갭 +0.80" in o["risk.rank"]["text"], o["risk.rank"]["text"])
    ck("필드 요약(05_regime)", "매매 80명" in o["regime.field"]["text"])
    ck("순위·갭 정보는 문서가 아니라 중계실에서 온다", "12.00%" in o["risk.rank"]["text"])

    print()
    print("[8] 종료 뒤·상황판 재료")
    r = CY.run(cfg, source="test", now=datetime(2026, 10, 10, 9, 0))
    ck("10/10 회차는 건너뛴다", r.get("skipped", "").startswith("대회 종료"), str(r))
    rec = CY.recent(5)
    ck("최근 회차는 최신이 앞", rec and rec[0]["at"] >= rec[-1]["at"] and all("opinions" in x for x in rec))
    lo = CY.latest_opinions()
    ck("최신 의견 묶음", lo.get("date") and isinstance(lo.get("opinions"), list))
finally:
    NW.DIR, NW.PROP, CY.CAND, CY.EXP_DIR, CY.LOG, EX.S, news.headlines, notify.alert, ST.load_positions, EX._FUNCS = bak
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("  임시 폴더 제거, 운영 기록 무손상")

print()
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
