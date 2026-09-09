# -*- coding: utf-8 -*-
"""헬스체크 자가 점검.

헬스체크의 일은 **문제를 알리는 것**이다. 그러니 무엇이 터지든 트레이스백으로
죽으면 안 된다. 실제로 pywintypes.error 가 OSError 가 아니라서 UIPI 로 막힌
SetWindowPos 가 예외 절에 안 걸렸고, 문제를 알려야 할 함수가 아무 말 없이
죽었다.
"""
import io, sys, shutil
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from pathlib import Path

LOG = Path('state/session_log.jsonl')
bak = shutil.copy(LOG, LOG.with_suffix('.hb')) if LOG.exists() else None
fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)

import execute as E
from imrl import hts_exec

sent = []
E.notify.alert = lambda t, b: sent.append((t, b)) or True
real_adapter = hts_exec.OrderAdapter
real_pid = hts_exec.axis_pid
real_main = hts_exec.find_main
real_state = hts_exec.session_state

class _Boom:
    """connect() 에서 터지는 어댑터."""
    exc = None
    def __init__(self, **kw): pass
    def connect(self): raise type(self).exc

try:
    # 창을 실제로 만지지 않는다. HTS 가 켜져 있든 꺼져 있든 같은 결과여야 한다.
    hts_exec.axis_pid = lambda: 4321
    hts_exec.find_main = lambda pid: 1
    hts_exec.session_state = lambda h: ("ok", "모의투자")
    hts_exec.OrderAdapter = _Boom

    print("[1] 권한에 막히면 트레이스백이 아니라 알림")
    import pywintypes
    _Boom.exc = pywintypes.error(5, 'SetWindowPos', '액세스가 거부되었습니다.')
    sent.clear()
    rc = E.healthcheck(notify_on_ok=False)
    ck("종료코드 8", rc == 8, f"rc={rc}")
    ck("알림이 나갔다", len(sent) == 1)
    body = sent[0][1] if sent else ""
    ck("관리자 권한을 지목한다", "관리자" in body, body.splitlines()[0][:50] if body else "")

    print()
    print("[2] 모르는 예외도 삼키지 않고 알린다")
    _Boom.exc = RuntimeError("무언가 이상하다")
    sent.clear()
    rc = E.healthcheck(notify_on_ok=False)
    ck("종료코드 8", rc == 8, f"rc={rc}")
    ck("예외 종류를 남긴다", sent and "RuntimeError" in sent[0][1])

    print()
    print("[3] 이미 다루던 예외는 그대로")
    _Boom.exc = hts_exec.HtsError("주문창을 찾지 못했다")
    sent.clear()
    rc = E.healthcheck(notify_on_ok=False)
    ck("종료코드 8", rc == 8, f"rc={rc}")
    ck("메시지를 그대로 전달", sent and "주문창을 찾지 못했다" in sent[0][1])

    print()
    print("[4] HTS 가 없으면 그것을 보고한다")
    hts_exec.axis_pid = lambda: 0
    sent.clear()
    rc = E.healthcheck(notify_on_ok=False)
    ck("종료코드 8", rc == 8, f"rc={rc}")
    ck("axis.exe 를 지목한다", sent and "axis.exe" in sent[0][1])
finally:
    hts_exec.OrderAdapter = real_adapter
    hts_exec.axis_pid = real_pid
    hts_exec.find_main = real_main
    hts_exec.session_state = real_state
    if bak: shutil.move(bak, LOG)
    print()
    print("  상태 파일 복원됨")

print()
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
