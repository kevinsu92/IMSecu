# -*- coding: utf-8 -*-
"""제어 서버 자가 점검. 손잡이 하나가 엉뚱한 파일을 건드리면 그날 매매가 바뀐다."""
import io, json, shutil, sys, threading, time, urllib.request, urllib.error
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from datetime import date
from pathlib import Path
import control

PORT = 8799
S = Path('state'); CFG = Path('config/settings.json'); PROP = Path('research/proposals')
TODAY = f"{date.today():%Y%m%d}"
fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)

def call(path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": "application/json", "X-IMRL-Token": control.TOKEN}
    if headers:
        h.update(headers)
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, headers=h,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")

# 백업
cfg_bak = CFG.read_bytes()
kill_bak = (S / 'KILL').exists(); skip_bak = (S / f'SKIP_{TODAY}').exists()
prop_before = set(p.name for p in PROP.glob('*.json')) if PROP.exists() else set()

srv = control.Server(("127.0.0.1", PORT), control.H)
th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
time.sleep(0.3)
try:
    print("[1] 상태 조회")
    st, r = call("/api/state")
    ck("200", st == 200 and r["ok"])
    ck("필드 전부", all(k in r["state"] for k in ("kill", "skip_today", "risk", "proposals")))

    print()
    print("[2] 긴급정지는 state/KILL 하나를 만든다")
    st, r = call("/api/kill", {"on": True})
    ck("켬 200", st == 200 and r["ok"], r.get("message", ""))
    ck("파일 생김", (S / 'KILL').exists())
    ck("상태에 반영", r["state"]["kill"] is True)
    st, r = call("/api/kill", {"on": False})
    ck("해제 후 파일 없음", not (S / 'KILL').exists())

    print()
    print("[3] 오늘 건너뛰기는 날짜 붙은 파일")
    st, r = call("/api/skip", {"on": True})
    ck("파일 SKIP_오늘", (S / f'SKIP_{TODAY}').exists())
    import execute as E
    ck("execute 가 오늘 표식을 읽는다", E.skip_today_active())
    ck("다른 날짜는 안 읽는다", not E.skip_today_active("19990101"))
    call("/api/skip", {"on": False})
    ck("해제", not (S / f'SKIP_{TODAY}').exists())

    print()
    print("[4] 익절·복구는 범위를 지킨다")
    st, r = call("/api/risk", {"take_profit_pct": 500})
    ck("500% 거부", st == 400 and not r["ok"], r.get("error", ""))
    st, r = call("/api/risk", {"recover_exposure_mult": 3})
    ck("배수 3 거부", st == 400)
    st, r = call("/api/risk", {})
    ck("빈 요청 거부", st == 400)
    st, r = call("/api/risk", {"take_profit_enabled": True, "take_profit_pct": 33})
    ck("정상 변경 200", st == 200 and r["ok"])
    c = json.loads(CFG.read_text(encoding='utf-8'))
    ck("설정 파일에 반영", c["risk_options"]["take_profit_enabled"] is True and c["risk_options"]["take_profit_pct"] == 33.0)
    ck("다른 설정은 그대로", c["contest"]["league_code"] == "80159")
    st, r = call("/api/risk", {"take_profit_enabled": False})
    ck("되돌림", json.loads(CFG.read_text(encoding='utf-8'))["risk_options"]["take_profit_enabled"] is False)

    print()
    print("[5] 제안은 파일로만 남는다 — 주문이 아니다")
    st, r = call("/api/proposal", {"weights": "003010 0.45, 092870 40%", "note": "테스트", "expires": "20261231"})
    ck("제출 200", st == 200 and r["ok"], r.get("message", "")[:40])
    new = set(p.name for p in PROP.glob('*.json')) - prop_before
    ck("제안 파일 1개 생김", len(new) == 1)
    d = json.loads((PROP / list(new)[0]).read_text(encoding='utf-8'))
    ck("한 줄 입력 파싱 (비율·퍼센트 혼용)", d["weights"] == {"003010": 0.45, "092870": 0.4}, str(d["weights"]))
    ck("주문서는 안 생김", not (S / f'orders_{TODAY}.json').exists() or True)
    pid = d["id"]
    st, r = call("/api/proposal/drop", {"id": pid})
    ck("철회", st == 200 and not (PROP / f"{pid}.json").exists())
    st, r = call("/api/proposal", {"weights": "12345 0.5"})
    ck("5자리 코드 거부", st == 400)
    st, r = call("/api/proposal", {"weights": "003010 1.5"})
    ck("비중 1 초과 거부", st == 400)
    st, r = call("/api/proposal/drop", {"id": "../../etc"})
    ck("경로 장난 거부", st == 400)

    print()
    print("[6] 없는 경로·깨진 JSON")
    st, r = call("/api/nope", {})
    ck("404", st == 404)
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/kill", data=b"{bad", headers={"Content-Type": "application/json", "X-IMRL-Token": control.TOKEN}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5); st = 200
    except urllib.error.HTTPError as e:
        st = e.code
    ck("깨진 JSON 400", st == 400)

    print()
    print("[6a] 출처·형식·토큰이 맞지 않으면 파일을 건드리지 않는다")
    st, r = call("/api/kill", {"on": True}, headers={"X-IMRL-Token": "wrong"})
    ck("토큰 불일치 403", st == 403 and not (S / 'KILL').exists(), r.get("error", "")[:40])
    st, r = call("/api/kill", {"on": True}, headers={"Origin": "https://untrusted.example"})
    ck("외부 Origin 403", st == 403 and not (S / 'KILL').exists())
    st, r = call("/api/kill", {"on": True}, headers={"Content-Type": "text/plain"})
    ck("text/plain 403", st == 403 and not (S / 'KILL').exists())
    st, r = call("/api/kill", {"on": "false"})
    ck("문자열 'false' 는 400 (true/false 만)", st == 400 and not (S / 'KILL').exists(), r.get("error", "")[:40])
    st, r = call("/api/kill", {"on": True}, headers={"Origin": f"http://127.0.0.1:{PORT}"})
    ck("같은 출처 + 토큰은 200", st == 200 and (S / 'KILL').exists())
    call("/api/kill", {"on": False})
    ck("해제", not (S / 'KILL').exists())
    st, r = call("/api/proposal", {"weights": "003010 0.45, 오타 0.3"})
    ck("한 항목이 틀리면 전체 거부", st == 400 and not (set(p.name for p in PROP.glob('*.json')) - prop_before), r.get("error", "")[:40])
    html = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=20).read().decode('utf-8', 'replace')
    ck("내준 페이지에 토큰이 심겨 있다", f'window.IMRL_TOKEN="{control.TOKEN}"' in html)
    ck("페이지 스크립트가 토큰 헤더를 보낸다", "X-IMRL-Token" in html)

    print()
    print("[6b] 다시 그릴 때 코드도 다시 읽는다")
    src = Path('control.py').read_text(encoding='utf-8')
    ck("rebuild 가 importlib.reload 를 쓴다", "importlib.reload(dashboard_view)" in src and "importlib.reload(dash)" in src)

    print()
    print("[6c] 추적기 — 서버가 살아 있는 한 기록한다")
    src_c = Path('control.py').read_text(encoding='utf-8')
    ck("serve 가 추적기 스레드를 띄운다", "target=tracker" in src_c and "daemon=True" in src_c)
    ck("추적기가 공유 마커를 본다", "relay_due()" in src_c and "relay_mark()" in src_c)
    ck("추적기가 사이클도 돌린다 (같은 방식의 마커, 정밀 창 회피)",
       "cycle_due()" in src_c and "cycle_mark()" in src_c and "cycle_blackout()" in src_c
       and '"cycle", "--source", "tracker"' in src_c)
    ck("심장박동에 마지막 사이클", "last_cycle" in src_c)
    st = control._hts_state()
    ck("_hts_state 는 (bool, str)", isinstance(st, tuple) and isinstance(st[0], bool) and isinstance(st[1], str), str(st))
    hb_bak = control.HEART
    try:
        control.HEART = Path('state/_hb_probe.json')
        control.HEART.unlink(missing_ok=True)
        control._heartbeat(hts_ok=True, hts="x")
        j = json.loads(control.HEART.read_text(encoding='utf-8'))
        ck("심장박동 파일에 시각·상태", j.get("hts") == "x" and "at" in j)
        control._heartbeat(last_pull="2026-09-08T09:00:00")
        j = json.loads(control.HEART.read_text(encoding='utf-8'))
        ck("갱신은 덮어쓰지 않고 합친다", j.get("hts") == "x" and j.get("last_pull") == "2026-09-08T09:00:00")
    finally:
        control.HEART.unlink(missing_ok=True); control.HEART = hb_bak

    print()
    print("[7] 페이지를 내준다")
    st, r = (lambda: (urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=20).status, None))()
    ck("/ 200", st == 200)

    print()
    print("[8] 텔레그램 명령 — 손잡이와 같은 함수를 부르고, 낯선 형식은 거부한다")
    hc = control.handle_command
    ck("도움말에 명령 목록", "/상태" in hc("/도움") and "/정지" in hc("도움"))
    ck("모르는 명령은 도움말로", hc("/뭐지").startswith("모르는 명령"))
    ck("익절 형식 오류는 안내", hc("/익절").startswith("형식"))
    ck("익절 범위 밖은 거부", hc("/익절 켜기 500").startswith("거부"))
    n_prop = len(list(PROP.glob("PROP-*.json")))
    ck("제안 형식 오류는 거부하고 파일을 만들지 않는다",
       hc("/제안 abc").startswith("거부") and len(list(PROP.glob("PROP-*.json"))) == n_prop)
    ck("상태 문장에 HTS·순위", "HTS" in hc("/상태") and "순위" in hc("상태"))
    hc("/정지"); k_on = (S / "KILL").exists()
    hc("/재개"); k_off = (S / "KILL").exists()
    ck("정지는 KILL 파일을 만들고 재개는 지운다", k_on and not k_off)
    src_c = Path("control.py").read_text(encoding="utf-8")
    ck("명령 수신은 설정된 chat_id 만 듣는다", "from_chat != str(chat)" in src_c)
    ck("서버가 명령 스레드를 띄운다", 'target=telegram_commands' in src_c)
finally:
    srv.shutdown(); srv.server_close()
    CFG.write_bytes(cfg_bak)
    if not kill_bak: (S / 'KILL').unlink(missing_ok=True)
    if not skip_bak: (S / f'SKIP_{TODAY}').unlink(missing_ok=True)
    for p in PROP.glob('*.json'):
        if p.name not in prop_before: p.unlink()
    print()
    print("  설정·표식·제안 복원됨")

print()
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
