"""주문 어댑터 드라이런. 입력까지만 하고 전송 버튼은 누르지 않는다.

사용법: python tools/order_dryrun.py BUY 005930 3 251000
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from imrl import hts_exec  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "state" / "hts_shots"


def main() -> int:
    side = sys.argv[1] if len(sys.argv) > 1 else "BUY"
    code = sys.argv[2] if len(sys.argv) > 2 else "005930"
    qty = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    price = int(sys.argv[4]) if len(sys.argv) > 4 else 251000

    ad = hts_exec.OrderAdapter(dry_run=True).connect()
    res = ad.place(side, code, qty, price)
    out = {"side": res.side, "code": res.code, "qty": res.qty, "price": res.price,
           "submitted": res.submitted, "screenshot": res.screenshot,
           "order_hwnd": ad.order, "main_hwnd": ad.main}
    (OUT / "dryrun.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        import traceback
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "dryrun.json").write_text(
            json.dumps({"error": repr(exc), "traceback": traceback.format_exc()},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(1)
