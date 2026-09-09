"""주문창의 수량·가격 입력 방법을 확정하기 위한 탐색 도구.

배경: 종목 칸만 표준 Edit 이고 수량·가격은 AfxWnd140 커스텀 컨트롤이다.
이런 컨트롤은 포커스를 받을 때 편집용 자식 창(edit-in-place)을 만드는 경우가 많다.
그래서 '포커스 전/후 컨트롤 목록 차이'를 관찰한다.

전역 입력을 쓰지 않는다. 관리자 권한 필요.

사용법
  python tools/hts_field.py list                      주문창 컨트롤 전체
  python tools/hts_field.py focus <hwnd>              해당 컨트롤에 포커스 후 변화 관찰
  python tools/hts_field.py type <hwnd> 123           포커스 후 WM_CHAR 전송, 반영 확인
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hts_safe import axis_pid, controls, enable_dpi  # noqa: E402

enable_dpi()
OUT = Path(__file__).resolve().parents[1] / "state" / "hts_shots"


def find_order_window(pid: int) -> int:
    """주문 화면(MDI 자식)을 찾는다.

    제목으로만 찾으면 안 된다. 화면을 연 직후에는 '[1200] 주식주문' 이지만
    이후 'Window1' 로 바뀌는 것을 확인했다. 따라서 제목 매칭을 먼저 시도하고,
    실패하면 MDIClient 의 가장 큰 자식 창을 쓴다.
    """
    by_title: list[tuple[int, int]] = []
    mdi_children: list[tuple[int, int]] = []

    def main_windows() -> list[int]:
        tops: list[tuple[int, int]] = []

        def cb(h, _):
            _, p = win32process.GetWindowThreadProcessId(h)
            if p == pid and win32gui.IsWindowVisible(h):
                rc = win32gui.GetWindowRect(h)
                tops.append(((rc[2] - rc[0]) * (rc[3] - rc[1]), h))

        win32gui.EnumWindows(cb, None)
        return [h for _, h in sorted(tops, reverse=True)]

    for top in main_windows():
        def cb(h, _):
            try:
                if not win32gui.IsWindowVisible(h):
                    return
                rc = win32gui.GetWindowRect(h)
                area = (rc[2] - rc[0]) * (rc[3] - rc[1])
                if "주식주문" in win32gui.GetWindowText(h):
                    by_title.append((area, h))
            except Exception:
                pass

        win32gui.EnumChildWindows(top, cb, None)

        mdi = win32gui.FindWindowEx(top, 0, "MDIClient", None)
        if mdi:
            def cb2(h, _):
                if win32gui.GetParent(h) != mdi or not win32gui.IsWindowVisible(h):
                    return
                rc = win32gui.GetWindowRect(h)
                mdi_children.append(((rc[2] - rc[0]) * (rc[3] - rc[1]), h))

            win32gui.EnumChildWindows(mdi, cb2, None)

    if by_title:
        return max(by_title)[1]
    if mdi_children:
        return max(mdi_children)[1]
    raise RuntimeError("주문 화면을 찾지 못했다. HTS 에서 화면 1200 을 열 것.")


def set_focus_in_thread(target_hwnd: int, ctrl: int) -> bool:
    """전역 포커스를 훔치지 않고 HTS 스레드 안에서만 포커스를 옮긴다."""
    cur = windll.kernel32.GetCurrentThreadId()
    tid = windll.user32.GetWindowThreadProcessId(target_hwnd, None)
    if tid and tid != cur:
        windll.user32.AttachThreadInput(cur, tid, True)
    try:
        windll.user32.SetFocus(ctrl)
        return windll.user32.GetFocus() == ctrl
    finally:
        if tid and tid != cur:
            windll.user32.AttachThreadInput(cur, tid, False)


def focused_control(target_hwnd: int) -> dict:
    cur = windll.kernel32.GetCurrentThreadId()
    tid = windll.user32.GetWindowThreadProcessId(target_hwnd, None)
    if tid and tid != cur:
        windll.user32.AttachThreadInput(cur, tid, True)
    try:
        f = windll.user32.GetFocus()
        if not f:
            return {}
        rc = win32gui.GetWindowRect(f)
        return {"hwnd": f, "class": win32gui.GetClassName(f),
                "text": win32gui.GetWindowText(f), "rect": list(rc)}
    finally:
        if tid and tid != cur:
            windll.user32.AttachThreadInput(cur, tid, False)


def dump(order: int, tag: str) -> list[dict]:
    rows = controls(order)
    (OUT / f"fields_{tag}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    pid = axis_pid()
    order = find_order_window(pid)
    OUT.mkdir(parents=True, exist_ok=True)

    if mode == "list":
        rows = dump(order, "list")
        vis = [r for r in rows if r["visible"]]
        print(json.dumps({"order_hwnd": order, "total": len(rows), "visible": len(vis)},
                         ensure_ascii=False))
        return 0

    ctrl = int(sys.argv[2])
    before = {r["hwnd"] for r in controls(order)}
    focused = set_focus_in_thread(order, ctrl)
    time.sleep(0.6)
    after_rows = controls(order)
    new = [r for r in after_rows if r["hwnd"] not in before]

    result = {"order_hwnd": order, "ctrl": ctrl, "focus_set": focused,
              "focused_now": focused_control(order), "new_controls": new}

    if mode == "type":
        text = sys.argv[3]
        target = new[0]["hwnd"] if new else ctrl
        for ch in text:
            win32api.PostMessage(target, win32con.WM_CHAR, ord(ch), 0)
            time.sleep(0.06)
        time.sleep(0.6)
        result["typed_into"] = target
        result["readback_target"] = win32gui.GetWindowText(target)
        result["readback_ctrl"] = win32gui.GetWindowText(ctrl)
        result["controls_after_type"] = [
            r for r in controls(order) if r["hwnd"] in {target, ctrl} or r["text"]
        ][:40]

    (OUT / f"field_{mode}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "controls_after_type"},
                     ensure_ascii=False)[:1200])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        import traceback
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "field_error.json").write_text(
            json.dumps({"error": repr(exc), "traceback": traceback.format_exc()},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(1)
