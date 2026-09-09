# -*- coding: utf-8 -*-
"""알림 경로 자가 점검. 실제 전송 1건 + 실패·재시도는 모의로."""
import sys, time
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from datetime import datetime
from imrl import notify

fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)

print("[1] 설정")
ck("텔레그램 설정됨", notify.is_configured())

print()
print("[2] 실제 전송")
t0 = time.time()
r = notify.send(f"[자가점검] 알림 경로 정상 · {datetime.now():%m-%d %H:%M:%S}")
ck("전송 성공", r, f"{time.time()-t0:.1f}초")

print()
print("[3] 실패 시 재시도")
calls = {"n": 0}
real = notify.requests.post
class FakeResp:
    status_code = 200
    text = "ok"
    def json(self): return {}
def flaky(*a, **k):
    calls["n"] += 1
    if calls["n"] < 3:
        raise TimeoutError("모의 네트워크 장애")
    return FakeResp()
notify.requests.post = flaky
t0 = time.time()
ok = notify._post_with_retry("t", "c", "x", False)
notify.requests.post = real
ck("두 번 실패 후 세 번째에 성공", ok and calls["n"] == 3, f"{calls['n']}회 시도")
ck("재시도 사이에 대기가 있다", time.time() - t0 >= 2.0, f"{time.time()-t0:.1f}초")

print()
print("[4] 계속 실패하면 포기하고 알린다")
calls["n"] = 0
def always_fail(*a, **k):
    calls["n"] += 1
    raise TimeoutError("모의 장애")
notify.requests.post = always_fail
ok = notify._post_with_retry("t", "c", "x", False)
notify.requests.post = real
ck("3회 시도 후 False", (not ok) and calls["n"] == 3, f"{calls['n']}회")

print()
print("[5] 429 요청 과다 처리")
calls["n"] = 0
class Resp429:
    status_code = 429
    text = "too many"
    def json(self): return {"parameters": {"retry_after": 1}}
seq = [Resp429(), FakeResp()]
def limited(*a, **k):
    calls["n"] += 1
    return seq[min(calls["n"] - 1, len(seq) - 1)]
notify.requests.post = limited
t0 = time.time()
ok = notify._post_with_retry("t", "c", "x", False)
notify.requests.post = real
ck("429 후 재시도해 성공", ok and calls["n"] == 2, f"{calls['n']}회, {time.time()-t0:.1f}초")

print()
print("[6] 비밀이 저장소에 들어가지 않는다")
# koreainvestment/kis-ai-extensions 는 앱키·토큰이 코드에 박히는 것을 훅으로 막는다.
# 우리는 토큰이 config/secrets.env 한 곳에만 있어야 하고, 추적 파일 어디에도
# 봇 토큰 형태(숫자:영숫자35자)가 없어야 한다. 원격이 생기는 날 새는 것을 미리 막는다.
import re as _re, subprocess as _sp
from pathlib import Path as _P
_ign = _sp.run(["git", "check-ignore", "-q", "config/secrets.env"]).returncode == 0
ck("config/secrets.env 는 git 무시 대상", _ign)
_files = _sp.run(["git", "ls-files"], capture_output=True, text=True, encoding="utf-8").stdout.split()
_pat = _re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")
_tok = notify.load_secrets().get("TELEGRAM_BOT_TOKEN", "")
_skip = {".png", ".jpg", ".jpeg", ".parquet", ".pyc", ".zip", ".gz", ".ico", ".woff", ".woff2", ".pdf"}
_hits = []
for _f in _files:
    _p = _P(_f)
    if _p.suffix.lower() in _skip or not _p.is_file():
        continue
    try:
        _txt = _p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if _pat.search(_txt) or (_tok and _tok in _txt):
        _hits.append(_f)
ck("추적 파일에 봇 토큰 형태의 문자열이 없다", not _hits, ", ".join(_hits[:5]))

print()
print("=" * 52)
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
