"""화면 검색창에 WM_CHAR 로 직접 문자를 보내 화면을 연다.

전역 키보드/마우스를 전혀 쓰지 않는다. 따라서 다른 창이 포커스를 가지고 있어도
안전하며, 사용자가 동시에 다른 작업을 해도 오입력이 발생하지 않는다.

관리자 권한 필요.
사용법: python tools/hts_search2.py 주식주문
"""
from __future__ import annotations

import json
import sys
import time
from ctypes import windll
from pathlib import Path

import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hts_safe import axis_pid, enable_dpi, main_window  # noqa: E402

enable_dpi()
OUT = Path(__file__).resolve().parents[1] / "state" / "hts_shots"


def snapshot(pid: int) -> dict[int, tuple]:
    seen: dict[int, tuple] = {}

    def walk(h, d=0):
        def cb(c, _):
            if win32gui.IsWindowVisible(c):
                rc = win32gui.GetWindowRect(c)
                if (rc[2] - rc[0]) > 80 and (rc[3] - rc[1]) > 40:
                    seen[c] = (win32gui.GetClassName(c), win32gui.GetWindowText(c), rc)
                if d < 3:
                    walk(c, d + 1)
        win32gui.EnumChildWindows(h, cb, None)

    def cb(h, _):
        _, p = win32process.GetWindowThreadProcessId(h)
        if p == pid and win32gui.IsWindowVisible(h):
            rc = win32gui.GetWindowRect(h)
            seen[h] = (win32gui.GetClassName(h), win32gui.GetWindowText(h), rc)
            walk(h)

    win32gui.EnumWindows(cb, None)
    return seen


def capture(hwnd: int, name: str) -> str | None:
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    if w < 20 or h < 20 or w > 12000 or h > 12000:
        return None
    hdc = win32gui.GetWindowDC(hwnd)
    mfc = win32ui.CreateDCFromHandle(hdc)
    sav = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc, w, h)
    sav.SelectObject(bmp)
    windll.user32.PrintWindow(hwnd, sav.GetSafeHdc(), 2)
    i = bmp.GetInfo()
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{name}.png"
    Image.frombuffer("RGB", (i["bmWidth"], i["bmHeight"]), bmp.GetBitmapBits(True),
                     "raw", "BGRX", 0, 1).save(p)
    win32gui.DeleteObject(bmp.GetHandle())
    sav.DeleteDC()
    mfc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hdc)
    return str(p)


def focus_control(main_hwnd: int, ctrl: int) -> bool:
    """전역 포커스를 훔치지 않고, HTS 스레드 안에서만 키보드 포커스를 옮긴다."""
    cur = windll.kernel32.GetCurrentThreadId()
    tid = windll.user32.GetWindowThreadProcessId(main_hwnd, None)
    if tid and tid != cur:
        windll.user32.AttachThreadInput(cur, tid, True)
    try:
        windll.user32.SetFocus(ctrl)
        return windll.user32.GetFocus() == ctrl
    finally:
        if tid and tid != cur:
            windll.user32.AttachThreadInput(cur, tid, False)


def type_chars(ctrl: int, text: str) -> None:
    """WM_CHAR 로 한 글자씩 보낸다. 한글도 UTF-16 코드 유닛으로 전달된다."""
    for ch in text:
        win32api.PostMessage(ctrl, win32con.WM_CHAR, ord(ch), 0)
        time.sleep(0.05)


def main() -> int:
    term = sys.argv[1] if len(sys.argv) > 1 else "주식주문"
    pid = axis_pid()
    main_hwnd = main_window(pid)

    edits = []

    def cb(h, _):
        if win32gui.GetClassName(h) == "Edit" and win32gui.IsWindowVisible(h):
            rc = win32gui.GetWindowRect(h)
            edits.append((rc[1], h, rc))

    win32gui.EnumChildWindows(main_hwnd, cb, None)
    edits.sort()
    _, edit, erc = edits[0]

    before = snapshot(pid)

    focused = focus_control(main_hwnd, edit)

    # 기존 내용 비우기: 백스페이스를 넉넉히 보낸다
    for _ in range(24):
        win32api.PostMessage(edit, win32con.WM_KEYDOWN, win32con.VK_BACK, 0)
        win32api.PostMessage(edit, win32con.WM_CHAR, 8, 0)
        win32api.PostMessage(edit, win32con.WM_KEYUP, win32con.VK_BACK, 0)
    time.sleep(0.4)

    type_chars(edit, term)
    time.sleep(0.8)
    readback = win32gui.GetWindowText(edit)

    win32api.PostMessage(edit, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
    win32api.PostMessage(edit, win32con.WM_CHAR, 13, 0)
    win32api.PostMessage(edit, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
    time.sleep(3.0)

    after = snapshot(pid)
    new = {h: v for h, v in after.items() if h not in before}
    rows = []
    ordered = sorted(new.items(),
                     key=lambda kv: -((kv[1][2][2] - kv[1][2][0]) * (kv[1][2][3] - kv[1][2][1])))
    for i, (h, v) in enumerate(ordered):
        rows.append({"hwnd": h, "class": v[0], "text": v[1], "rect": list(v[2]),
                     "size": [v[2][2] - v[2][0], v[2][3] - v[2][1]],
                     "shot": capture(h, f"s2_{i}_{h}") if i < 5 else None})

    result = {"term": term, "focused": focused, "readback": readback,
              "new_count": len(new), "new": rows}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "search2.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps({"term": term, "focused": focused, "readback": readback,
                      "new": len(new)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        import traceback
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "search2.json").write_text(
            json.dumps({"error": repr(exc), "traceback": traceback.format_exc()},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(1)
