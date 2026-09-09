"""특정 hwnd를 캡처하고 컨트롤 텍스트를 덤프한다. 관리자 권한 필요.

사용법: python tools/hts_shot.py <hwnd> [이름]
"""
from __future__ import annotations

import json
import sys
from ctypes import windll
from pathlib import Path

import win32gui
import win32ui
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hts_safe import controls, enable_dpi  # noqa: E402

enable_dpi()
OUT = Path(__file__).resolve().parents[1] / "state" / "hts_shots"


def capture(hwnd: int, name: str) -> str:
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
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


def main() -> int:
    hwnd = int(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else f"shot_{hwnd}"
    info = {
        "hwnd": hwnd,
        "class": win32gui.GetClassName(hwnd),
        "text": win32gui.GetWindowText(hwnd),
        "rect": list(win32gui.GetWindowRect(hwnd)),
        "shot": capture(hwnd, name),
        "controls": [c for c in controls(hwnd, max_depth=7) if c["visible"]],
    }
    (OUT / f"{name}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps({"shot": info["shot"], "controls": len(info["controls"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        import traceback
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "shot_error.json").write_text(
            json.dumps({"error": repr(exc), "traceback": traceback.format_exc()},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(1)
