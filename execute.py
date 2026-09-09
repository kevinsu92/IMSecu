"""주문서를 HTS 에 입력한다.

`run.py plan` 이 만든 state/orders_YYYYMMDD.json 을 읽어 싸이칸플러스 주문 화면에
채워 넣는다. 각 주문마다 입력 화면을 캡처해 텔레그램으로 보낸다.

**기본은 드라이런이다.** 실제 전송은 `--live` 를 명시해야 한다. 수량·가격 칸은
값을 되읽을 수 없어 화면 캡처가 유일한 검증 수단이므로, 확인 없이 전송하지 않는다.

관리자 권한으로 실행해야 한다 (HTS 가 관리자 권한이라 UIPI 때문).

사용법
  python execute.py                      드라이런: 입력만, 캡처 전송
  python execute.py --live               실제 전송
  python execute.py --only 033790        특정 종목만
  python execute.py --date 20260908      다른 날짜 주문서
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from datetime import date, datetime, time as dtime
from pathlib import Path

from imrl import data, hts_exec, notify, risk, state

# Windows 콘솔 기본 코드페이지는 cp949 라 '⚠' 같은 문자를 만나면 print 가
# UnicodeEncodeError 로 죽는다. 실제로 15:20 집행 직전 리포트 출력에서 터졌다.
# 주문은 이미 저장된 뒤라 조용히 실패하는 것이 아니라 요란하게 실패하지만,
# 스케줄러 실행에서는 그 뒤 단계가 통째로 날아간다. 출력 인코딩을 UTF-8 로 고정한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass



ROOT = Path(__file__).resolve().parent

# 규정상 매매 가능 시간 (매뉴얼 p11). 시간외거래는 지원하지 않는다.
REGULAR_OPEN = dtime(9, 0)
REGULAR_CLOSE = dtime(15, 20)
PREOPEN_START = dtime(8, 30)
CLOSE_AUCTION_END = dtime(15, 30)


class Tee:
    """표준출력을 파일에도 남긴다.

    작업 스케줄러로 실행하면 콘솔이 없어 출력이 사라진다. 무인 운영 중
    무슨 일이 있었는지 확인하려면 로그가 필요하다.
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a", encoding="utf-8")
        self.stdout = sys.stdout
        self.file.write(f"\n{'=' * 60}\n{datetime.now():%Y-%m-%d %H:%M:%S}\n")

    def write(self, s):
        self.stdout.write(s)
        self.file.write(s)
        self.file.flush()

    def flush(self):
        self.stdout.flush()
        self.file.flush()


# 킬 스위치 파일 위치.
#
# state/ 는 OneDrive 동기화 폴더 안이다. 동기화는 수십 초에서 분 단위로 지연되므로
# "원격에서 즉시 정지"라는 목적에 맞지 않는다. 로컬 경로를 **먼저** 보고,
# OneDrive 경로도 함께 본다 — 둘 중 하나만 있어도 정지다.
KILL_FILE = ROOT / "state" / "KILL"
KILL_FILE_LOCAL = Path("C:/imrl_state/KILL")


def acquire_single_instance() -> object | None:
    """전역 뮤텍스를 잡는다. 이미 돌고 있으면 None.

    이 프로그램은 전역 마우스·키보드를 직접 조작한다. 두 개가 동시에 돌면
    한쪽이 클릭한 자리에 다른 쪽이 타이핑해 수량과 가격이 섞인 주문이 나간다.
    스케줄러 실행과 수동 실행이 겹치는 것은 충분히 일어날 수 있는 일이다.
    """
    from ctypes import windll, c_void_p

    handle = windll.kernel32.CreateMutexW(None, True, "Global\\IMRL_Execute_SingleInstance")
    if not handle or windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return c_void_p(handle)


def skip_today_active(day: str | None = None) -> bool:
    """오늘 하루만 주문을 내지 않는다.

    KILL 은 지울 때까지 멈춘다. 그래서 "오늘은 넘어가자"에 KILL 을 쓰면 내일
    지우는 것을 잊는 순간 대회가 조용히 끝난다. 날짜가 붙은 파일은 자정이
    지나면 저절로 효력이 없어진다 — 잊어도 안전한 쪽으로 기운다.
    """
    day = day or f"{date.today():%Y%m%d}"
    return (ROOT / "state" / f"SKIP_{day}").exists()


def kill_switch_active() -> bool:
    """긴급 정지.

    무인 운영 중 뭔가 잘못됐을 때 사람이 개입할 수 있는 유일한 수단이다.
    state/KILL 파일을 만들면 그 뒤로는 주문을 내지 않는다. 파일 하나면 되므로
    텔레그램이 죽어 있어도, 원격에서도(OneDrive 동기화) 멈출 수 있다.
    """
    return KILL_FILE.exists() or KILL_FILE_LOCAL.exists()


def hide_own_console() -> None:
    """자기 콘솔 창을 숨긴다.

    작업 스케줄러가 python.exe 를 띄우면 콘솔 창이 생기고 그 창이 포그라운드를
    가져간다. 그러면 포커스 검증에 걸려 모든 주문이 거부된다(실제로 4건 전부 실패).
    출력은 이미 로그 파일로 남기므로 콘솔은 필요 없다.
    """
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def load_config() -> dict:
    return json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))


def market_state(now: datetime | None = None, cfg: dict | None = None) -> str:
    """장 상태. 주말뿐 아니라 **설정된 휴장일과 대회 기간**도 본다.

    주말만 보던 시절에는 추석 당일에도 "정규시장"으로 판정해 실전송이 허용됐다.
    """
    now = now or datetime.now()
    cfg = cfg or load_config()
    from imrl.data import TradingCalendar

    cal = TradingCalendar.from_config(cfg["contest"])
    today = now.date()
    if today < cal.start or today > cal.end:
        return "대회기간 아님"
    if not cal.is_trading_day(today):
        return "휴장일"
    if now.weekday() >= 5:
        return "휴장(주말)"
    t = now.time()
    if PREOPEN_START <= t < REGULAR_OPEN:
        return "장전 동시호가"
    if REGULAR_OPEN <= t < REGULAR_CLOSE:
        return "정규시장"
    if REGULAR_CLOSE <= t < CLOSE_AUCTION_END:
        return "장마감 동시호가"
    return "장외"


def setup_tasks() -> int:
    """tools/setup_tasks.ps1 을 실행해 작업 스케줄러를 재등록한다.

    왜 여기에 있나
      스케줄러 등록에는 관리자 권한이 필요하고, 그건 UAC 프롬프트를 사람이
      눌러야 얻어진다. 그런데 이 스크립트 자체는 이미 RunLevel=Highest 로
      등록된 작업(IMRL_Execute)을 통해 관리자 권한으로 실행될 수 있다.
      즉 사람이 UAC 를 한 번 눌러 만들어 둔 권한을 재사용해, 이후의 등록 변경을
      다시 UAC 없이 처리한다. 등록 내용이 바뀔 때마다 사람을 부르지 않아도 된다.

      권한을 몰래 올리는 것이 아니다. 이미 사람이 부여한 권한을, 그 권한을
      부여한 목적 그대로 쓰는 것뿐이다. 관리자 권한이 아니면 그냥 거부한다.
    """
    import subprocess

    if not is_admin():
        print("관리자 권한이 아니라 스케줄러를 등록할 수 없다.")
        print("관리자 PowerShell 에서 직접 실행하거나, 이미 등록된 IMRL_Execute 작업을 통해")
        print("실행할 것:  state/exec_args.txt 에 '--setup-tasks' 를 넣고 작업을 시작한다.")
        return 2

    script = ROOT / "tools" / "setup_tasks.ps1"
    if not script.exists():
        print(f"등록 스크립트가 없다: {script}")
        return 2

    print(f"스케줄러 등록 실행: {script}")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.stdout:
        print(r.stdout.rstrip())
    if r.stderr:
        print("stderr:")
        print(r.stderr.rstrip())
    print(f"종료 코드 {r.returncode}")
    return 0 if r.returncode == 0 else 9


def reset_form() -> int:
    """주문 폼을 알려진 상태로 되돌린다.

    시장가 체크가 켜져 있으면 가격칸이 회색 0 이 되고, 우리가 레이아웃을 잡는
    **노란 가격칸이 화면에서 사라진다.** 그러면 앵커 탐색이 실패해 아무것도
    못 한다. 실제로 그 상태에 빠졌다 — 좌표가 어긋난 클릭이 시장가를 켜버렸다.

    앵커가 없는 상태에서는 실측 절대좌표를 쓸 수밖에 없다. 폼을 고정 크기로
    못박은 뒤라 이 좌표는 결정적이다.
    """
    import time

    import win32gui

    from imrl.hts_anchor import AnchorError, find_price_box, is_checked

    #: 주문창 2453x1514 고정 기준 실측 좌표 (2026-09-05)
    MARKET_CHECKBOX = (1753, 380)
    TAB_BUY = (1293, 183)

    ad = hts_exec.OrderAdapter(dry_run=True)
    ad.pid = hts_exec.axis_pid()
    if not ad.pid:
        print("axis.exe 가 실행 중이 아니다.")
        return 7
    ad.main = hts_exec.find_main(ad.pid)
    ad.order = hts_exec.find_order_window(ad.pid)
    hts_exec.ensure_main_size(ad.main)
    w, h = hts_exec.pin_order_window(ad.order)
    print(f"주문창 고정 {w}x{h}")

    img = hts_exec.grab_window(ad.order)
    if is_checked(img, MARKET_CHECKBOX):
        print("시장가가 켜져 있다 — 해제한다")
        hts_exec.real_click(ad.order, ad.main, *MARKET_CHECKBOX)
        time.sleep(1.0)
        img = hts_exec.grab_window(ad.order)
        if is_checked(img, MARKET_CHECKBOX):
            print("시장가를 해제하지 못했다.")
            hts_exec.capture(ad.order, "reset_fail")
            return 8
    else:
        print("시장가 꺼져 있음")

    hts_exec.real_click(ad.order, ad.main, *TAB_BUY)
    time.sleep(0.8)

    # 종목·수량·가격을 비운다.
    #
    # 집행 경로는 입력 전에 항상 칸을 지우므로 남은 값이 주문을 바꾸지는
    # 않는다. 그래도 비운다 — 드라이런 뒤 폼에 그럴듯한 주문이 그대로 남아
    # 있으면, 사람이 F9 를 잘못 눌렀을 때 그게 나간다. 되돌릴 수 없는 것을
    # 한 번의 오타 거리에 두지 않는다.
    try:
        ad.connect()
        cleared = []
        for name in ("symbol_rect", "qty_rect", "price_rect"):
            if name not in ad.layout:
                continue
            try:
                ad._click(name)
                hts_exec.assert_focus(ad.main)
                hts_exec.clear_field()
                cleared.append(name)
            except Exception as exc:
                print(f"  {name} 을 비우지 못했다: {type(exc).__name__}")
        print(f"칸 비움: {', '.join(cleared) if cleared else '없음'}")
    except Exception as exc:
        print(f"  칸 비우기 생략 ({type(exc).__name__}: {exc})")
    time.sleep(0.5)
    img = hts_exec.grab_window(ad.order)

    shot = hts_exec.capture(ad.order, "reset_after")
    print(f"캡처: {shot}")
    try:
        box = find_price_box(img)
        print(f"가격칸 복구 확인: {box}")
        return 0
    except AnchorError as exc:
        print(f"가격칸을 아직 못 찾는다: {exc}")
        return 8


def explore(screens: list[str]) -> int:
    """HTS 화면을 열어 캡처한다 (중계실 대체 화면 탐색용).

    저녁 중계실 입력이 무인 운영에 남은 마지막 사람 손이다. 웹 중계실은 로그인이
    필요하고 비밀번호 자동화는 할 수 없다. 그런데 HTS 는 아침에 사람이 로그인해
    하루 종일 떠 있으므로, 같은 수치를 HTS 안에서 읽을 수 있다면 비밀번호 없이
    자동화된다. 이 명령은 그 화면이 존재하는지 찾기 위한 것이다.
    """
    import time
    from imrl import hts_exec as H
    pid = H.axis_pid()
    if not pid:
        print("HTS 가 실행 중이 아니다.")
        return 1
    main_hwnd = H.find_main(pid)
    if not main_hwnd:
        print("메인 창을 찾지 못했다.")
        return 1
    print(f"HTS pid={pid} 메인창={main_hwnd}")
    try:
        p = H.capture(main_hwnd, "explore_main")
        print(f"  메인 창 캡처 -> {p}")
    except Exception as exc:
        print(f"  메인 창 캡처 실패 — {exc}")
    import win32gui
    for sc in screens:
        try:
            if "," in sc:
                # "x,y" 형식이면 그 클라이언트 좌표를 클릭한다. 툴바 버튼을 눌러
                # 화면번호를 알아내기 위한 것이다 — 번호를 추측하는 것보다 확실하다.
                cx, cy = (int(v) for v in sc.split(","))
                H.real_click(main_hwnd, main_hwnd, cx, cy)
                time.sleep(2.0)
                p = H.capture(main_hwnd, f"click_{cx}_{cy}")
            else:
                H.open_screen(main_hwnd, sc, wait=5.0)
                time.sleep(1.2)
                p = H.capture(main_hwnd, f"screen_{sc}")
            print(f"  {sc}: 캡처 -> {p}")
        except Exception as exc:
            print(f"  {sc}: 실패 — {exc}")
    return 0


def _log_session(pid: int) -> None:
    """HTS 로그인 상태를 한 줄씩 기록한다.

    왜 재는가
      아침 로그인이 **매일** 필요한지 **재부팅할 때만** 필요한지 아직 모른다.
      HTS 의 IdleTimeout 은 0 이라 유휴 로그아웃은 없고, Windows 업데이트도
      대회 기간 동안 멈춰 있다. 세션이 밤을 넘긴다면 사람이 하는 일이 20일에
      한두 번으로 줄어든다. 그건 추측할 게 아니라 관측하면 아는 것이다.

      헬스체크가 매시간 돌므로 여기에 붙이면 공짜다. 부팅 시각을 함께 남겨야
      "세션이 끊겼다" 와 "재부팅했다" 를 구분할 수 있다.
    """
    import json as _json
    from datetime import datetime as _dt
    try:
        import subprocess as _sp
        boot = _sp.run(["powershell", "-NoProfile", "-Command",
                        "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('s')"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=15).stdout.strip()
    except Exception:
        boot = ""
    logged_in = False
    if pid:
        try:
            hts_exec.find_order_window(pid)
            logged_in = True
        except Exception:
            logged_in = bool(hts_exec.find_main(pid))
    row = {"at": _dt.now().isoformat(timespec="seconds"), "pid": pid,
           "logged_in": logged_in, "boot": boot}
    try:
        p = ROOT / "state" / "session_log.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(row, ensure_ascii=False) + chr(10))
    except OSError:
        pass


def healthcheck(notify_on_ok: bool = False) -> int:
    """장 마감 전에 HTS 가 주문 가능한 상태인지 확인하고, 아니면 알린다.

    무인 운영의 실패는 대부분 조용하다. 15:21 에 전송 게이트가 막히면 그날 매매가
    통째로 날아가는데, 그때는 이미 고칠 시간이 없다. 실제로 겪은 사례가 HTS
    **화면잠금**이다 — 프로세스도 살아 있고 주문창도 열려 있어 창 열거는 전부
    성공하는데 화면이 그려지지 않아 캡처만 실패한다.

    그래서 집행 30분 전에 미리 같은 검사를 돌려 사람이 고칠 시간을 만든다.
    반환값 0 = 정상, 그 외 = 조치 필요.
    """
    import subprocess
    import time

    problems: list[str] = []
    cfg = load_config()

    pid = hts_exec.axis_pid()
    _log_session(pid)
    if not pid and cfg.get("hts", {}).get("auto_launch", True):
        # HTS 가 꺼져 있으면 띄운다.
        #
        # 무인 운영에서 가장 흔한 시작 실패가 "재부팅 후 HTS 를 아무도 안 켬"이다.
        # 실행 자체는 자격증명과 무관하므로 자동화할 수 있다. 로그인 창까지 올라오면
        # 남는 사람 조작은 비밀번호 하나이고, HTS 의 비밀번호 저장 옵션을 켜두면
        # 그것도 클릭 하나로 줄어든다. **이 프로그램은 비밀번호를 다루지 않는다.**
        exe = cfg.get("hts", {}).get("exe_path", "")
        if exe and Path(exe).exists():
            try:
                # **작업 스케줄러의 잡 오브젝트에서 떼어낸다.**
                #
                # 그냥 띄우면 HTS 가 이 작업의 자식으로 붙고, 작업이 끝날 때
                # 프로세스 트리가 같이 정리돼 HTS 도 죽는다. 실제로 그렇게
                # 됐다 - 로그에는 "실행했다 / 프로세스 확인" 이 찍혔는데 잠시 뒤
                # axis.exe 가 사라져 있었다.
                DETACHED = 0x00000008        # DETACHED_PROCESS
                NEW_GROUP = 0x00000200       # CREATE_NEW_PROCESS_GROUP
                BREAKAWAY = 0x01000000       # CREATE_BREAKAWAY_FROM_JOB
                try:
                    subprocess.Popen([exe], cwd=str(Path(exe).parent.parent),
                                     close_fds=True,
                                     creationflags=DETACHED | NEW_GROUP | BREAKAWAY)
                except OSError:
                    # 잡이 breakaway 를 허용하지 않으면 그 플래그 없이 재시도한다.
                    subprocess.Popen([exe], cwd=str(Path(exe).parent.parent),
                                     close_fds=True,
                                     creationflags=DETACHED | NEW_GROUP)
                print(f"  HTS 가 꺼져 있어 실행했다: {exe}")
                for _ in range(20):          # 최대 60초 대기
                    time.sleep(3)
                    pid = hts_exec.axis_pid()
                    if pid:
                        break
                if pid:
                    print("  HTS 프로세스 확인. 로그인 창에서 비밀번호 입력이 필요하다.")
                else:
                    print("  실행했으나 프로세스를 확인하지 못했다.")
            except OSError as exc:
                print(f"  HTS 실행 실패: {exc}")
        else:
            print(f"  HTS 실행 경로가 없다: {exe!r}")

    if not pid:
        problems.append("axis.exe 가 실행 중이 아니다. 싸이칸플러스를 켤 것.")
    else:
        try:
            main = hts_exec.find_main(pid)
            stt, title = hts_exec.session_state(main)
            if stt == "locked":
                problems.append(f"HTS 화면잠금 상태다 ({title}). 잠금을 해제할 것.")
            # 제목 표식 없음은 차단 사유가 아니다. 정상 모의 세션에서도 사라진 적이 있다.
            # 실제 판정은 아래 계좌번호 대조로 한다.
            elif stt == "no_marker":
                print(f"  참고: 메인 창에 모의 표식이 없다 ({title!r}). 계좌로 검증한다.")

            if not problems:
                ad = hts_exec.OrderAdapter(dry_run=True)
                ad.connect()
                if not ad.symbol_edit:
                    problems.append("종목 입력칸을 찾지 못했다.")
                # 수량·가격은 자식 창이 없어 컨트롤로 잡히지 않는 것이 정상이다
                # (실측: 주문창 Edit 4개, 비-readonly 2개 전부 종목칸).
                # 대신 전송 버튼 좌표가 창 안에 있는지 본다 — 앵커 외삽이 틀어지면
                # 여기가 창 밖으로 나가고, 그 상태로 클릭하면 엉뚱한 곳을 누른다.
                bx, by = ad.layout.get("submit_button", (0, 0))
                import win32gui as _w
                rc = _w.GetWindowRect(ad.order)
                ow, oh = rc[2] - rc[0], rc[3] - rc[1]
                if not (0 < bx < ow and 0 < by < oh):
                    problems.append(
                        f"전송 버튼 좌표 ({bx},{by}) 가 주문창({ow}x{oh}) 밖이다. "
                        "앵커 외삽이 어긋났다 — 이 상태로는 전송이 차단된다.")
                else:
                    print(f"  전송 버튼 ({bx},{by}) 주문창({ow}x{oh}) 안")
                want = cfg.get("contest", {}).get("mock_account", "")
                acct = ad.read_account()
                if not want:
                    problems.append("config.contest.mock_account 가 없다. "
                                    "실계좌 오발주를 막을 기준이 없다.")
                elif not acct:
                    problems.append("주문창에서 계좌번호를 읽지 못했다.")
                elif want not in acct:
                    problems.append(f"계좌 불일치: 기대 {want}, 화면 {acct!r}. "
                                    "대회 계좌로 다시 로그인할 것.")
                else:
                    print(f"  계좌 확인 {acct!r}")

                closed = ad.dismiss_benign_popups()
                if closed:
                    print(f"  HTS 알림창을 닫았다: {', '.join(closed)} (장운영알람 — 집행 전에도 같은 처리)")
                stale = ad._popups()
                if stale:
                    problems.append("주문창 위에 팝업이 떠 있다: "
                                    + ", ".join(t for _, t in stale))
        except (hts_exec.HtsError, hts_exec.AnchorError, OSError) as exc:
            problems.append(str(exc))
        except Exception as exc:
            # pywintypes.error 는 OSError 가 아니다. 그래서 UIPI 로 막힌
            # SetWindowPos 가 위 절에 안 걸리고 트레이스백으로 프로세스를
            # 죽였다 — **문제를 알리는 함수가 문제를 만나 아무 말 없이 죽는**
            # 형태다. 무엇이 터지든 여기서 잡아 알림으로 바꾼다.
            code = getattr(exc, "winerror", None) or (
                exc.args[0] if exc.args and isinstance(exc.args[0], int) else None)
            if code == 5:
                problems.append(
                    "창 조작이 권한에 막혔다 (액세스 거부). HTS 가 관리자 권한으로 "
                    "돌고 있으므로 이 프로그램도 관리자여야 한다. "
                    "scripts/start_watchdog.bat 으로 실행하거나 IMRL 작업 스케줄러를 쓸 것.")
            else:
                problems.append(f"점검 중 예외: {type(exc).__name__}: {exc}")

    if kill_switch_active():
        problems.append("KILL 파일이 있다. 자동매매가 정지된 상태다.")

    # 주문서는 signal_time(15:05) 에 생기므로 그 전에는 없는 것이 정상이다.
    # 시간 조건 없이 확인하면 14:50 점검이 매일 오탐을 낸다. 오탐이 반복되면
    # 진짜 경고도 무시하게 된다.
    day = f"{date.today():%Y%m%d}"
    try:
        sig = load_config()["execution"].get("signal_time", "15:05")
        hh, mm = (int(x) for x in sig.split(":"))
        after_signal = datetime.now().time() >= dtime(hh, mm)
    except (KeyError, ValueError):
        after_signal = True
    if after_signal and not (ROOT / "state" / f"orders_{day}.json").exists():
        problems.append(f"오늘 주문서(orders_{day}.json)가 없다. run.py plan 이 돌았는지 확인할 것.")

    if problems:
        body = "\n".join(f"- {p}" for p in problems)
        print("헬스체크 실패:")
        print(body)
        try:
            notify.alert("자동매매 사전점검 실패",
                         body + "\n\n집행 전에 조치할 것.")
        except Exception as exc:
            print(f"(텔레그램 알림도 실패: {exc})")
        return 8

    print("헬스체크 통과 — 주문 가능한 상태다.")
    if notify_on_ok:
        try:
            notify.send("자동매매 사전점검 통과. 예정대로 집행한다.")
        except Exception:
            pass
    return 0


def diagnose() -> int:
    """주문창을 기준 크기로 맞춘 뒤 전체를 캡처하고, 계산된 좌표 주변을 잘라 저장한다.

    좌표가 맞는지 눈으로 확인하기 위한 것이다. 추측으로 고치지 말고 이걸로 실측할 것.
    """
    import ctypes

    import win32gui
    from PIL import Image

    from imrl import hts_anchor

    ad = hts_exec.OrderAdapter(dry_run=True)
    ad.pid = hts_exec.axis_pid()
    # HTS 가 꺼져 있으면 pid 0 으로 계속 진행하다 grab_window 에서
    # "관리자 권한을 확인하라" 는 엉뚱한 메시지를 내며 죽는다. 원인을 바로 말한다.
    if not ad.pid:
        print("axis.exe 가 실행 중이 아니다. 싸이칸플러스를 켜고 모의투자로 로그인한 뒤 "
              "주식주문(화면 1200)을 열 것.")
        return 7
    ad.main = hts_exec.find_main(ad.pid)
    ad.order = hts_exec.find_order_window(ad.pid)

    # 앵커 후보를 먼저 보여준다. 실패해도 무엇을 봤는지 알 수 있어야 고칠 수 있다.
    dbg: list = []
    img = hts_exec.grab_window(ad.order)
    try:
        box = hts_anchor.find_price_box(img, debug=dbg)
        print(f"가격칸 후보 {len(dbg)}개 (면적 큰 순):")
        for area, x0, y0, x1, y1, ratio in dbg:
            print(f"    면적={area:<8} rect=({x0},{y0},{x1},{y1}) "
                  f"{x1 - x0}x{y1 - y0} 비율={ratio:.2f}")
        print(f"선택: {box}")
    except hts_anchor.AnchorError as exc:
        print(f"앵커 실패: {exc}")
        hts_exec.capture(ad.order, "diagnose_full")
        return 6
    ad.layout = hts_anchor.build_layout(box)
    rc = win32gui.GetWindowRect(ad.order)
    print(f"주문창 hwnd={ad.order}  크기={rc[2] - rc[0]}x{rc[3] - rc[1]}")
    print("가격칸(노란 박스) 기준 계산 좌표:")
    for k, v in ad.layout.items():
        print(f"  {k:<20} {v}")

    # 모든 Edit 을 스타일과 함께 덤프한다.
    # 종목 칸과 주문금액 칸을 좌표로 구분하려다 반복해서 틀렸다.
    # 주문금액은 ES_READONLY(0x0800) 이고 종목은 편집 가능하다는 점이 확실한 구분자다.
    ES_READONLY = 0x0800
    GWL_STYLE = -16
    edits = []

    def cb(h, _):
        if win32gui.GetClassName(h) != "Edit":
            return
        r2 = win32gui.GetWindowRect(h)
        st = ctypes.windll.user32.GetWindowLongW(h, GWL_STYLE)
        edits.append({
            "hwnd": h,
            "rect": [r2[0] - rc[0], r2[1] - rc[1], r2[2] - rc[0], r2[3] - rc[1]],
            "w": r2[2] - r2[0], "h": r2[3] - r2[1],
            "visible": bool(win32gui.IsWindowVisible(h)),
            "readonly": bool(st & ES_READONLY),
            "text": win32gui.GetWindowText(h),
        })

    win32gui.EnumChildWindows(ad.order, cb, None)
    print(f"Edit 컨트롤 {len(edits)}개")
    print("  (GetWindowText 는 다른 프로세스 컨트롤에서 빈 문자열을 돌려준다.")
    print("   WM_GETTEXT 를 직접 보내야 실제 값이 읽힌다.)")
    for e in sorted(edits, key=lambda x: (x["rect"][1], x["rect"][0])):
        if e["visible"]:
            wm = hts_exec.read_edit(e["hwnd"])
            print(f"  hwnd={e['hwnd']:<9} rect={e['rect']} {e['w']}x{e['h']} "
                  f"readonly={e['readonly']} GetWindowText={e['text'][:16]!r} "
                  f"WM_GETTEXT={wm[:16]!r}")

    # 전송 게이트 사전 점검.
    #
    # 전송 직전 검사는 종목·수량·가격 세 칸의 값을 되읽어 대조한다. 그중 하나라도
    # 컨트롤을 못 찾으면 **주문을 아예 보내지 않는다.** 그 사실을 15:21 장 마감
    # 직전에 알게 되면 그날 매매가 통째로 날아간다. 여기서 미리 확인한다.
    print()
    print("전송 게이트 점검 (여기서 하나라도 실패하면 실주문이 거부된다)")
    try:
        ad.refresh_layout()
        checks = [
            ("종목칸", ad.symbol_edit),
            ("수량칸", ad.qty_edit),
            ("가격칸", ad.price_edit),
        ]
        gate_ok = True
        for name, hwnd in checks:
            if hwnd:
                print(f"  {name:<6} hwnd={hwnd:<9} 현재값={hts_exec.read_edit(hwnd)!r}")
            else:
                print(f"  {name:<6} 컨트롤을 찾지 못했다 — 이 상태로는 전송이 차단된다")
                gate_ok = False
        title = win32gui.GetWindowText(ad.main)
        mock = hts_exec.MOCK_MARKER in title
        print(f"  모의세션 {'확인' if mock else '아님'}  (창 제목 {title!r})")
        gate_ok = gate_ok and mock
        stale = ad._popups()
        if stale:
            print("  경고: 팝업이 떠 있다 — " + ", ".join(t for _, t in stale))
            gate_ok = False
        print(f"  판정: {'통과 — 실주문 가능' if gate_ok else '실패 — 실주문 차단됨'}")
    except (hts_exec.HtsError, hts_anchor.AnchorError) as exc:
        print(f"  게이트 점검 실패: {exc}")

    full = hts_exec.capture(ad.order, "diagnose_full")
    print(f"전체 캡처: {full}")
    img = Image.open(full)

    for key, val in ad.layout.items():
        if key == "form_crop":
            continue
        if len(val) == 4:
            x1, y1, x2, y2 = val
        else:
            x1, y1 = val[0] - 110, val[1] - 45
            x2, y2 = val[0] + 110, val[1] + 45
        pad = 25
        img.crop((max(0, x1 - pad), max(0, y1 - pad), x2 + pad, y2 + pad)).save(
            hts_exec.SHOTS / f"diagnose_{key}.png")
        print(f"  {key:<20} {val}  -> diagnose_{key}.png")
    return 0


def submitted_path(day: str) -> Path:
    return ROOT / "state" / f"submitted_{day}.json"


def load_submitted(day: str) -> set[str]:
    """오늘 이미 전송한 주문의 키 집합.

    작업을 두 번 실행하면 같은 주문이 또 나간다. 실제로 막을 장치가 없었다.
    주문서 항목마다 키를 만들어 전송 이력을 남기고, 이미 있으면 건너뛴다.
    """
    p = submitted_path(day)
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for r in json.loads(p.read_text(encoding="utf-8")):
        # 같은 키에 여러 줄이 쌓인다(PENDING_SEND -> 결과). 마지막 줄이 현재 상태다.
        out[r["key"]] = r.get("status", "submitted")
    return out


def _ledger_rows(day: str) -> list[dict]:
    """오늘 원장의 모든 행. `load_submitted` 는 키->상태만 주므로 금액이 필요할 때 쓴다."""
    p = submitted_path(day)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def order_key(o: dict) -> str:
    """중복 전송 방지용 키.

    예전에는 수량과 지정가까지 넣었다. 그러면 같은 종목을 다시 주문할 때
    가격이 1틱만 달라져도 **새 키가 되어 중복 방지가 그냥 통과한다.**
    재실행이나 재시도 상황에서 정확히 뚫리라고 만든 것처럼 동작했다.

    이 시스템은 하루 한 번 배치로 돈다. "오늘 이 종목을 이 방향으로 이미
    주문했는가"가 실제로 묻고 싶은 질문이므로 키는 side + code 면 충분하다.
    """
    # 분할 매도는 조각마다 다른 키를 준다.
    #
    # 분할이 붙기 전에는 세 조각이 모두 같은 키였다. 그래서 1/3 을 보낸 뒤
    # 프로세스가 죽고 재시도 작업이 돌면, 그 키가 이미 기록돼 있다는 이유로
    # **남은 2/3 가 통째로 걸러졌다.** 포지션의 1/3 만 팔린 채 나머지 매도가
    # 조용히 사라지고, 그러면 15:21 매수 자금이 모자라 그날 리밸런싱 전체가
    # 무산된다 — 분할을 넣어 막으려던 바로 그 사건이다.
    part = o.get("_split")
    return f"{o['side']}:{o['code']}" + (f"#{part}" if part else "")


#: 자동 재시도를 허용하는 상태. **확인된 미접수만** 다시 보낸다.
RETRYABLE = {"not_accepted"}
#: 사람이 HTS 주문내역과 대조해야 풀리는 상태.
NEEDS_REVIEW = {"pending_send", "unknown", "modal"}


def record_attempt(o: dict, status: str, note: str = "",
                   res=None) -> None:
    """주문 시도를 원장에 남긴다. **클릭 전과 후 모두** 호출한다.

    왜 클릭 전에 남기는가
      예전에는 `res.submitted` 가 True 일 때만 기록했다. 그런데 `submit()` 은
      수량 칸을 못 읽으면 접수 여부와 무관하게 `unknown` 을 돌려준다. 즉
      **실제로 접수된 주문이 원장에 남지 않을 수 있다.** 그 상태에서 15:24
      재시도가 돌면 같은 키가 없으므로 같은 주문을 다시 클릭한다 — 4,500만원
      포지션이 두 배가 된다.

      클릭 직전에 `pending_send` 를 먼저 확정해 두면, 클릭 후 프로세스가 죽어도
      다음 실행이 "이 주문은 이미 손댔다"는 사실을 안다. 접수 여부는 몰라도
      **다시 누르지는 않는다.** 모르는 것을 아는 것보다, 모른다는 사실을
      기록해 두는 편이 안전하다.

      기록은 알림보다 **먼저** 한다. send_photo 의 네트워크 제한시간이 60초라
      그 사이에 프로세스가 끝나면 클릭 사실이 사라진다.
    """
    day = f"{date.today():%Y%m%d}"
    p = submitted_path(day)
    rows = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    row = {
        "key": order_key(o),
        "time": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "side": o.get("side", ""), "code": o.get("code", ""),
        "name": o.get("name", o.get("code", "")),
        "market": o.get("market", ""),
        "qty": o.get("qty"), "price": o.get("limit_price"),
        "note": note,
    }
    if res is not None:
        row["screenshot"] = getattr(res, "screenshot", None)
    rows.append(row)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 원자적 교체. 쓰다 죽으면 원장이 통째로 깨진다.
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def load_orders(day: str) -> list[dict]:
    path = ROOT / "state" / f"orders_{day}.json"
    if not path.exists():
        raise SystemExit(f"주문서가 없다: {path}\n먼저 `python run.py plan` 을 실행할 것.")
    return json.loads(path.read_text(encoding="utf-8"))


def is_trading_day(yyyymmdd: str) -> tuple[bool, str]:
    """그 날 증시가 열리나.

    스케줄러의 매도·매수 작업은 월~금으로 걸려 있지만 **공휴일은 평일이다.**
    대회 기간의 휴장일 09-24, 09-25, 10-05 는 전부 목·금·월이라 그대로 발사된다.
    닫힌 장에 주문을 넣으면 거부되거나, 더 나쁘게는 예약주문으로 남아 다음
    개장에 의도하지 않은 시각에 체결된다.

    판단 근거는 설정 파일의 대회 달력 하나뿐이다. 읽지 못하면 막지 않는다 —
    달력을 못 읽었다고 장중에 주문을 세우는 편이 더 위험하다.
    """
    try:
        from datetime import datetime as _dt
        from imrl.data import TradingCalendar
        cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        cal = TradingCalendar.from_config(cfg["contest"])
        d = _dt.strptime(yyyymmdd, "%Y%m%d").date()
    except Exception as exc:
        return True, f"달력을 읽지 못했다({exc.__class__.__name__}) — 막지 않는다"
    if cal.is_trading_day(d):
        return True, ""
    why = "주말" if d.weekday() >= 5 else "휴장일"
    return False, f"{d} 은 {why}이다"


def main() -> int:
    ap = argparse.ArgumentParser(description="주문서를 HTS 에 입력")
    ap.add_argument("--live", action="store_true", help="실제로 주문을 전송한다")
    ap.add_argument("--only", help="이 종목코드만 처리")
    ap.add_argument("--side", choices=["BUY", "SELL"],
                    help="이 방향만 처리. 매도를 연속매매에 먼저 내보내 대금을 확보한 뒤 "
                         "매수를 종가 동시호가에 넣기 위한 것이다")
    ap.add_argument("--date", default=f"{date.today():%Y%m%d}", help="주문서 날짜 YYYYMMDD")
    ap.add_argument("--no-notify", action="store_true", help="텔레그램 전송 생략")
    ap.add_argument("--diagnose", action="store_true",
                    help="주문창 전체를 캡처하고 계산된 좌표를 덤프한다")
    ap.add_argument("--setup-tasks", action="store_true",
                    help="작업 스케줄러를 재등록한다 (관리자 권한 필요)")
    ap.add_argument("--reset-form", action="store_true",
                    help="주문 폼을 알려진 상태로 되돌린다 (시장가 해제 + 매수 탭)")
    ap.add_argument("--explore", nargs="*", metavar="화면번호",
                    help="메인 창을 캡처하고, 화면번호를 주면 그 화면들을 열어 캡처한다. "
                         "중계실 수치를 HTS 안에서 읽을 수 있는지 찾기 위한 탐색용")
    ap.add_argument("--run-tool", metavar="NAME",
                    help="tools/NAME.py 의 main() 을 이 프로세스(승격) 안에서 실행한다. "
                         "HTS 가 관리자 권한이라 비승격에서는 창을 만질 수 없어, 진단·설정 "
                         "도구를 스케줄러 작업(IMRL_Execute) 경로로 태우기 위한 것이다. "
                         "tools/ 아래 파일명만 받는다.")
    ap.add_argument("--healthcheck", action="store_true",
                    help="HTS 가 주문 가능한 상태인지 점검하고 문제가 있으면 텔레그램으로 알린다")

    # 작업 스케줄러에 등록된 작업은 인자가 고정이다. 등록을 다시 하려면 매번 UAC 를
    # 눌러야 하므로, 파일로 인자를 전달할 수 있게 해 둔다. 한 번 쓰면 지운다.
    argv = sys.argv[1:]
    extra = ROOT / "state" / "exec_args.txt"
    if extra.exists():
        # utf-8-sig 로 읽어 BOM 을 제거한다. PowerShell 의 -Encoding utf8 은 BOM 을
        # 붙이는데, 그대로 두면 argparse 가 '﻿--diagnose' 를 거부하고
        # 로그가 만들어지기 전에 종료해 원인 파악이 어려워진다.
        argv += extra.read_text(encoding="utf-8-sig").split()
        extra.unlink()
    args = ap.parse_args(argv)
    hide_own_console()
    # stderr 도 같은 로그로 보낸다. 작업 스케줄러로 돌 때 예외 메시지가
    # 사라지면 원인 파악이 불가능하다(실제로 겪었다).
    tee = Tee(ROOT / "state" / "execute.log")
    sys.stdout = tee
    sys.stderr = tee

    _mutex = acquire_single_instance()
    if _mutex is None:
        print("execute.py 가 이미 실행 중이다. 전역 마우스·키보드를 공유하면 "
              "주문이 섞이므로 이번 실행을 중단한다.")
        return 6

    if args.setup_tasks:
        return setup_tasks()

    if args.run_tool:
        # 이름만 받는다. 경로 구분자·점이 들어오면 거부 — tools/ 밖으로 못 나간다.
        import re as _re
        name = str(args.run_tool)
        if not _re.fullmatch(r"[A-Za-z0-9_]+", name):
            print(f"--run-tool 이름 형식 오류: {name!r}")
            return 2
        path = ROOT / "tools" / f"{name}.py"
        if not path.exists():
            print(f"도구가 없다: {path}")
            return 2
        import importlib.util
        spec = importlib.util.spec_from_file_location(f"tool_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            rc = mod.main() if hasattr(mod, "main") else 0
            return int(rc or 0)
        except Exception as exc:
            import traceback
            print(f"도구 실패: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            return 6

    if args.reset_form:
        return reset_form()

    if args.healthcheck:
        return healthcheck(notify_on_ok=not args.no_notify)
    if args.explore is not None:
        return explore(args.explore)

    if args.diagnose:
        try:
            return diagnose()
        except Exception as exc:
            import traceback
            print(f"진단 실패: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            return 6

    # 검사 순서가 중요하다.
    #
    # 긴급 정지를 **가장 먼저** 본다. 권한 검사를 앞에 두면, 사람이 KILL 파일을
    # 만들어 둔 상태에서 비승격으로 돌 때 관리자 권한 필요(2)로 끝나 정지가
    # 걸렸다는 사실이 보고되지 않는다. 종료코드로 원인을 읽어야 하는데 엉뚱한
    # 이유가 남는다. 정지는 어떤 상황에서도 확인되고 보고돼야 한다.
    #
    # 주문 유무도 권한보다 앞이다. 주문이 없으면 창을 만질 일이 없고,
    # 매도 없는 날의 15:10 실행이 정확히 그 경우다.
    if kill_switch_active():
        msg = (f"긴급 정지 파일이 있어 주문하지 않는다: {KILL_FILE}\n"
               "재개하려면 이 파일을 지울 것.")
        print(msg)
        if not args.no_notify:
            notify.alert("긴급 정지 활성", msg)
        return 7

    # 장이 닫힌 날에는 창을 만지지 않는다. 모의 실행은 막지 않는다 —
    # 주말에 점검하는 것이 정상적인 사용이다.
    if args.live:
        open_, why = is_trading_day(args.date)
        if not why:
            pass
        elif not open_:
            msg = f"{why}. 주문을 전송하지 않는다."
            print(msg)
            if not args.no_notify:
                notify.alert("휴장일 — 주문 생략", msg)
            return 9
        else:
            print(why)

    if args.live and skip_today_active(args.date):
        msg = f"오늘({args.date}) 건너뛰기 표식이 있어 주문하지 않는다. 내일은 정상 진행된다."
        print(msg)
        if not args.no_notify:
            notify.alert("오늘 매매 건너뜀", msg)
        return 7

    orders = load_orders(args.date)
    if args.only:
        orders = [o for o in orders if o["code"] == args.only]
    if not orders:
        print("처리할 주문이 없다.")
        return 0
    # 방향 필터도 권한보다 앞이다. 오늘 매도가 없으면 15:10 실행은 할 일이
    # 없는데, 뒤에 두면 HTS 연결과 창 조작을 다 한 뒤에야 그것을 안다.
    if args.side:
        before = len(orders)
        orders = [o for o in orders if o["side"].upper() == args.side]
        print(f"방향 필터 {args.side}: {before}건 -> {len(orders)}건")
        if not orders:
            print(f"{args.side} 주문이 없다. 종료한다.")
            return 0

    if not is_admin():
        print("관리자 권한이 필요하다. HTS 가 관리자 권한으로 실행되므로 "
              "일반 권한에서는 창 조작이 UIPI 에 막힌다.")
        return 2


    cfg = load_config()
    ms = market_state(cfg=cfg)

    already = load_submitted(args.date)

    # 방향 필터.
    #
    # 왜 나누는가 — 종가 동시호가는 주문을 전부 접수한 뒤 15:30 에 한꺼번에 체결한다.
    # 즉 15:21 에 매수를 넣는 시점에는 매도가 아직 체결되지 않아 **매도대금이
    # 예수금에 없다.** 90% 투자 상태에서 종목을 교체하려면 90% 를 팔고 90% 를 사야
    # 하는데 그 시점 현금은 10% 뿐이라 매수가 예수금 부족으로 거부된다.
    # 실제로 계산해보면 4,500만원 매수에 1,000만원만 있어 3,500만원이 모자랐다.
    #
    # 그래서 매도는 연속매매 구간(15:10)에 먼저 내보내 즉시 체결시키고,
    # 매수만 종가 동시호가에 넣는다. 매뉴얼 p13 의 총평가금액 산식이
    # "당일매도금액"을 당일 반영한다고 명시하므로 대금은 같은 날 쓸 수 있다.

    # 매도 분할.
    #
    # 종목당 4,500만원을 거래대금 하한 30억 종목에 한 번에 던지면 일거래대금의
    # 1.5% 를 단일 주문으로 내는 것이다. 백테스트는 종가 체결 가정이라 이 충격이
    # 0 으로 잡혀 있다. 그리고 매도가 부분체결되면 15:21 매수 자금이 모자라
    # 그날 리밸런싱 전체가 무산된다.
    #
    # 실측 소요가 3건 22초라 시간 여유는 충분하다. 분할해서 시장에 흡수될 시간을 준다.
    split_n = int(cfg.get("execution", {}).get("sell_split", 1))
    if args.side == "SELL" and split_n > 1 and orders:
        chunked = []
        for o in orders:
            q = int(o["qty"])
            base_q, rem = divmod(q, split_n)
            for k in range(split_n):
                part = base_q + (1 if k < rem else 0)
                if part <= 0:
                    continue
                c2 = dict(o)
                c2["qty"] = part
                c2["_split"] = f"{k + 1}/{split_n}"
                chunked.append(c2)
        print(f"매도 분할: {len(orders)}건 -> {len(chunked)}건 ({split_n}분할)")
        orders = chunked

    # 이미 전송한 주문은 건너뛴다. 작업을 두 번 돌려 같은 주문이 또 나가는 것을 막는다.
    #
    # **분할 뒤, 리스크 검사 앞**이라는 순서가 중요하다.
    #   분할 앞에 두면 세 조각이 아직 하나의 주문이라, 1/3 만 전송된 상태에서
    #   재시도할 때 남은 조각까지 같이 걸러진다. 포지션의 1/3 만 팔린 채 나머지
    #   매도가 조용히 사라지고, 그러면 15:21 매수 자금이 모자라 그날 리밸런싱
    #   전체가 무산된다 — 분할을 넣어 막으려던 바로 그 사건이다.
    #
    #   리스크 검사 뒤에 두면 검사가 **이미 보낸 주문까지 포함해** 평가한다.
    #   재시도 때 남은 조각 하나만 나가는데 게이트는 전체 금액을 보고 판단하므로
    #   현금 검사가 엉뚱한 결론을 낸다. 게이트는 실제로 나갈 것만 봐야 한다.
    if already and args.live:
        keep, done, review = [], 0, []
        for o in orders:
            st = already.get(order_key(o))
            if st is None or st in RETRYABLE:
                keep.append(o)          # 처음이거나 **확인된 미접수**만 다시 보낸다
            elif st in NEEDS_REVIEW:
                review.append((order_key(o), st))
            else:
                done += 1               # submitted — 이미 나갔다
        if done:
            print(f"이미 접수된 주문 {done}건은 건너뛴다.")
        if review:
            # 접수 여부를 모르는 주문이 있다. 다시 누르면 중복 접수가 되고,
            # 안 누르면 미접수로 남는다. 어느 쪽도 자동으로 고를 수 없다.
            #
            # 그래서 **그 주문만** 건너뛰고 나머지는 계속 보낸다. 예전에는 전체
            # 실행을 중단했는데, 그러면 불명 주문 하나가 한 번도 안 나간 주문까지
            # 막아 무인 운영이 통째로 멈춘다. 다시 클릭하지 않는다는 안전성은
            # 그대로 지키면서 나머지 주문은 진행하는 편이 낫다.
            lines = [f"{k} = {s}" for k, s in review]
            print("접수 여부 불명 주문은 건너뛴다 (다시 누르지 않는다):")
            for l in lines:
                print("  " + l)
            print("  HTS 주문내역에서 접수 여부를 확인한 뒤,")
            print(f"  state/submitted_{args.date}.json 의 status 를 "
                  "'submitted' 또는 'not_accepted' 로 고칠 것.")
            if not args.no_notify:
                notify.alert("접수 불명 주문 있음 — 해당 주문만 건너뜀",
                             chr(10).join(lines + [
                                 f"나머지 {len(keep)}건은 정상 진행한다.",
                                 "HTS 주문내역과 대조가 필요하다."]))
        orders = keep
        if not orders:
            print("전송할 새 주문이 없다.")
            return 0

    print(f"장 상태: {ms}   주문 {len(orders)}건   모드: {'실전송' if args.live else '드라이런'}")

    if args.live and ms in ("휴장(주말)", "휴장일", "장외", "대회기간 아님"):
        print(f"{ms} 이므로 실제 전송을 거부한다. 드라이런으로 다시 실행할 것.")
        return 3

    # 집행 중에는 화면을 독점한다. 전역 마우스·키보드를 쓰기 때문에 다른 창이
    # 포그라운드를 가져가거나 클릭 지점을 덮으면 주문이 거부된다(그렇게 설계했다 —
    # 엉뚱한 창에 입력하는 것보다 거부되는 편이 낫다).
    #
    # 그래서 시작 직전에 알린다. 실측상 3건에 13~39초이므로 1분이면 끝난다.
    if not args.no_notify:
        try:
            mode = "실전송" if args.live else "드라이런"
            notify.send("\n".join([
                f"자동매매 집행 시작 ({len(orders)}건, {mode}).",
                "약 1분간 컴퓨터 화면을 사용하지 말 것 — "
                "다른 창이 앞에 오면 주문이 거부된다.",
            ]))
        except Exception:
            pass

    adapter = hts_exec.OrderAdapter(dry_run=not args.live)
    try:
        adapter.connect()
        # 전송 전 검사에서 계좌가 바뀌지 않았는지 확인하는 데 쓴다
        # 키 이름은 반드시 config 와 일치해야 한다.
        # 9.7.6 에서 모의세션 판정을 계좌 대조로 바꾸면서 healthcheck 만 고치고
        # 여기(실전송 경로)는 옛 이름 account_prefix 로 남아 있었다. 그러면
        # expected_account 가 "" 가 되고 _preflight 가 "기대 계좌번호가 설정돼
        # 있지 않다"로 **모든 주문을 차단**한다. 드라이런은 submit() 이 먼저
        # 리턴해 이 분기를 안 타므로 드라이런 4/4 성공으로도 안 잡혔다.
        adapter.expected_account = cfg.get("contest", {}).get("mock_account", "")
        if not adapter.expected_account:
            print("config.contest.mock_account 가 비어 있다. 계좌 대조 없이는 "
                  "실계좌 오발주를 막을 수 없으므로 중단한다.")
            return 2
        acct = adapter.read_account()
        print(f"계좌/필명: {acct or '(읽기 실패)'}")
    except hts_exec.HtsError as exc:
        print(f"HTS 연결 실패: {exc}")
        if not args.no_notify:
            notify.alert("HTS 연결 실패", str(exc))
        return 4

    # --- 전송 직전 독립 리스크 검사 ------------------------------------- #
    #
    # 주문서는 15:05 에 만들어지고 전송은 15:21 이다. 그 사이에 시세가 변하고,
    # 종목이 정지될 수 있고, 주문서 파일이 손상되거나 오래된 것일 수 있다.
    # 그래서 **전송 시점에** 시세를 다시 받아 검사한다. 계획 시점 검사만으로는
    # "9분 전에는 맞았던 주문"을 막지 못한다.
    #
    # 이 검사는 전략과 완전히 분리돼 있다. 왜 그 종목을 골랐는지 보지 않고,
    # 나가면 안 되는 주문인지만 본다.
    live_quotes = {}
    for o in orders:
        try:
            live_quotes[o["code"]] = data.fetch_quote(o["code"])
        except Exception as exc:
            print(f"  시세 조회 실패 {o['code']}: {exc}")

    principal = int(cfg.get("contest", {}).get("principal", 100_000_000))
    board = state.load_leaderboard()
    equity = principal + int(principal * float(board.get("my_return_pct", 0.0)) / 100)

    # 계획 단계가 남긴 예수금 추정치를 읽는다.
    #
    # HTS 에서 주문가능금액을 읽는 경로가 없어 이 값은 run.py 가 계산한 추정치다.
    # 완전히 독립적이지 않다는 한계를 알고 쓴다 — 그래도 리스크 게이트가 현금을
    # 아예 안 보는 것보다는 낫다. 파일이 없으면 None 이 넘어가 WARN 만 남는다.
    cash_estimate = None
    meta_path = Path(f"state/meta_{date.today():%Y%m%d}.json")
    if meta_path.exists():
        try:
            _meta = json.loads(meta_path.read_text(encoding="utf-8"))
            cash_estimate = int(_meta.get("cash_estimate"))
            # 평가금액도 계획 시점의 값을 쓴다. 리더보드 수익률은 갱신일을 모른다.
            if _meta.get("equity"):
                equity = int(_meta["equity"])
        except Exception as exc:
            print(f"  예수금 추정치를 읽지 못했다: {exc}")

    # 15:10 에 접수된 매도 대금을 현금에 더한다.
    #
    # meta 의 cash_estimate 는 15:05 계획 시점, 즉 **매도 전** 값이다. 그런데
    # 15:21 매수는 `--side BUY` 로 돌아 주문서에 매도가 한 건도 없다. 그래서
    # 리스크 게이트의 "매수 <= 현금 + 매도대금" 검사에서 매도대금이 0 이 되고,
    # 매도가 정상 체결됐어도 프로그램은 그걸 모른 채 INSUFFICIENT_CASH 로
    # 막는다. 1일차는 현금이 가득해 숨고 **2일차 리밸런싱부터 터진다.**
    #
    # HTS 에서 주문가능금액을 읽는 경로가 없으므로, 오늘 원장에 **접수됨으로
    # 확정된** 매도만 세고 체결률을 곱해 보수적으로 잡는다. 접수 불명·미접수
    # 매도는 세지 않는다.
    if args.side == "BUY" and cash_estimate is not None:
        fill_rate = float(cfg.get("requirements", {}).get("assumed_fill_rate", 0.85))
        sold = 0
        for row in _ledger_rows(args.date):
            if row.get("status") == "submitted" and str(row.get("side", "")).upper() == "SELL":
                sold += int(row.get("qty") or 0) * int(row.get("price") or 0)
        if sold:
            add = int(sold * fill_rate)
            print(f"  접수된 매도 {sold:,}원 x 체결률 {fill_rate:.0%} = {add:,}원을 "
                  f"예수금에 더한다 ({cash_estimate:,} -> {cash_estimate + add:,}).")
            cash_estimate += add

    rep = risk.check_orders(cfg, orders, live_quotes, equity, principal,
                            market_open=ms in ("정규시장", "장마감 동시호가", "장전 동시호가"),
                            cash=cash_estimate)
    print()
    print(rep.text())
    if rep.blocked:
        print("리스크 검사에서 차단됐다. 한 건도 전송하지 않는다.")
        if not args.no_notify:
            notify.alert("리스크 검사 차단",
                         "\n".join(v.message for v in rep.blocks[:8]))
        return 7



    results, failures, placed = [], [], []
    for i, o in enumerate(orders, 1):
        label = f"[{o['side']}] {o['name']}({o['code']}) {o['qty']:,}주 @ {o['limit_price']:,}원"
        print(f"\n{i}/{len(orders)}  {label}")
        # 킬 스위치는 주문마다 다시 본다. 프로세스 시작 시 한 번만 보면
        # 1번 주문을 보고 놀라 파일을 만들어도 2~4번은 그대로 나간다.
        # 정지는 늦게 걸릴수록 쓸모가 없다.
        if kill_switch_active():
            print("  KILL 파일 감지 — 남은 주문을 취소한다.")
            if not args.no_notify:
                notify.alert("킬 스위치 작동",
                             f"{i-1}건 처리 후 중단. 남은 주문 {len(orders)-i+1}건 취소.")
            break

        # **클릭 전에** 시도 사실을 확정한다.
        #
        # place() 안에서 전송 버튼이 눌린 뒤 프로세스가 죽거나 예외가 나면,
        # 접수됐는지 아닌지 알 수 없는 상태가 남는다. 이때 원장이 비어 있으면
        # 다음 재시도가 같은 주문을 다시 누른다. 먼저 pending_send 를 남겨 두면
        # 접수 여부는 몰라도 **다시 누르지는 않는다.**
        if args.live:
            record_attempt(o, "pending_send", "전송 버튼을 누르기 직전")

        try:
            res = adapter.place(o["side"], o["code"], int(o["qty"]), int(o["limit_price"]))
        except (hts_exec.HtsError, hts_exec.AnchorError, ValueError, OSError) as exc:
            print(f"  실패: {exc}")
            # 예외가 클릭 전에 났는지 후에 났는지 알 수 없다. pending_send 상태를
            # 그대로 두어 다음 실행이 사람 확인을 요구하게 한다.
            failures.append({"order": o, "error": str(exc)})
            if not args.no_notify:
                notify.alert("주문 입력 실패", f"{label}\n\n{exc}")
            continue

        print(f"  입력 완료. 전송상태={res.status or ('예' if res.submitted else '드라이런')}")
        if res.note:
            print(f"  {res.note}")
        print(f"  캡처: {res.screenshot}")
        results.append(res)
        placed.append((res, o))          # 결과와 그 조각의 주문을 짝지어 둔다

        # 결과를 **알림보다 먼저** 확정한다. send_photo 제한시간이 60초라
        # 그 사이 프로세스가 끝나면 클릭 사실이 원장에서 사라진다.
        if args.live:
            record_attempt(o, res.status or ("submitted" if res.submitted else "unknown"),
                           res.note, res)

        if not args.no_notify:
            tag = {"submitted": "전송·접수 확인", "dry_run": "드라이런 — 전송 안 함",
                   "modal": "팝업 발생 — 중단", "not_accepted": "접수 실패",
                   "unknown": "접수 여부 불명"}.get(res.status, res.status)
            caption = "\n".join([f"{i}/{len(orders)} {label}", tag, res.note]).strip()
            notify.send_photo(res.screenshot, caption)

        # 확인 모달이 떠 있으면 다음 주문의 키 입력이 전부 그 창으로 들어간다.
        # 화면에 보이지 않는 곳에 숫자를 타이핑하는 셈이므로 즉시 중단한다.
        if res.status == "modal":
            failures.append({"order": o, "error": res.note})
            if not args.no_notify:
                notify.alert(
                    "팝업으로 중단",
                    "\n".join([label, res.note,
                               f"남은 주문 {len(orders)-i}건을 보내지 않았다."]))
            break

        if res.status in ("not_accepted", "unknown"):
            failures.append({"order": o, "error": res.note})

        # 전송했다는 사실은 체결을 뜻하지 않는다. 지정가는 미체결·부분체결이
        # 정상이고 거부될 수도 있다. 원장은 "눌렀다/접수됐다"까지만 말하고,
        # 실제 체결은 중계실·체결내역 확인 후 `run.py fill` 로 넣는다.

    # 접수된 주문을 **추정 체결**로 보유에 반영한다.
    #
    # 무인 운영에서 이게 없으면 다음 날이 무너진다. 보유가 비어 있으면
    # make_orders 가 held=0 으로 보고 목표 수량 전체를 다시 매수하고, 예수금
    # 추정도 보유 평가를 0 으로 잡아 통과시킨다. 이미 90% 투자된 계좌에 90%
    # 매수가 또 나가는 것이다.
    #
    # 체결을 확인할 수 없으므로 이것은 사실이 아니라 **가정**이다. 그래서
    # assumed 표시를 붙여 실제 체결로 교정할 수 있게 남긴다. 가정의 방향도
    # 의도적이다 — 안 샀는데 샀다고 보면 다음 날 없는 것을 팔려다 그 주문만
    # 거부되지만, 샀는데 안 샀다고 보면 두 배로 산다. 후자가 훨씬 나쁘다.
    # 회전율은 assumed_fill_rate 0.85 로 이미 낮춰 잡으므로 과대계상되지 않는다.
    accepted = [(r, o) for r, o in placed if getattr(r, "submitted", False)]
    if args.live and accepted:
        # 원장이 보유보다 새로우면(지난 기록이 도중에 끊긴 흔적) 보유를 원장에서 다시 만든다.
        if state.heal_positions():
            print("보유를 원장에서 다시 만들었다 (이전 기록이 도중에 끊긴 흔적).")
        # 조각 키를 함께 넣는다. 분할 매도 세 조각은 값이 전부 같아서, 키가 없으면
        # 기록 단계에서 하나로 접힌다 (state.trade_id 참조).
        assumed = [{"date": f"{date.today():%Y-%m-%d}", "side": r.side, "code": r.code,
                    "name": o.get("name", r.code), "market": o.get("market", ""),
                    "qty": int(r.qty), "price": int(r.price), "assumed": True,
                    "key": order_key(o)}
                   for r, o in accepted]
        added, dup = state.record_trades(assumed)
        pos = state.apply_fills_to_positions(added)
        print()
        print(f"추정 체결 {len(added)}건을 보유에 반영했다"
              + (f" (중복 {dup}건 무시)" if dup else "") + ".")
        for c, p in pos.items():
            print(f"  {p.get('name', c)} ({c})  {p['qty']:,}주  평단 {p.get('avg_price', 0):,}원")
        print("  실제 체결 확인 후 `run.py fill` 로 교정할 것 (assumed 표시가 붙어 있다).")

    print(f"\n완료: 성공 {len(results)}건, 실패 {len(failures)}건")
    if failures:
        print("실패 목록:")
        for f in failures:
            print(f"  {f['order']['name']}({f['order']['code']}): {f['error']}")
    if not args.live:
        print("\n드라이런이었다. 캡처의 종목명·수량·가격을 확인한 뒤 --live 로 다시 실행할 것.")
    return 0 if not failures else 5


if __name__ == "__main__":
    sys.exit(main())
