"""싸이칸플러스 주문 실행 어댑터.

2026-09-05 실측으로 확정한 입력 경로
  종목    표준 Edit → WM_CHAR 메시지. 포그라운드 불필요, 되읽기 가능
  수량    커스텀 뷰 → 메시지 클릭으로 캐럿 이동 + keybd_event(스캔코드) 로 입력
  가격    동일
  탭·버튼 메시지 클릭

왜 keybd_event 인가
  WM_CHAR/WM_KEYDOWN 메시지는 커스텀 뷰에 전혀 반영되지 않았다(수량·가격 칸에는
  HWND 자체가 없다). MapVirtualKey 로 스캔코드를 채운 keybd_event 만 동작했다.
  이 경로는 전역 입력이므로 HTS 가 포그라운드여야 하고, 매 입력 직전에
  포커스를 검증한다(어긋나면 입력하지 않고 예외).

  정정(2026-09-05): 예전 주석은 "pywinauto send_keys(SendInput) 도 반영되지 않았다"고
  적었는데 부정확하다. pywinauto 는 기본적으로 텍스트를 **VK_PACKET** 으로 보내고
  그것을 처리하지 않는 앱에서는 `vk_packet=False` 가 필요하다(공식 문서). 실제
  VK + 스캔코드를 실은 SendInput 은 keybd_event 와 **같은 경로**다.
  즉 "SendInput 으로 바꾸면 될지도"는 시도할 가치가 없다 — 지금 하는 것과 같은 일이다.

백그라운드 실행이 불가능한 이유 (실측, research/background_execution/FINDINGS.md)
  전역 입력은 **입력 데스크톱**으로만 간다. 별도 데스크톱(CreateDesktop)에서는
  SendInput·keybd_event 가 둘 다 ERROR_ACCESS_DENIED(5) 로 실패한다.
  화면잠금도 입력 데스크톱을 Winlogon 으로 바꾸므로 같은 실패다.
  따라서 집행 순간에는 HTS 가 반드시 사용자 화면 맨 앞에 있어야 한다.

검증의 한계
  수량·가격 칸에는 자식 창이 없어 값을 되읽을 수 없다. 따라서 전송 전 화면을
  캡처해 사람이 확인하거나(1단계), OCR 로 대조한다(2단계). 확인 없이 전송하지 않는다.

주의: 이 모듈은 관리자 권한에서만 동작한다. HTS 가 관리자 권한으로 실행되기 때문이다.
"""

from __future__ import annotations

import time
from ctypes import byref, windll, wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image

from .hts_anchor import (AnchorError, amount_rect, build_layout, count_amount_glyphs,
                          find_price_box, is_checked)

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "state" / "hts_shots"

# WindowFromPoint 는 HWND(64비트 포인터)를 돌려준다. ctypes 기본 restype 은 c_int 라
# 그대로 두면 상위 비트가 잘려 엉뚱한 핸들이 나온다. 대부분의 경우 값이 작아
# 우연히 맞지만, 틀리는 날 조용히 틀린다. 명시한다.
windll.user32.WindowFromPoint.restype = wintypes.HWND
windll.user32.WindowFromPoint.argtypes = [wintypes.POINT]

KEYEVENTF_KEYUP = 0x0002
EM_SETSEL = 0x00B1
CWP_SKIPINVISIBLE = 0x0001

# 모의투자 세션 표식. 메인 창 제목에 들어 있다.
# 실계좌에 주문을 내는 것은 되돌릴 수 없으므로 전송 전에 반드시 확인한다.
MOCK_MARKER = "모의"

#: 싸이칸 Plus 의 화면잠금 상태. 메인 창 제목이 '싸이칸 Plus-화면잠금' 이 된다.
#:
#: 무인 운영에서 가장 조용한 실패다. HTS 는 켜져 있고 주식주문 창도 열려 있어
#: 창 열거는 전부 성공하는데, 화면이 그려지지 않아 PrintWindow 가 0 을 돌려준다.
#: 예전 오류 메시지는 이 상황에서 "관리자 권한을 확인하라"고 엉뚱한 곳을 가리켰다.
#: 15:21 에 이 상태면 그날 매매가 통째로 날아가므로 장중 이전에 미리 잡아야 한다.
LOCK_MARKER = "화면잠금"


def blocking_popups(pid: int, main: int, order: int) -> list[str]:
    """HTS 가 띄운 대화상자 제목 목록(무해한 '알람' 제외). 비어 있지 않으면 주문 입력이 막힌다.

    2026-09-08: 화면잠금·재로그인 요청 같은 대화상자는 주문창이 멀쩡히 있어도 입력을
    삼킨다. 감시자의 준비 판정이 주문창 존재만 보면 그 상태를 "준비됨"으로 잘못 읽는다.
    """
    out: list[str] = []

    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        try:
            _, p = win32process.GetWindowThreadProcessId(h)
        except Exception:
            return
        if p != pid or h in (main, order):
            return
        cls = win32gui.GetClassName(h)
        txt = win32gui.GetWindowText(h)
        if cls == "#32770" or win32gui.GetWindow(h, win32con.GW_OWNER):
            if txt.strip() not in OrderAdapter.BENIGN_POPUP_TITLES:
                out.append(txt.strip() or cls)

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return out


def session_state(main: int) -> tuple[str, str]:
    """메인 창 제목으로 HTS 세션 상태를 판정한다. (상태, 제목)

    상태: ok | locked | no_marker | no_window

    주의: 제목의 '모의' 표식은 **신뢰할 수 있는 모의세션 판정이 아니다.**
    2026-09-05 재로그인 후 정상 모의 세션인데도 제목이 '싸이칸 Plus' 로만 떴다
    (그 전에는 '(S071)모의시장' 이었다). 표식이 없다고 실거래인 것이 아니다.

    실거래 여부의 판정 기준은 **계좌번호 대조**(`OrderAdapter._preflight`)다.
    그쪽이 '아무 모의계좌'가 아니라 '대회용 그 계좌'인지까지 확인하므로 더 강하다.
    이 함수는 화면잠금 감지가 주 목적이고, 표식 유무는 참고 정보로만 돌려준다.
    """
    if not main:
        return "no_window", ""
    title = win32gui.GetWindowText(main)
    if LOCK_MARKER in title:
        return "locked", title
    if MOCK_MARKER not in title:
        return "no_marker", title
    return "ok", title

# 주문창을 항상 이 크기로 맞춘 뒤 아래 좌표를 쓴다.
#
# 배경: 창을 최대화하면 창 크기만 커지고 **앱 내용은 확대되지 않는다**(좌상단에
# 원래 크기로 그려진다). 반대로 창을 줄이면 내용이 잘린다. 배율을 추정해 좌표를
# 외삽하려 했으나, 기준으로 삼은 Edit 을 잘못 짚으면 좌표가 창 밖으로 나가는 등
# 실패가 조용히 누적된다(실제로 주문금액 칸을 종목 칸으로 오인했다).
# 그래서 추정하지 않고 창 크기를 고정한다. 결정적이고 검증도 쉽다.
ORDER_W, ORDER_H = 2453, 1577

LAYOUT = {
    "tab_sell": (1215, 191),
    "tab_buy": (1304, 191),
    "symbol_rect": (1355, 236, 1553, 284),
    "qty_rect": (1355, 305, 1553, 353),
    "price_rect": (1355, 374, 1553, 422),
    # 자동(현재가) 체크박스. 켜져 있으면 가격이 현재가로 강제되므로 반드시 꺼야 한다.
    "auto_price_checkbox": (1394, 443),
    "market_checkbox": (1764, 397),
    # 전송 버튼: 매수는 "매수(F9)", 매도는 "매도(F12)". 좌표는 동일하다.
    "submit_button": (2284, 677),
    "form_crop": (1200, 150, 2450, 760),
}

class HtsError(RuntimeError):
    """화면 상태가 예상과 다를 때. 절대 삼키지 말 것."""


class FocusError(HtsError):
    """의도한 창이 포그라운드가 아닐 때. 오입력 방지를 위해 즉시 중단한다."""


def enable_dpi() -> None:
    try:
        windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        windll.user32.SetProcessDPIAware()


# --------------------------------------------------------------------------- #
# 창 찾기
# --------------------------------------------------------------------------- #

def console_text(raw: bytes) -> str:
    """Windows 기본 도구의 출력을 문자열로.

    tasklist 같은 도구는 **콘솔 코드페이지**로 쓴다. 한국어 PC 에서는 cp949 다.
    `python -X utf8` 로 켠 프로세스가 `text=True` 로 받으면 UTF-8 로 디코드하다
    깨지고, 그때 subprocess 는 예외를 올리는 대신 **stdout 을 None 으로** 준다.

    이게 감시자에서 실제로 터졌다. axis.exe 가 없을 때 tasklist 는
    "정보: 지정한 조건에 맞는 작업을 실행하고 있지 않습니다" 를 한국어로 찍는데,
    그 첫 바이트 0xC1 이 UTF-8 이 아니다. 즉 **HTS 가 꺼져 있을 때만** 죽었다 —
    감시자가 HTS 를 띄워야 하는 바로 그 상황이다.

    코드페이지는 물어서 안다. 콘솔이 없으면(스케줄러) GetConsoleOutputCP 가 0 을
    주므로 OEM 코드페이지로 떨어진다. scripts/start_watchdog.bat 이 chcp 65001 을 하면 그 값이
    그대로 잡힌다. 우리가 읽는 필드는 어차피 ASCII 라 replace 로 충분하다.
    """
    enc = "utf-8"
    try:
        cp = windll.kernel32.GetConsoleOutputCP() or windll.kernel32.GetOEMCP()
        if cp:
            enc = "utf-8" if cp == 65001 else f"cp{cp}"
    except Exception:
        pass
    try:
        return raw.decode(enc, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def axis_pid() -> int:
    import subprocess

    try:
        raw = subprocess.run(["tasklist", "/FI", "IMAGENAME eq axis.exe",
                              "/FO", "CSV", "/NH"],
                             capture_output=True, timeout=20).stdout or b""
    except Exception:
        return 0
    for line in console_text(raw).splitlines():
        parts = [x.strip('" ') for x in line.split('","')]
        if parts and parts[0].lower().startswith("axis"):
            return int(parts[1])
    return 0


def _visible_tops(pid: int) -> list[tuple[int, int]]:
    tops: list[tuple[int, int]] = []

    def cb(h, _):
        _, p = win32process.GetWindowThreadProcessId(h)
        if p == pid and win32gui.IsWindowVisible(h):
            rc = win32gui.GetWindowRect(h)
            tops.append(((rc[2] - rc[0]) * (rc[3] - rc[1]), h))

    win32gui.EnumWindows(cb, None)
    return sorted(tops, reverse=True)


def find_main(pid: int) -> int:
    """HTS 메인 창.

    "가장 큰 보이는 창"으로 고르면 안 된다. **최소화된 창도 IsWindowVisible 은
    True 를 돌려주고 rect 만 (-32000,-32000) 근처로 바뀐다.** 그래서 메인이
    최소화된 순간 133x51 짜리 부속 창이 1등이 되어 메인으로 뽑혔고, 그 뒤의
    좌표·캡처·클릭이 전부 엉뚱한 창을 향했다.

    메인 창의 확실한 표식은 **MDIClient 자식을 가진다**는 것이다. 주문 화면(1200)이
    그 안에 뜨기 때문이다. 크기와 무관하므로 최소화 상태에서도 정확히 잡힌다.
    """
    owners: list[tuple[int, int]] = []

    def cb(h, _):
        _, p = win32process.GetWindowThreadProcessId(h)
        if p != pid:
            return
        if win32gui.FindWindowEx(h, 0, "MDIClient", None):
            rc = win32gui.GetWindowRect(h)
            owners.append(((rc[2] - rc[0]) * (rc[3] - rc[1]), h))

    win32gui.EnumWindows(cb, None)
    if owners:
        owners.sort(reverse=True)
        return owners[0][1]

    # MDIClient 가 아직 없으면(로그인 직후 등) 크기 기준으로 폴백하되,
    # 최소화된 창은 제외한다.
    tops = [(a, h) for a, h in _visible_tops(pid)
            if not windll.user32.IsIconic(h)]
    if not tops:
        raise HtsError("HTS 메인 창을 찾지 못했다. 실행 및 모의투자 로그인 상태를 확인할 것.")
    return tops[0][1]


def find_order_window(pid: int) -> int:
    """주문 화면(MDI 자식).

    제목은 열린 직후 '[1200] 주식주문' 이지만 이후 'Window1' 로 바뀌므로
    제목만으로 찾으면 안 된다. 제목 매칭 후 MDIClient 최대 자식으로 폴백한다.
    """
    by_title: list[tuple[int, int]] = []
    mdi_kids: list[tuple[int, int]] = []

    for _, top in _visible_tops(pid):
        def cb(h, _):
            if not win32gui.IsWindowVisible(h):
                return
            rc = win32gui.GetWindowRect(h)
            if "주식주문" in win32gui.GetWindowText(h):
                by_title.append(((rc[2] - rc[0]) * (rc[3] - rc[1]), h))

        win32gui.EnumChildWindows(top, cb, None)

        mdi = win32gui.FindWindowEx(top, 0, "MDIClient", None)
        if mdi:
            def cb2(h, _):
                if win32gui.GetParent(h) != mdi or not win32gui.IsWindowVisible(h):
                    return
                rc = win32gui.GetWindowRect(h)
                mdi_kids.append(((rc[2] - rc[0]) * (rc[3] - rc[1]), h))

            win32gui.EnumChildWindows(mdi, cb2, None)

    if by_title:
        return max(by_title)[1]
    if mdi_kids:
        return max(mdi_kids)[1]
    raise HtsError("주문 화면을 찾지 못했다. HTS 에서 화면 1200 을 열 것.")


def open_screen(main: int, screen_no: str = "1200", wait: float = 4.0) -> None:
    """메인 창의 화면 검색칸에 화면번호를 넣어 화면을 연다.

    검색칸은 표준 Edit 이고 WM_CHAR 를 정상 처리한다(주문 폼의 커스텀 칸과 다르다).
    포그라운드가 아니어도 되므로 전역 입력을 쓰지 않는다.

    주의: 이 칸은 화면 '코드'(CK120000)가 아니라 숫자 화면번호(1200) 또는
    화면명(주식주문)을 받는다. 잘못된 값이면 '일치되는 항목이 없습니다' 팝업만 뜬다.
    """
    edits: list[tuple[int, int]] = []

    def cb(h, _):
        if win32gui.GetClassName(h) == "Edit" and win32gui.IsWindowVisible(h):
            rc = win32gui.GetWindowRect(h)
            edits.append((rc[1], h))

    win32gui.EnumChildWindows(main, cb, None)
    if not edits:
        raise HtsError("화면 검색칸을 찾지 못했다.")
    edits.sort()
    box = edits[0][1]

    # 스레드 안에서만 포커스를 옮긴다 (전역 포커스를 훔치지 않는다)
    cur = windll.kernel32.GetCurrentThreadId()
    tid = windll.user32.GetWindowThreadProcessId(main, None)
    if tid and tid != cur:
        windll.user32.AttachThreadInput(cur, tid, True)
    try:
        windll.user32.SetFocus(box)
    finally:
        if tid and tid != cur:
            windll.user32.AttachThreadInput(cur, tid, False)

    for _ in range(24):
        win32api.PostMessage(box, win32con.WM_KEYDOWN, win32con.VK_BACK, 0)
        win32api.PostMessage(box, win32con.WM_CHAR, 8, 0)
        win32api.PostMessage(box, win32con.WM_KEYUP, win32con.VK_BACK, 0)
    time.sleep(0.3)
    for ch in screen_no:
        win32api.PostMessage(box, win32con.WM_CHAR, ord(ch), 0)
        time.sleep(0.05)
    time.sleep(0.4)
    win32api.PostMessage(box, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
    win32api.PostMessage(box, win32con.WM_CHAR, 13, 0)
    win32api.PostMessage(box, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
    time.sleep(wait)


ES_READONLY = 0x0800
GWL_STYLE = -16
WM_GETTEXT = 0x000D


def find_symbol_edit(order: int, price_box: tuple[int, int, int, int]) -> tuple[int, tuple]:
    """종목 입력 Edit 을 **컨트롤로** 찾는다. 좌표로 추정하지 않는다.

    가격칸 기준 오프셋으로 종목 칸 위치를 계산했더니 행 간격 비율이 미세하게 달라
    클릭이 칸을 빗나갔고, 그 결과 **종목이 이전 값 그대로 남은 채 수량·가격만
    바뀌는** 사고가 났다(엑시콘을 주문해야 하는데 화면에는 SK하이닉스가 남아 있었다).
    실전송이었다면 엉뚱한 종목을 샀을 것이다.

    종목 칸은 실제 Edit 컨트롤이므로 열거해서 고르면 추정이 필요 없다.
    구분 기준:
      - 편집 가능해야 한다 (주문금액 칸은 ES_READONLY)
      - 가격칸과 x 범위가 겹친다 (같은 열에 세로로 배치되어 있다)
      - 가격칸보다 위에 있다
    """
    l, t, _, _ = win32gui.GetWindowRect(order)
    px0, py0, px1, py1 = price_box
    cands: list[tuple[int, int, tuple]] = []

    def cb(h, _):
        if win32gui.GetClassName(h) != "Edit" or not win32gui.IsWindowVisible(h):
            return
        if windll.user32.GetWindowLongW(h, GWL_STYLE) & ES_READONLY:
            return
        rc = win32gui.GetWindowRect(h)
        r = (rc[0] - l, rc[1] - t, rc[2] - l, rc[3] - t)
        if r[3] > py0:                       # 가격칸보다 아래면 종목 칸이 아니다
            return
        overlap = min(r[2], px1) - max(r[0], px0)
        if overlap < (px1 - px0) * 0.4:      # 같은 열에 있지 않다
            return
        cands.append((py0 - r[3], h, r))     # 가격칸에 가까운 것부터

    win32gui.EnumChildWindows(order, cb, None)
    if not cands:
        raise HtsError(
            "종목 입력 Edit 을 찾지 못했다. 주문 화면(1200)이 열려 있는지 확인할 것."
        )
    cands.sort()
    return cands[0][1], cands[0][2]


def find_edit_at(order: int, rect: tuple[int, int, int, int]) -> int | None:
    """주어진 사각형과 겹치는 편집 가능한 Edit 컨트롤을 찾는다. 없으면 None.

    수량·가격 칸은 좌표 보간으로 위치를 잡는다. 보간은 계산이지 확인이 아니므로,
    거기에 실제로 값이 들어갔는지는 별도로 읽어봐야 안다. 과거 사고가 전부
    "좌표는 맞다고 믿었는데 입력이 다른 곳에 들어갔다" 형태였다.
    """
    l, t, _, _ = win32gui.GetWindowRect(order)
    rx0, ry0, rx1, ry1 = rect
    best: list[tuple[float, int]] = []

    def cb(h, _):
        if win32gui.GetClassName(h) != "Edit" or not win32gui.IsWindowVisible(h):
            return
        if windll.user32.GetWindowLongW(h, GWL_STYLE) & ES_READONLY:
            return
        rc = win32gui.GetWindowRect(h)
        a = (rc[0] - l, rc[1] - t, rc[2] - l, rc[3] - t)
        ox = min(a[2], rx1) - max(a[0], rx0)
        oy = min(a[3], ry1) - max(a[1], ry0)
        if ox <= 0 or oy <= 0:
            return
        area = (rx1 - rx0) * (ry1 - ry0)
        best.append((-(ox * oy) / max(area, 1), h))

    win32gui.EnumChildWindows(order, cb, None)
    if not best:
        return None
    best.sort()
    return best[0][1]


def _digits(text: str) -> str:
    """화면 표기에서 숫자만 남긴다. 천단위 콤마·공백·단위를 무시하고 비교하기 위함."""
    return "".join(ch for ch in text if ch.isdigit())


def read_edit(hwnd: int, timeout_ms: int = 1500) -> str:
    """Edit 의 텍스트를 읽는다. 응답이 없으면 빈 문자열.

    이 Edit 은 서브클래싱돼 있어 값을 못 읽는 경우가 있다. 읽히면 검증에 쓰고,
    못 읽으면 캡처 확인에 맡긴다.
    """
    from ctypes import create_unicode_buffer, byref, c_ulong

    buf = create_unicode_buffer(64)
    res = c_ulong(0)
    ok = windll.user32.SendMessageTimeoutW(
        hwnd, WM_GETTEXT, 64, buf, 0x0002, timeout_ms, byref(res))
    return buf.value.strip() if ok else ""


def ensure_main_size(main: int) -> tuple[int, int]:
    """메인 창을 최대화한다.

    주문창은 MDI 자식이라 **메인 창보다 커질 수 없다.** 재로그인 후 메인이
    960x490 으로 뜬 적이 있는데, 그 상태에서는 주문창을 기준 크기(2453x1577)로
    맞추려 해도 클리핑돼 수량·가격 칸이 화면 밖으로 나간다. 그러면 컨트롤 탐색이
    실패하고 전송 게이트가 막힌다 — 실제로 그렇게 막혔다.

    그래서 순서가 중요하다: 메인 최대화 -> 주문창 크기 지정 -> 레이아웃 인식.
    """
    # 최소화 상태면 먼저 복원한다. 최소화된 창은 화면에 그려지지 않아
    # PrintWindow 가 0 을 돌려주고(화면잠금과 같은 증상) 레이아웃 인식이 통째로
    # 실패한다. IsZoomed 만 보면 최소화를 못 잡는다 — 최소화는 Zoomed 가 아니다.
    if windll.user32.IsIconic(main):
        windll.user32.ShowWindow(main, win32con.SW_RESTORE)
        time.sleep(0.8)
    if not windll.user32.IsZoomed(main):
        windll.user32.ShowWindow(main, win32con.SW_MAXIMIZE)
        time.sleep(1.0)
    rc = win32gui.GetWindowRect(main)
    w, h = rc[2] - rc[0], rc[3] - rc[1]
    screen_w = windll.user32.GetSystemMetrics(0)
    if w < screen_w * 0.6:
        # 최대화가 먹지 않았다. 화면 크기로 직접 배치한다.
        win32gui.SetWindowPos(main, 0, 0, 0, screen_w,
                              windll.user32.GetSystemMetrics(1),
                              win32con.SWP_NOZORDER)
        time.sleep(1.0)
        rc = win32gui.GetWindowRect(main)
        w, h = rc[2] - rc[0], rc[3] - rc[1]
    return w, h


def pin_order_window(order: int) -> tuple[int, int]:
    """주문창을 **고정 위치·고정 크기**로 못박는다.

    왜 고정인가
      MDI 자식이 최대화 상태와 복원 상태를 오갔다(2453x1514 <-> 3840x1518).
      크기가 바뀌면 폼이 다시 그려지고, 색 앵커가 잡는 노란 가격칸 위치도 달라진다.
      실제로 최대화된 순간에는 앵커 탐색이 아예 실패했다. 매번 다른 화면을 상대로
      좌표를 추론하는 것보다, **화면 자체를 항상 같게 만드는 편**이 훨씬 확실하다.

      고정한 뒤에도 색 앵커는 그대로 쓴다. 그때 앵커의 역할은 '찾기'가 아니라
      '이 화면이 내가 아는 그 화면이 맞는지 확인하기'가 된다.

    못박지 못하면 예외를 던진다. 어긋난 좌표로 진행하는 것이 가장 위험하다.
    """
    if windll.user32.IsIconic(order):
        windll.user32.ShowWindow(order, win32con.SW_RESTORE)
        time.sleep(0.5)
    # 최대화 상태면 먼저 복원한다. 복원하지 않고 크기를 주면 무시된다.
    if windll.user32.IsZoomed(order):
        windll.user32.ShowWindow(order, win32con.SW_RESTORE)
        time.sleep(0.5)

    want_w, want_h = ORDER_W, ORDER_H
    parent = win32gui.GetParent(order)
    if parent:
        prc = win32gui.GetWindowRect(parent)
        want_w = min(want_w, max(prc[2] - prc[0] - 4, 200))
        want_h = min(want_h, max(prc[3] - prc[1] - 4, 200))

    for attempt in range(3):
        # 좌상단 (0,0) 에 고정한다. 위치까지 고정해야 캡처 좌표가 매번 같다.
        win32gui.SetWindowPos(order, 0, 0, 0, want_w, want_h, win32con.SWP_NOZORDER)
        time.sleep(0.5)
        if not win32gui.IsWindow(order) or not win32gui.IsWindowVisible(order):
            raise HtsError("크기 조정 중 주문창이 사라졌다. 화면 1200 을 다시 열 것.")
        rc = win32gui.GetWindowRect(order)
        w, h = rc[2] - rc[0], rc[3] - rc[1]
        if abs(w - want_w) <= 8 and abs(h - want_h) <= 8:
            return w, h
        if windll.user32.IsZoomed(order):
            windll.user32.ShowWindow(order, win32con.SW_RESTORE)
            time.sleep(0.5)

    raise HtsError(
        f"주문창을 {want_w}x{want_h} 로 고정하지 못했다(현재 {w}x{h}). "
        "창 크기가 계속 바뀌면 좌표를 신뢰할 수 없으므로 중단한다."
    )


# --------------------------------------------------------------------------- #
# 포커스·입력
# --------------------------------------------------------------------------- #

def raise_window(hwnd: int, attempts: int = 6) -> bool:
    windll.user32.ShowWindow(hwnd, win32con.SW_RESTORE)
    windll.user32.BringWindowToTop(hwnd)
    cur = windll.kernel32.GetCurrentThreadId()
    fg = windll.user32.GetForegroundWindow()
    tids = {windll.user32.GetWindowThreadProcessId(fg, None),
            windll.user32.GetWindowThreadProcessId(hwnd, None)}
    for tid in tids:
        if tid and tid != cur:
            windll.user32.AttachThreadInput(cur, tid, True)
    try:
        for _ in range(attempts):
            windll.user32.SetForegroundWindow(hwnd)
            windll.user32.SetActiveWindow(hwnd)
            # 포그라운드만으로는 부족하다. 다른 앱이 항상-위(topmost) 로 떠 있으면
            # 포커스는 우리 것이어도 화면상으로는 그 창이 덮고 있어 물리 클릭을
            # 가로챈다(실제로 Claude 데스크톱 창이 그랬다). Z-순서를 명시적으로 올린다.
            windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
            windll.user32.SetWindowPos(
                hwnd, -2, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
            time.sleep(0.25)
            if windll.user32.GetForegroundWindow() == hwnd:
                return True
    finally:
        for tid in tids:
            if tid and tid != cur:
                windll.user32.AttachThreadInput(cur, tid, False)
    return False


def assert_focus(hwnd: int) -> None:
    fg = windll.user32.GetForegroundWindow()
    if fg == hwnd or windll.user32.GetAncestor(fg, 2) == hwnd:
        return
    raise FocusError(
        f"포커스가 HTS(hwnd={hwnd})가 아니라 hwnd={fg} "
        f"({win32gui.GetWindowText(fg)!r})에 있다. 오입력 방지를 위해 중단한다."
    )


SMTO_ABORTIFHUNG = 0x0002
SMTO_NORMAL = 0x0000


def send_timeout(hwnd: int, msg: int, wparam: int, lparam: int, timeout_ms: int = 4000) -> int:
    """SendMessage 의 타임아웃 있는 버전.

    win32gui.SendMessage 는 대상 창의 스레드가 메시지를 처리할 때까지 **무한히**
    기다린다. 다른 프로세스의 창에 쓰면 앱이 잠깐 바쁜 것만으로도 자동화가
    영구히 멈춘다(실제로 주문 1건 처리 중 멈춰 작업을 강제 종료해야 했다).
    장중에 이런 일이 나면 주문을 놓친다. 그래서 항상 타임아웃을 건다.
    """
    from ctypes import byref, c_ulong

    result = c_ulong(0)
    ok = windll.user32.SendMessageTimeoutW(
        hwnd, msg, wparam, lparam,
        SMTO_NORMAL | SMTO_ABORTIFHUNG, timeout_ms, byref(result),
    )
    if not ok:
        raise HtsError(
            f"창(hwnd={hwnd})이 {timeout_ms}ms 안에 메시지({msg})를 처리하지 않았다. "
            "HTS 가 응답하지 않거나 대화상자가 떠 있을 수 있다."
        )
    return result.value


def msg_click(order: int, rel_x: int, rel_y: int) -> int:
    """창 기준 좌표에 클릭 메시지를 보낸다. 커서를 움직이지 않는다."""
    l, t, _, _ = win32gui.GetWindowRect(order)
    sx, sy = l + rel_x, t + rel_y
    cur = order
    for _ in range(10):
        pt = wintypes.POINT(sx, sy)
        windll.user32.ScreenToClient(cur, byref(pt))
        ch = windll.user32.ChildWindowFromPointEx(cur, pt, CWP_SKIPINVISIBLE)
        if not ch or ch == cur:
            break
        cur = ch
    pt = wintypes.POINT(sx, sy)
    windll.user32.ScreenToClient(cur, byref(pt))
    lp = win32api.MAKELONG(pt.x, pt.y)
    win32api.PostMessage(cur, win32con.WM_MOUSEMOVE, 0, lp)
    time.sleep(0.06)
    win32api.PostMessage(cur, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
    time.sleep(0.06)
    win32api.PostMessage(cur, win32con.WM_LBUTTONUP, 0, lp)
    time.sleep(0.35)
    return cur


MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


def real_click(order: int, main: int, rel_x: int, rel_y: int) -> None:
    """창 기준 좌표를 실제 마우스로 클릭한다.

    메시지 주입(WM_LBUTTONDOWN/UP)은 쓰지 않는다. 2026-09-05 검증 중 이 방식으로
    **HTS 가 크래시했다** — DOWN 을 받은 컨트롤이 마우스 캡처 루프에 들어갔는데
    UP 전달이 지연되면서 앱이 갇혔다. 키보드를 스캔코드 실입력으로 넣어야 했던 것과
    같은 이유로, 클릭도 앱이 기대하는 실제 입력으로 보내는 편이 안전하다.

    전역 입력이므로 호출 직전 반드시 포커스를 검증한다. 어긋나면 클릭하지 않는다.
    """
    # 다른 앱이 포커스를 되가져가는 일이 흔하다(예: 개발 중 Claude 데스크톱 앱).
    # 한 번 어긋났다고 바로 포기하면 정상 상황에서도 주문을 놓치므로 몇 번 재시도한다.
    # 다만 포커스가 확인되지 않으면 **절대 클릭하지 않는다**. 잘못된 창을 누르는 것이
    # 주문을 놓치는 것보다 훨씬 나쁘다.
    last: Exception | None = None
    for attempt in range(4):
        try:
            assert_focus(main)
        except FocusError as exc:
            last = exc
            raise_window(main)
            time.sleep(0.35)
            continue

        l, t, _, _ = win32gui.GetWindowRect(order)
        x, y = l + rel_x, t + rel_y
        windll.user32.SetCursorPos(x, y)
        time.sleep(0.12)
        try:
            assert_focus(main)
        except FocusError as exc:
            last = exc
            raise_window(main)
            time.sleep(0.35)
            continue

        # 포커스 검증만으로는 부족하다. HTS 가 포그라운드여도 그 **위에** 다른 창이
        # 겹쳐 있으면 물리 클릭은 그 창이 먹는다. 툴팁·알림 배너·다른 앱의
        # always-on-top 창이 전부 여기 해당하고, 그때 우리는 "클릭했다"고 믿은 채
        # 아무 일도 일어나지 않은 화면에 계속 타이핑한다.
        #
        # 커서 위치의 실제 창이 주문창 계열인지 확인한다. 아니면 클릭하지 않는다.
        pt = wintypes.POINT(x, y)
        under = windll.user32.WindowFromPoint(pt)
        root = win32gui.GetAncestor(under, 2) if under else 0   # GA_ROOTOWNER
        if root and root != win32gui.GetAncestor(order, 2):
            last = FocusError(
                f"클릭 지점 ({x},{y}) 의 창이 주문창이 아니다: "
                f"{win32gui.GetClassName(under)!r} {win32gui.GetWindowText(root)!r}. "
                "다른 창이 겹쳐 있다.")
            raise_window(main)
            time.sleep(0.35)
            continue

        windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.06)
        windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.35)
        return

    raise FocusError(
        f"HTS 포커스를 4회 시도했으나 확보하지 못해 클릭하지 않았다. "
        f"다른 앱이 포커스를 계속 가져가고 있다. 마지막 상태: {last}"
    )


def type_scan(text: str, pause: float = 0.07) -> None:
    """스캔코드를 실은 keybd_event 입력.

    SendInput 과 WM_CHAR 는 커스텀 뷰에 반영되지 않는다. 스캔코드가 있어야 한다.
    전역 입력이므로 호출 전 반드시 assert_focus 로 검증할 것.
    """
    for ch in text:
        vk = windll.user32.VkKeyScanW(ord(ch)) & 0xFF
        scan = windll.user32.MapVirtualKeyW(vk, 0)
        windll.user32.keybd_event(vk, scan, 0, 0)
        time.sleep(0.03)
        windll.user32.keybd_event(vk, scan, KEYEVENTF_KEYUP, 0)
        time.sleep(pause)


def clear_field() -> None:
    """현재 캐럿이 있는 칸을 비운다. 클릭 시 값이 전체 선택되므로 Delete 로 지운다."""
    for vk in (win32con.VK_DELETE,) * 3 + (win32con.VK_BACK,) * 12:
        scan = windll.user32.MapVirtualKeyW(vk, 0)
        windll.user32.keybd_event(vk, scan, 0, 0)
        time.sleep(0.02)
        windll.user32.keybd_event(vk, scan, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)


# --------------------------------------------------------------------------- #
# 캡처
# --------------------------------------------------------------------------- #

def grab_window(hwnd: int) -> Image.Image:
    """창을 비트맵으로 캡처해 PIL 이미지로 돌려준다. 파일로 저장하지 않는다."""
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    hdc = win32gui.GetWindowDC(hwnd)
    mfc = win32ui.CreateDCFromHandle(hdc)
    sav = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc, w, h)
    sav.SelectObject(bmp)
    ok = windll.user32.PrintWindow(hwnd, sav.GetSafeHdc(), 2)
    info = bmp.GetInfo()
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                           bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    win32gui.DeleteObject(bmp.GetHandle())
    sav.DeleteDC()
    mfc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hdc)
    if not ok:
        # 원인을 구분해서 말한다. 예전 메시지는 무조건 "관리자 권한을 확인하라"
        # 였는데, 실제로 관리자 권한으로 돌려도 같은 실패가 났다. 그때의 원인은
        # HTS 가 켜져 있지만 **로그인이 안 된 상태**여서 창이 그려지지 않은 것이었다.
        # 잘못된 원인을 지목하는 오류 메시지는 없는 것보다 나쁘다.
        why = []
        if windll.user32.IsIconic(hwnd):
            why.append("창이 최소화돼 있다")
        if w <= 1 or h <= 1:
            why.append(f"창 크기가 {w}x{h} 다 — 그려지지 않은 상태")
        if not win32gui.IsWindowVisible(hwnd):
            why.append("창이 숨겨져 있다")
        title = win32gui.GetWindowText(hwnd)
        root = win32gui.GetAncestor(hwnd, 2)
        stt, root_title = session_state(root)
        if stt == "locked":
            why.append(f"HTS 가 화면잠금 상태다 (메인 창 {root_title!r}) — 잠금을 해제할 것")
        elif stt == "no_marker":
            why.append(f"메인 창에 모의 표식이 없다 ({root_title!r}) — 참고 정보")
        detail = "; ".join(why) if why else "원인 불명"
        raise HtsError(
            f"창 캡처에 실패했다(PrintWindow=0, hwnd={hwnd}, {w}x{h}, 제목 {title!r}). "
            f"추정 원인: {detail}. "
            "싸이칸플러스에 모의투자로 로그인하고 주식주문(1200) 화면을 열어둘 것. "
            "그래도 안 되면 관리자 권한 여부를 확인할 것."
        )
    return img


def capture(hwnd: int, name: str, crop: tuple | None = None) -> Path:
    img = grab_window(hwnd)
    if crop:
        x1, y1, x2, y2 = crop
        img = img.crop((max(0, x1), max(0, y1), min(x2, img.width), min(y2, img.height)))
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"{name}.png"
    img.save(path)
    return path


# --------------------------------------------------------------------------- #
# 어댑터
# --------------------------------------------------------------------------- #

@dataclass
class SubmitResult:
    """전송 결과. bool 하나로는 '눌렀다' 와 '접수됐다' 를 구분할 수 없다."""

    ok: bool
    status: str          # dry_run | submitted | modal | not_accepted | unknown
    note: str
    screenshot: str | None = None

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class FillResult:
    side: str
    code: str
    qty: int
    price: int
    market_order: bool
    screenshot: str
    submitted: bool
    status: str = ""
    note: str = ""


class OrderAdapter:
    """주문 입력·전송.

    dry_run=True 이면 입력까지만 하고 전송 버튼을 누르지 않는다. 기본값이 True 인 것은
    의도적이다. 화면 인식이 어긋난 상태로 전송하면 되돌릴 수 없다.

    좌표는 매 작업 직전에 화면에서 가격칸(노란 박스)을 찾아 계산한다.
    폼 위치가 고정이 아니기 때문이다(좌측 패널 폭, 창 크기에 따라 이동).
    """

    def __init__(self, dry_run: bool = True):
        enable_dpi()
        self.dry_run = dry_run
        self.pid = 0
        self.main = 0
        self.order = 0
        self.layout: dict = {}
        self.symbol_edit = 0
        self.qty_edit: int | None = None
        self.price_edit: int | None = None
        self.symbol_readback = ""
        #: 입력 직전 각 칸의 픽셀 해시. 입력 후 값과 비교해 "정말 들어갔는지" 본다.
        self.field_hash_before: dict[str, int | None] = {}
        self.expected_account = ""

    #: 수량·가격 칸을 읽지 못했을 때에도 전송을 허용할지. 기본은 불허다.
    #: 두 칸은 좌표 보간으로 찾으므로 과거 오입력 사고가 전부 여기서 났다.
    allow_unverified_fields = False

    def connect(self) -> "OrderAdapter":
        self.pid = axis_pid()
        if not self.pid:
            raise HtsError("axis.exe 가 실행 중이 아니다. 싸이칸플러스를 켜고 모의투자로 로그인할 것.")
        self.main = find_main(self.pid)
        ensure_main_size(self.main)
        try:
            self.order = find_order_window(self.pid)
            pin_order_window(self.order)
            self.refresh_layout()
        except (HtsError, AnchorError):
            # 주문 화면이 없거나 폼을 못 찾으면 화면 1200 을 열고 한 번 더 시도한다.
            # 무인 운영 중 HTS 가 재시작되면 화면이 닫혀 있을 수 있다.
            open_screen(self.main, "1200")
            self.order = find_order_window(self.pid)
            pin_order_window(self.order)
            self.refresh_layout()
        return self

    def refresh_layout(self) -> dict:
        """폼 좌표를 실측 두 점으로 확정한다.

        AnchorError 는 HtsError 로 감싸 올린다. 원래 별개 예외라 호출측의
        `except HtsError` 를 그냥 통과해 프로세스가 알림 없이 죽었다. 폼이 가려지는
        것은 가장 흔한 실패라 반드시 잡혀야 한다.

        추측을 최대한 없앤 구조다.
          종목칸 — 실제 Edit 컨트롤 좌표 (열거로 확정)
          가격칸 — 화면의 노란 박스 (색으로 확정)
          수량칸 — 위 둘 **사이를 보간**. 세 칸이 같은 열에 등간격으로 놓여 있으므로
                   양 끝을 알면 가운데는 계산이 아니라 결정이다.

        오프셋 외삽으로 종목칸을 잡았다가 클릭이 빗나가 **종목이 안 바뀐 채
        수량·가격만 들어간** 사고가 있었다. 실전송이었다면 엉뚱한 종목을 샀다.
        """
        try:
            shot = grab_window(self.order)
            self.layout = build_layout(find_price_box(shot), shot)
        except AnchorError as exc:
            raise HtsError(f"폼 위치를 찾지 못했다: {exc}") from exc
        self.symbol_edit, sym_rect = find_symbol_edit(self.order, self.layout["price_rect"])
        self.layout["symbol_rect"] = sym_rect

        # 수량칸 = 종목칸과 가격칸의 중간 행
        sx1, sy1, sx2, sy2 = sym_rect
        px1, py1, px2, py2 = self.layout["price_rect"]
        mid_y = (sy1 + py1) // 2
        h = sy2 - sy1
        self.layout["qty_rect"] = (sx1, mid_y, sx2, mid_y + h)

        # 보간으로 잡은 두 칸의 실제 컨트롤을 해석해 둔다. 잡히면 전송 직전에
        # 값을 읽어 대조할 수 있고, 못 잡으면 그 사실 자체를 기록해 검증 없이
        # 전송되는 일을 막는다.
        self.qty_edit = find_edit_at(self.order, self.layout["qty_rect"])
        self.price_edit = find_edit_at(self.order, self.layout["price_rect"])
        return self.layout

    def read_account(self) -> str:
        """주문창 상단의 계좌·필명 문자열. 모의투자 계좌인지 확인하는 데 쓴다."""
        best = ""
        l, t, _, _ = win32gui.GetWindowRect(self.order)

        def cb(h, _):
            nonlocal best
            if win32gui.GetClassName(h) != "Edit":
                return
            txt = read_edit(h)
            if "-" in txt and len(txt) > len(best):
                best = txt

        win32gui.EnumChildWindows(self.order, cb, None)
        return best

    # -- 내부 -------------------------------------------------------------- #

    def _field_hash(self, key: str) -> int | None:
        """폼에서 해당 칸 영역의 픽셀 요약. 값을 읽을 수 없는 칸의 유일한 관측 수단."""
        try:
            rect = self.layout.get(key)
            if not rect or len(rect) != 4:
                return None
            img = grab_window(self.order).crop(tuple(int(v) for v in rect))
            return hash(img.tobytes())
        except (HtsError, OSError, ValueError):
            return None

    def _click(self, key: str) -> None:
        v = self.layout[key]
        if len(v) == 4:
            real_click(self.order, self.main, (v[0] + v[2]) // 2, (v[1] + v[3]) // 2)
        else:
            real_click(self.order, self.main, *v)

    def _enter_field(self, key: str, text: str, attempts: int = 3) -> None:
        """칸을 클릭해 캐럿을 두고 비운 뒤 스캔코드로 입력한다.

        클릭과 키 입력 모두 전역 입력이라 중간에 다른 앱이 포커스를 가져가면
        입력이 엉뚱한 창으로 샌다. 그래서 한 단계라도 포커스가 어긋나면 그 시도를
        버리고 **클릭부터 다시** 한다. 클릭 없이 타이핑만 재개하면 캐럿이 어느 칸에
        있는지 보장할 수 없기 때문이다.
        """
        last: Exception | None = None
        for _ in range(attempts):
            try:
                self._click(key)
                assert_focus(self.main)
                clear_field()
                assert_focus(self.main)
                # 기준선은 **비운 직후**에 찍는다.
                #
                # 예전에는 비우기 전에 찍었다. 그러면 넣으려는 값이 이미 그 칸에
                # 있던 값과 같을 때 입력 전후 해시가 같아져, 정상 입력을 "칸을
                # 빗나갔다"로 오판한다. 분할 매도가 정확히 그 경우다 - 세 조각의
                # 수량이 모두 같아서 1/3 만 나가고 2/3, 3/3 이 거부됐다.
                # 매도 미체결은 그날 리밸런싱 전체를 무산시키는 사건이다.
                #
                # 빈 칸과 값이 든 칸은 항상 다르므로, 이 기준선이면 "입력이
                # 실제로 들어갔는가"를 값과 무관하게 판정한다.
                self.field_hash_before[key] = self._field_hash(key)
                type_scan(text)
                assert_focus(self.main)
                time.sleep(0.3)
                return
            except FocusError as exc:
                last = exc
                raise_window(self.main)
                time.sleep(0.5)
        raise FocusError(
            f"'{key}' 입력 중 포커스를 {attempts}회 놓쳤다. 입력을 신뢰할 수 없어 중단한다. "
            f"다른 앱이 포커스를 가져가고 있는지 확인할 것. 마지막 상태: {last}"
        )

    def _ensure_auto_price_off(self) -> None:
        img = grab_window(self.order)
        if is_checked(img, self.layout["auto_price_checkbox"]):
            assert_focus(self.main)
            self._click("auto_price_checkbox")
            time.sleep(0.4)
            img = grab_window(self.order)
            if is_checked(img, self.layout["auto_price_checkbox"]):
                raise HtsError(
                    "'자동(현재가)' 체크를 해제하지 못했다. 가격이 현재가로 강제되므로 중단한다."
                )

    # -- 공개 API ---------------------------------------------------------- #

    def select_side(self, side: str) -> None:
        self.refresh_layout()
        self._click("tab_buy" if side.upper() == "BUY" else "tab_sell")
        time.sleep(0.6)

    def fill(self, code: str, qty: int, price: int, market_order: bool = False) -> Path:
        """종목·수량·가격을 채운다. 전송하지 않는다."""
        if qty <= 0:
            raise ValueError(f"수량이 0 이하다: {qty}")
        if not market_order and price <= 0:
            raise ValueError(f"지정가 주문인데 가격이 0 이하다: {price}")

        # 세 칸 모두 스캔코드 키 입력으로 통일한다.
        #
        # 종목만 WM_CHAR 로 넣었을 때 글자는 들어가지만 앱의 종목 조회가 돌지 않아
        # **종목명과 시세가 이전 종목 그대로 남는** 사고가 났다(코드는 000660 인데
        # 이름은 삼성전자). 그대로 전송하면 엉뚱한 종목을 주문하게 된다.
        if not raise_window(self.main):
            raise FocusError("HTS 를 포그라운드로 올리지 못했다. 입력하지 않는다.")
        self.refresh_layout()

        self._enter_field("symbol_rect", code)
        time.sleep(1.2)

        # 종목이 실제로 바뀌었는지 확인한다. 이 검증이 없어서 이전 종목 그대로
        # 주문될 뻔했다. 값을 읽을 수 있을 때는 반드시 대조한다.
        got = read_edit(self.symbol_edit)
        self.symbol_readback = got
        if got != code:
            raise HtsError(
                f"종목 입력이 반영되지 않았다: 기대 {code}, 화면 {got!r}. "
                "엉뚱한 종목을 주문할 위험이 있어 중단한다."
            )

        # 종목을 넣으면 폼이 다시 그려지므로 좌표를 갱신한다
        self.refresh_layout()
        self._enter_field("qty_rect", str(qty))

        if market_order:
            assert_focus(self.main)
            self._click("market_checkbox")
        else:
            # '자동(현재가)' 가 켜져 있으면 가격이 현재가로 강제되어 지정가 주문이
            # 의도와 달라진다. 상태를 읽을 API 가 없으므로 화면에서 확인하고 끈다.
            self._ensure_auto_price_off()
            self._enter_field("price_rect", str(price))
        time.sleep(0.5)
        self._check_amount(qty, price, market_order)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return capture(self.order, f"order_{code}_{stamp}", self.layout["form_crop"])

    #: 닫아도 되는 HTS 자체 창. **제목이 정확히 이것일 때만** 닫는다.
    #: '알람' 은 장운영알람 패널이다 — 08:00, 08:30, 15:20 같은 장 운영 시각에
    #: HTS 가 스스로 띄우는 비모달 알림창이고, 하필 15:20 은 우리 매수 시각이다.
    #: 2026-09-07 드라이런이 이 창 하나 때문에 3건 전부 막혔다.
    BENIGN_POPUP_TITLES = ("알람",)

    def dismiss_benign_popups(self) -> list[str]:
        """무해하다고 아는 HTS 창을 닫는다. 닫은 제목 목록을 돌려준다.

        전역 입력을 쓰지 않는다. 그 창 핸들에 WM_CLOSE 를 보낼 뿐이라 포커스가
        어디 있든 다른 창에는 아무 일도 없다. 모르는 대화상자는 건드리지 않는다 —
        그건 여전히 전송을 막는 사유다. 무엇이 떠 있었는지는 로그로 남긴다.
        """
        closed: list[str] = []
        for hwnd, tag in self._popups():
            title = tag.split("|", 1)[-1].strip()
            if title not in self.BENIGN_POPUP_TITLES:
                continue
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                closed.append(title)
            except Exception as exc:                    # UIPI 등. 못 닫으면 아래 검사가 막는다
                print(f"  팝업 '{title}' 을 닫지 못했다: {exc}")
        if closed:
            time.sleep(0.4)
            print(f"  HTS 알림창 닫음: {', '.join(closed)}")
        return closed

    def _popups(self) -> list[tuple[int, str]]:
        """HTS 프로세스가 띄운 팝업/대화상자 목록."""
        out: list[tuple[int, str]] = []

        def cb(h, _):
            if not win32gui.IsWindowVisible(h):
                return
            try:
                _, pid = win32process.GetWindowThreadProcessId(h)
            except Exception:
                return
            if pid != self.pid or h in (self.main, self.order):
                return
            cls = win32gui.GetClassName(h)
            txt = win32gui.GetWindowText(h)
            if cls == "#32770" or win32gui.GetWindow(h, win32con.GW_OWNER):
                out.append((h, f"{cls}|{txt}"))

        win32gui.EnumWindows(cb, None)
        return out

    def submit(self, expect: dict | None = None) -> "SubmitResult":
        """주문 전송. dry_run 이면 누르지 않는다.

        전송은 되돌릴 수 없으므로 그 직전에 화면 상태를 다시 확인하고,
        **누른 뒤에도 결과를 확인한다.**

        예전에는 버튼을 클릭한 뒤 무조건 True 를 돌려줬다. 그러면 클릭이
        빗나갔거나 창이 거부했거나 확인 모달이 떠서 접수되지 않은 경우에도
        "전송 성공" 으로 기록된다. 자동매매에서 가장 나쁜 실패 형태다 —
        틀렸다는 사실조차 남지 않아 다음 주문이 잘못된 전제 위에서 실행된다.
        """
        # 전송 전 검사는 **드라이런에서도** 돈다.
        #
        # 예전에는 dry_run 이 _preflight 보다 먼저 반환해서, 드라이런이 계좌·모의
        # 세션·기대값 대조를 한 번도 실행하지 않았다. 그러면 드라이런 통과가
        # "전송 직전까지 정상"을 뜻하지 않는다 — 실제로 계좌 검사가 100% 주문을
        # 막던 버그가 드라이런에서 안 잡힌 적이 있다. 드라이런과 실전송의 차이는
        # **마지막 클릭 하나**여야 한다.
        if expect:
            self._preflight(expect)

        if self.dry_run:
            return SubmitResult(False, "dry_run",
                                "드라이런 — 전송 전 검사까지 통과, 클릭만 생략했다.", None)

        before = {h for h, _ in self._popups()}
        # 접수 판정용 기준선. 전송에 성공하면 폼이 비워져 이 칸의 글자 수가
        # 0(또는 '0' 한 글자)으로 떨어진다. 수량 칸은 자식 창이 없어 못 읽지만
        # 주문금액 칸은 이미 자릿수 대조에 쓰고 있으므로 그대로 재사용한다.
        amt_before = self._amount_glyphs()

        assert_focus(self.main)
        self._click("submit_button")
        time.sleep(1.2)

        stamp = datetime.now().strftime("%H%M%S")
        shot = capture(self.order, f"after_submit_{(expect or {}).get('code','na')}_{stamp}")

        # (1) 새 팝업이 떴는가. 확인 모달이 떠 있으면 주문은 아직 접수되지 않았고,
        #     더 중요하게는 **다음 주문의 키 입력이 전부 그 모달로 들어간다.**
        new_popups = [(h, t) for h, t in self._popups()
                      if h not in before
                      and t.split("|", 1)[-1].strip() not in self.BENIGN_POPUP_TITLES]
        if new_popups:
            titles = ", ".join(t for _, t in new_popups)
            return SubmitResult(
                False, "modal",
                f"전송 후 팝업이 떴다: {titles}. 접수 여부를 알 수 없고 이 창이 "
                f"이후 입력을 가로채므로 중단한다.", str(shot))

        # (2) 전송에 성공하면 주문 폼의 수량 칸이 비워진다. 이걸 접수 신호로 쓴다.
        #     읽을 수 없으면 성공으로 단정하지 않고 unknown 으로 남긴다.
        if self.qty_edit:
            left = _digits(read_edit(self.qty_edit))
            if left in ("", "0"):
                return SubmitResult(True, "submitted", "수량 칸이 비었다 — 접수로 판단.", str(shot))
            if expect and left == _digits(str(expect.get("qty", ""))):
                return SubmitResult(
                    False, "not_accepted",
                    f"전송 후에도 수량 {left} 이 그대로 남아 있다. 접수되지 않은 것으로 "
                    f"판단해 중단한다.", str(shot))
            return SubmitResult(False, "unknown",
                                f"전송 후 수량 칸 값이 예상 밖이다: {left!r}", str(shot))

        # (3) 수량 칸을 못 읽는 환경(현재가 그렇다)에서는 **주문금액 칸**으로 판정한다.
        #
        #     이 판정이 없으면 매 주문이 unknown 으로 남는다. 그러면 접수 여부를
        #     모르니 자동 재시도도 못 하고, 무인 운영이 매일 사람 확인에서 멈춘다.
        #     접수되면 폼이 비워지므로 금액 칸 글자 수가 확 줄어드는 것이 신호다.
        #     기준선을 못 읽었거나 변화가 애매하면 여전히 unknown 으로 남긴다 —
        #     모르는 것을 안다고 하지 않는다.
        # 금액 칸을 **여러 번** 읽는다.
        #
        # 전송 직후에는 폼이 아직 다시 그려지는 중이라 한 번의 판독이 실패하거나
        # 어중간한 값을 줄 수 있다. 그걸 그대로 unknown 으로 남기면 그 주문은
        # 자동 재시도 대상에서 빠지고 사람이 HTS 주문내역을 봐야 한다 —
        # 무인 운영에서 가장 비싼 결과다. 안정될 때까지 몇 번 더 본다.
        amt_after = None
        for _ in range(6):
            v = self._amount_glyphs()
            if v is not None and (v <= 1 or v == amt_before):
                amt_after = v          # 확정적인 값이면 즉시 채택
                break
            if v is not None:
                amt_after = v
            time.sleep(0.4)

        if amt_before is not None and amt_after is not None:
            if amt_after <= 1 and amt_before > 1:
                return SubmitResult(True, "submitted",
                                    f"주문금액 칸이 비워졌다({amt_before}자 -> {amt_after}자) "
                                    "— 접수로 판단.", str(shot))
            if amt_after == amt_before and amt_before > 1:
                return SubmitResult(False, "not_accepted",
                                    f"주문금액 칸이 그대로다({amt_before}자). 접수되지 "
                                    "않은 것으로 판단해 중단한다.", str(shot))
            return SubmitResult(False, "unknown",
                                f"주문금액 칸 변화가 애매하다: {amt_before}자 -> {amt_after}자",
                                str(shot))

        return SubmitResult(False, "unknown",
                            "수량 칸과 주문금액 칸을 모두 읽지 못해 접수 여부를 "
                            "확인하지 못했다.", str(shot))

    def _amount_glyphs(self) -> int | None:
        """주문금액 칸의 글자 수. 읽지 못하면 None."""
        try:
            rect = amount_rect(self.layout["price_rect"])
            return count_amount_glyphs(grab_window(self.order), rect)
        except (HtsError, KeyError, ValueError, OSError):
            return None

    def _check_amount(self, qty, price, market_order=False) -> None:
        """주문금액 자릿수를 대조한다. **경고만 남기고 막지 않는다.**

        주문금액 = 수량 x 가격 이라 이 한 값이 두 칸을 동시에 교차검증한다.
        값 자체는 읽지 못한다(자식 창이 없고, 캡처에서 0~9 폰트 템플릿을 다 모을 수
        없었다 — 4 와 8 이 없었다). 대신 **글자 수**는 폰트 지식 없이 셀 수 있고,
        그것만으로 주된 실패 모드가 잡힌다 — 키 입력이 하나 빠져 3,035 가 3,03 이
        되면 금액이 32,656,600 -> 326,080 으로 자릿수가 8 에서 6 으로 바뀐다.

        차단이 아니라 경고인 이유: 오탐 한 번이 그날 매매를 통째로 날린다.
        1주일 관측해 신뢰가 서면 차단으로 승격한다.
        """
        try:
            want_amt = int(qty or 0) * int(price or 0)
            if want_amt <= 0 or market_order:
                return
            rect = amount_rect(self.layout["price_rect"])
            got_n = count_amount_glyphs(grab_window(self.order), rect)
            want_n = len(str(want_amt))
            if got_n is None:
                print("  주문금액 칸을 읽지 못했다 (경고).")
            elif got_n != want_n:
                print(f"  경고: 주문금액 자릿수 불일치 — 기대 {want_amt:,} ({want_n}자), "
                      f"화면 {got_n}자. 수량·가격 입력이 잘렸을 수 있다. "
                      "캡처를 반드시 확인할 것.")
            else:
                print(f"  주문금액 {want_amt:,} ({want_n}자) 자릿수 일치")
        except (HtsError, KeyError, ValueError, OSError) as exc:
            print(f"  주문금액 대조 생략: {exc}")

    def _preflight(self, expect: dict) -> None:
        """전송 직전 검사. 실패하면 예외를 던져 전송을 막는다."""
        problems: list[str] = []

        # (1) 의도한 계좌인가. 실계좌 오발주는 유일하게 되돌릴 수 없는 사고다.
        #
        # 판정 기준은 **계좌번호**다. 제목의 '모의' 표식은 재로그인 후 사라진 적이
        # 있어(2026-09-05) 그것만 보면 정상 세션을 막고, 반대로 표식이 있다는 이유로
        # 엉뚱한 모의계좌를 통과시킬 수도 있다. 계좌번호 대조는 둘 다 막는다.
        title = win32gui.GetWindowText(self.main)
        acct_now = self.read_account()
        want_acct = expect.get("account") or self.expected_account
        if want_acct:
            if not acct_now:
                problems.append("주문창에서 계좌번호를 읽지 못했다. 확인 없이 전송하지 않는다.")
            elif want_acct not in acct_now:
                problems.append(
                    f"계좌 불일치: 기대 {want_acct}, 화면 {acct_now!r}. "
                    "의도한 계좌가 아니면 절대 전송하지 않는다."
                )
        else:
            problems.append(
                "기대 계좌번호가 설정돼 있지 않다(config.contest.mock_account). "
                "실계좌 오발주를 막을 수 없으므로 전송하지 않는다."
            )
        if MOCK_MARKER not in title:
            print(f"  참고: 메인 창 제목에 '{MOCK_MARKER}' 표식이 없다 ({title!r}). "
                  "계좌번호로 검증한다.")

        # (2) 종목이 의도한 것인가
        got = read_edit(self.symbol_edit)
        if got != expect["code"]:
            problems.append(f"종목 불일치: 기대 {expect['code']}, 화면 {got!r}")

        # (4) 시장가 체크가 의도와 같은가
        img = grab_window(self.order)
        market_on = is_checked(img, self.layout["market_checkbox"])
        if market_on != bool(expect.get("market_order")):
            problems.append(
                f"시장가 체크 상태가 의도와 다르다: 화면 {market_on}, 기대 {bool(expect.get('market_order'))}"
            )

        # (5) 자동(현재가)이 꺼져 있는가 (지정가 주문일 때)
        if not expect.get("market_order"):
            if is_checked(img, self.layout["auto_price_checkbox"]):
                problems.append("'자동(현재가)' 가 켜져 있다. 지정가가 현재가로 덮인다.")

        # (6) **수량이 의도한 값인가.** 수량 칸은 좌표 보간으로 찾으므로 세 칸 중
        #     가장 신뢰도가 낮다. 종목만 검증하고 수량을 안 보면, 종목은 맞는데
        #     수량이 이전 값 그대로거나 자릿수가 잘린 주문이 그대로 나간다.
        # 수량·가격 칸에는 **자식 창이 없다.** 2026-09-05 실측: 주문창의 Edit 은
        # 4개뿐이고 비-readonly 2개가 전부 종목칸이다. 즉 WM_GETTEXT 로 값을
        # 되읽을 방법이 원리적으로 없다. 컨트롤 존재를 요구하는 검사는 통과 불가라
        # 그대로 두면 영원히 주문이 안 나간다.
        #
        # 대신 **입력이 그 칸에 실제로 들어갔는지**를 픽셀 변화로 본다.
        # 과거 사고는 전부 "칸을 빗나가 입력이 다른 곳으로 갔다" 형태였고
        # (수량·가격이 이전 값 그대로 남음) 그건 이 검사로 잡힌다.
        #
        # 잡지 못하는 것은 **올바른 칸에 틀린 숫자를 넣은 경우**다. 그건 스캔코드
        # 입력 경로가 결정적이라는 것과 전송 캡처를 사람이 보는 것으로만 방어된다.
        # 이 한계를 숨기지 않고 적어둔다.
        for key, label in (("qty_rect", "수량"), ("price_rect", "가격")):
            if key == "price_rect" and expect.get("market_order"):
                continue
            before = self.field_hash_before.get(key)
            after = self._field_hash(key)
            if before is None or after is None:
                problems.append(f"{label} 칸 상태를 확인하지 못했다.")
            elif before == after:
                problems.append(
                    f"{label} 칸이 입력 전후로 전혀 바뀌지 않았다. "
                    "입력이 다른 곳으로 갔을 수 있어 전송하지 않는다.")

        self._check_amount(expect.get("qty"), expect.get("price"),
                           expect.get("market_order"))

        # (8) 전송 전에 이미 팝업이 떠 있으면 클릭·입력이 그쪽으로 새어나간다.
        #     무해하다고 아는 창은 먼저 닫는다. 남는 것이 있으면 그때 막는다.
        self.dismiss_benign_popups()
        stale = self._popups()
        if stale:
            problems.append(
                "주문창 위에 팝업이 떠 있다: " + ", ".join(t for _, t in stale))

        # (9) 전송 버튼 좌표가 주문창 안에 있는가 (앵커 오검출 방어)
        bx, by = self.layout["submit_button"]
        rc = win32gui.GetWindowRect(self.order)
        w, h = rc[2] - rc[0], rc[3] - rc[1]
        if not (0 < bx < w and 0 < by < h):
            problems.append(f"전송 버튼 좌표 ({bx},{by}) 가 주문창({w}x{h}) 밖이다. 앵커 오검출.")

        if problems:
            shot = capture(self.order, f"preflight_fail_{expect['code']}_"
                                       f"{datetime.now():%H%M%S}", self.layout["form_crop"])
            raise HtsError(
                "전송 전 검사 실패 — 주문을 보내지 않았다:\n  "
                + "\n  ".join(problems)
                + f"\n  캡처: {shot}"
            )

    def place(self, side: str, code: str, qty: int, price: int,
              market_order: bool = False) -> FillResult:
        # 창을 먼저 앞으로 올린다. select_side 의 탭 클릭도 전역 마우스 입력이라
        # 포커스가 HTS 에 있어야 한다. 이 호출이 fill() 에만 있어서 탭 클릭 단계에서
        # 전부 거부된 적이 있다.
        if not raise_window(self.main):
            raise FocusError("HTS 를 포그라운드로 올리지 못했다. 입력하지 않는다.")
        self.select_side(side)
        shot = self.fill(code, qty, price, market_order)
        res = self.submit({"code": code, "qty": qty, "price": price,
                           "market_order": market_order,
                           "account": self.expected_account})
        return FillResult(side.upper(), code, qty, price, market_order, str(shot),
                          res.ok, res.status, res.note)
