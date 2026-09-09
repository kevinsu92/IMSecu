"""HTS의 모든 창을 훑어 텍스트로 검색한다. 관리자 권한 필요.

사용법: python tools/hts_grep.py 주문 매수 매도
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from ctypes import windll
from pathlib import Path

import win32gui
import win32process

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
    return [int(p.strip('" ').split('","')[0]) for p in []] or [
        int([x.strip('" ') for x in line.split('","')][1])
        for line in out.splitlines()
        if [x.strip('" ') for x in line.split('","')][0].lower().startswith("axis")
    ]


def walk(hwnd, depth, acc, max_depth=6):
    def cb(child, _):
        try:
            rc = win32gui.GetWindowRect(child)
            acc.append({"hwnd": child, "depth": depth, "class": win32gui.GetClassName(child),
                        "text": win32gui.GetWindowText(child), "rect": list(rc),
                        "w": rc[2]-rc[0], "h": rc[3]-rc[1],
                        "visible": bool(win32gui.IsWindowVisible(child))})
            if depth < max_depth:
                walk(child, depth+1, acc, max_depth)
        except Exception:
            pass
    win32gui.EnumChildWindows(hwnd, cb, None)


def main():
    words = sys.argv[1:] or ["주문", "매수", "매도", "종목"]
    pat = re.compile("|".join(re.escape(w) for w in words))
    pids = axis_pids()
    rows = []

    def cb(h, _):
        _, pid = win32process.GetWindowThreadProcessId(h)
        if pid in pids:
            rc = win32gui.GetWindowRect(h)
            rows.append({"hwnd": h, "depth": 0, "class": win32gui.GetClassName(h),
                         "text": win32gui.GetWindowText(h), "rect": list(rc),
                         "w": rc[2]-rc[0], "h": rc[3]-rc[1],
                         "visible": bool(win32gui.IsWindowVisible(h))})
            walk(h, 1, rows)

    win32gui.EnumWindows(cb, None)
    hits = [r for r in rows if r["text"] and pat.search(r["text"])]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "grep.json").write_text(json.dumps({"words": words, "total": len(rows),
                                               "hits": hits[:120]}, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(json.dumps({"total": len(rows), "hits": len(hits)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
