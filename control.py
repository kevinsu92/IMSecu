# -*- coding: utf-8 -*-
"""상황판에 손잡이를 단다.

상황판(state/dashboard.html)은 파일이라 볼 수만 있고 바꿀 수는 없었다. 무인
운영에서 사람에게 남은 권한이 "지켜보기"뿐이면 그건 통제가 아니다. 여기서 같은
화면에 네 개의 손잡이를 단다. 전부 **프로그램이 이미 읽는 파일**에 쓰는 것이고,
새 경로는 하나도 만들지 않는다.

  긴급정지        state/KILL                       execute.py 가 rc=7, 감시자가 정지
  오늘 건너뛰기   state/SKIP_<YYYYMMDD>            자정이 지나면 저절로 무효
  익절·손실복구   config/settings.json risk_options 다음 15:05 계획부터 적용
  제안            research/proposals/*.json         다음 계획의 후보 — 관문을 통과해야 실행

127.0.0.1 에만 묶는다. 이 PC 안에서만 닿고, 밖에서는 포트 자체가 보이지 않는다.
인증을 따로 두지 않는 이유가 그것이다 — 이 PC 에 앉은 사람은 어차피 파일을 직접
고칠 수 있다.

    python control.py            127.0.0.1:8765
    python control.py --port N
"""

from __future__ import annotations

import json
import re
import secrets
import sys
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
S = ROOT / "state"
CFG = ROOT / "config" / "settings.json"
PROP = ROOT / "research" / "proposals"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
for st in (sys.stdout, sys.stderr):
    try:
        st.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_lock = threading.Lock()
_log = S / "control.log"

#: 이 프로세스가 내준 페이지만 손잡이를 누를 수 있다. 서버가 뜰 때 만든 토큰을 페이지에
#: 심고, 바꾸는 요청은 그 토큰을 헤더로 돌려줘야 한다. 127.0.0.1 바인딩만으로는 이 PC 의
#: 브라우저에 열린 **다른 사이트**가 요청을 보내는 것을 막지 못한다(외부 검토 재현).
TOKEN = secrets.token_hex(16)
#: execute.py 가 함께 보는 로컬 정지 파일. 상황판이 한쪽만 보면 "해제"가 거짓말이 된다.
KILL_LOCAL = Path("C:/imrl_state/KILL")
MAX_BODY = 64 * 1024


def _bool(v, name: str = "on") -> bool:
    """JSON true/false 만 받는다. "false" 문자열이 참이 되는 일이 없어야 한다."""
    if isinstance(v, bool):
        return v
    raise ValueError(f"{name} 은 true/false 여야 한다")


def log(msg: str) -> None:
    line = f"[{datetime.now():%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        S.mkdir(parents=True, exist_ok=True)
        with _log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 손잡이 — 각각 기존 파일 하나를 건드린다
# --------------------------------------------------------------------------- #

def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def state_now() -> dict:
    today = f"{date.today():%Y%m%d}"
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    ro = cfg.get("risk_options", {})
    props = []
    if PROP.exists():
        for p in sorted(PROP.glob("*.json")):
            if p.name.startswith("_"):
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                props.append({"id": d.get("id", p.stem), "expires": d.get("expires", ""),
                              "weights": d.get("weights", {}), "note": d.get("note", "")})
            except (OSError, ValueError):
                continue
    return {
        "kill": (S / "KILL").exists() or KILL_LOCAL.exists(),
        "kill_paths": [str(p) for p in (S / "KILL", KILL_LOCAL) if p.exists()],
        "skip_today": (S / f"SKIP_{today}").exists(),
        "today": today,
        "risk": {
            "take_profit_enabled": bool(ro.get("take_profit_enabled", False)),
            "take_profit_pct": float(ro.get("take_profit_pct", 20.0)),
            "recover_enabled": bool(ro.get("recover_enabled", True)),
            "recover_below_pct": float(ro.get("recover_below_pct", -20.0)),
            "recover_exposure_mult": float(ro.get("recover_exposure_mult", 1.5)),
        },
        "proposals": props,
        "at": datetime.now().isoformat(timespec="seconds"),
    }


def set_kill(on: bool) -> str:
    p = S / "KILL"
    if on:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"상황판에서 {datetime.now():%Y-%m-%d %H:%M:%S} 에 켬\n", encoding="utf-8")
        return "신규 주문 전송 중지 — 지울 때까지 새 주문이 나가지 않는다 (이미 접수된 주문은 취소되지 않는다)"
    p.unlink(missing_ok=True)
    try:
        KILL_LOCAL.unlink(missing_ok=True)      # 로컬 정지 파일도 함께 — 한쪽만 지우면 해제가 거짓이다
    except OSError:
        pass
    return "신규 주문 전송 중지 해제"


def set_skip(on: bool) -> str:
    p = S / f"SKIP_{date.today():%Y%m%d}"
    if on:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"상황판에서 {datetime.now():%H:%M:%S} 에 켬\n", encoding="utf-8")
        return "오늘 매도·매수를 건너뛴다. 계획·대조·리포트는 그대로 돈다. 내일은 정상"
    p.unlink(missing_ok=True)
    return "오늘 건너뛰기 해제"


def set_risk(body: dict) -> str:
    """익절·손실복구. 범위를 벗어나면 거부한다 — 오타 하나가 전략을 바꾸면 안 된다."""
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    ro = cfg.setdefault("risk_options", {})
    changed = []
    if "take_profit_enabled" in body:
        ro["take_profit_enabled"] = _bool(body["take_profit_enabled"], "take_profit_enabled"); changed.append("익절 " + ("켬" if ro["take_profit_enabled"] else "끔"))
    if "take_profit_pct" in body:
        v = float(body["take_profit_pct"])
        if not (5.0 <= v <= 200.0):
            raise ValueError("익절 문턱은 5~200% 사이여야 한다")
        ro["take_profit_pct"] = v; changed.append(f"익절 문턱 {v:g}%")
    if "recover_enabled" in body:
        ro["recover_enabled"] = _bool(body["recover_enabled"], "recover_enabled"); changed.append("손실복구 " + ("켬" if ro["recover_enabled"] else "끔"))
    if "recover_below_pct" in body:
        v = float(body["recover_below_pct"])
        if not (-60.0 <= v <= -1.0):
            raise ValueError("손실복구 문턱은 -60~-1% 사이여야 한다")
        ro["recover_below_pct"] = v; changed.append(f"복구 문턱 {v:g}%")
    if "recover_exposure_mult" in body:
        v = float(body["recover_exposure_mult"])
        if not (1.0 <= v <= 2.0):
            raise ValueError("복구 배수는 1.0~2.0 사이여야 한다")
        ro["recover_exposure_mult"] = v; changed.append(f"복구 배수 {v:g}")
    if not changed:
        raise ValueError("바꿀 항목이 없다")
    _write_atomic(CFG, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    return ", ".join(changed) + " — 다음 15:05 계획부터 적용"


def add_proposal(body: dict) -> str:
    """제안 파일을 쓴다. 검증은 결정 엔진이 한다 — 여기서는 모양만 본다."""
    w_raw = body.get("weights") or {}
    if isinstance(w_raw, str):
        # "003010 0.45, 092870 0.4" 같은 한 줄 입력도 받는다
        w_raw = {}
        for part in re.split(r"[,\n]+", body.get("weights", "")):
            if not part.strip():
                continue
            m = re.match(r"\s*(\d{6})\s*[:= ]\s*([0-9.]+)\s*(%?)\s*$", part)
            if not m:
                # 한 항목이라도 못 읽으면 **전체를 거부**한다. 조용히 건너뛰면 사용자는
                # 전부 접수됐다고 믿는다(외부 검토 지적).
                raise ValueError(f"읽을 수 없는 항목: {part.strip()!r} — 예: 003010 0.45, 092870 40%")
            v = float(m.group(2))
            # 퍼센트는 % 를 붙였을 때만 퍼센트다. "1.5" 를 1.5% 로 읽어 주면
            # 실수가 조용히 작은 비중으로 둔갑한다 — 그건 거부가 맞다.
            if m.group(3):
                v = v / 100.0
            elif v > 1.0:
                raise ValueError(f"{m.group(1)} 비중 {v:g} — 소수(0.45) 또는 퍼센트(45%) 로 적을 것")
            w_raw[m.group(1)] = v
    weights = {}
    for c, v in w_raw.items():
        c = str(c).strip()
        if not re.fullmatch(r"\d{6}", c):
            raise ValueError(f"종목코드가 6자리가 아니다: {c!r}")
        v = float(v)
        if v <= 0 or v > 1:
            raise ValueError(f"{c} 비중 {v} 은 0~1 사이여야 한다")
        weights[c] = v
    if not weights:
        raise ValueError("비중이 비어 있다. 예: 003010 0.45, 092870 0.4")
    exp = str(body.get("expires") or "").strip()
    if exp and not re.fullmatch(r"\d{8}", exp):
        raise ValueError("만료일은 YYYYMMDD")
    pid = f"PROP-{datetime.now():%Y%m%d-%H%M%S}"
    rec = {"id": pid, "source": str(body.get("source") or "dashboard")[:40],
           "hypothesis": str(body.get("hypothesis") or "")[:40],
           "weights": weights, "note": str(body.get("note") or "")[:300],
           "expires": exp or f"{date.today():%Y%m%d}"}
    _write_atomic(PROP / f"{pid}.json", json.dumps(rec, ensure_ascii=False, indent=1))
    return f"{pid} 제출 — 다음 15:05 계획에서 기준선과 비교된다. 관문을 통과해야 실행"


def drop_proposal(pid: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", pid or ""):
        raise ValueError("제안 ID 형식")
    p = PROP / f"{pid}.json"
    if not p.exists():
        raise ValueError("그런 제안이 없다")
    p.unlink()
    return f"{pid} 철회"


def rebuild() -> str:
    """상황판을 다시 그린다 — **코드도 다시 읽는다.**

    이 서버는 며칠씩 떠 있다. 파이썬은 한 번 들여온 모듈을 캐시하므로, 상황판
    코드를 고쳐도 서버를 재시작하기 전까지는 옛 화면을 그렸다(실제로 그랬다).
    매번 다시 읽으면 고친 즉시 반영되고, 비용은 무시할 만하다.
    """
    import importlib
    import dashboard_view
    import dashboard as dash
    importlib.reload(dashboard_view)
    importlib.reload(dash)
    dash.build(with_news=False)
    return "상황판 다시 그림"


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class Server(ThreadingHTTPServer):
    # HTTPServer 는 SO_REUSEADDR 를 켠다. Windows 에서는 그 플래그가 **같은 포트에
    # 두 번째 바인드를 허용**해 서버가 둘이 되고, 어느 쪽이 요청을 받는지 정해지지
    # 않는다. 손잡이 서버가 둘이면 같은 파일을 두 프로세스가 다시 그린다.
    # 끄면 두 번째 인스턴스는 바인드에서 죽는다 — 그게 맞다.
    allow_reuse_address = False


class H(BaseHTTPRequestHandler):
    server_version = "imrl-control/1"

    def log_message(self, fmt, *args):      # 기본 접근 로그는 소음이다
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/dashboard.html"):
            html = S / "dashboard.html"
            if not html.exists():
                with _lock:
                    rebuild()
            body = html.read_bytes()
            # 손잡이 토큰을 심는다. 파일로 연 페이지에는 없다 — 그 페이지는 어차피 못 누른다.
            inject = ("<script>window.IMRL_TOKEN=" + json.dumps(TOKEN) + ";</script>").encode("utf-8")
            body = body.replace(b"<script>", inject + b"<script>", 1)
            self._send(200, body, "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(200, {"ok": True, "state": state_now()})
        else:
            self._json(404, {"ok": False, "error": "없는 경로"})

    def _reject_origin(self) -> str:
        """이 PC 의 이 서버가 내준 페이지에서 온 요청인가. 아니면 거부 사유를 돌려준다."""
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        if host not in ("127.0.0.1", "localhost"):
            return f"Host 가 로컬이 아니다: {host!r}"
        origin = self.headers.get("Origin")
        if origin:
            oh = (urlparse(origin).hostname or "").lower()
            if oh not in ("127.0.0.1", "localhost"):
                return f"Origin 이 로컬이 아니다: {origin!r}"
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return f"Content-Type 은 application/json 이어야 한다 ({ctype or '없음'})"
        if (self.headers.get("X-IMRL-Token") or "") != TOKEN:
            return "토큰 불일치 — 상황판을 새로 열 것 (서버가 다시 뜨면 토큰이 바뀐다)"
        return ""

    def do_POST(self):
        path = urlparse(self.path).path
        why = self._reject_origin()
        if why:
            log(f"{path} 거부(출처): {why}")
            return self._json(403, {"ok": False, "error": why})
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self._json(413, {"ok": False, "error": "본문이 너무 크다"})
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        except ValueError:
            return self._json(400, {"ok": False, "error": "JSON 이 아니다"})
        if not isinstance(body, dict):
            return self._json(400, {"ok": False, "error": "JSON 객체가 아니다"})
        try:
            with _lock:
                if path == "/api/kill":
                    msg = set_kill(_bool(body.get("on")))
                elif path == "/api/skip":
                    msg = set_skip(_bool(body.get("on")))
                elif path == "/api/risk":
                    msg = set_risk(body)
                elif path == "/api/proposal":
                    msg = add_proposal(body)
                elif path == "/api/proposal/drop":
                    msg = drop_proposal(str(body.get("id", "")))
                elif path == "/api/rebuild":
                    msg = rebuild()
                else:
                    return self._json(404, {"ok": False, "error": "없는 경로"})
                if path != "/api/rebuild":
                    try:
                        rebuild()
                    except Exception as exc:          # 손잡이는 됐다. 그림만 못 그렸다.
                        log(f"상황판 갱신 실패: {exc}")
            log(f"{path} -> {msg}")
            self._json(200, {"ok": True, "message": msg, "state": state_now()})
        except (ValueError, TypeError) as exc:
            log(f"{path} 거부: {exc}")
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            log(f"{path} 오류: {type(exc).__name__}: {exc}")
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


# --------------------------------------------------------------------------- #
# 추적기 — 서버가 떠 있는 한 계속 기록한다
# --------------------------------------------------------------------------- #

HEART = S / "control_heartbeat.json"
TRACK_POLL_SEC = 30


def _hts_state() -> tuple[bool, str]:
    try:
        from imrl import hts_exec
        pid = hts_exec.axis_pid()
        if not pid:
            return False, "HTS 꺼짐"
        try:
            hts_exec.find_order_window(pid)
            return True, f"준비됨 (pid {pid})"
        except Exception:
            return False, f"실행 중(pid {pid}) — 로그인 전이거나 화면 1200 미개방"
    except Exception as exc:
        return False, f"확인 실패 {type(exc).__name__}"


def _heartbeat(**fields) -> None:
    try:
        cur = json.loads(HEART.read_text(encoding="utf-8")) if HEART.exists() else {}
    except (OSError, ValueError):
        cur = {}
    cur.update(fields)
    cur["at"] = datetime.now().isoformat(timespec="seconds")
    try:
        _write_atomic(HEART, json.dumps(cur, ensure_ascii=False, indent=1))
    except OSError:
        pass


def tracker() -> None:
    """서버가 살아 있는 동안 계속 돈다.

    감시자는 장중(08:30~16:15)만 산다. 그 밖의 시간 — 저녁의 순위표 갱신, 새벽의
    참가자 증가, 재부팅 뒤 로그인 직후 — 는 이 스레드가 맡는다. 하는 일은 셋이다.
      1. 30초마다 HTS 상태를 보고, 바뀌면 상황판을 다시 그린다 (로그인하면 즉시 반영)
      2. 한 시간마다 중계실을 긁는다 (감시자와 같은 마커를 봐서 두 번 긁지 않는다)
      3. 한 시간마다 사이클을 돌린다 — 뉴스 → 전문가 점검 → 제외 제안 → 기록 (같은 방식의 마커)
         14:30~15:35 는 정밀 회차(14:45)와 계획(15:05)의 시간이라 쉰다
      4. 심장박동 파일을 쓴다 — 상황판이 "서버가 살아 있고 마지막 기록이 언제인지" 를 보인다
    """
    import subprocess
    sys.path.insert(0, str(ROOT))
    import watchdog as _w
    last_hts = None
    last_rebuild = 0.0
    log("추적기 시작 — HTS 30초, 중계실 60분, 사이클 60분, 심장박동")
    while True:
        try:
            ok, why = _hts_state()
            changed = (ok, why) != last_hts
            try:
                _w.keep_hts_awake()          # 밤에도 HTS 가 유휴 잠금에 걸리지 않게 (감시자와 같은 함수)
            except Exception:
                pass
            last_hts = (ok, why)
            pulled = cycled = False
            if _w.relay_due():
                _w.relay_mark()
                r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "run.py"), "relay", "--auto"],
                                   cwd=str(ROOT), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=180)
                head = next((l for l in (r.stdout or "").splitlines() if "참가" in l), "")
                log(f"중계실 수집: {head.strip()[:70] or ('rc=' + str(r.returncode))}")
                pulled = True
            if _w.cycle_due() and not _w.cycle_blackout():
                _w.cycle_mark()
                r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "run.py"), "cycle", "--source", "tracker"],
                                   cwd=str(ROOT), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=300)
                head = next((l for l in (r.stdout or "").splitlines() if "사이클" in l), "")
                log(f"시간별 사이클: {head.strip()[:90] or ('rc=' + str(r.returncode))}")
                cycled = True
            try:
                prev = json.loads(HEART.read_text(encoding="utf-8")) if HEART.exists() else {}
            except (OSError, ValueError):
                prev = {}
            stamp = datetime.now().isoformat(timespec="seconds")
            _heartbeat(hts_ok=ok, hts=why,
                       last_pull=stamp if pulled else prev.get("last_pull"),
                       last_cycle=stamp if cycled else prev.get("last_cycle"))
            if changed or pulled or cycled or time.time() - last_rebuild > 600:
                with _lock:
                    try:
                        rebuild()
                    except Exception as exc:
                        log(f"상황판 갱신 실패: {exc}")
                last_rebuild = time.time()
                if changed:
                    log(f"HTS 상태: {why}")
        except Exception as exc:
            log(f"추적기 오류: {type(exc).__name__}: {exc}")
        time.sleep(TRACK_POLL_SEC)


# --------------------------------------------------------------------------- #
# 텔레그램 명령 — 휴대폰에서 프로그램을 통제한다
# --------------------------------------------------------------------------- #

TG_OFFSET = S / "telegram_offset.txt"
DASH_HTML = S / "dashboard.html"

HELP = "\n".join([
    "명령 (슬래시 없이도 됨):",
    "/상태 — 실행 판정·HTS·정지 여부·오늘 주문·순위",
    "/정지 · /재개 — 신규 주문 전송 중지 켜기/끄기",
    "/건너뛰기 · /건너뛰기해제 — 오늘 매도·매수만 건너뛰기",
    "/익절 켜기 [문턱%] · /익절 끄기",
    "/복구 켜기 · /복구 끄기 — 손실 구간 노출 확대",
    "/제안 003010 0.45, 092870 40% | 메모 — 다음 15:05 계획에서 기준선과 비교",
    "/제안목록 · /철회 PROP-ID",
    "/상황판 — 상황판 HTML 파일을 보낸다 (휴대폰에서 열어 보기)",
    "/갱신 — 상황판 다시 그리기",
    "/도움 — 이 안내",
])

_ALIASES = {
    "status": "status", "상태": "status",
    "kill": "kill", "정지": "kill", "stop": "kill",
    "resume": "resume", "재개": "resume", "해제": "resume",
    "skip": "skip", "건너뛰기": "skip",
    "unskip": "unskip", "건너뛰기해제": "unskip",
    "tp": "tp", "익절": "tp",
    "recover": "recover", "복구": "recover", "손실복구": "recover",
    "propose": "propose", "제안": "propose",
    "proposals": "proposals", "제안목록": "proposals",
    "drop": "drop", "철회": "drop",
    "dash": "dash", "상황판": "dash", "dashboard": "dash",
    "rebuild": "rebuild", "갱신": "rebuild",
    "help": "help", "도움": "help", "도움말": "help", "start": "help",
}

_ON = ("on", "켜기", "켬", "켜")
_OFF = ("off", "끄기", "끔", "꺼")


def _status_text() -> str:
    st = state_now()
    ok, why = _hts_state()
    day = f"{date.today():%Y%m%d}"
    try:
        p = S / f"orders_{day}.json"
        orders = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except Exception:
        orders = []
    led: dict[str, int] = {}
    try:
        p = S / f"submitted_{day}.json"
        for r in (json.loads(p.read_text(encoding="utf-8")) if p.exists() else []):
            k = str(r.get("status", "submitted"))
            led[k] = led.get(k, 0) + 1
    except Exception:
        pass
    try:
        board = json.loads((S / "leaderboard.json").read_text(encoding="utf-8"))
    except Exception:
        board = {}
    ro = st.get("risk_options") or {}
    lines = [
        f"[{datetime.now():%m-%d %H:%M}] HTS {'준비됨' if ok else '미준비'} — {why}",
        f"신규 주문 중지 {'켜짐' if st.get('kill') else '꺼짐'} · 오늘 건너뛰기 {'켜짐' if st.get('skip_today') else '꺼짐'}",
        f"익절 {'켜짐' if ro.get('take_profit_enabled') else '꺼짐'}({ro.get('take_profit_pct', 20):g}%) · "
        f"손실복구 {'켜짐' if ro.get('recover_enabled', True) else '꺼짐'}",
        f"오늘 주문서 {len(orders)}건 · 원장 " + (", ".join(f"{k} {v}" for k, v in led.items()) if led else "없음"),
        f"순위 {board.get('my_rank') or '—'} / 참가 {board.get('n_field') or '—'} · 1위 {float(board.get('leader_return_pct') or 0):+.2f}% "
        f"(중계실 {str(board.get('updated', ''))[5:16]})",
    ]
    return "\n".join(lines)


def handle_command(text: str) -> str:
    """한 줄 명령을 처리해 답문을 돌려준다. 예외는 문장으로 돌려준다 — 조용히 죽지 않는다."""
    t = (text or "").strip()
    if not t:
        return HELP
    parts = t.lstrip("/").split(None, 1)
    cmd = _ALIASES.get(parts[0].lower())
    arg = parts[1].strip() if len(parts) > 1 else ""
    try:
        if cmd is None:
            return "모르는 명령: " + t[:40] + "\n\n" + HELP
        if cmd == "help":
            return HELP
        if cmd == "status":
            return _status_text()
        if cmd == "kill":
            return set_kill(True)
        if cmd == "resume":
            return set_kill(False)
        if cmd == "skip":
            return set_skip(True)
        if cmd == "unskip":
            return set_skip(False)
        if cmd == "tp":
            a = arg.split()
            if not a or a[0] not in _ON + _OFF:
                return "형식: /익절 켜기 [문턱%] 또는 /익절 끄기"
            body: dict = {"take_profit_enabled": a[0] in _ON}
            if len(a) > 1:
                body["take_profit_pct"] = float(a[1].rstrip("%"))
            return set_risk(body)
        if cmd == "recover":
            a = arg.split()
            if not a or a[0] not in _ON + _OFF:
                return "형식: /복구 켜기 또는 /복구 끄기"
            return set_risk({"recover_enabled": a[0] in _ON})
        if cmd == "propose":
            if not arg:
                return "형식: /제안 003010 0.45, 092870 40% | 메모"
            w, _, memo = arg.partition("|")
            return add_proposal({"weights": w.strip(), "note": memo.strip(), "source": "telegram"})
        if cmd == "proposals":
            files = sorted(PROP.glob("PROP-*.json"))
            if not files:
                return "대기 중인 제안 없음"
            out = []
            for f in files[-10:]:
                try:
                    r = json.loads(f.read_text(encoding="utf-8"))
                    out.append(f"{r.get('id')} {r.get('weights')} 만료 {r.get('expires')}")
                except Exception:
                    out.append(f.stem)
            return "\n".join(out)
        if cmd == "drop":
            return drop_proposal(arg)
        if cmd == "rebuild":
            with _lock:
                return rebuild()
        if cmd == "dash":
            with _lock:
                rebuild()
            from imrl import notify
            sent = notify.send_document(
                DASH_HTML, f"상황판 {datetime.now():%m-%d %H:%M} — 그 시각의 상황판. 파일 안의 손잡이는 작동하지 않는다")
            return "상황판 파일 전송" + ("" if sent else " 실패")
    except ValueError as exc:
        return f"거부: {exc}"
    except Exception as exc:
        return f"오류: {type(exc).__name__}: {exc}"
    return HELP


def telegram_commands() -> None:
    """텔레그램 명령을 오래 기다리며 받는다. 설정된 chat_id 에서 온 글만 듣는다.

    상황판은 이 PC 안에서만 열리므로, 밖에서 프로그램을 만지는 길이 이것이다.
    긴급정지·건너뛰기·익절·제안·상황판 파일 — 상황판의 손잡이와 같은 함수를 부른다.
    낯선 chat 의 글은 무시하고 기록만 남긴다. 처리 위치(offset)는 파일에 남겨
    재시작 뒤 같은 명령을 두 번 실행하지 않는다.
    """
    import requests
    from imrl import notify
    s = notify.load_secrets()
    token, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log("텔레그램 명령: 토큰/채팅 ID 없음 — 비활성")
        return
    try:
        offset = int(TG_OFFSET.read_text(encoding="utf-8").strip() or 0)
    except Exception:
        offset = 0
    log("텔레그램 명령 수신 시작 (/도움)")
    while True:
        try:
            r = requests.get(notify.API.format(token=token, method="getUpdates"),
                             params={"offset": offset, "timeout": 25,
                                     "allowed_updates": json.dumps(["message"])},
                             timeout=40)
            if r.status_code != 200:
                time.sleep(10)
                continue
            for upd in r.json().get("result", []):
                offset = int(upd["update_id"]) + 1
                try:
                    TG_OFFSET.write_text(str(offset), encoding="utf-8")
                except OSError:
                    pass
                msg = upd.get("message") or {}
                from_chat = str((msg.get("chat") or {}).get("id", ""))
                text = (msg.get("text") or "").strip()
                if from_chat != str(chat):
                    log(f"텔레그램 명령: 낯선 발신 무시 (chat {from_chat[:12]})")
                    continue
                if not text:
                    continue
                reply = handle_command(text)
                log(f"텔레그램 명령: {text[:40]!r} -> {reply.splitlines()[0][:60]}")
                notify.send(reply, dedupe=False)
        except Exception as exc:
            log(f"텔레그램 명령 오류: {type(exc).__name__}: {exc}")
            time.sleep(10)


def serve(port: int = 8765) -> None:
    try:
        srv = Server(("127.0.0.1", port), H)
    except OSError as exc:
        log(f"포트 {port} 를 이미 다른 인스턴스가 쓰고 있다 — 이 인스턴스는 끝낸다 ({exc.errno})")
        return
    log(f"제어 서버 시작 http://127.0.0.1:{port}  (이 PC 안에서만 닿는다)")
    threading.Thread(target=tracker, daemon=True, name="tracker").start()
    threading.Thread(target=telegram_commands, daemon=True, name="telegram").start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        log("제어 서버 종료")


if __name__ == "__main__":
    port = 8765
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    serve(port)
