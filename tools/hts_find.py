"""HTS의 모든 창/자식창을 크기 순으로 훑어 주문 화면을 찾는다. 관리자 권한 필요."""
from __future__ import annotations

import json
import subprocess
from ctypes import windll
from pathlib import Path

import win32gui
import win32process
import win32ui
from PIL import Image

try:
    windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    windll.user32.SetProcessDPIAware()

OUT = Path(__file__).resolve().parents[1] / "state" / "hts_shots"


def axis_pids():
    raw = subprocess.run(["tasklist", "/FI", "IMAGENAME eq axis.exe", "/FO", "CSV", "/NH"],
                         capture_output=True).stdout or b""
    # tasklist 는 콘솔 코드페이지로 쓴다. UTF-8 모드에서 text=True 로 받으면
    # axis.exe 가 없을 때의 한국어 안내문에서 깨져 stdout 이 None 이 된다.
    # 우리가 읽는 CSV 필드는 ASCII 라 replace 로 충분하다.
    out = raw.decode("cp949", "replace")
    pids = []
    for line in out.splitlines():
        parts = [p.strip('" ') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower().startswith("axis"):
            pids.append(int(parts[1]))
    return pids


def walk(hwnd, depth=0, max_depth=4, acc=None):
    if acc is None:
        acc = []
    def cb(child, _):
        try:
            rc = win32gui.GetWindowRect(child)
            acc.append({"hwnd": child, "depth": depth, "class": win32gui.GetClassName(child),
                        "text": win32gui.GetWindowText(child), "rect": list(rc),
                        "w": rc[2] - rc[0], "h": rc[3] - rc[1],
                        "visible": bool(win32gui.IsWindowVisible(child))})
            if depth < max_depth:
                walk(child, depth + 1, max_depth, acc)
        except Exception:
            pass
    win32gui.EnumChildWindows(hwnd, cb, None)
    return acc


def capture(hwnd, name):
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    if w < 40 or h < 40 or w > 12000 or h > 12000:
        return None
    hdc = win32gui.GetWindowDC(hwnd)
    mfc = win32ui.CreateDCFromHandle(hdc)
    sav = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap(); bmp.CreateCompatibleBitmap(mfc, w, h); sav.SelectObject(bmp)
    windll.user32.PrintWindow(hwnd, sav.GetSafeHdc(), 2)
    i = bmp.GetInfo()
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{name}.png"
    Image.frombuffer("RGB", (i["bmWidth"], i["bmHeight"]), bmp.GetBitmapBits(True),
                     "raw", "BGRX", 0, 1).save(p)
    win32gui.DeleteObject(bmp.GetHandle()); sav.DeleteDC(); mfc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hdc)
    return str(p)


def main():
    pids = axis_pids()
    tops = []

    def cb(h, _):
        _, pid = win32process.GetWindowThreadProcessId(h)
        if pid in pids:
            rc = win32gui.GetWindowRect(h)
            tops.append({"hwnd": h, "class": win32gui.GetClassName(h),
                         "text": win32gui.GetWindowText(h), "rect": list(rc),
                         "w": rc[2] - rc[0], "h": rc[3] - rc[1],
                         "visible": bool(win32gui.IsWindowVisible(h)), "top": True})

    win32gui.EnumWindows(cb, None)

    all_rows = list(tops)
    for t in tops:
        if t["visible"] and t["w"] > 300:
            all_rows.extend(walk(t["hwnd"]))

    big = [r for r in all_rows if r["visible"] and r["w"] >= 400 and r["h"] >= 300]
    big.sort(key=lambda r: -(r["w"] * r["h"]))

    for i, r in enumerate(big[:8]):
        r["shot"] = capture(r["hwnd"], f"find{i}_{r['hwnd']}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "find.json").write_text(json.dumps({"pids": pids, "big": big[:20], "total": len(all_rows)},
                                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"total": len(all_rows), "big": len(big)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
