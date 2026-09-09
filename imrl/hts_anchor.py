"""주문 폼의 위치를 화면에서 직접 찾는다.

왜 이렇게 하는가
  주문 화면의 폼 위치는 고정이 아니다. 다음이 전부 좌표를 바꾼다.
    - 좌측 호가창 패널 폭 (스플리터를 움직이면 폼이 좌우로 이동)
    - 창 최대화 (창만 커지고 내용은 좌상단에 원래 크기로 그려진다)
    - 매수/매도 탭 전환
  절대좌표, 창 크기 고정, 배율 외삽을 차례로 시도했으나 전부 조용히 어긋났다.
  특히 종목 칸으로 알고 클릭한 곳이 실제로는 주문금액 칸이었던 적이 있다.
  좌표가 틀리면 수량·가격이 안 들어가고, 최악의 경우 엉뚱한 곳을 누른다.

방법
  가격 입력칸은 주문창에서 유일하게 연노란색으로 칠해져 있다. 이 색 블록을
  찾아 기준점으로 삼고, 나머지 좌표는 가격칸의 폭·높이 배수로 계산한다.
  폼이 어디로 옮겨가든, 크기가 바뀌든 따라간다.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# 기준 배율에서 가격칸 = (1355,374)-(1553,422): 폭 198, 높이 48
# 같은 배율에서 행 간격은 69 (수량 305 → 가격 374). 높이 대비 1.4375 배.
ROW_PITCH_RATIO = 69 / 48

# 가격칸 왼쪽 위를 원점으로 한 오프셋. 폭·높이의 배수로 두어 배율에 무관하게 만든다.
OFFSETS = {
    "symbol": (0.0, -2 * ROW_PITCH_RATIO),
    "qty": (0.0, -1 * ROW_PITCH_RATIO),
    # 탭 y 도 재실측(2026-09-05). 계산 140 vs 실제 183.
    "tab_sell": (-140 / 204, -171 / 56),
    "tab_buy": (-51 / 204, -171 / 56),
    # 체크박스 두 개는 초기값이 옛 화면을 눈대중한 값이라 어긋나 있었다.
    # 2026-09-05 진단 캡처로 실측해 보정했다.
    # 2026-09-05 재실측. 이전 값은 전송 버튼을 **버튼 아래 상태바**에 떨어뜨렸고
    # (계산 707 vs 실제 버튼 y 595~693) 자동(현재가) 체크박스는 148px 오른쪽으로
    # 밀려 있었다. 원인은 비율 외삽이다 — 전송 버튼 배수가 가격칸 폭의 4.69배라
    # 가격칸 크기가 198->204 로 3% 커지자 오차가 수십 px 로 증폭됐다.
    #
    # 그래서 가까운 것만 비율로 두고 **전송 버튼은 색으로 직접 찾는다**(find_submit_button).
    # 아래 값은 가격칸 (1346,354,1550,410) 204x56 기준 실측이다.
    "auto_price_checkbox": (15 / 204, 84 / 56),
    "market_checkbox": (407 / 204, 26 / 56),
    "submit_button": (926 / 204, 290 / 56),   # 색 탐지 실패 시 폴백
}


def find_submit_button(img: Image.Image) -> tuple[int, int] | None:
    """매수/매도 전송 버튼을 색으로 찾는다. 찾으면 중심 좌표, 못 찾으면 None.

    버튼은 크고 진한 파란 사각형이라 배경·입력칸과 확실히 구분된다.
    비율 외삽으로 잡던 시절 계산 좌표가 버튼 아래 상태바에 떨어졌는데,
    그 상태로 클릭하면 **주문이 조용히 전송되지 않는다** — 오류도 안 난다.
    가격칸을 노란색으로 찾는 것과 같은 이유로 여기도 색을 쓴다.
    """
    a = np.asarray(img.convert("RGB")).astype(int)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mask = (b > 120) & (b - r > 50) & (b - g > 25)

    h, w = mask.shape
    best = None
    # 폼은 창 우측에 있다. 좌측 시세판의 파란 요소를 배제한다.
    x_from = int(w * 0.55)
    cols = mask[:, x_from:]
    # 가로로 충분히 넓은 행만 남겨 버튼 몸통을 찾는다
    wide = cols.sum(axis=1)
    thresh = max(60, int((w - x_from) * 0.08))
    rows = np.nonzero(wide > thresh)[0]
    if len(rows) == 0:
        return None

    groups: list[tuple[int, int]] = []
    start = prev = rows[0]
    for y in rows[1:]:
        if y - prev > 3:
            groups.append((start, prev))
            start = y
        prev = y
    groups.append((start, prev))

    for y0, y1 in groups:
        if y1 - y0 < 30:          # 버튼은 충분히 높다. 얇은 띠는 헤더·구분선이다
            continue
        band = cols[y0:y1 + 1]
        xs = np.nonzero(band.sum(axis=0) > (y1 - y0) * 0.6)[0]
        if len(xs) < 80:
            continue
        cx = int(x_from + int((xs.min() + xs.max()) / 2))
        cy = int((y0 + y1) // 2)
        # 가장 아래쪽(=주문 폼 하단)의 큰 파란 블록을 전송 버튼으로 본다
        if best is None or cy > best[1]:
            best = (cx, cy)
    return best


def is_checked(img: Image.Image, point: tuple[int, int], box: int = 9) -> bool:
    """체크박스가 켜져 있는지 픽셀로 판별한다.

    체크된 상자에는 어두운 체크 표시가 있고, 해제된 상자는 안이 흰색이다.
    상태를 읽을 API 가 없어(커스텀 렌더링) 화면을 보는 수밖에 없다.

    켜져 있는 '자동(현재가)' 를 모르고 두면 가격이 현재가로 강제되어
    지정가 주문이 의도와 달라진다. 그래서 반드시 확인한다.
    """
    px = img.load()
    x, y = point
    dark = 0
    total = 0
    for dy in range(-box, box + 1):
        for dx in range(-box, box + 1):
            xx, yy = x + dx, y + dy
            if 0 <= xx < img.width and 0 <= yy < img.height:
                r, g, b = px[xx, yy][:3]
                total += 1
                if r < 140 and g < 140 and b < 140:
                    dark += 1
    if total == 0:
        return False
    return dark / total > 0.12


class AnchorError(RuntimeError):
    """폼 위치를 찾지 못했을 때. 좌표를 추측해서 진행하면 안 된다."""


def _is_pale_yellow(px) -> bool:
    r, g, b = px[0], px[1], px[2]
    return r > 235 and g > 235 and b < 215 and (r - b) > 40


def _components(mask: list[list[bool]], w: int, h: int) -> list[tuple[int, int, int, int, int]]:
    """4방향 연결요소의 (픽셀수, x0, y0, x1, y1) 목록.

    행 단위로 이어붙이는 방식은 칸 안의 숫자 텍스트가 노란 픽셀을 끊어
    실패했다. 연결요소로 묶으면 텍스트가 있어도 하나의 박스로 잡힌다.
    """
    seen = [[False] * w for _ in range(h)]
    out = []
    for sy in range(h):
        row = mask[sy]
        for sx in range(w):
            if not row[sx] or seen[sy][sx]:
                continue
            stack = [(sx, sy)]
            seen[sy][sx] = True
            n = 0
            x0 = x1 = sx
            y0 = y1 = sy
            while stack:
                x, y = stack.pop()
                n += 1
                if x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
                if y < y0:
                    y0 = y
                if y > y1:
                    y1 = y
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < w and 0 <= ny < h and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            out.append((n, x0, y0, x1, y1))
    return out


def find_price_box(img: Image.Image, debug: list | None = None) -> tuple[int, int, int, int]:
    """가격 입력칸(연노란색 사각형)의 좌표를 찾는다.

    성능을 위해 2배 축소한 마스크에서 연결요소를 찾고, 원본 좌표로 환산한다.
    마스크는 numpy 로 만든다. 파이썬 픽셀 루프로 하면 주문 한 건에 수십 초가 걸려
    장중에 쓸 수 없다.
    """
    import numpy as np

    step = 2
    arr = np.asarray(img.convert("RGB"), dtype=np.int16)[::step, ::step]
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    m = (r > 235) & (g > 235) & (b < 215) & ((r - b) > 40)
    h, w = m.shape
    mask = m.tolist()

    comps = _components(mask, w, h)
    cands = []
    for n, x0, y0, x1, y1 in comps:
        bw, bh = (x1 - x0 + 1) * step, (y1 - y0 + 1) * step
        if bw < 70 or bh < 20:
            continue
        ratio = bw / bh
        if not (2.0 <= ratio <= 9.0):
            continue
        cands.append((bw * bh, x0 * step, y0 * step, (x1 + 1) * step, (y1 + 1) * step, ratio))

    if debug is not None:
        debug.extend(sorted(cands, reverse=True)[:8])

    if not cands:
        raise AnchorError(
            "가격 입력칸(연노란색)을 찾지 못했다. 주문 화면(1200)이 열려 있고 "
            "다른 창에 가려지지 않았는지 확인할 것."
        )

    cands.sort(reverse=True)
    _, x0, y0, x1, y1, _ = cands[0]
    return x0, y0, x1, y1


def build_layout(price_box: tuple[int, int, int, int],
                 img: Image.Image | None = None) -> dict:
    """가격칸을 기준으로 폼의 나머지 좌표를 계산한다.

    img 를 주면 전송 버튼은 비율이 아니라 **색으로 직접 찾는다.**
    """
    x0, y0, x1, y1 = price_box
    fw, fh = x1 - x0, y1 - y0
    if not (80 <= fw <= 700 and 20 <= fh <= 180):
        raise AnchorError(f"가격칸 크기가 비정상이다: {fw}x{fh}")

    def pt(key: str) -> tuple[int, int]:
        dx, dy = OFFSETS[key]
        return int(x0 + dx * fw), int(y0 + dy * fh)

    def field(key: str) -> tuple[int, int, int, int]:
        x, y = pt(key)
        return (x, y, x + fw, y + fh)

    submit = pt("submit_button")
    if img is not None:
        found = find_submit_button(img)
        if found:
            submit = found

    return {
        "price_rect": (x0, y0, x1, y1),
        "qty_rect": field("qty"),
        "symbol_rect": field("symbol"),
        "tab_sell": pt("tab_sell"),
        "tab_buy": pt("tab_buy"),
        "auto_price_checkbox": pt("auto_price_checkbox"),
        "market_checkbox": pt("market_checkbox"),
        "submit_button": submit,
        "form_crop": (max(0, int(x0 - 1.2 * fw)), max(0, int(y0 - 4.5 * fh)),
                      int(x0 + 6.2 * fw), int(y0 + 4.5 * fh)),
    }


# 주문금액 칸 위치 (가격칸 기준). 캡처 실측 2026-09-05:
# 가격칸 (1346,354,1550,410) 일 때 주문금액 박스 (2106,357,2411,407).
AMOUNT_OFFSET = (760 / 204, 3 / 56, 305 / 204, 50 / 56)


def amount_rect(price_box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = price_box
    fw, fh = x1 - x0, y1 - y0
    dx, dy, w, h = AMOUNT_OFFSET
    ax = int(x0 + dx * fw)
    ay = int(y0 + dy * fh)
    return (ax, ay, int(ax + w * fw), int(ay + h * fh))


def count_amount_glyphs(img: Image.Image, rect: tuple[int, int, int, int]) -> int | None:
    """주문금액 칸의 **숫자 글자 수**를 센다. 판독 불가면 None.

    왜 글자 수인가
      주문금액 = 수량 x 가격 이므로 이 한 값이 두 칸을 동시에 교차검증한다.
      그런데 이 칸은 자식 창이 없어 값을 읽을 수 없고, 폰트 템플릿을 만들려니
      캡처에 0~9 가 다 나오지 않는다(4, 8 이 없었다).

      숫자를 알아보는 대신 **몇 글자인지**만 세면 폰트 지식이 필요 없다.
      그리고 이것만으로 주된 실패 모드가 잡힌다 — 키 입력이 하나 빠져
      3,035 가 3,03 이 되면 금액이 32,656,600 -> 326,080 이 되어 자릿수가 바뀐다.
      쉼표는 폭과 세로 위치가 달라 제외한다.
    """
    a = np.asarray(img.convert("L"))
    x0, y0, x1, y1 = rect
    h, w = a.shape
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        return None
    # 테두리를 피해 안쪽만 본다
    box = a[y0 + 4:y1 - 4, x0 + 4:x1 - 4]
    if box.size == 0:
        return None
    dark = box < 128
    if not dark.any():
        return 0

    col = dark.sum(axis=0)
    groups: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(col):
        if v > 0 and start is None:
            start = i
        elif v == 0 and start is not None:
            groups.append((start, i - 1))
            start = None
    if start is not None:
        groups.append((start, len(col) - 1))

    rows = np.nonzero(dark.any(axis=1))[0]
    if len(rows) == 0:
        return 0
    top, bot = rows.min(), rows.max()
    height = bot - top + 1

    # 실측 형태(2026-09-05, 주문금액 32,656,600):
    #   숫자      폭 16  높이 26  = 전체 잉크높이의 0.62
    #   쉼표      폭  4  높이  8  = 0.19   (아래쪽에만 걸린다)
    #   박스 테두리 폭  1  높이 42  = 1.00   (위아래를 관통한다)
    # 숫자만 폭이 넓고 높이가 중간이다. 이 두 조건으로 나머지가 전부 걸러진다.
    digits = 0
    for g0, g1 in groups:
        seg = dark[:, g0:g1 + 1]
        srows = np.nonzero(seg.any(axis=1))[0]
        if len(srows) == 0:
            continue
        gw = g1 - g0 + 1
        gh = srows.max() - srows.min() + 1
        if gw < 5:                      # 쉼표·테두리는 가늘다
            continue
        if not (0.40 * height <= gh <= 0.85 * height):
            continue                    # 테두리(1.00)·쉼표(0.19) 제외
        digits += 1
    return digits
