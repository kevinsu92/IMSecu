# -*- coding: utf-8 -*-
"""장중 감시자. HTS 가 준비되면 밀린 단계를 실행한다.

왜 필요한가
  스케줄러는 **시각 기반**이다. 15:10 에 무조건 매도를 돌리고, 그때 HTS 가 로그인
  전이면 그 단계는 그냥 실패한다. 사람이 15:25 에 로그인해도 15:21 매수는 이미
  지나갔다. 하루가 통째로 날아가는데 원인은 "조금 늦게 로그인했다" 하나다.

  이 감시자는 **상태 기반**이다. 계속 돌면서 두 조건을 함께 본다.
    (1) 그 단계의 시각이 지났는가
    (2) HTS 가 실제로 주문 가능한 상태인가
  둘 다 참이고 아직 안 했으면 그때 실행한다. 로그인이 늦으면 늦은 만큼 늦게,
  그러나 마감 전이면 반드시 나간다.

중복 방지
  단계 완료 여부를 **주문 원장으로 판정한다.** 별도 마커 파일을 두면 그 파일과
  실제 전송이 어긋날 수 있다. 원장은 이미 execute.py 가 클릭 전에 쓰는 것이고,
  거기에 있는 주문은 다시 누르지 않는다. 스케줄러 작업과 감시자가 같은 단계를
  동시에 집어도 실제 전송은 한 번만 일어난다.

스케줄러를 대체하지 않는다
  기존 작업은 그대로 둔다. 감시자는 **빠진 것만 채운다.** 둘 다 도는 것이
  안전한 이유는 위의 중복 방지 때문이다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import date, datetime, time as dtime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

for st in (sys.stdout, sys.stderr):
    try:
        st.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PY = sys.executable

#: (단계, 시작시각, 마감시각, 명령, HTS 필요 여부)
#:
#: 마감은 그 단계가 의미를 잃는 시점이다. 매도는 15:21 매수 전에 대금이 들어와야
#: 하므로 15:19, 매수는 종가 동시호가가 끝나는 15:30 직전인 15:28 이다.
STEPS = [
    # 뉴스 기록·검토 → 위험 종목 제외 제안. 15:05 계획이 제안 파일을 읽으므로 그 전에.
    ("newswatch", dtime(14, 45), dtime(15, 4),  [PY, "-X", "utf8", "run.py", "newswatch"], False),
    ("plan",      dtime(15, 5),  dtime(15, 19), [PY, "-X", "utf8", "run.py", "plan"], False),
    ("sell",      dtime(15, 10), dtime(15, 19), [PY, "-X", "utf8", "execute.py", "--live", "--side", "SELL"], True),
    ("buy",       dtime(15, 21), dtime(15, 28), [PY, "-X", "utf8", "execute.py", "--live", "--side", "BUY"], True),
    ("reconcile", dtime(15, 40), dtime(16, 5),  [PY, "-X", "utf8", "run.py", "reconcile"], False),
    ("status",    dtime(16, 0),  dtime(16, 10), [PY, "-X", "utf8", "run.py", "status", "--push"], False),
]

START, END = dtime(8, 30), dtime(16, 15)
POLL_SEC = 30
RETRY_GAP_SEC = 90          # 같은 단계를 다시 시도하기까지 최소 간격


_ONCE: set[str] = set()


def log_once(key: str, msg: str) -> None:
    """같은 말을 30초마다 반복하면 로그가 읽을 수 없게 된다."""
    if key not in _ONCE:
        _ONCE.add(key)
        log(msg)


def log(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        p = ROOT / "state" / "watchdog.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


#: 화면 1200 자동 열기 재시도 간격. 실패를 초 단위로 반복하지 않는다.
_last_open_try = 0.0
OPEN_GAP_SEC = 60


#: HTS 실행 재시도 간격. 실패를 초 단위로 반복하지 않는다.
_last_launch = 0.0
LAUNCH_GAP_SEC = 120


def _launch_hts() -> bool:
    """HTS 를 띄운다. 로그인은 하지 않는다 — 창을 올리는 것까지가 전부다."""
    global _last_launch
    if time.time() - _last_launch < LAUNCH_GAP_SEC:
        return False
    _last_launch = time.time()
    try:
        cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        exe = cfg.get("hts", {}).get("exe_path", "")
    except Exception:
        exe = ""
    if not exe or not Path(exe).exists():
        log(f"HTS 실행 경로가 없다: {exe!r}")
        return False
    try:
        # 작업 스케줄러의 잡에서 떼어낸다. 안 그러면 감시자가 끝날 때 HTS 도 죽는다.
        DETACHED, NEW_GROUP, BREAKAWAY = 0x00000008, 0x00000200, 0x01000000
        try:
            subprocess.Popen([exe], cwd=str(Path(exe).parent.parent), close_fds=True,
                             creationflags=DETACHED | NEW_GROUP | BREAKAWAY)
        except OSError:
            subprocess.Popen([exe], cwd=str(Path(exe).parent.parent), close_fds=True,
                             creationflags=DETACHED | NEW_GROUP)
        log(f"HTS 를 실행했다: {exe}")
    except OSError as exc:
        log(f"HTS 실행 실패: {exc}")
        return False
    for _ in range(20):
        time.sleep(3)
        from imrl import hts_exec
        if hts_exec.axis_pid():
            return True
    return False


def hts_ready() -> tuple[bool, str]:
    """HTS 가 주문 가능한 상태인가. **아니면 열 수 있는 만큼 직접 연다.**

    프로세스만 보면 안 된다. 로그인 전에도 axis.exe 는 떠 있다.

    로그인은 사람만 할 수 있지만 **화면 1200 을 여는 것은 자동화할 수 있다.**
    사람에게 "로그인하고 화면도 열어라"라고 요구하면 그 중 하나를 빠뜨리는 날
    하루가 통째로 날아간다. 로그인만 하면 나머지는 여기서 한다.

    로그인 여부는 **MDIClient 자식을 가진 메인 창**으로 판정한다. 로그인
    대화상자는 단순 #32770 이라 MDIClient 가 없다. 그 구분이 없으면 로그인
    화면에 화면번호를 타이핑하게 되고, 그건 비밀번호 칸에 숫자를 넣는 것이다.
    """
    global _last_open_try
    try:
        from imrl import hts_exec
    except Exception as exc:
        return False, f"모듈 로드 실패: {exc}"
    pid = hts_exec.axis_pid()
    if not pid:
        # HTS 가 없으면 **직접 띄운다.**
        #
        # 재부팅 후가 이 경우다. 감시자는 로그온 트리거로 바로 뜨지만 HTS 는
        # 시작 프로그램에 없어 아무도 켜지 않는다. 헬스체크가 켜주긴 하는데
        # 매시간이라 14:05 에 재부팅하면 14:40 까지 로그인 창조차 안 뜬다.
        # 그 사이 15:05 계획이 지나가면 그날이 날아간다.
        #
        # 실행은 자격증명과 무관하므로 여기서 한다. 로그인 창까지 올려두면
        # 사람이 할 일은 비밀번호 하나로 줄고, 그 시점이 언제든 감시자가
        # 준비 상태를 잡아 밀린 단계를 실행한다.
        if _launch_hts():
            pid = hts_exec.axis_pid()
        if not pid:
            return False, "axis.exe 없음 — 실행을 시도했다"
        return False, "HTS 를 띄웠다 — 로그인 창에서 비밀번호 입력 필요"
    order = None
    try:
        order = hts_exec.find_order_window(pid)
    except Exception:
        order = None
    if order:
        # 주문창이 있어도 잠금·재로그인 대화상자가 떠 있으면 입력이 그쪽으로 새거나 삼켜진다.
        # 2026-09-08 09:30~14:50 화면잠금이 그 상태였다. 준비됨으로 읽지 않는다 — 상태 전환
        # 알림이 곧바로 사람에게 간다.
        try:
            main = hts_exec.find_main(pid)
            stt, title = hts_exec.session_state(main) if main else ("?", "")
            if stt == "locked":
                return False, f"HTS 화면잠금 ({title}) — 사람이 잠금을 풀어야 한다"
            pops = hts_exec.blocking_popups(pid, main, order)
            if pops:
                return False, "HTS 대화상자: " + ", ".join(pops[:2]) + " — 확인 또는 재로그인이 필요하다"
        except Exception as exc:
            log_once("popchk", f"팝업 검사 실패 (무시): {type(exc).__name__}: {exc}")
        return True, "준비됨"

    # 여기부터는 "프로세스는 있는데 주문 화면이 없다".
    main = hts_exec.find_main(pid)
    if not main:
        return False, "로그인 전 (메인 창 없음) — 사람이 로그인해야 한다"

    if time.time() - _last_open_try < OPEN_GAP_SEC:
        return False, "주문 화면(1200) 여는 중"
    _last_open_try = time.time()
    try:
        log("주문 화면(1200)이 없다 — 직접 연다")
        hts_exec.open_screen(main, "1200", wait=6.0)
        hts_exec.find_order_window(pid)
        log("주문 화면(1200) 열림")
        return True, "준비됨 (자동으로 화면 1200 을 열었다)"
    except Exception as exc:
        return False, f"화면 1200 자동 열기 실패: {exc}"


def _orders_today() -> list[dict]:
    p = ROOT / "state" / f"orders_{date.today():%Y%m%d}.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _expected_keys(o: dict, side: str) -> list[str]:
    """이 주문이 원장에 남길 조각 키. execute.py 의 분할 규칙과 같아야 한다."""
    try:
        cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        split_n = int(cfg.get("execution", {}).get("sell_split", 1))
    except Exception:
        split_n = 1
    code = str(o.get("code", ""))
    if side == "SELL" and split_n > 1:
        q = int(o.get("qty", 0) or 0)
        base_q, rem = divmod(q, split_n)
        keys = []
        for k in range(split_n):
            part = base_q + (1 if k < rem else 0)
            if part > 0:
                keys.append(f"{side}:{code}#{k + 1}/{split_n}")
        return keys or [f"{side}:{code}"]
    return [f"{side}:{code}"]


def _ledger_today() -> dict[str, str]:
    p = ROOT / "state" / f"submitted_{date.today():%Y%m%d}.json"
    if not p.exists():
        return {}
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, str] = {}
    for r in rows:
        out[str(r.get("key", ""))] = str(r.get("status", "submitted"))
    return out


def step_done(name: str) -> bool:
    """그 단계가 이미 끝났는가. **원장과 산출물로만 판정한다.**"""
    day = f"{date.today():%Y%m%d}"
    if name == "plan":
        return (ROOT / "state" / f"orders_{day}.json").exists()
    if name in ("sell", "buy"):
        side = name.upper()
        want = [o for o in _orders_today() if str(o.get("side", "")).upper() == side]
        if not want:
            return True            # 그 방향 주문이 없으면 할 일이 없다
        led = _ledger_today()
        # 조각 **전부**를 본다. 예전에는 종목·방향 키가 하나라도 있으면 완료로 봐서
        # 3분할 매도의 1/3 만 나간 채 완료가 됐다(외부 검토 재현). 안 보낸 조각이나
        # 확인된 미접수(not_accepted)가 남아 있으면 아직 할 일이 있다.
        # 접수 불명(unknown/pending_send/modal)은 다시 누르지 않으므로 완료로 친다.
        for o in want:
            for k in _expected_keys(o, side):
                st = led.get(k)
                if st is None or st == "not_accepted":
                    return False
        return True
    if name == "reconcile":
        return (ROOT / "state" / f"reconcile_{day}.done").exists()
    if name == "status":
        return (ROOT / "state" / f"status_{day}.done").exists()
    if name == "newswatch":
        return (ROOT / "state" / f"newswatch_{day}.done").exists()
    return False


def mark(name: str) -> None:
    if name in ("reconcile", "status", "newswatch"):
        (ROOT / "state" / f"{name}_{date.today():%Y%m%d}.done").touch()


def run_step(name: str, cmd: list[str]) -> int:
    log(f"실행: {name}  ({' '.join(cmd[3:])})")
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        log(f"  {name}: 제한시간 초과")
        return 124
    tail = [l for l in (r.stdout or "").splitlines() if l.strip()][-4:]
    for l in tail:
        log("  " + l.strip())
    if r.returncode:
        err = [l for l in (r.stderr or "").splitlines() if l.strip()][-2:]
        for l in err:
            log("  ! " + l.strip())
    log(f"  {name}: 종료코드 {r.returncode}")
    if r.returncode == 0:
        mark(name)
    return r.returncode


CONTROL_PORT = 8765
CONTROL_GAP_SEC = 120
_control_last_try = 0.0


def control_alive(port: int = CONTROL_PORT) -> bool:
    """제어 서버가 듣고 있나. 연결만 해 보고 끊는다."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def ensure_control() -> None:
    """제어 서버가 죽어 있으면 다시 띄운다.

    상황판의 손잡이(긴급정지·건너뛰기·익절·제안)는 이 서버가 있어야 눌린다.
    로그온 작업으로도 뜨지만, 실제로 한 번 밤사이에 죽어 있었고 스케줄러는
    이유를 말해 주지 않았다(0x8007042B, 운영 로그 꺼짐). 감시자는 장중 내내
    30초마다 돌고 스스로 재시작 보호를 받으니 여기서 한 번 더 지킨다 —
    손잡이가 필요한 시간이 정확히 감시자가 깨어 있는 시간이다.

    자식으로 붙이지 않는다. 감시자는 16:15 에 끝나지만 서버는 남아야 한다.
    HTS 를 띄울 때와 같은 플래그다.
    """
    global _control_last_try
    if control_alive():
        return
    time.sleep(1.0)                 # 순간적인 미응답에 서버를 하나 더 띄우지 않는다
    if control_alive():
        return
    if time.time() - _control_last_try < CONTROL_GAP_SEC:
        return
    _control_last_try = time.time()
    DETACHED, NEW_GROUP, BREAKAWAY = 0x00000008, 0x00000200, 0x01000000
    cmd = [PY, "-X", "utf8", str(ROOT / "control.py"), "--port", str(CONTROL_PORT)]
    try:
        try:
            subprocess.Popen(cmd, cwd=str(ROOT), close_fds=True,
                             creationflags=DETACHED | NEW_GROUP | BREAKAWAY)
        except OSError:
            subprocess.Popen(cmd, cwd=str(ROOT), close_fds=True,
                             creationflags=DETACHED | NEW_GROUP)
        log(f"제어 서버가 죽어 있어 다시 띄웠다 (127.0.0.1:{CONTROL_PORT})")
    except Exception as exc:
        log(f"제어 서버 재시작 실패: {type(exc).__name__}: {exc}")


RELAY_GAP_SEC = 3600
_relay_last = 0.0
RELAY_MARK = ROOT / "state" / "relay_last_pull.txt"


def relay_due(gap: int = RELAY_GAP_SEC) -> bool:
    """한 시간이 지났나. 감시자와 제어 서버가 **같은 파일**을 보므로 둘이 같은
    시간에 두 번 긁지 않는다."""
    try:
        last = float(RELAY_MARK.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        last = 0.0
    return time.time() - last >= gap


def relay_mark() -> None:
    try:
        RELAY_MARK.parent.mkdir(parents=True, exist_ok=True)
        RELAY_MARK.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def ensure_relay() -> None:
    """한 시간에 한 번 중계실을 긁어 최신 순위·분산을 기록한다.

    16:00 한 번으로는 하루 안에서 무슨 일이 있었는지 모른다. 순위표가 장중에도
    갱신된다면 σ_f 와 내 순위가 시간대별로 남고, 결정 엔진은 언제나 가장 최신
    한 장을 읽게 된다. 대회 전(정지 모드)에도 돈다 — 참가자수가 개막까지 는다.

    실패해도 조용히 넘긴다. 순위 수집은 매매에 필요한 것이 아니다.
    """
    global _relay_last
    if time.time() - _relay_last < 60 or not relay_due():
        return
    _relay_last = time.time()
    relay_mark()
    try:
        r = subprocess.run([PY, "-X", "utf8", "run.py", "relay", "--auto"], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        head = next((l for l in (r.stdout or "").splitlines() if "중계실 수집" in l or "참가" in l), "")
        log(f"중계실 시간별 수집: {head.strip()[:80] or ('rc=' + str(r.returncode))}")
    except Exception as exc:
        log(f"중계실 수집 실패: {type(exc).__name__}: {exc}")


CYCLE_GAP_SEC = 3600
_cycle_last = 0.0
CYCLE_MARK = ROOT / "state" / "cycle_last_run.txt"


def cycle_due(gap: int = CYCLE_GAP_SEC) -> bool:
    """시간별 사이클 차례인가. 중계실 마커와 같은 방식 — 감시자와 제어 서버가 같은 파일을 본다."""
    try:
        last = float(CYCLE_MARK.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        last = 0.0
    return time.time() - last >= gap


def cycle_mark() -> None:
    try:
        CYCLE_MARK.parent.mkdir(parents=True, exist_ok=True)
        CYCLE_MARK.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def cycle_blackout(now: datetime | None = None) -> bool:
    """14:30~15:35 — 정밀 회차(14:45)와 계획(15:05)의 시간. 시간별 회차는 쉰다."""
    try:
        from imrl.cycle import blackout
        return blackout(now)
    except Exception:
        return False


def ensure_cycle() -> None:
    """한 시간에 한 번 뉴스 → 전문가 점검 → 제외 제안 → 기록을 돌린다 (run.py cycle).

    14:45 한 번으로는 하루 안에서 무엇이 언제 나왔는지 모르고, 그 한 번이 실패하면
    15:05 계획은 아무 제안도 못 받는다. 시간별 회차는 후보 캐시로 뉴스만 긁으므로
    시세 출처를 두드리지 않는다. 실패해도 조용히 넘긴다 — 매매에 필요한 것이 아니다.
    """
    global _cycle_last
    if cycle_blackout():
        return
    if time.time() - _cycle_last < 60 or not cycle_due():
        return
    _cycle_last = time.time()
    cycle_mark()
    try:
        r = subprocess.run([PY, "-X", "utf8", "run.py", "cycle", "--source", "watchdog"], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        head = next((l for l in (r.stdout or "").splitlines() if "사이클" in l), "")
        log(f"시간별 사이클: {head.strip()[:90] or ('rc=' + str(r.returncode))}")
    except Exception as exc:
        log(f"사이클 실패: {type(exc).__name__}: {exc}")


KEEPALIVE_SEC = 300
_keep_last = 0.0


def keep_hts_awake() -> None:
    """HTS 유휴 잠금을 막는다 — 5분마다 이동량 0 의 마우스 이벤트로 시스템 유휴 시계를 되돌린다.

    2026-09-08 1일차: 08:40 정상이던 HTS 가 09:30 에 화면잠금이었다. 그 사이 윈도 잠금
    사건은 없었고(Winlogon 로그) 레지스트리 IdleTimeout=0 은 '기본값' 이었을 가능성이 크다.
    잠금은 비밀번호로만 풀리고 사람은 학교에 있다. 그래서 잠기지 않게 한다.

    이동량 0 의 mouse_event 는 커서를 움직이지 않고 어떤 창에도 입력을 보내지 않는다.
    GetLastInputInfo 만 갱신된다(실측). 사람이 쓰는 동안에도 아무 영향이 없다.
    """
    global _keep_last
    if time.time() - _keep_last < KEEPALIVE_SEC:
        return
    _keep_last = time.time()
    try:
        from ctypes import windll
        windll.user32.mouse_event(0x0001, 0, 0, 0, 0)     # MOUSEEVENTF_MOVE, dx = dy = 0
    except Exception as exc:
        log_once("keepalive", f"유휴 방지 입력 실패 (무시): {type(exc).__name__}: {exc}")


def rest_day(day: date) -> tuple[bool, str]:
    """오늘 단계를 실행하면 안 되는가. (안 됨, 이유) 를 돌려준다.

    감시자는 **매일** 트리거된다 (일별 + 로그온). 단계 작업들은 월~금으로
    걸려 있지만 감시자는 자기가 직접 단계를 실행하므로, 여기서 막지 않으면
    토·일에도 15:05 에 계획하고 주문을 낸다. 그리고 대회 기간의 휴장일
    09-24, 09-25, 10-05 는 전부 목·금·월이라 **요일만으로는 걸러지지 않는다.**

    달력을 읽지 못하면 막지 않는다. 못 읽었다는 이유로 장중에 손을 놓는 것이
    더 위험하다. 대신 그 사실을 로그에 남긴다.
    """
    try:
        cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        start_date = cfg.get("contest", {}).get("start_date", "")
    except Exception as exc:
        log(f"설정을 읽지 못했다 ({exc.__class__.__name__}) — 휴장일 판단 없이 진행한다")
        return False, ""
    if start_date and str(day) < start_date:
        return True, f"대회 시작 전이다 ({start_date})"
    try:
        from imrl.data import TradingCalendar
        if not TradingCalendar.from_config(cfg["contest"]).is_trading_day(day):
            return True, "오늘은 장이 열리지 않는다"
    except Exception as exc:
        log(f"달력을 읽지 못했다 ({exc.__class__.__name__}) — 휴장일 판단 없이 진행한다")
    return False, ""


# --------------------------------------------------------------------------- #
# 손 떼기 알림
# --------------------------------------------------------------------------- #

#: 집행 창 직전 알림 시각. 매도 15:10 두 분 전이면 하던 일을 멈추고 손을 뗄 시간이 된다.
HANDS_OFF_AT = dtime(15, 8)
#: 이 시각이 지나면 어떤 단계도 화면을 쓰지 않는다 (매수 마감 15:28 + 여유).
HANDS_BACK_AT = dtime(15, 30)


def _notice_path(key: str) -> Path:
    return ROOT / "state" / f"notice_{key}_{date.today():%Y%m%d}.done"


def notice_sent(key: str) -> bool:
    return _notice_path(key).exists()


def notice_mark(key: str) -> None:
    try:
        _notice_path(key).parent.mkdir(parents=True, exist_ok=True)
        _notice_path(key).touch()
    except OSError:
        pass


def _order_summary() -> tuple[int, int]:
    """오늘 주문서의 (매도 건수, 매수 건수)."""
    n_sell = n_buy = 0
    for o in _orders_today():
        side = str(o.get("side", "")).upper()
        if side == "SELL":
            n_sell += 1
        elif side == "BUY":
            n_buy += 1
    return n_sell, n_buy


def _piece_summary() -> dict[str, int]:
    """원장 기준 조각 집계: 접수·미접수·불명·미전송."""
    led = _ledger_today()
    out = {"submitted": 0, "not_accepted": 0, "unknown": 0, "unsent": 0}
    for o in _orders_today():
        side = str(o.get("side", "")).upper()
        if side not in ("SELL", "BUY"):
            continue
        for k in _expected_keys(o, side):
            st = led.get(k)
            if st is None:
                out["unsent"] += 1
            elif st == "submitted":
                out["submitted"] += 1
            elif st == "not_accepted":
                out["not_accepted"] += 1
            else:
                out["unknown"] += 1
    return out


def ensure_hands_notice(now: dtime, dry: bool = False, send=None) -> str | None:
    """집행 창 앞뒤로 텔레그램을 보낸다. 하루에 각각 한 번.

    왜 필요한가
      집행은 전역 마우스·키보드로 HTS 에 입력한다. 사람이 그 순간 자판을 치면
      숫자가 수량 칸에 섞이고, 전송 전 검사는 "다른 칸으로 갔다"만 잡지 "맞는 칸에
      틀린 숫자"는 못 잡는다. 컴퓨터로 다른 일을 하면서 대회를 치르려면 언제 손을
      떼고 언제 다시 잡아도 되는지 알려줘야 한다.

    시작 알림  15:08 — 매도 15:10 두 분 전. 오늘 주문 건수를 같이 적는다.
    끝 알림    매도·매수 단계가 모두 끝났을 때(더 이상 클릭이 없다), 늦어도 15:30.
              접수·미접수·불명·미전송 집계를 적는다. 재시도 작업은 원장에 남은 것이
              없으면 창을 건드리지 않으므로(execute.py 가 connect 전에 끝난다)
              그 뒤로는 화면을 써도 된다.

    휴장일·대회 전(dry)에는 보내지 않는다. 반환값은 보낸 알림의 이름(시험용).
    표식을 먼저 찍고 보낸다 — 전송이 실패해도 30초마다 다시 보내지 않는다.
    """
    if dry or now < HANDS_OFF_AT or now >= END:
        return None
    if send is None:
        from imrl import notify
        send = notify.send

    if not notice_sent("handsoff"):
        notice_mark("handsoff")
        n_sell, n_buy = _order_summary()
        kill = (ROOT / "state" / "KILL").exists() or Path("C:/imrl_state/KILL").exists()
        skip = (ROOT / "state" / f"SKIP_{date.today():%Y%m%d}").exists()
        lines = [
            f"⌨ 손 떼기 — {HANDS_OFF_AT:%H:%M}부터 {HANDS_BACK_AT:%H:%M}까지 HTS 주문 입력 시간.",
            "키보드·마우스를 만지지 말 것. PC 잠금·절전·항상 위 창 금지.",
            (f"오늘 주문서: 매도 {n_sell}건 · 매수 {n_buy}건" if step_done("plan")
             else "오늘 주문서: 아직 없음 (15:05 계획 대기)"),
        ]
        if kill:
            lines.append("긴급 정지(KILL) 상태 — 해제하지 않으면 주문은 나가지 않는다.")
        if skip:
            lines.append("오늘 건너뛰기 표식 — 매도·매수는 나가지 않는다.")
        lines.append("입력이 끝나면 다시 알린다.")
        try:
            send("\n".join(lines))
        except Exception as exc:
            log(f"손 떼기 알림 실패: {type(exc).__name__}: {exc}")
        return "handsoff"

    if not notice_sent("handsback"):
        sell0 = next(s[1] for s in STEPS if s[0] == "sell")
        finished = (now >= sell0 and step_done("plan")
                    and step_done("sell") and step_done("buy"))
        if not finished and now < HANDS_BACK_AT:
            return None
        notice_mark("handsback")
        p = _piece_summary()
        head = ("✅ 주문 입력 끝 — 컴퓨터를 써도 된다." if finished
                else f"⏱ 주문 입력 시간 종료({HANDS_BACK_AT:%H:%M}) — 컴퓨터를 써도 된다.")
        lines = [head]
        if step_done("plan"):
            lines.append(f"접수 {p['submitted']} · 미접수 {p['not_accepted']} · "
                         f"불명 {p['unknown']} · 미전송 {p['unsent']} (조각 기준)")
        else:
            lines.append("오늘 주문서가 없다 — 15:05 계획이 돌지 않았다.")
        lines.append("15:40 대조와 16:00 리포트는 화면을 쓰지 않는다.")
        try:
            send("\n".join(lines))
        except Exception as exc:
            log(f"입력 끝 알림 실패: {type(exc).__name__}: {exc}")
        return "handsback"
    return None


def main() -> int:
    log("=" * 56)
    log("감시자 시작 — HTS 준비 상태를 보고 밀린 단계를 실행한다")

    # 권한을 먼저 확인한다.
    #
    # HTS 가 관리자 권한으로 돌기 때문에 비승격에서는 창 조작이 UIPI 에 막힌다.
    # 자식 프로세스는 부모 권한을 그대로 상속하므로, 감시자가 비승격이면
    # 매도·매수가 전부 rc=2 로 실패한다. 감시자는 계속 도는데 주문만 안 나가는
    # 형태라 조용히 하루가 지나간다. 그래서 시작할 때 크게 알린다.
    try:
        import ctypes
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        admin = False
    if admin:
        log("관리자 권한 확인 — 매도·매수 실행 가능")
    else:
        log("!! 관리자 권한이 아니다. 매도·매수가 전부 실패한다.")
        log("!! scripts/start_watchdog.bat 으로 실행하거나 IMRL_Watchdog 작업을 쓸 것.")
        log("!! 계획·대조·리포트는 정상 동작한다.")

    # HTS 는 시각과 무관하게 지금 띄운다. 저녁에 재부팅하면 감시자는 곧 끝나지만,
    # 그 전에 HTS 를 올려 두면 사람이 할 일은 로그인 창에 비밀번호 하나뿐이다.
    # (승격된 이 프로세스가 띄우므로 UAC 창이 뜨지 않는다.)
    try:
        ok0, why0 = hts_ready()
        log(f"시작 시 HTS: {'준비됨' if ok0 else why0}")
    except Exception as exc:
        log(f"시작 시 HTS 확인 실패: {type(exc).__name__}: {exc}")

    dry, dry_why = rest_day(date.today())
    if dry:
        log(f"{dry_why}. 감시만 하고 실행하지 않는다.")

    last_try: dict[str, float] = {}
    last_ready = None

    while True:
        now = datetime.now().time()
        if now >= END:
            log("장 마감 시각을 지났다. 감시자를 종료한다.")
            return 0
        ensure_control()
        if now < START:
            time.sleep(POLL_SEC)
            continue
        ensure_relay()
        ensure_cycle()
        ensure_hands_notice(now, dry=dry)
        keep_hts_awake()

        if (ROOT / "state" / "KILL").exists() or Path("C:/imrl_state/KILL").exists():
            log("KILL 파일 감지 — 아무것도 실행하지 않는다.")
            time.sleep(POLL_SEC)
            continue
        if (ROOT / "state" / f"SKIP_{date.today():%Y%m%d}").exists():
            log_once("SKIP", f"오늘 건너뛰기 표식 — 매도·매수를 내지 않는다 (계획·대조·리포트는 진행).")
            skip_today = True
        else:
            skip_today = False

        ready, why = hts_ready()
        if ready != last_ready:
            log(f"HTS 상태 변화: {'준비됨' if ready else '미준비'} ({why})")
            # 2026-09-08 1일차: 09:30 부터 HTS 가 화면잠금이었는데 매시간 점검 알림이 텔레그램
            # 요청 과다에 밀려 사람이 제때 몰랐다. 상태가 바뀌는 **그 순간** 한 번 알린다.
            # (30초 폴링이라 최대 30초 지연. 같은 글은 notify 가 20분간 억제한다.)
            if last_ready is not None and not dry:
                try:
                    from imrl import notify
                    if ready:
                        notify.send(f"HTS 준비됨 — {why}. 밀린 단계가 있으면 지금 실행한다.")
                    else:
                        notify.alert("HTS 미준비 — 지금 확인할 것",
                                     f"{why}" + chr(10) + "잠금 해제 또는 로그인이 필요하다. 컴퓨터 앞이 아니면 "
                                     "휴대폰 Chrome 원격 데스크톱으로 이 PC 에 접속해 풀고 바로 끊을 것"
                                     "(15:08~15:30 은 접속 금지). 15:10 매도·15:21 매수는 HTS 가 준비된 순간 "
                                     "곧바로 나간다(창 안이면).")
                except Exception as exc:
                    log(f"HTS 상태 알림 실패: {type(exc).__name__}: {exc}")
            last_ready = ready

        for name, t0, t1, cmd, need_hts in STEPS:
            if not (t0 <= now < t1):
                continue
            if step_done(name):
                continue
            if need_hts and not ready:
                continue
            if need_hts and skip_today:
                continue
            if time.time() - last_try.get(name, 0) < RETRY_GAP_SEC:
                continue
            last_try[name] = time.time()
            if dry:
                log(f"[{dry_why}] {name} 을 실행할 조건이지만 건너뛴다")
                continue
            run_step(name, cmd)

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("사용자 중단")
        sys.exit(0)
