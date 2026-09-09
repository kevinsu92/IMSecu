# -*- coding: utf-8 -*-
"""실행 게이트 자가 점검 (명세 13절 / G4)."""
import json, os, sys
os.environ["IMRL_QUIET"] = "1"          # 시험은 텔레그램을 보내지 않는다 (imrl/notify.py)
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from pathlib import Path
from imrl import decision as dec

fails, checks = [], 0
def ck(name, cond, detail=""):
    global checks
    checks += 1
    print(("  OK   " if cond else "  FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond: fails.append(name)

def gi(**kw):
    base = dict(mode="execute", reconciled=True, unresolved_orders=[],
                unconfirmed_rules=[], validation_ran=True,
                state_hash_now="h", state_hash_at_decision="h",
                action_uses_unconfirmed_rule=False)
    base.update(kw)
    return dec.GateInput(**base)

print("[1] 통과 조건")
ok, r = dec.execution_gate(gi())
ck("모든 조건 충족 -> 통과", ok and not r, str(r))

print()
print("[2] 각 조건이 실제로 막는가")
cases = [
    ("shadow 모드",        gi(mode="shadow"),                      "MODE_SHADOW"),
    ("advisory 모드",      gi(mode="advisory"),                    "MODE_ADVISORY"),
    ("계좌 미대조",         gi(reconciled=False),                   "ACCOUNT_NOT_RECONCILED"),
    ("접수 불명 주문",      gi(unresolved_orders=["BUY:A"]),        "UNRESOLVED_ORDERS"),
    ("미확인 규정 사용",    gi(action_uses_unconfirmed_rule=True),  "ACTION_USES_UNCONFIRMED_RULE"),
    ("확인용 비교 미실행",  gi(validation_ran=False),               "NO_VALIDATION_MANIFEST"),
    ("상태 해시 불일치",    gi(state_hash_now="x"),                 "STATE_HASH_MISMATCH"),
]
for name, g, want in cases:
    ok, r = dec.execution_gate(g)
    hit = any(x.startswith(want) for x in r)
    ck(f"{name} -> 차단", (not ok) and hit, str(r))

print()
print("[3] 차단 시 무엇으로 떨어지는가")
_, r1 = dec.execution_gate(gi(reconciled=False))
ck("계좌 미대조 -> 신규 주문 없음", dec.gate_fallback(r1) == "NO_NEW_ORDERS", dec.gate_fallback(r1))
_, r2 = dec.execution_gate(gi(unresolved_orders=["BUY:A"]))
ck("접수 불명 -> 신규 주문 없음", dec.gate_fallback(r2) == "NO_NEW_ORDERS", dec.gate_fallback(r2))
_, r3 = dec.execution_gate(gi(mode="shadow"))
ck("shadow -> 기준 정책", dec.gate_fallback(r3) == "BASELINE", dec.gate_fallback(r3))
_, r4 = dec.execution_gate(gi(validation_ran=False))
ck("확인 미실행 -> 기준 정책", dec.gate_fallback(r4) == "BASELINE", dec.gate_fallback(r4))

print()
print("[4] 플래그 하나로 우회되지 않는가")
multi = gi(mode="shadow", reconciled=False, validation_ran=False,
           state_hash_now="x", action_uses_unconfirmed_rule=True,
           unresolved_orders=["BUY:A"])
ok, r = dec.execution_gate(multi)
ck("여섯 조건 동시 위반이 전부 보고된다", (not ok) and len(r) == 6, f"{len(r)}건 {r}")

print()
print("[5] 현재 설정")
cfg = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
mode = cfg["decision_engine"]["mode"]
ck("mode 는 shadow/advisory/execute 중 하나", mode in ("shadow", "advisory", "execute"), mode)
src = Path("run.py").read_text(encoding="utf-8")
ck("plan 은 mode=execute 일 때만 엔진을 붙인다",
   'decision_engine", {}).get("mode") == "execute"' in src)
ck("게이트 차단 시 기존 targets 를 쓴다", "기준 정책으로 진행한다" in src)
ck("계좌 불명이면 주문 생성 자체를 멈춘다", "신규 주문을 만들지 않는다" in src)

print()
print("=" * 60)
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
