"""로그인된 HTS의 창 구조를 덤프한다.

관리자 권한으로 실행할 것. 일반 권한에서는 UIPI에 막혀 아무것도 못 본다.

사용법
  python tools/hts_dump.py                 창 목록 + 각 창 캡처
  python tools/hts_dump.py --hwnd 12345    특정 창의 컨트롤 트리까지 덤프
"""
from __future__ import annotations

import argparse
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


def axis_pids() -> list[int]:
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
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def capture(hwnd: int, name: str) -> str | None:
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    if w < 40 or h < 40 or w > 12000 or h > 12000:
        return None
    hdc = win32gui.GetWindowDC(hwnd)
    mfc = win32ui.CreateDCFromHandle(hdc)
    sav = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc, w, h)
    sav.SelectObject(bmp)
    res = windll.user32.PrintWindow(hwnd, sav.GetSafeHdc(), 2)
    info = bmp.GetInfo()
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                           bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    img.save(path)
    win32gui.DeleteObject(bmp.GetHandle())
    sav.DeleteDC()
    mfc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hdc)
    return str(path) if res else f"{path} (PrintWindow=0, 화면 내용 불완전 가능)"


def controls(hwnd: int) -> list[dict]:
    l, t, _, _ = win32gui.GetWindowRect(hwnd)
    rows = []

    def cb(child, _):
        try:
            rc = win32gui.GetWindowRect(child)
            rows.append({
                "hwnd": child,
                "id": windll.user32.GetDlgCtrlID(child),
                "class": win32gui.GetClassName(child),
                "text": win32gui.GetWindowText(child),
                "rect": [rc[0] - l, rc[1] - t, rc[2] - l, rc[3] - t],
                "visible": bool(win32gui.IsWindowVisible(child)),
            })
        except Exception:
            pass

    win32gui.EnumChildWindows(hwnd, cb, None)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=int, default=None)
    args = ap.parse_args()

    pids = axis_pids()
    if not pids:
        print(json.dumps({"error": "axis.exe 미실행"}, ensure_ascii=False))
        return 2

    wins = []

    def cb(hwnd, _):
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid in pids and win32gui.IsWindowVisible(hwnd):
            rc = win32gui.GetWindowRect(hwnd)
            if (rc[2] - rc[0]) > 100 and (rc[3] - rc[1]) > 100:
                wins.append({"hwnd": hwnd, "class": win32gui.GetClassName(hwnd),
                             "title": win32gui.GetWindowText(hwnd), "rect": list(rc)})

    win32gui.EnumWindows(cb, None)
    wins.sort(key=lambda w: -( (w["rect"][2]-w["rect"][0]) * (w["rect"][3]-w["rect"][1]) ))

    result = {"pids": pids, "windows": wins}
    for i, w in enumerate(wins[:6]):
        w["shot"] = capture(w["hwnd"], f"win{i}_{w['hwnd']}")

    if args.hwnd:
        result["controls"] = controls(args.hwnd)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dump.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"windows": len(wins), "out": str(OUT / "dump.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
