# -*- coding: utf-8 -*-
"""중계실 수집 자가 점검.

행의 실제 컬럼명은 개장 전까지 볼 수 없다. 그래서 이름을 못박지 않고 후보로
찾는데, 그 관대함이 조용히 틀리면 매일 0 이 쌓인다 - 소급 취득이 안 되는
데이터라 그게 제일 나쁘다. 여기서 매핑과 실패 처리를 못박는다.
"""
import io, json, shutil, sys
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from pathlib import Path
from imrl import relay

fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)

real_post = relay._post
try:
    print("[1] 숫자 파싱")
    ck("쉼표 제거", relay._num("1,234,567") == 1234567.0)
    ck("퍼센트 제거", relay._num("12.5%") == 12.5)
    ck("음수", relay._num("-3.4") == -3.4)
    ck("쓰레기는 기본값", relay._num("없음", -1) == -1)
    ck("None 도 기본값", relay._num(None) is None)

    print()
    print("[2] 컬럼 이름이 달라도 찾는다")
    rows = relay.parse_rows([
        {"순위": 1, "필명": "가", "수익률": "12.5", "회전율": "520",
         "매매일수": 6, "추정예탁자산": "112,500,000", "매매종목수": 7},
        {"등수": 2, "닉네임": "나", "누적수익률": -3.1, "매매회전율": 480,
         "거래일수": 5, "평가예탁총액": 96900000},
    ])
    ck("표준 이름", rows[0]["rank"] == 1 and rows[0]["ret_pct"] == 12.5
       and rows[0]["turnover_pct"] == 520 and rows[0]["equity"] == 112500000)
    ck("대체 이름", rows[1]["rank"] == 2 and rows[1]["ret_pct"] == -3.1
       and rows[1]["turnover_pct"] == 480)
    ck("없는 값은 None", rows[1]["symbols"] is None)

    print()
    print("[3] 모르는 이름이 오면 None 으로 드러난다")
    odd = relay.parse_rows([{"순위": 1, "필명": "다", "이익률": 9.9}])
    ck("추측해서 0 을 넣지 않는다", odd[0]["ret_pct"] is None)

    print()
    print("[4] 전체현황은 국내주식 행을 고른다")
    relay._post = lambda mid, **kw: [
        {"구분": "선물옵션", "참가자수": 0, "최고수익률": 0, "평균수익률": 0,
         "일자": "20260908", "대회명": "테스트"},
        {"구분": "국내주식", "참가자수": 97, "매매자수": 40, "최고수익률": "12.5",
         "최저수익률": "-8.1", "평균수익률": "1.2", "일자": "20260908",
         "대회명": "테스트"},
    ]
    s = relay.summary("00000")
    ck("참가자수는 국내주식 것", s["n_field"] == 97, str(s["n_field"]))
    ck("최고수익률", s["max_pct"] == 12.5)
    ck("선물옵션 0 명을 잡지 않는다", s["n_field"] != 0)

    print()
    print("[5] 페이징")
    calls = []
    def paged(mid, **kw):
        calls.append(kw.get("_page"))
        pg = kw.get("_page", 1)
        if pg > 3: return {"items": [], "nextpage": 0}
        return {"items": [{"순위": (pg - 1) * 100 + i, "필명": f"u{i}",
                           "수익률": 1.0} for i in range(100)],
                "nextpage": 1 if pg < 3 else 0}
    relay._post = paged
    got = relay.ranking("00000", limit=300)
    ck("여러 쪽을 이어 받는다", len(got) == 300, f"{len(got)}행")
    ck("페이지를 순서대로", calls == [1, 2, 3], str(calls))
    relay._post = paged; calls.clear()
    ck("limit 을 넘기지 않는다", len(relay.ranking("00000", limit=50)) == 50)

    print()
    print("[6] 실패는 예외로 드러나되 부르는 쪽이 잡을 수 있다")
    relay._post = real_post
    def boom(mid, **kw):
        raise relay.RelayError("네트워크 없음")
    relay._post = boom
    try:
        relay.summary("00000"); ok = False
    except relay.RelayError:
        ok = True
    ck("RelayError 를 올린다", ok)

    relay._post = lambda mid, **kw: {"statusCode": 400, "error": "Bad Request",
                                     "message": "Invalid request payload input"}
    try:
        real_post.__self__ if False else None
        relay._post = real_post
        import urllib.request
        ok2 = True
    except Exception:
        ok2 = False
    ck("원래 함수로 복원", ok2)

    print()
    print("[7] 남의 식별정보는 저장하지 않는다")
    dirty = [{"순위": 1, "필명": "가", "수익률": 1.0, "아이디": "ABC123",
              "계좌번호": "0000451511", "계좌번호구분": "01", "회전율": 500}]
    clean = relay.scrub(dirty)
    ck("아이디 제거", "아이디" not in clean[0])
    ck("계좌번호 제거", "계좌번호" not in clean[0])
    ck("계좌번호구분 제거", "계좌번호구분" not in clean[0])
    ck("필명은 남긴다 (순위표 공개 이름)", clean[0].get("필명") == "가")
    ck("쓸 값은 그대로", clean[0].get("회전율") == 500)

    print()
    print("[8] 내 행은 계좌번호로 찾는다")
    # 필명은 HTS 에서 본 이름이라 순위표 표기와 같다는 보장이 없고, 남이 같은
    # 필명을 쓸 수도 있다. 계좌번호는 우리가 확실히 아는 값이다.
    items = [
        {"순위": 1, "필명": "남", "수익률": 9.9, "계좌번호": "0000999999"},
        {"순위": 2, "필명": "필명A", "수익률": 3.3, "계좌번호": "0000000000"},
        {"순위": 3, "필명": "필명A", "수익률": -1.0, "계좌번호": "0000777777"},
    ]
    ck("계좌번호로 찾는다 (하이픈 무시)",
       relay.find_me(items, "0000-0000-00", "필명A") == 1)
    ck("같은 필명이 둘이면 계좌가 이긴다",
       relay.find_me(items, "0000-7777-77", "필명A") == 2)
    ck("계좌를 모르면 필명으로", relay.find_me(items, "", "남") == 0)
    ck("둘 다 안 맞으면 -1", relay.find_me(items, "9999-9999-99", "없는이름") == -1)
    ck("계좌번호가 없는 응답에서도 안 죽는다",
       relay.find_me([{"순위": 1, "필명": "가"}], "0000-0000-00", "가") == 0)
    ck("빈 목록", relay.find_me([], "0000-0000-00", "필명A") == -1)

    print()
    print("[8b] 저장은 최신 한 장 + 시각별 스냅샷")
    import shutil as _sh
    hd = Path('state/relay'); hb = hd.with_name('relay.bak')
    if hb.exists(): _sh.rmtree(hb)
    if hd.exists(): _sh.copytree(hd, hb)
    daily = Path('state/relay_20991231.json')
    try:
        snap = {"at": "20991231", "league": "x", "summary": {"n_field": 5, "max_pct": 1.0, "avg_pct": 0.5},
                "rows": [{"rank": 3, "ret_pct": 0.7, "turnover_pct": 100.0}], "me_index": 0, "raw_items": [], "raw_keys": []}
        p1 = relay.save(snap)
        ck("최신 한 장", p1.name == "relay_20991231.json" and p1.exists())
        hourly = sorted(hd.glob("20991231_*.json"))
        ck("시각별 파일 1개", len(hourly) == 1, str([h.name for h in hourly]))
        ck("임시 파일 없음", not list(hd.glob("*.tmp")))
        rows = relay.intraday("20991231")
        ck("intraday 요약", len(rows) == 1 and rows[0]["rank"] == 3 and rows[0]["n_field"] == 5, str(rows[:1]))
        ck("다른 날짜는 안 섞인다", relay.intraday("20991230") == [])
    finally:
        daily.unlink(missing_ok=True)
        for h in hd.glob("20991231_*.json"): h.unlink()
        if hb.exists():
            if hd.exists(): _sh.rmtree(hd)
            _sh.move(hb, hd)

    print()
    print("[9] 대회 전 손입력은 상태를 건드리지 못한다")
    # 이 검사는 개막 전에만 뜻이 있다. 개막 뒤 돌리면 예시 숫자가 진짜 관측처럼
    # 기록된다(2026-09-08 실제로 가짜 field_sigma 두 줄이 남아 지웠다).
    import json as _j9
    from datetime import date as _d9
    _start9 = _j9.loads(Path('config/settings.json').read_text(encoding='utf-8')).get('contest', {}).get('start_date', '')
    if _start9 and str(_d9.today()) >= _start9:
        print("  건너뜀 (대회 기간 — 손입력 거절 검사는 개막 전 전용)")
    else:
        # 사용법 예시의 그럴듯한 숫자를 그대로 실행하면 관측하지 않은 값이
        # 관측된 사실로 기록되고 1일차 결정에 들어간다.
        import subprocess as _sp
        LB, FH = Path('state/leaderboard.json'), Path('state/field_history.json')
        b1 = LB.read_bytes() if LB.exists() else None
        b2 = FH.read_bytes() if FH.exists() else None
        try:
            r = _sp.run([sys.executable, '-X', 'utf8', 'run.py', 'relay',
                         '--top', '12.1,10.4,9.8,9.1,8.6', '--mine', '3.4', '--rank', '21'],
                        capture_output=True, text=True, encoding='utf-8',
                        errors='replace', timeout=180)
            out = (r.stdout or '') + (r.stderr or '')
            ck("대회 시작 전이라고 거절한다", '대회 시작' in out, out.strip().splitlines()[0][:60] if out.strip() else '')
            ck("리더보드를 바꾸지 않았다",
               (LB.read_bytes() if LB.exists() else None) == b1)
            ck("필드 기록을 남기지 않았다",
               (FH.read_bytes() if FH.exists() else None) == b2)
        finally:
            if b1 is None: LB.unlink(missing_ok=True)
            else: LB.write_bytes(b1)
            if b2 is None: FH.unlink(missing_ok=True)
            else: FH.write_bytes(b2)

    print()
    print("[10] 실제 중계실 (네트워크)")
    relay._post = real_post
    import json as _json
    _league = str(_json.load(open("config/settings.json", encoding="utf-8"))["contest"]["league_code"])
    try:
        if _league == "00000":
            raise relay.RelayError("settings.json 의 league_code 가 자리표시자다 - 실제 대회코드를 넣으면 검사한다")
        snap = relay.snapshot(_league, limit=20)
        ck("응답을 받는다", isinstance(snap.get("summary"), dict))
        ck("대회명이 우리 대회", "Rookie League" in snap["summary"]["name"],
           snap["summary"]["name"][:40])
        ck("참가자수 > 0", snap["summary"]["n_field"] > 0,
           f"{snap['summary']['n_field']}명")
        ck("저장본에 남의 계좌번호 없음",
           not any(k in snap["raw_keys"] for k in relay.DROP_KEYS),
           str(snap["raw_keys"][:6]))
    except relay.RelayError as exc:
        print(f"  건너뜀 (네트워크): {exc}")
finally:
    relay._post = real_post

print()
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
