# -*- coding: utf-8 -*-
"""세션 판정 자가 점검. 합성 기록으로 두 결론을 모두 낸다."""
import io, json, shutil, sys
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from pathlib import Path
from imrl import session as S

LOG = Path('state/session_log.jsonl')
MARK = Path('state/session_verdict.txt')
bak = [(p, shutil.copy(p, p.with_suffix(p.suffix + '.sv')) if p.exists() else None)
       for p in (LOG, MARK)]
fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)

def write(rows):
    LOG.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                   encoding='utf-8')

def rec(h, boot, logged=True):
    return {"at": f"2026-09-{8 + h // 24:02d}T{h % 24:02d}:40:00",
            "pid": 100, "logged_in": logged, "boot": boot}

try:
    print("[1] 기록 부족")
    MARK.unlink(missing_ok=True)
    write([rec(h, "B1") for h in range(8, 13)])
    v = S.verdict()
    ck("5건이면 판정 보류", not v.determined and v.kind == "unknown", v.text)

    print()
    print("[2] 재부팅할 때만 필요 (끊김 없음)")
    write([rec(h, "B1") for h in range(8, 15)] +
          [rec(24 + h, "B1") for h in range(8, 15)])
    v = S.verdict()
    ck("14건, 끊김 0 -> per_boot", v.determined and v.kind == "per_boot", v.text[:60])
    ck("최장 유지시간 계산", v.longest_hours > 20, f"{v.longest_hours:.1f}시간")

    print()
    print("[3] 매일 필요 (같은 부팅 안에서 끊김)")
    rows = [rec(h, "B1") for h in range(8, 15)]
    rows += [rec(24 + 8, "B1", logged=False)]          # 다음날 아침 끊김
    rows += [rec(24 + h, "B1") for h in range(9, 15)]
    write(rows)
    v = S.verdict()
    ck("같은 부팅 내 끊김 -> per_day", v.determined and v.kind == "per_day", v.text[:60])
    ck("끊김 횟수 1", v.drops == 1, f"{v.drops}회")

    print()
    print("[4] 재부팅은 끊김으로 세지 않는다")
    rows = [rec(h, "B1") for h in range(8, 15)]
    rows += [rec(24 + h, "B2") for h in range(8, 15)]   # 부팅이 바뀜
    write(rows)
    v = S.verdict()
    ck("부팅 경계는 끊김 아님", v.drops == 0 and v.kind == "per_boot", f"끊김 {v.drops}회")
    ck("부팅 2회로 센다", v.boots == 2, f"{v.boots}회")

    print()
    print("[5] 결론은 한 번만 알린다")
    MARK.unlink(missing_ok=True)
    ck("처음엔 알린다", S.mark_announced("per_boot"))
    ck("같은 결론은 다시 안 알린다", not S.mark_announced("per_boot"))
    ck("결론이 바뀌면 다시 알린다", S.mark_announced("per_day"))

    print()
    print("[6] 대회 전 기록은 세지 않는다")
    # 대회 전에는 사람이 HTS 를 껐다 켠다. 그 껐음이 "세션 끊김"으로 읽히면
    # 판정이 통째로 뒤집힌다.
    import json as _j
    cfg = _j.loads(Path('config/settings.json').read_text(encoding='utf-8'))
    start = cfg['contest']['start_date']
    pre = [{"at": "2026-09-06T12:00:00", "pid": 1, "logged_in": True, "boot": "B0"},
           {"at": "2026-09-06T13:00:00", "pid": 0, "logged_in": False, "boot": "B0"}]
    write(pre)
    v = S.verdict()
    ck("대회 전 기록만 있으면 0건", v.records == 0 and v.drops == 0, f"{v.records}건")
    write(pre + [rec(h, "B1") for h in range(8, 15)]
              + [rec(24 + h, "B1") for h in range(8, 15)])
    v = S.verdict()
    ck("대회 전 끊김이 판정을 뒤집지 않는다",
       v.determined and v.kind == "per_boot" and v.drops == 0, v.text[:50])
    ck("기록 수도 대회 기간만", v.records == 14, f"{v.records}건")

    print()
    print("[7] 상태 쓰기는 원자적이다")
    from imrl import state as _st
    tgt = Path('state/_atomic_probe.json')
    try:
        _st._write(tgt, {"a": 1, "b": [1, 2, 3]})
        ck("파일이 생긴다", tgt.exists())
        ck("임시 파일이 남지 않는다", not tgt.with_suffix('.json.tmp').exists())
        ck("내용이 온전하다", json.loads(tgt.read_text(encoding='utf-8')) == {"a": 1, "b": [1, 2, 3]})
        _st._write(tgt, {"a": 2})
        ck("덮어쓰기도 온전", json.loads(tgt.read_text(encoding='utf-8')) == {"a": 2})
    finally:
        tgt.unlink(missing_ok=True); tgt.with_suffix('.json.tmp').unlink(missing_ok=True)

    print()
    print("[8] 깨진 입력")
    LOG.write_text('{"at":"x"}\nnot json\n{"at":"2026-09-08T08:40:00","logged_in":true,"boot":"B1"}\n',
                   encoding='utf-8')
    v = S.verdict()
    ck("깨진 줄을 건너뛰고 동작", v.records == 2, f"{v.records}건")
    LOG.unlink(missing_ok=True)
    v = S.verdict()
    ck("파일 없어도 죽지 않는다", not v.determined and v.records == 0)
finally:
    LOG.unlink(missing_ok=True); MARK.unlink(missing_ok=True)
    for p, b in bak:
        if b: shutil.move(b, p)
    print()
    print("  상태 파일 복원됨")

print()
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
