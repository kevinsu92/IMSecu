# -*- coding: utf-8 -*-
"""공동 시장·경쟁자 시나리오 생성.

왜 공동인가
  후보 행동을 비교하려면 **같은 미래**에서 재봐야 한다. 후보마다 다른 난수를 쓰면
  차이의 대부분이 난수 오차가 된다. 그래서 외생 충격(가격 경로, 경쟁자 충격)은
  경로 단위로 한 번 뽑아 모든 후보가 공유하고, 후보에 따라 달라지는 것은
  주문·체결·현금뿐이다.

  경쟁자도 같은 시장에서 거래한다. 독립 로그정규 299명만 뽑으면 공통 급등·급락이
  사라져, "뒤처졌을 때 같은 포트폴리오를 들고만 있어도 공통 상승으로 역전된다"는
  잘못된 결론이 나온다. 그래서 경쟁자 수익률에 **같은 경로의 시장 요인**을 넣는다.

가격 모델 — 블록 부트스트랩
  명세 7.3 의 첫 번째 방식이다. 과거 패널에서 **같은 날짜 블록**을 통째로 뽑아
  종목 간 상관과 연속성을 보존한다. 파라미터를 학습하지 않으므로 짧은 표본에서
  과적합하지 않고, 실제로 일어났던 조합만 재현한다.

  한계는 정직하게 적는다: 표본 밖의 사건은 나오지 않고, 유니버스가 오늘 기준으로
  선택돼 있어 생존 편향이 남는다. 이것은 예측이 아니라 **후보 비교용 공통 척도**다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: 경쟁자 모형. 이름은 가정의 이름이지 관측 결과가 아니다(명세 8.4).
FIELD_MODELS = ("FIELD_SHARED_MARKET", "FIELD_THEME_CLUSTER", "FIELD_LATE_RISK_UP",
                "FIELD_LEADER_REDUCES", "FIELD_HEAVIER_TAIL")

#: 모형별 파라미터. 근거가 관측이 아니라 가정이므로 scenario_only 로만 쓴다.
#:   beta      경쟁자 수익률이 시장 요인에 얹히는 정도
#:   idio      경쟁자 고유 변동성(20일 누적, 비율)
#:   skew      우측 꼬리 두께(로그정규 시프트)
#:   late_mult 후반 위험 확대 배수 (1.0 = 없음)
FIELD_PARAMS = {
    "FIELD_SHARED_MARKET":  {"beta": 1.20, "idio": 0.18, "skew": 0.00, "late_mult": 1.00},
    "FIELD_THEME_CLUSTER":  {"beta": 1.00, "idio": 0.28, "skew": 0.15, "late_mult": 1.00},
    "FIELD_LATE_RISK_UP":   {"beta": 1.10, "idio": 0.22, "skew": 0.10, "late_mult": 1.45},
    "FIELD_LEADER_REDUCES": {"beta": 0.85, "idio": 0.16, "skew": 0.05, "late_mult": 0.75},
    "FIELD_HEAVIER_TAIL":   {"beta": 1.00, "idio": 0.24, "skew": 0.40, "late_mult": 1.00},
}


@dataclass
class PathBatch:
    """한 모형에 배정된 경로 묶음. 후보들이 이것을 공유한다."""
    model_id: str
    codes: list[str]
    #: (n_paths, n_days, n_codes) 일별 수익률(비율)
    returns: np.ndarray
    #: (n_paths, n_days) 시장 요인 일별 수익률(비율)
    market: np.ndarray
    #: (n_paths, n_field) 경쟁자의 **관측시점 대비** 최종 자산 배수
    field_multiplier: np.ndarray
    #: 재현용
    seed_entropy: tuple


def _block_bootstrap(hist: np.ndarray, n_paths: int, n_days: int,
                     rng: np.random.Generator, block: int = 5) -> np.ndarray:
    """과거 일별 수익률 패널에서 날짜 블록을 뽑아 경로를 만든다.

    hist: (T, n_codes). 같은 행을 통째로 뽑으므로 그날의 종목 간 상관이 보존된다.
    block 을 5로 둔 것은 주 단위 연속성을 남기기 위한 것이고, 최적값이 아니다.
    """
    T = hist.shape[0]
    if T < block + 1:
        raise ValueError(f"과거 표본이 너무 짧다: {T}일")
    n_blocks = int(np.ceil(n_days / block))
    starts = rng.integers(0, T - block, size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_paths, -1)
    return hist[idx[:, :n_days]]


def sample_paths(model_id: str, hist: np.ndarray, codes: list[str],
                 n_paths: int, n_days: int, n_field: int,
                 rng: np.random.Generator, block: int = 5,
                 sigma_obs: float | None = None) -> PathBatch:
    """한 경쟁자 모형에 대한 경로 묶음.

    경쟁자 최종 배수는 **관측시점 이후**의 수익률이다. 대회 시작부터의 누적이
    아니다(명세 8.5). 시장 요인을 공유하므로 내 포트폴리오와 상관을 갖는다.
    """
    p = FIELD_PARAMS[model_id]
    rets = _block_bootstrap(hist, n_paths, n_days, rng, block)      # (P, D, C)
    market = rets.mean(axis=2)                                       # (P, D) 등가중 대용

    # 경쟁자: 시장 누적 + 고유 충격. 고유 충격은 시프트 로그정규로 우측 꼬리를 준다.
    mkt_cum = np.prod(1.0 + market, axis=1) - 1.0                    # (P,)
    # idio 는 **20일 누적** 고유 변동성이다. 남은 날이 적으면 그만큼 줄여야 한다 —
    # 마지막 하루에 20일치 분산을 쓰면 리드를 지키는 후보와 추격 후보를 모두
    # 잘못 잰다(2026-09-07 외부 검토 재현: 1일과 20일의 분산이 같았다).
    # 독립 증분 가정의 sqrt(시간) 환산이다. 남은 날 0 이면 미래 수익도 0 이다.
    s = p["idio"] * p["late_mult"] * float(np.sqrt(max(n_days, 0) / 20.0))
    sigma_log = np.sqrt(np.log1p((s / 1.0) ** 2))
    mu_log = -0.5 * sigma_log ** 2 + p["skew"] * sigma_log
    idio = np.exp(rng.normal(mu_log, sigma_log, size=(n_paths, n_field))) - 1.0
    field = (1.0 + p["beta"] * mkt_cum[:, None]) * (1.0 + idio)

    if sigma_obs and sigma_obs > 0:
        # 관측된 필드 분산에 **수준만** 맞춘다.
        #
        # 우승 확률은 필드 분산 하나에 30배씩 흔들리는데(sigma_f 15%p 에서 10%,
        # 30%p 에서 0.3%), 위 모수는 전부 추측이다. 중계실 상위권 수익률에서
        # 분산을 추정하는 계측기는 이미 있다 — 여기 연결하지 않으면 매일 재는
        # 값이 결정에 아무 영향을 주지 않는다.
        #
        # 모형이 가진 **모양**(왜도, 시장 상관, 후반 가속)은 그대로 두고 퍼짐만
        # 바꾼다. 그래야 다섯 모형을 나눠 둔 의미가 남는다. 한 번의 추정이
        # 모형을 뒤엎지 못하도록 배율에 상한을 건다.
        spread = field.std(axis=1, keepdims=True)
        k = np.where(spread > 1e-9, sigma_obs / np.maximum(spread, 1e-9), 1.0)
        k = np.clip(k, 0.5, 2.0)
        mu = field.mean(axis=1, keepdims=True)
        field = mu + (field - mu) * k

    field = np.maximum(field, 0.0)     # 자산은 음수가 될 수 없다

    ent = getattr(getattr(rng.bit_generator, "seed_seq", None), "entropy", None)
    if isinstance(ent, (list, tuple)):
        seed_entropy = tuple(int(x) for x in ent)
    elif ent is None:
        seed_entropy = ()
    else:
        seed_entropy = (int(ent),)          # 정수 시드도 받는다 (tuple(int) 는 TypeError 였다)
    return PathBatch(model_id=model_id, codes=list(codes), returns=rets,
                     market=market, field_multiplier=field,
                     seed_entropy=seed_entropy)


def make_batches(hist: np.ndarray, codes: list[str], n_paths_total: int,
                 n_days: int, n_field: int, master_seed: int,
                 purpose: str, models: tuple[str, ...] = FIELD_MODELS,
                 block: int = 5,
                 sigma_obs: float | None = None) -> list[PathBatch]:
    """모형별 층화표집. 모형끼리는 **독립 난수 흐름**을 쓴다(명세 10.2).

    purpose 는 'selection' / 'validation'. 서로 다른 하위 스트림을 받아야
    확인용 경로가 선택에 오염되지 않는다.
    """
    m = len(models)
    if n_paths_total < 2 * m:
        raise ValueError(f"모형당 2경로 미만이다: {n_paths_total}/{m}")
    base, rem = divmod(n_paths_total, m)
    counts = [base + (1 if i < rem else 0) for i in range(m)]

    # 용도·모형 ID 를 시드에 고정 배정한다. 후보 순서나 청크 크기가 경로를
    # 바꾸면 재현이 깨지므로, 스트림 배정은 계산 시작 전에 확정한다.
    purpose_off = {"selection": 0, "validation": 1}[purpose]
    ss = np.random.SeedSequence([master_seed, purpose_off])
    children = ss.spawn(m)
    return [sample_paths(mid, hist, codes, n, n_days, n_field,
                         np.random.default_rng(ch), block, sigma_obs)
            for mid, n, ch in zip(sorted(models), counts, children)]
