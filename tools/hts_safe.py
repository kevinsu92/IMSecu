"""HTS 자동화 안전 계층.

배경: 전역 키 입력(SendInput/send_keys)은 그 순간 키보드 포커스를 가진 창으로
들어간다. 2026-09-05 검증 중 HTS 대신 다른 애플리케이션 입력창에 문자가 들어가는
사고가 실제로 발생했다. 주문 자동화에서 이런 오입력은 치명적이므로, 이 모듈은
두 가지 원칙을 강제한다.

  1. 기본은 전역 입력을 쓰지 않는다. 대상 컨트롤에 직접 메시지를 보낸다.
  2. 전역 입력이 불가피하면 직전에 포커스를 검증하고, 어긋나면 예외를 던진다.
"""
from __future__ import annotations

import subprocess
import time
from ctypes import windll

import win32api
import win32con
import win32gui
import win32process


class FocusError(RuntimeError):
    """의도한 창이 포그라운드가 아닐 때. 절대 무시하지 말 것."""


def enable_dpi() -> None:
    try:
        windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        windll.user32.SetProcessDPIAware()


def axis_pid() -> int:
    raw = subprocess.run(["tasklist", "/FI", "IMAGENAME eq axis.exe", "/FO", "CSV", "/NH"],
                         capture_output=True).stdout or b""
    # tasklist 는 콘솔 코드페이지로 쓴다. UTF-8 모드에서 text=True 로 받으면
    # axis.exe 가 없을 때의 한국어 안내문에서 깨져 stdout 이 None 이 된다.
    # 우리가 읽는 CSV 필드는 ASCII 라 replace 로 충분하다.
    out = raw.decode("cp949", "replace")
    for line in out.splitlines():
        parts = [x.strip('" ') for x in line.split('","')]
        if parts and parts[0].lower().startswith("axis"):
            return int(parts[1])
    return 0


def main_window(pid: int) -> int:
    best = []

    def cb(h, _):
        _, p = win32process.GetWindowThreadProcessId(h)
        if p == pid and win32gui.IsWindowVisible(h):
            rc = win32gui.GetWindowRect(h)
            best.append(((rc[2] - rc[0]) * (rc[3] - rc[1]), h))

    win32gui.EnumWindows(cb, None)
    if not best:
        raise FocusError("HTS 창을 찾지 못했다")
    return max(best)[1]


def raise_window(hwnd: int, attempts: int = 6) -> bool:
    """창을 포그라운드로 올린다. 성공 여부를 정직하게 돌려준다."""
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
            time.sleep(0.25)
            if windll.user32.GetForegroundWindow() == hwnd:
                return True
    finally:
        for tid in tids:
            if tid and tid != cur:
                windll.user32.AttachThreadInput(cur, tid, False)
    return False


def assert_focus(hwnd: int) -> None:
    """전역 입력 직전에 반드시 호출한다. 어긋나면 입력하지 않고 멈춘다."""
    fg = windll.user32.GetForegroundWindow()
    if fg == hwnd:
        return
    # 자식 창이 포그라운드일 수도 있다. 루트가 같으면 허용한다.
    root = windll.user32.GetAncestor(fg, 2)  # GA_ROOT
    if root == hwnd:
        return
    raise FocusError(
        f"포커스가 HTS(hwnd={hwnd})가 아니라 hwnd={fg} "
        f"({win32gui.GetWindowText(fg)!r})에 있다. 오입력 방지를 위해 중단한다."
    )


# --------------------------------------------------------------------------- #
# 메시지 기반 조작 — 전역 입력을 쓰지 않으므로 다른 창에 새어나갈 수 없다
# --------------------------------------------------------------------------- #

def set_text(ctrl_hwnd: int, text: str) -> str:
    """컨트롤에 값을 넣고 실제 반영된 값을 돌려준다.

    WM_SETTEXT 만으로는 앱이 변경을 인지하지 못하는 경우가 있어
    EN_CHANGE 통지를 부모에게 함께 보낸다.
    """
    win32gui.SendMessage(ctrl_hwnd, win32con.WM_SETTEXT, 0, text)
    parent = win32gui.GetParent(ctrl_hwnd)
    ctrl_id = windll.user32.GetDlgCtrlID(ctrl_hwnd)
    if parent:
        win32gui.SendMessage(parent, win32con.WM_COMMAND,
                             (win32con.EN_CHANGE << 16) | (ctrl_id & 0xFFFF), ctrl_hwnd)
    return win32gui.GetWindowText(ctrl_hwnd)


def click_control(ctrl_hwnd: int) -> None:
    """버튼을 메시지로 누른다. 마우스 커서를 움직이지 않는다."""
    win32gui.SendMessage(ctrl_hwnd, win32con.BM_CLICK, 0, 0)


def post_key(ctrl_hwnd: int, vk: int) -> None:
    """특정 컨트롤에만 키를 보낸다. 전역 입력이 아니다."""
    win32api.PostMessage(ctrl_hwnd, win32con.WM_KEYDOWN, vk, 0)
    time.sleep(0.05)
    win32api.PostMessage(ctrl_hwnd, win32con.WM_KEYUP, vk, 0)


def controls(hwnd: int, max_depth: int = 4) -> list[dict]:
    """창의 컨트롤 트리를 창 기준 상대좌표로 반환한다.

    EnumChildWindows 는 직계 자식이 아니라 **모든 후손**을 열거한다.
    따라서 추가로 재귀하면 같은 컨트롤이 여러 번 잡힌다(초기 구현의 버그).
    깊이는 부모 사슬을 거슬러 올라가 계산한다.
    """
    l, t, _, _ = win32gui.GetWindowRect(hwnd)
    rows: list[dict] = []
    seen: set[int] = set()

    def depth_of(child: int) -> int:
        d, cur = 0, child
        while cur and cur != hwnd and d < 16:
            cur = win32gui.GetParent(cur)
            d += 1
        return d

    def cb(child, _):
        if child in seen:
            return
        seen.add(child)
        try:
            rc = win32gui.GetWindowRect(child)
            rows.append({
                "hwnd": child,
                "depth": depth_of(child),
                "id": windll.user32.GetDlgCtrlID(child),
                "class": win32gui.GetClassName(child),
                "text": win32gui.GetWindowText(child),
                "rect": [rc[0] - l, rc[1] - t, rc[2] - l, rc[3] - t],
                "visible": bool(win32gui.IsWindowVisible(child)),
            })
        except Exception:
            pass

    win32gui.EnumChildWindows(hwnd, cb, None)
    rows.sort(key=lambda r: (r["rect"][1], r["rect"][0]))
    return rows
