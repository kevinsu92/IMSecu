# -*- coding: utf-8 -*-
"""익절·손실복구·뉴스·상황판 자가 점검.

두 옵션은 규정 상한을 건드리는 코드다. 켰을 때 원금 기준 50% 를 넘거나 총노출이
100% 를 넘으면 그날로 실격이다. 그래서 여기서 못박는다.
"""
import io, json, shutil, sys
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
from pathlib import Path
import pandas as pd

fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)

from imrl import portfolio, news
cfg = json.loads(Path('config/settings.json').read_text(encoding='utf-8'))
P = int(cfg['contest']['principal'])
sc = pd.DataFrame({'code': list('ABCDEF'), 'name': list('abcdef'),
                   'score': [9, 8, 7, 6, 5, 4]})

def tg(c, eq):
    return portfolio.build_targets(c, sc, 'phase_early', equity=eq, principal=P)

print("[1] 기본 설정")
ck("익절 기본 꺼짐", not cfg['risk_options'].get('take_profit_enabled'))
ck("손실복구 기본 켜짐", bool(cfg['risk_options'].get('recover_enabled')))

print()
print("[2] 익절 — 꺼져 있으면 아무 일도 없다")
t = tg(cfg, int(P * 1.5))
ck("+50% 에서도 매수 목표가 나온다", len(t) > 0, f"{len(t)}종목")

print()
print("[3] 익절 — 켜면 문턱에서 전량 현금")
c2 = json.loads(json.dumps(cfg)); c2['risk_options']['take_profit_enabled'] = True
c2['risk_options']['take_profit_pct'] = 20.0
ck("문턱 아래(+15%)는 그대로", len(tg(c2, int(P * 1.15))) > 0)
ck("문턱(+20%)에서 목표 0 = 전량 매도", len(tg(c2, int(P * 1.20))) == 0)
ck("그 위(+40%)도 0", len(tg(c2, int(P * 1.40))) == 0)
ck("손실 중(-10%)에는 발동 안 함", len(tg(c2, int(P * 0.90))) > 0)

print()
print("[4] 손실복구 — 뒤지면 노출을 키우되 규정은 지킨다")
BASE_INV = 1.0 - cfg["portfolio"]["phase_early"]["cash_buffer"]   # 평시 노출 (2026-09-07: 95%)
base = tg(cfg, P)['target_weight']
down = tg(cfg, int(P * 0.75))['target_weight']
ck(f"본전은 {BASE_INV:.0%} 노출", abs(base.sum() - BASE_INV) < 1e-6, f"{base.sum():.3f}")
ck("-25% 에서 노출이 커진다", down.sum() > base.sum() + 0.01, f"{down.sum():.3f}")
ck("총노출 100% 이하", down.sum() <= 1.0 + 1e-9, f"{down.sum():.3f}")
worst = 0.0
for eq in range(int(P * 0.4), int(P * 1.6), int(P * 0.05)):
    w = tg(cfg, eq)['target_weight']
    if len(w):
        worst = max(worst, float(w.iloc[0]) * eq / P)
_capw = cfg["requirements"]["max_single_weight_rule"] - cfg["requirements"].get("single_weight_margin", 0.05)
ck(f"어떤 수익률에서도 원금 기준 {_capw:.0%} 를 안 넘는다", worst <= _capw + 1e-6, f"최대 {worst:.1%}")
ck("문턱 위(-15%)에서는 발동 안 함",
   abs(tg(cfg, int(P * 0.85))['target_weight'].sum() - BASE_INV) < 1e-6)
c3 = json.loads(json.dumps(cfg)); c3['risk_options']['recover_enabled'] = False
ck(f"끄면 -25% 에서도 {BASE_INV:.0%}", abs(tg(c3, int(P * 0.75))['target_weight'].sum() - BASE_INV) < 1e-6)

print()
print("[5] 뉴스 — 실패해도 예외를 올리지 않는다")
real = news._fetch
news._fetch = lambda q: (_ for _ in ()).throw(OSError("offline"))
ck("오프라인이면 빈 목록", news.headlines("아무거나") == [])
news._fetch = lambda q: "<rss><item><title>테스트 급등 &amp; 테마</title><pubDate>Mon, 07 Sep 2026 01:00:00 GMT</pubDate><link>https://x</link></item></rss>"
h = news.headlines("테스트")
ck("제목 파싱 + HTML 엔티티 해제", h and h[0]['title'] == "테스트 급등 & 테마", str(h[:1]))
ck("주의어 표시", h and set(h[0]['flags']) == {"급등", "테마"}, str(h[0]['flags']) if h else "")
d = news.collect(["테스트", ""])
ck("빈 이름은 건너뛴다", list(d['by_name'].keys()) == ["테스트"])
news._fetch = real

print()
print("[6] 상황판 — 파일 하나로 서고 데이터가 들어 있다")
import dashboard as dash
out = dash.build(with_news=False)
body = out.read_text(encoding='utf-8')
ck("파일 생성", out.exists() and out.stat().st_size > 5000, f"{out.stat().st_size}B")
ck("데이터가 박혀 있다", '"contest"' in body and '"steps"' in body)
ck("</script> 조기 종료 방지", body.count("</script>") == 2)
# "외부 의존" 은 네트워크에서 무언가를 **불러오는** 태그다. 본문에 적힌
# http://127.0.0.1 안내문은 의존이 아니다 — 문자열이 아니라 태그를 본다.
import re as _re
_ext = _re.findall(r'<(?:script[^>]+src|link[^>]+href)\s*=\s*["\']https?://', body, _re.I)
# "cdn" 문자열 검사는 뺐다 — 뉴스 기사 URL(무작위 토큰)에 우연히 들어가 거짓 실패를 냈다. 태그만 본다.
ck("외부 의존 없음 (네트워크 로드 태그 0개)", not _ext, str(_ext[:2]))
ck("생성 시각이 있다", '"generated"' in body)
# 크롬에서 파일로 열어도 살아 있는 서버로 옮겨 앉는다 — 클로드에서 보는 것과 같아지는 길
ck("파일로 열리면 제어 서버를 찾는 스니펫", "location.protocol !== 'file:'" in body and "location.replace(D.control_url)" in body)
ck("제어 서버 주소가 데이터에 있다", '"control_url": "http://127.0.0.1:8765/"' in body)
ck("시간별 중계실 섹션이 렌더 순서에 있다", "intradaySec()" in body)
ck("연결 표시가 렌더 순서 앞에 있다", "head()+liveIndicator()" in body)
ck("30초 핑 + 복귀 시 새로고침", "fetch('/api/state',{cache:'no-store'})" in body and "location.reload()" in body)
ck("심장박동 데이터", '"heartbeat"' in body)
ck("사이클 섹션이 렌더 순서에 있고 데이터가 박혀 있다", "intradaySec()+relaySec()+cycleSec()" in body and '"cycles"' in body and '"opinions"' in body and '"candidates"' in body)
ck("전문가 카드가 실시간 의견을 그린다", "지금 점검" in body and "byE[e.id]" in body)
ck("뉴스 섹션은 사이클 기록을 먼저 쓴다", '"news_intraday"' in body and "사이클 기록" in Path('tools/dashboard.py').read_text(encoding='utf-8'))
ck("연결 표시에 원천별 신선도", "D.fresh" in body and "기록 없음" in body)
ck("서버 CORS 를 열지 않는다 (no-cors 탐침만)", "no-cors" in body and "Access-Control" not in Path('control.py').read_text(encoding='utf-8'))

print()
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
