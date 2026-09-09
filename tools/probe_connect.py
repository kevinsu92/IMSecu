# -*- coding: utf-8 -*-
"""주문창 연결만 해 본다 (입력·클릭·알림 없음).

헬스체크와 같은 `OrderAdapter.connect()` 경로 — 메인 창 크기 확인, 주문창 고정,
폼 좌표 인식 — 만 밟고 끝난다. 주문서 유무를 보지 않으므로 대회 전날 저녁에
돌려도 텔레그램이 울리지 않는다.

용도: 사람이 다른 창에서 일하는 동안 이 경로가 HTS 를 앞으로 끌어오는지 실측.
승격이 필요하므로 `state/exec_args.txt` 에 `--run-tool probe_connect` 를 넣고
IMRL_Execute 작업으로 태운다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from ctypes import windll
    from imrl import hts_exec as H

    fg0 = windll.user32.GetForegroundWindow()
    t0 = time.time()
    ad = H.OrderAdapter(dry_run=True)
    ad.connect()
    fg1 = windll.user32.GetForegroundWindow()
    print(f"probe_connect: connect {time.time() - t0:.1f}s, symbol_edit={bool(ad.symbol_edit)}, "
          f"foreground before={fg0} after={fg1} main={ad.main} "
          f"{'(HTS 가 앞으로 나왔다)' if fg1 == ad.main else '(포그라운드 그대로)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
