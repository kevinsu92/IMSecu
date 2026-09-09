# -*- coding: utf-8 -*-
"""HTS 장운영알람을 끈다.

왜 끄는가
  HTS 가 장 운영 시각(08:00·08:30·15:20)에 스스로 띄우는 '알람' 패널이 주문창을
  덮으면 전송 전 검사가 주문을 막는다. 프로그램이 그 창을 닫도록 고쳤지만,
  애초에 안 뜨는 편이 낫다.

왜 이렇게 하는가
  HTS 는 관리자 권한으로 돈다. 비승격 프로세스의 클릭은 UIPI 가 버리므로 화면
  자동화 도구로는 손을 못 댄다. 이 스크립트는 `execute.py --run-tool` 로 승격된
  스케줄러 작업 안에서 돈다. 좌표를 찍지 않는다 — 컨트롤을 **핸들로** 찾고
  핸들로 누른다.

  누를 때는 **PostMessage** 다. SendMessage(BM_CLICK) 은 버튼 처리기가 끝날 때까지
  돌아오지 않는데, 그 버튼이 모달 대화상자를 열면 대화상자가 닫힐 때까지 이
  프로세스가 멈춘다. 실제로 그렇게 멈춰서 작업을 강제 종료해야 했다.

모드 (state/alarm_mode.txt 한 단어, 없으면 dump)
  dump   : 지금 보이는 HTS 대화상자('알람' 제외)의 컨트롤을 전부 찍고 캡처한다. 안 바꾼다.
  open   : '알람' 창의 '설정' 을 눌러(비동기) 대화상자를 연 뒤 dump 와 같다.
  tab N  : 대화상자에 탭/트리가 있으면 N 번째를 고른 뒤 dump.
  apply  : 라벨에 '장운영' 이 든 체크박스가 **정확히 하나**면 끄고 확인/저장을 누른다.
  close  : 대화상자에 WM_CLOSE 를 보낸다. 아무것도 저장하지 않는다.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import win32con
import win32gui
import win32process

from imrl import hts_exec as H

BM_CLICK, BM_GETCHECK = 0x00F5, 0x00F0
TVM_GETNEXTITEM, TVM_SELECTITEM = 0x110A, 0x110B
TVGN_ROOT, TVGN_NEXT, TVGN_CARET = 0x0, 0x1, 0x9
WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON = 0x0201, 0x0202, 0x0001


def click_ctrl(hw: int) -> None:
    """컨트롤 가운데에 마우스 다운/업을 **그 창 앞으로** 보낸다.

    MFC 의 AfxWnd 버튼(확인/취소/적용)은 BM_CLICK 을 모른다. 마우스 메시지는
    안다. PostMessage 라 이 프로세스는 기다리지 않고, 전역 입력이 아니라 다른
    창에는 아무 일도 없다.
    """
    l, t, r, b = win32gui.GetWindowRect(hw)
    x, y = (r - l) // 2, (b - t) // 2
    lp = (y << 16) | (x & 0xFFFF)
    win32gui.PostMessage(hw, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    win32gui.PostMessage(hw, WM_LBUTTONUP, 0, lp)


def select_tree_item(tree: int, index: int) -> bool:
    """트리의 index 번째 최상위 항목을 고른다. 항목 텍스트는 다른 프로세스 메모리라
    읽지 않는다 — 순서로 고르고, 결과 페이지를 덤프해서 확인한다."""
    item = win32gui.SendMessage(tree, TVM_GETNEXTITEM, TVGN_ROOT, 0)
    for _ in range(index):
        if not item:
            return False
        item = win32gui.SendMessage(tree, TVM_GETNEXTITEM, TVGN_NEXT, item)
    if not item:
        return False
    win32gui.PostMessage(tree, TVM_SELECTITEM, TVGN_CARET, item)
    return True
TCM_GETITEMCOUNT, TCM_SETCURFOCUS = 0x1304, 0x1330
BS_MASK = 0x0F
CHECK_STYLES = {0x2, 0x3, 0x5, 0x6}          # CHECKBOX AUTOCHECKBOX 3STATE AUTO3STATE
OK_LABELS = ("확인", "저장", "적용", "OK")


def hts_tops(pid):
    out = []
    def cb(hw, _):
        if win32process.GetWindowThreadProcessId(hw)[1] == pid:
            out.append((hw, win32gui.GetClassName(hw), win32gui.GetWindowText(hw),
                        bool(win32gui.IsWindowVisible(hw))))
    win32gui.EnumWindows(cb, None)
    return out


def children(hw):
    out = []
    def cb(c, _):
        try:
            out.append((c, win32gui.GetClassName(c), win32gui.GetWindowText(c),
                        win32gui.GetWindowLong(c, win32con.GWL_STYLE),
                        bool(win32gui.IsWindowVisible(c))))
        except Exception:
            pass
    try:
        win32gui.EnumChildWindows(hw, cb, None)
    except Exception:
        pass
    return out


def is_checkbox(cls, style):
    return cls == "Button" and (style & BS_MASK) in CHECK_STYLES


def checked(hw):
    try:
        return win32gui.SendMessage(hw, BM_GETCHECK, 0, 0)   # 읽기만 — 즉시 돌아온다
    except Exception:
        return -1


def dump(hw, label):
    kids = children(hw)
    print(f"{label} hwnd={hw} 제목={win32gui.GetWindowText(hw)!r} "
          f"rect={win32gui.GetWindowRect(hw)} 자식 {len(kids)}개")
    boxes = []
    for c, cls, txt, st, vis in kids:
        t = txt.strip()
        tag = ""
        if is_checkbox(cls, st):
            tag = f"  [체크 {checked(c)}]"; boxes.append((c, t, checked(c), vis))
        elif cls == "Button":
            tag = "  [버튼]"
        elif cls in ("SysTabControl32", "SysTreeView32", "SysListView32", "ComboBox"):
            tag = f"  [{cls}]"
        elif not t:
            continue
        print(f"    {'v' if vis else '-'} hwnd={c:<9} {cls:<16} {t[:64]!r}{tag}")
    return kids, boxes


def visible_dialogs(pid):
    return [hw for hw, cls, txt, vis in hts_tops(pid)
            if cls == "#32770" and vis and txt != "알람"]


def main() -> int:
    mode = "dump"
    mf = ROOT / "state" / "alarm_mode.txt"
    if mf.exists():
        mode = (mf.read_text(encoding="utf-8").strip() or "dump")
        mf.unlink()
    print(f"모드: {mode}")
    pid = H.axis_pid()
    if not pid:
        print("HTS 가 실행 중이 아니다."); return 1

    if mode.split()[0] == "open":
        alarm = [hw for hw, cls, txt, vis in hts_tops(pid) if cls == "#32770" and txt == "알람"]
        if not alarm:
            print("'알람' 창이 없다(숨김 포함)."); return 2
        btn = next((c for c, cls, t, st, v in children(alarm[0]) if cls == "Button" and t.strip() == "설정"), None)
        if not btn:
            print("'설정' 버튼 없음"); return 3
        win32gui.PostMessage(btn, BM_CLICK, 0, 0)
        time.sleep(1.5)
        # "open N" 이면 연 뒤 N 번째 트리 항목까지 고른다
        mode = ("tree " + mode.split()[1]) if len(mode.split()) > 1 else "dump"

    dlgs = visible_dialogs(pid)
    print(f"보이는 대화상자 {len(dlgs)}개: {[win32gui.GetWindowText(d) for d in dlgs]}")
    if not dlgs:
        print("열린 설정 대화상자가 없다. 모드 open 으로 다시."); return 4
    dlg = dlgs[0]

    words = mode.split()
    if words and words[0] in ("tree", "apply") and len(words) > 1:
        tree = next((c for c, cls, t, st, v in children(dlg) if cls == "SysTreeView32"), None)
        if tree and select_tree_item(tree, int(words[1])):
            time.sleep(1.0)
            print(f"트리 항목 {words[1]} 선택")
        else:
            print("트리를 못 찾았거나 항목 번호가 범위 밖"); return 7
        mode = words[0]

    if mode.startswith("tab"):
        n = int(mode.split()[1]) if len(mode.split()) > 1 else 0
        tabs = [c for c, cls, t, st, v in children(dlg) if cls == "SysTabControl32"]
        if tabs:
            win32gui.PostMessage(tabs[0], TCM_SETCURFOCUS, n, 0)
            time.sleep(0.8)
            print(f"탭 {n} 선택")

    kids, boxes = dump(dlg, "대화상자")
    try:
        print(f"캡처: {H.capture(dlg, f'alarm_settings_{dlg}')}")
    except Exception as exc:
        print(f"캡처 실패: {exc}")

    if mode == "close":
        win32gui.PostMessage(dlg, win32con.WM_CLOSE, 0, 0)
        time.sleep(0.8)
        print(f"WM_CLOSE 보냄. 남아 있음: {win32gui.IsWindow(dlg) and win32gui.IsWindowVisible(dlg)}")
        return 0

    if mode != "apply":
        print("바꾸지 않았다. 대화상자는 열린 채다. (apply / close / tab N)")
        return 0

    targets = [(c, t, s) for c, t, s, v in boxes if "장운영" in t]
    if len(targets) != 1:
        print(f"'장운영' 체크박스가 정확히 1개가 아니다({len(targets)}) — 멈춘다: {[t for _, t, _ in targets]}")
        return 5
    c, t, s = targets[0]
    if s == 0:
        print(f"이미 꺼져 있다: {t!r}")
    else:
        win32gui.PostMessage(c, BM_CLICK, 0, 0)
        time.sleep(0.5)
        print(f"체크 해제: {t!r} -> {checked(c)}")
    ok = next(((cc, tt.strip()) for cc, cls, tt, st, v in children(dlg)
               if cls in ("Button", "AfxWnd140") and tt.strip() in OK_LABELS and v), None)
    if not ok:
        print("확인/저장 버튼을 못 찾았다 — 그대로 둔다."); return 6
    click_ctrl(ok[0])
    time.sleep(1.0)
    print(f"'{ok[1]}' 누름. 대화상자 남아 있음: {win32gui.IsWindow(dlg) and win32gui.IsWindowVisible(dlg)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
