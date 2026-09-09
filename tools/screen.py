"""전체 화면 캡처. 관리자 권한으로 실행하면 관리자 창도 온전히 담긴다."""
from ctypes import windll
from pathlib import Path

try:
    windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    windll.user32.SetProcessDPIAware()

from PIL import ImageGrab

OUT = Path(__file__).resolve().parents[1] / "state" / "hts_shots"
OUT.mkdir(parents=True, exist_ok=True)
img = ImageGrab.grab(all_screens=True)
print("size", img.size)
img.thumbnail((1900, 1900))
img.save(OUT / "desktop.png")
print("saved", OUT / "desktop.png")
