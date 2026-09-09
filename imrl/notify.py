"""텔레그램 알림 계층.

대회 개시일 기준 주문 실행은 사람이 한다. 따라서 "무엇을 몇 주 얼마에" 를
휴대폰으로 즉시 받아보는 것이 시스템의 실질적인 출력이다.
자동 주문이 완성된 뒤에도 체결 보고·자격요건 경보·장애 알림 통로로 계속 쓴다.

토큰은 config/secrets.env 에 두고 git 에서 제외한다. settings.json 에는 넣지 않는다.
텔레그램이 설정돼 있지 않으면 콘솔로 출력하므로, 설정 없이도 시스템은 동작한다.
"""

from __future__ import annotations

import html
import os
from datetime import datetime
import json
from pathlib import Path

import time

import requests

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "config" / "secrets.env"

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 15


# --------------------------------------------------------------------------- #
# 설정
# --------------------------------------------------------------------------- #

def load_secrets() -> dict[str, str]:
    """secrets.env 를 읽는다. 환경변수가 있으면 그쪽을 우선한다."""
    out: dict[str, str] = {}
    if SECRETS.exists():
        for line in SECRETS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


def save_secrets(values: dict[str, str]) -> None:
    """기존 값을 유지하면서 갱신한다."""
    cur = load_secrets()
    cur.update(values)
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{k}={v}" for k, v in sorted(cur.items()))
    SECRETS.write_text(
        "# 텔레그램 자격증명. git 에 올리지 말 것 (.gitignore 에 등록돼 있다).\n" + body + "\n",
        encoding="utf-8",
    )


def is_configured() -> bool:
    s = load_secrets()
    return bool(s.get("TELEGRAM_BOT_TOKEN") and s.get("TELEGRAM_CHAT_ID"))


# --------------------------------------------------------------------------- #
# 전송
# --------------------------------------------------------------------------- #

def send(text: str, silent: bool = False, dedupe: bool = True) -> bool:
    """설정돼 있으면 텔레그램으로, 아니면 콘솔로 보낸다.

    긴 메시지는 텔레그램 제한(4096자)에 맞춰 나눠 보낸다.
    """
    # 시험 중에는 보내지 않는다. 회귀 시험이 긴급정지 파일을 만들어 execute.py 를 돌리자
    # 실제 텔레그램에 "긴급 정지 활성" 이 갔고, 사람은 그것이 시험인지 사고인지 구분할
    # 수 없었다. 시험은 이 변수를 켜고 돈다 — 자식 프로세스까지 물려받는다.
    if os.environ.get("IMRL_QUIET"):
        print("[조용한 모드 — 텔레그램 미전송] " + text)
        return False
    # 같은 글을 20분 안에 다시 보내지 않는다. 1일차에 계획 실패 알림이 7번, 점검 실패가
    # 매시간 같은 문장으로 나가 채널이 요청 과다(429)에 걸렸다. 억제된 전송은 True 를
    # 돌려준다 — 같은 내용이 이미 전달됐기 때문이다. 기록은 state/notify_log.jsonl.
    if dedupe and _recently_sent(text):
        print("[중복 억제 — 20분 내 같은 알림] " + text[:80].replace(chr(10), " "))
        return True
    s = load_secrets()
    token, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print(text)
        return False

    ok = True
    for chunk in _split(text, 3900):
        if not _post_with_retry(token, chat, chunk, silent):
            print(chunk)          # 못 보냈으면 최소한 로그에는 남긴다
            ok = False
    _record_sent(text, ok)
    return ok


DEDUPE_SEC = 1200
_RECENT = Path(__file__).resolve().parent.parent / "state" / "notify_recent.json"
_LOG = Path(__file__).resolve().parent.parent / "state" / "notify_log.jsonl"


def _text_key(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:16]


def _recently_sent(text: str) -> bool:
    try:
        d = json.loads(_RECENT.read_text(encoding="utf-8")) if _RECENT.exists() else {}
    except Exception:
        d = {}
    t = d.get(_text_key(text))
    return bool(t) and (time.time() - float(t)) < DEDUPE_SEC


def _record_sent(text: str, ok: bool) -> None:
    now = time.time()
    try:
        d = json.loads(_RECENT.read_text(encoding="utf-8")) if _RECENT.exists() else {}
    except Exception:
        d = {}
    if ok:
        d[_text_key(text)] = now
        d = {k: v for k, v in d.items() if now - float(v) < DEDUPE_SEC * 3}
        try:
            _RECENT.parent.mkdir(parents=True, exist_ok=True)
            _RECENT.write_text(json.dumps(d), encoding="utf-8")
        except OSError:
            pass
    try:
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "ok": ok,
                                "head": text.strip().splitlines()[0][:80] if text.strip() else ""},
                               ensure_ascii=False) + chr(10))
    except OSError:
        pass


def _post_with_retry(token: str, chat: str, chunk: str, silent: bool,
                     attempts: int = 3) -> bool:
    """텔레그램 전송. 실패하면 잠깐 쉬고 다시 보낸다.

    왜 재시도가 필요한가
      이 채널이 무인 운영 중 사용자에게 닿는 **유일한 경로**다. 학교에 있는
      동안 프로그램이 보내는 것 말고는 상태를 알 방법이 없다. 그런데 예전에는
      한 번 실패하면 그 알림이 그냥 사라졌다. 실제로 읽기 제한시간 초과가
      한 번 있었고, 그게 접수 불명 경고였다면 그날 아무도 몰랐을 것이다.

      실패의 대부분은 네트워크 일시 장애다. 몇 초 뒤 다시 보내면 대개 간다.
      429(요청 과다)는 텔레그램이 대기 시간을 알려주므로 그만큼 기다린다.
    """
    delay = 2.0
    for i in range(attempts):
        try:
            r = requests.post(
                API.format(token=token, method="sendMessage"),
                json={"chat_id": chat, "text": chunk, "parse_mode": "HTML",
                      "disable_notification": silent},
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                try:
                    wait = float(r.json().get("parameters", {}).get("retry_after", delay))
                except Exception:
                    wait = delay
                # 2026-09-08 1일차: retry_after 300~570초를 그대로 기다려 계획 재시도가 6~9분씩
                # 밀렸다. 알림 채널이 집행 경로를 붙잡으면 안 된다. 10초 넘게 기다리라면 포기하고
                # 로그에 남긴다 — 다음 알림(30초 뒤 감시자 루프)이 다시 시도한다.
                print(f"[알림 지연] 요청 과다 — 텔레그램 요구 {wait:.0f}초, 최대 10초만 기다린다")
                if wait > 10:
                    return False
                time.sleep(wait)
                continue
            print(f"[알림 실패 {r.status_code}] {r.text[:200]}")
        except Exception as exc:
            print(f"[알림 실패 {i + 1}/{attempts}] {exc}")
        if i < attempts - 1:
            time.sleep(delay)
            delay *= 2
    return False


def send_document(path: str | Path, caption: str = "") -> bool:
    """파일 전송. 상황판 HTML 을 휴대폰으로 보내는 데 쓴다 (/상황판 명령)."""
    s = load_secrets()
    token, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHAT_ID")
    p = Path(path)
    if not p.exists():
        print(f"[알림 실패] 파일 없음: {p}")
        return False
    if not token or not chat:
        print(f"[파일] {p}" + chr(10) + caption)
        return False
    try:
        with p.open("rb") as f:
            r = requests.post(
                API.format(token=token, method="sendDocument"),
                data={"chat_id": chat, "caption": caption[:1024]},
                files={"document": (p.name, f, "text/html")},
                timeout=90,
            )
        if r.status_code != 200:
            print(f"[알림 실패 {r.status_code}] {r.text[:200]}")
            return False
        return True
    except Exception as exc:
        print(f"[알림 실패] {exc}")
        return False


def send_photo(path: str | Path, caption: str = "") -> bool:
    """이미지 전송. 주문 입력 화면을 눈으로 확인받는 데 쓴다.

    수량·가격 칸은 값을 되읽을 수 없으므로, 전송 전 화면 캡처가 유일한 검증 수단이다.
    """
    s = load_secrets()
    token, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHAT_ID")
    p = Path(path)
    if not p.exists():
        print(f"[알림 실패] 이미지 없음: {p}")
        return False
    if not token or not chat:
        print(f"[이미지] {p}\n{caption}")
        return False
    try:
        with p.open("rb") as f:
            r = requests.post(
                API.format(token=token, method="sendPhoto"),
                data={"chat_id": chat, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"photo": f},
                timeout=60,
            )
        if r.status_code != 200:
            print(f"[알림 실패 {r.status_code}] {r.text[:200]}")
            return False
        return True
    except Exception as exc:
        print(f"[알림 실패] {exc}")
        return False


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def _e(s) -> str:
    """HTML 파스 모드에서 안전하게."""
    return html.escape(str(s))


# --------------------------------------------------------------------------- #
# 메시지 포맷
# --------------------------------------------------------------------------- #

def format_orders(orders: list, equity: int) -> str:
    if not orders:
        return "<b>오늘 주문 없음</b>\n목표 포트폴리오와 현재 보유가 일치한다."

    lines = ["<b>오늘의 주문</b>", ""]
    total_buy = total_sell = 0
    for i, o in enumerate(orders, 1):
        tag = "매도" if o.side == "SELL" else "매수"
        lines.append(f"{i}. [{tag}] <b>{_e(o.name)}</b> ({o.code})")
        lines.append(f"    {o.qty:,}주 @ {o.limit_price:,}원 = {o.amount:,}원")
        lines.append(f"    <i>{_e(o.reason)}</i>")
        if o.side == "BUY":
            total_buy += o.amount
        else:
            total_sell += o.amount
    lines += [
        "",
        f"매도 {total_sell:,}원 / 매수 {total_buy:,}원",
        f"평가자산 기준 {equity:,}원",
        "",
        # 예전 문구는 "미체결 시 현재가로 정정"이었는데 **그런 기능이 없다.**
        # 주문 정정·취소 경로 자체가 미구현이다. 없는 동작을 알림에 적으면
        # 미체결 상황에서 시스템이 알아서 처리한다고 믿게 된다.
        "지정가 주문(매수 +1% / 매도 -1%). 미체결은 장 마감에 자동 소멸한다.",
    ]
    return "\n".join(lines)


def format_status(constraint_report: str, positions: dict, my_return: float | None = None) -> str:
    lines = ["<b>일일 현황</b>", ""]
    if my_return is not None:
        lines.append(f"수익률 <b>{my_return:+.2f}%</b>")
        lines.append("")
    lines.append("<pre>" + _e(constraint_report) + "</pre>")
    if positions:
        lines.append("")
        lines.append("<b>보유 종목</b>")
        for c, p in positions.items():
            lines.append(f"  {_e(p.get('name', c))} ({c}) {p['qty']:,}주 @ {p.get('avg_price', 0):,}원")
    return "\n".join(lines)


def alert(title: str, detail: str) -> bool:
    """경보. 자격 요건 미달, 자동화 장애 등 즉시 확인이 필요한 건."""
    return send(f"<b>⚠ {_e(title)}</b>\n\n{_e(detail)}")


def heartbeat(note: str = "") -> bool:
    """무인 운영 중 생존 신호. 알림음 없이 보낸다."""
    return send(f"heartbeat {_e(note)}".strip(), silent=True)
