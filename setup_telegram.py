"""텔레그램 알림 설정.

준비 (사람이 해야 하는 부분, 2분)
  1. 텔레그램에서 @BotFather 를 찾아 /newbot 실행
  2. 봇 이름과 아이디를 정하면 토큰을 준다 (예: 8123456789:AAF...)
  3. 방금 만든 봇과의 대화방을 열고 아무 메시지나 한 번 보낸다  ← 이걸 해야 chat_id 가 잡힌다

설정
  python setup_telegram.py --token 8123456789:AAF...

  chat_id 는 자동으로 찾는다. 여러 개면 목록을 보여주고 --chat-id 로 지정하게 한다.
  결과는 config/secrets.env 에 저장되며 git 에서 제외된다.

확인
  python setup_telegram.py --test
"""

from __future__ import annotations

import argparse
import getpass
import sys
import time

import requests

from imrl import notify

API = "https://api.telegram.org/bot{token}/{method}"


def get_me(token: str) -> dict:
    r = requests.get(API.format(token=token, method="getMe"), timeout=15)
    j = r.json()
    if not j.get("ok"):
        raise SystemExit(f"토큰이 유효하지 않다: {j.get('description')}")
    return j["result"]


def find_chats(token: str) -> list[dict]:
    """봇에게 온 메시지에서 chat_id 를 수집한다."""
    r = requests.get(API.format(token=token, method="getUpdates"), timeout=20)
    j = r.json()
    if not j.get("ok"):
        raise SystemExit(f"getUpdates 실패: {j.get('description')}")

    chats: dict[int, dict] = {}
    for upd in j.get("result", []):
        msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post")
        if not msg:
            continue
        c = msg.get("chat", {})
        if c.get("id") is not None:
            chats[c["id"]] = c
    return list(chats.values())


def describe(chat: dict) -> str:
    name = chat.get("title") or " ".join(
        x for x in (chat.get("first_name"), chat.get("last_name")) if x
    ) or chat.get("username") or "(이름 없음)"
    return f"{chat['id']}  {name}  [{chat.get('type')}]"


def wait_for_chat(token: str, username: str, seconds: int = 120) -> list[dict]:
    """대화방이 잡힐 때까지 기다린다.

    토큰을 넣는 시점에 아직 봇에게 메시지를 보내지 않은 경우가 흔하다.
    그때마다 토큰을 다시 입력하게 만들지 않으려고, 여기서 기다린다.
    """
    print()
    print(f"  대화방을 찾는 중... 텔레그램에서 @{username} 에게 아무 메시지나 보내라.")
    deadline = time.time() + seconds
    while time.time() < deadline:
        chats = find_chats(token)
        if chats:
            return chats
        left = int(deadline - time.time())
        print(f"\r  대기 중... {left}초 남음 (Ctrl+C 로 중단)", end="", flush=True)
        time.sleep(3)
    print()
    return []


def cmd_setup(args) -> int:
    token = args.token.strip()
    me = get_me(token)
    username = me.get("username")
    print(f"봇 확인: @{username} ({me.get('first_name')})")

    # chat_id 를 못 찾더라도 토큰은 먼저 저장한다. 재시도 시 다시 입력하지 않게 하려는 것이다.
    notify.save_secrets({"TELEGRAM_BOT_TOKEN": token})

    chat_id = args.chat_id
    if not chat_id:
        chats = find_chats(token)
        if not chats:
            chats = wait_for_chat(token, username)
        if not chats:
            print()
            print("  chat_id 를 찾지 못했다. 토큰은 저장해 두었다.")
            print(f"  @{username} 대화방에 메시지를 보낸 뒤 아래 명령으로 이어서 진행할 것:")
            print("    python setup_telegram.py --resume")
            return 2
        if len(chats) > 1:
            print("\n여러 대화방이 잡혔다. --chat-id 로 하나를 지정할 것:")
            for c in chats:
                print("  " + describe(c))
            return 3
        chat_id = str(chats[0]["id"])
        print(f"\nchat_id 자동 탐지: {describe(chats[0])}")

    notify.save_secrets({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": str(chat_id)})
    print(f"저장 완료: {notify.SECRETS}")

    ok = notify.send(
        "<b>iM Rookie League 자동매매 시스템</b>\n\n"
        "텔레그램 연결 완료. 이 대화방으로 주문서·체결 현황·자격요건 경보가 온다."
    )
    print("테스트 메시지 전송:", "성공" if ok else "실패")
    return 0 if ok else 4


def cmd_test(_args) -> int:
    if not notify.is_configured():
        print(f"설정이 없다. 먼저 --token 으로 설정할 것. ({notify.SECRETS})")
        return 2
    s = notify.load_secrets()
    print(f"chat_id = {s.get('TELEGRAM_CHAT_ID')}")
    ok = notify.send("테스트 메시지. 정상 수신되면 설정 완료다.")
    print("전송:", "성공" if ok else "실패")
    return 0 if ok else 3


def prompt_token() -> str:
    """토큰을 화면에 남기지 않고 입력받는다.

    getpass 는 입력을 에코하지 않는다. 터미널 스크롤백이나 화면 공유에
    토큰이 남지 않도록 하기 위한 것이다.
    """
    print("=" * 60)
    print("  텔레그램 알림 설정")
    print("=" * 60)
    print()
    print("  준비:")
    print("   1) 텔레그램에서 @BotFather → /newbot → 토큰 받기")
    print("   2) 만든 봇 대화방을 열고 아무 메시지나 한 번 보내기")
    print()
    print("  토큰은 화면에 표시되지 않는다. 붙여넣고 Enter.")
    print()
    token = getpass.getpass("  봇 토큰: ").strip()
    print()
    return token


def main() -> int:
    ap = argparse.ArgumentParser(description="텔레그램 알림 설정")
    ap.add_argument("--token", help="@BotFather 가 준 봇 토큰")
    ap.add_argument("--chat-id", help="대화방 id (자동 탐지 실패 시에만 지정)")
    ap.add_argument("--test", action="store_true", help="현재 설정으로 테스트 메시지 전송")
    ap.add_argument("--interactive", action="store_true", help="토큰을 직접 입력받는다")
    ap.add_argument("--resume", action="store_true",
                    help="저장된 토큰으로 chat_id 탐지만 다시 시도한다")
    args = ap.parse_args()

    if args.test:
        return cmd_test(args)

    if args.resume and not args.token:
        saved = notify.load_secrets().get("TELEGRAM_BOT_TOKEN")
        if not saved:
            print("저장된 토큰이 없다. --token 으로 먼저 설정할 것.")
            return 1
        args.token = saved
        args.interactive = True

    if not args.token:
        if args.interactive or sys.stdin.isatty():
            args.token = prompt_token()
            if not args.token:
                print("  토큰이 비어 있다. 중단한다.")
                return 1
        else:
            ap.print_help()
            return 1

    try:
        rc = cmd_setup(args)
    except SystemExit as exc:
        print(f"\n  실패: {exc}")
        rc = 5

    if args.interactive or sys.stdin.isatty():
        print()
        input("  Enter 를 누르면 창이 닫힌다. ")
    return rc


if __name__ == "__main__":
    sys.exit(main())
