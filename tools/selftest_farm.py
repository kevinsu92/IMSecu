# -*- coding: utf-8 -*-
"""ETF 제외 확정 시 주식 폴백이 실제로 도는지 확인한다."""
import io, json, shutil, subprocess, sys
sys.stdout.reconfigure(encoding='utf-8')
from datetime import date
from pathlib import Path
S = Path('state'); DAY = f"{date.today():%Y%m%d}"
CFG = Path('config/settings.json')
shutil.copy(CFG, CFG.with_suffix('.fbbak'))
fails, checks = [], 0
def ck(n, c, d=""):
    global checks; checks += 1
    print(("  OK   " if c else "  FAIL ") + n + (("  " + d) if d else ""))
    if not c: fails.append(n)
try:
    # 1) 저변동 종목 회전 자체
    sys.path.insert(0, '.')
    from imrl import turnover, universe
    cfg = json.loads(CFG.read_text(encoding='utf-8'))
    uni = universe.build(cfg)
    p0 = turnover.stock_pool(uni, set(), 0, n=2)
    p1 = turnover.stock_pool(uni, set(), 1, n=2)
    p5 = turnover.stock_pool(uni, set(), 5, n=2)
    ck("폴백 풀 2종목", len(p0) == 2, str([x['name'] for x in p0]))
    ck("날짜마다 종목이 바뀐다",
       {x['code'] for x in p0} != {x['code'] for x in p1},
       f"{[x['name'] for x in p0]} vs {[x['name'] for x in p1]}")
    ck("여러 날 돌려도 반복되지 않는다",
       len({x['code'] for x in p0 + p1 + p5}) >= 5,
       str(len({x['code'] for x in p0 + p1 + p5})))
    ck("보유 종목은 제외된다",
       not ({x['code'] for x in turnover.stock_pool(uni, {p0[0]['code']}, 0, n=2)}
            & {p0[0]['code']}))

    # 2) plan 이 실제로 폴백을 쓰는가
    print()
    cfg['rules_status'] = {'etf_counts_for_turnover': False,
                           '_confirmed_at': '2026-09-08T16:00:00'}
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    for f in (f'orders_{DAY}.json', f'meta_{DAY}.json'):
        (S / f).unlink(missing_ok=True)
    r = subprocess.run([sys.executable, '-X', 'utf8', 'run.py', 'plan'],
                       capture_output=True, text=True, encoding='utf-8', timeout=900)
    ck("ETF 제외 확정 시 plan 정상 종료", r.returncode == 0, r.stderr[-150:] if r.returncode else "")
    ck("주식 폴백을 쓴다고 보고", "개별주식 폴백" in r.stdout,
       str([l.strip() for l in r.stdout.splitlines() if "폴백" in l or "회전율:" in l][:2]))
    op = S / f'orders_{DAY}.json'
    if op.exists():
        o = json.loads(op.read_text(encoding='utf-8'))
        etf = {'459580', '469830', '357870'}
        ck("주문서에 파밍 ETF 가 없다", not (etf & {x['code'] for x in o}),
           str([x['code'] for x in o]))
finally:
    shutil.move(CFG.with_suffix('.fbbak'), CFG)
    for f in (f'orders_{DAY}.json', f'meta_{DAY}.json'):
        (S / f).unlink(missing_ok=True)
    print("\n  설정 원복됨")
print()
print(f"검사 {checks}건 / 실패 {len(fails)}건")
for f in fails: print("  X " + f)
sys.exit(1 if fails else 0)
