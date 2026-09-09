# -*- coding: utf-8 -*-
"""뉴스 기록·검토·제안 자가 점검.

여기서 못박는 것: 표식이 방향 판단이 아니라 매매제한 위험이라는 것, 제안이
규정 상한을 넘지 않는다는 것, 후보 밖 종목의 표식은 제안을 만들지 않는다는 것.
"""
import io, json, shutil, sys
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from pathlib import Path
from imrl import news_watch as NW, news

fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)

D, P = NW.DIR, NW.PROP
d_bak = D.with_name("news.bak"); p_bak = P.with_name("proposals.bak")
for src, dst in ((D, d_bak), (P, p_bak)):
    if dst.exists(): shutil.rmtree(dst)
    if src.exists(): shutil.copytree(src, dst)
real_head = news.headlines
try:
    print("[1] 표식 분류")
    _w = "Mon, 07 Sep 2026 01:00:00 GMT"
    hard, soft = NW.classify([{"title": "A사 투자경고 지정 예고… 급락", "when": _w}, {"title": "B 테마주 부각", "when": _w}])
    ck("경성: 투자경고", "투자경고" in hard)
    ck("연성: 급락·테마", set(soft) >= {"급락", "테마"})
    ck("연성이 경성으로 새지 않는다", "테마" not in hard and "급락" not in hard)
    ck("호재 기사는 표식 없음", NW.classify([{"title": "C사 수주 급증, 목표가 상향", "when": "Mon, 07 Sep 2026 01:00:00 GMT"}]) == ([], []))

    print()
    print("[1b] 오래된 기사는 표식이 아니다")
    from datetime import date as _d
    T = _d(2026, 9, 7)
    ck("6월 기사 → 78일 전", NW.age_days("Sat, 20 Jun 2026 09:00:00 +0900", T) == 79 or NW.age_days("Sat, 20 Jun 2026 09:00:00 +0900", T) == 78)
    ck("오늘 기사 → 0일", NW.age_days("Mon, 07 Sep 2026 06:00:00 +0900", T) == 0)
    ck("못 읽는 날짜 → None", NW.age_days("어제쯤", T) is None)
    old = [{"title": "X 결국 하루 거래정지", "when": "Sat, 20 Jun 2026 09:00:00 +0900"}]
    ck("78일 전 '거래정지' 는 경성 표식 아님", NW.classify(old, T) == ([], []))
    fresh = [{"title": "X 거래정지 예고", "when": "Fri, 04 Sep 2026 09:00:00 +0900"}]
    ck("3일 전 '거래정지' 는 경성 표식", "거래정지" in NW.classify(fresh, T)[0])
    nodate = [{"title": "X 관리종목 지정"}]
    ck("날짜 없는 기사는 표식에 안 쓴다", NW.classify(nodate, T) == ([], []))

    print()
    print("[2] 제외 제안 — 후보(top2) 안의 경성 표식만")
    if D.exists(): shutil.rmtree(D)
    for f in P.glob("PROP-NEWS-*.json"): f.unlink()
    snap = {"A": {"name": "가", "hard": ["거래정지"], "soft": []},
            "B": {"name": "나", "hard": [], "soft": []},
            "C": {"name": "다", "hard": [], "soft": ["테마"]},
            "D": {"name": "라", "hard": ["관리종목"], "soft": []}}
    ranked = ["A", "B", "C", "D", "E"]
    out = NW.propose_exclusions("20260999", snap, ranked, top_n=2, cap_w=0.45)
    ck("제안 1건", len(out) == 1)
    rec = json.loads(out[0].read_text(encoding="utf-8"))
    ck("A 제외, B·C 승격", set(rec["weights"]) == {"B", "C"}, str(rec["weights"]))
    ck("D(경성)는 승격 안 됨", "D" not in rec["weights"])
    ck("비중이 규정 상한 이하", all(w <= 0.45 + 1e-9 for w in rec["weights"].values()))
    ck("총노출 0.90", abs(sum(rec["weights"].values()) - 0.90) < 1e-6, f"{sum(rec['weights'].values()):.3f}")
    ck("오늘만 유효", rec["expires"] == "20260999")
    ck("제외 종목 기록", rec["excluded"] == ["A"])
    for f in P.glob("PROP-NEWS-*.json"): f.unlink()
    out2 = NW.propose_exclusions("20260999", snap, ["B", "C", "A", "D"], 2, 0.45)
    ck("후보 밖(3위) 표식은 제안 없음", out2 == [])
    clean = {k: {"name": v["name"], "hard": [], "soft": v["soft"]} for k, v in snap.items()}
    ck("표식 없으면 제안 없음", NW.propose_exclusions("20260999", clean, ranked, 2, 0.45) == [])

    print()
    print("[3] 기록과 이력")
    p1 = NW.save("20260901", {"A": {"name": "가", "headlines": [], "hard": [], "soft": []}})
    p2 = NW.save("20260902", {"A": {"name": "가", "headlines": [], "hard": ["거래정지"], "soft": []}})
    h = NW.history(7)
    ck("이틀치", [x["date"] for x in h] == ["20260901", "20260902"])
    ck("임시 파일 없음", not list(D.glob("*.tmp")))
    ck("표식이 이력에 남는다", h[1]["by_code"]["A"]["hard"] == ["거래정지"])

    print()
    print("[4] 오프라인이면 빈 기사, 예외 없음")
    news.headlines = lambda name, limit=5: []
    res = NW.run({"A": "가"}, ["A"], 1, 0.45, day="20260903")
    ck("기사 0건", res["snapshot"]["A"]["headlines"] == [])
    ck("표식 없음·제안 없음", res["hard"] == {} and res["proposals"] == [])
    news.headlines = lambda name, limit=5: [{"title": f"{name} 상장폐지 사유 발생", "when": "Mon, 07 Sep 2026 01:00:00 GMT", "url": "", "flags": []}]
    res = NW.run({"A": "가", "B": "나"}, ["A", "B", "C"], 2, 0.45, day="20260904")
    ck("두 후보 모두 경성이면 남는 후보 없음 → 제안 없음", res["proposals"] == [] and set(res["hard"]) == {"A", "B"})
finally:
    news.headlines = real_head
    for src, dst in ((D, d_bak), (P, p_bak)):
        if src.exists(): shutil.rmtree(src)
        if dst.exists(): shutil.move(dst, src)
    print()
    print("  뉴스 기록·제안 폴더 복원됨")

print()
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
