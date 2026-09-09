# -*- coding: utf-8 -*-
"""제안 채널 자가 점검.

제안은 후보이지 주문이 아니다. 여기서 못박는 것은 둘이다 — 틀린 제안이 규정을
넘는 후보로 둔갑하지 않는 것, 그리고 틀린 이유가 드러나는 것.
"""
import io, json, shutil, sys
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from datetime import date
from pathlib import Path
from imrl import proposals as P

fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)

D = P.DIR
bak = D.with_name("proposals.bak")
if bak.exists(): shutil.rmtree(bak)
if D.exists(): shutil.copytree(D, bak)
def reset():
    if D.exists(): shutil.rmtree(D)
    D.mkdir(parents=True)
def put(name, obj):
    (D / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

UNI = {"003010", "092870", "005930"}
CAP = 0.45
today = date(2026, 9, 8)
try:
    print("[1] 정상 제안은 실행 가능한 후보가 된다")
    reset(); put("a", {"id": "P1", "source": "06_risk", "weights": {"003010": 0.45, "092870": 0.4},
                       "note": "x", "expires": "20261001"})
    a = P.load_actions(UNI, CAP, today)
    ck("후보 1건", len(a) == 1)
    ck("action_id 에 PROP 접두", a[0].action_id == "PROP:P1")
    ck("실행 가능", a[0].feasible and not a[0].exclusion_reasons)
    ck("비중 보존", a[0].weights == {"003010": 0.45, "092870": 0.4})
    ck("출처가 note 에 남는다", a[0].note.startswith("[06_risk]"))

    print()
    print("[2] 규정을 넘는 제안은 후보로 둔갑하지 않는다")
    reset(); put("b", {"id": "P2", "weights": {"003010": 0.9}, "expires": "20261001"})
    a = P.load_actions(UNI, CAP, today)
    ck("상한 초과는 실행 불가", not a[0].feasible)
    ck("이유에 원금 50% 규정", any("원금 50%" in r for r in a[0].exclusion_reasons))
    ck("비중은 상한으로 잘린다 (기록용)", abs(a[0].weights["003010"] - CAP) < 1e-9)

    reset(); put("c", {"id": "P3", "weights": {"003010": 0.45, "092870": 0.45, "005930": 0.45},
                       "expires": "20261001"})
    a = P.load_actions(UNI, CAP, today)
    ck("총노출 초과는 실행 불가", not a[0].feasible and any("총노출" in r for r in a[0].exclusion_reasons))

    print()
    print("[3] 유니버스 밖 종목 = 매매제한·유동성 필터 밖")
    reset(); put("d", {"id": "P4", "weights": {"000000": 0.3, "003010": 0.3}, "expires": "20261001"})
    a = P.load_actions(UNI, CAP, today)
    ck("실행 불가", not a[0].feasible)
    ck("이유에 종목코드", any("000000" in r for r in a[0].exclusion_reasons))
    ck("정상 종목은 남는다", "003010" in a[0].weights and "000000" not in a[0].weights)

    print()
    print("[4] 만료·깨진 파일·빈 비중")
    reset()
    put("e", {"id": "OLD", "weights": {"003010": 0.4}, "expires": "20260901"})
    (D / "broken.json").write_text("{not json", encoding="utf-8")
    put("f", {"id": "EMPTY", "weights": {}, "expires": "20261001"})
    put("_ignored", {"id": "IGN", "weights": {"003010": 0.4}, "expires": "20261001"})
    a = P.load_actions(UNI, CAP, today)
    ids = [x.action_id for x in a]
    ck("만료는 건너뛴다", "PROP:OLD" not in ids)
    ck("깨진 JSON 은 조용히 무시", "PROP:broken" not in ids)
    ck("_ 로 시작하면 무시", "PROP:IGN" not in ids)
    ck("빈 비중은 실행 불가로 남긴다", "PROP:EMPTY" in ids and not [x for x in a if x.action_id == "PROP:EMPTY"][0].feasible)

    print()
    print("[5] 상황판 요약")
    reset(); put("g", {"id": "S1", "source": "human", "weights": {"003010": 0.4}, "expires": "20260901"})
    s = P.summary(today)
    ck("요약 1건", len(s) == 1 and s[0]["id"] == "S1")
    ck("만료 표시", s[0]["expired"] is True)

    print()
    print("[6] 제안 폴더가 없어도 죽지 않는다")
    if D.exists(): shutil.rmtree(D)
    ck("빈 목록", P.load_actions(UNI, CAP, today) == [])
    ck("요약도 빈 목록", P.summary(today) == [])
finally:
    if D.exists(): shutil.rmtree(D)
    if bak.exists(): shutil.move(bak, D)
    else: D.mkdir(parents=True, exist_ok=True)
    print()
    print("  제안 폴더 복원됨")

print()
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
