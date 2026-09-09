# -*- coding: utf-8 -*-
"""체결 기록의 신원 — 분할 조각은 각각 세고, 같은 것을 두 번 넣으면 한 번만 센다.

외부 검토(CONTEST_WINNING_DASHBOARD_REVIEW_2026-09-07.md P0-04)가 재현한 결함:
분할 매도 세 조각이 같은 날·종목·수량·지정가라 하나로 접혀 보유의 2/3 가 팔리지
않은 것으로 남았다. 조각 키가 신원에 들어가면 셋이 따로 선다. 사람이 넣는 체결
(키 없음)의 중복 보호는 그대로다. 전부 임시 폴더에서 돈다.
"""
import json, shutil, sys, tempfile
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from pathlib import Path
from imrl import state as ST

fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)

tmp = Path(tempfile.mkdtemp(prefix="imrl_ledger_"))
bak = (ST.TRADES_FILE, ST.POSITIONS_FILE)
ST.TRADES_FILE = tmp / "trades.json"; ST.POSITIONS_FILE = tmp / "positions.json"
D = "2026-09-09"
def piece(i, side="SELL", qty=30):
    return {"date": D, "side": side, "code": "A", "name": "가", "market": "KOSPI", "qty": qty,
            "price": 1000, "assumed": True, "key": f"{side}:A#{i}"}
try:
    print("[1] 분할 조각 셋은 셋이다")
    a, d = ST.record_trades([piece(1), piece(2), piece(3)])
    ck("값이 같아도 키가 다르면 각각 기록", len(a) == 3 and d == 0, f"added={len(a)} dup={d}")
    a, d = ST.record_trades([piece(1), piece(2), piece(3)])
    ck("같은 조각을 다시 넣으면 전부 중복", len(a) == 0 and d == 3)
    ck("신원에 조각 키가 들어간다", ST.trade_id(piece(2)).endswith("|SELL:A#2"))

    print()
    print("[2] 사람이 넣는 체결(키 없음)은 예전 규칙 — 값이 같으면 중복")
    ST.TRADES_FILE.unlink()
    m = {"date": D, "side": "BUY", "code": "B", "qty": 10, "price": 500}
    a, d = ST.record_trades([m]); a2, d2 = ST.record_trades([dict(m)])
    ck("두 번 넣으면 한 번", len(a) == 1 and len(a2) == 0 and d2 == 1)
    ck("키 없는 신원은 예전 형식 그대로", ST.trade_id(m) == f"{D}|BUY|B|10|500")

    print()
    print("[3] 보유 반영 — 90주 매수 뒤 30주 x 3조각 매도면 보유 0")
    ST.TRADES_FILE.unlink(missing_ok=True); ST.POSITIONS_FILE.unlink(missing_ok=True)
    b, _ = ST.record_trades([{"date": "2026-09-08", "side": "BUY", "code": "A", "name": "가", "market": "KOSPI",
                              "qty": 90, "price": 1000, "assumed": True, "key": "BUY:A"}])
    pos = ST.apply_fills_to_positions(b)
    ck("매수 90주", pos.get("A", {}).get("qty") == 90)
    s, _ = ST.record_trades([piece(1), piece(2), piece(3)])
    pos = ST.apply_fills_to_positions(s)
    ck("세 조각 매도 뒤 보유 없음 (예전에는 60주 유령 보유)", "A" not in pos, str(pos))

    print()
    print("[4] 재시도 회차 — 1조각 먼저, 나머지 두 조각은 다음 실행에서")
    ST.TRADES_FILE.unlink(missing_ok=True); ST.POSITIONS_FILE.unlink(missing_ok=True)
    b, _ = ST.record_trades([{"date": "2026-09-08", "side": "BUY", "code": "A", "name": "가", "market": "KOSPI",
                              "qty": 90, "price": 1000, "assumed": True, "key": "BUY:A"}])
    ST.apply_fills_to_positions(b)
    s1, _ = ST.record_trades([piece(1)]); ST.apply_fills_to_positions(s1)
    s2, d2 = ST.record_trades([piece(2), piece(3)]); pos = ST.apply_fills_to_positions(s2)
    ck("두 번째 실행이 남은 두 조각을 기록", len(s2) == 2 and d2 == 0)
    ck("최종 보유 0", "A" not in pos)

    print()
    print("[5] execute.py 가 조각 키를 붙여 넘긴다")
    src = Path('execute.py').read_text(encoding='utf-8')
    ck("추정 체결에 order_key(o)", '"key": order_key(o)' in src and "placed.append((res, o))" in src)
finally:
    ST.TRADES_FILE, ST.POSITIONS_FILE = bak
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("  임시 폴더 제거, 운영 기록 무손상")

print()
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
