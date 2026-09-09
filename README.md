# 자동 모의투자 시스템

iM증권 대학생 모의투자대회(2026-09-08 ~ 10-08)용 **종가 단일가 1일 1회 리밸런싱** 시스템입니다.
HTS(싸이칸 Plus)를 Win32 GUI 자동화로 조작해 주문하고, 텔레그램으로 알리며, 로컬 상황판에서 제어합니다.
## 동작 흐름

장중에는 매매하지 않습니다.

```
시세 수집 ─▶ 유니버스 ─▶ 점수화 ─▶ 계획 ─▶ 집행 ─▶ 대조 ─▶ 보고
data.py     universe.py  alpha.py  portfolio.py  execute.py  run.py     relay.py
                                   turnover.py   risk.py                notify.py
                                                 hts_exec.py
```

| 시각 | 단계 | 하는 일 |
|---|---|---|
| 08:40~14:50 매시 | 점검 | HTS 준비 상태·시세 신선도 확인 |
| 15:05 | 계획 | 상위 2종목 × 49% + 회전율용 ETF 주문서 작성 |
| 15:10 | 매도 | 랭킹에서 빠진 종목 매도 |
| 15:21 | 매수 | 리스크 게이트 통과분만 HTS 주문창에 입력 |
| 15:40 | 대조 | 접수·체결 결과를 원장에 반영 |
| 16:00 | 보고 | 중계실 순위·회전율 기록, 텔레그램 요약 |

- `watchdog.py` — HTS 가 준비되면 밀린 단계를 실행하고, 주문 원장으로 중복 전송을 막습니다.
- `control.py` — 상황판 서빙(127.0.0.1:8765), 긴급정지(`state/KILL`)·건너뛰기 손잡이.
- `imrl/notify.py` — 텔레그램 주문 캡처·경보 전송, 정지·재개 명령 수신.

## 대회 제약과 설계

| 규정 | 설계 |
|---|---|
| 순위 = 최종일 수익률 | 2종목 집중으로 우측 꼬리 추구 |
| 단일 종목 원금의 50% 이하 | 49% 상한 + 현금 완충 (`imrl/portfolio.py`) |
| 매매회전율 500% 이상 | CD금리 ETF 왕복으로 충족 (`imrl/turnover.py`) |
| 매매일수·종목수 하한 | `run.py status` 로 진척 추적 |
| 증권사 API 없음 | Win32 GUI 자동화 (`imrl/hts_exec.py`, `imrl/hts_anchor.py`) |

규칙: [TRADING_RULES.md](TRADING_RULES.md) · 결정 이유: [DECISIONS.md](DECISIONS.md)

## 빠른 시작 (Windows, Python 3.12+)

```cmd
pip install -r requirements.txt
copy config\secrets.env.example config\secrets.env
```

1. `config/secrets.env` 에 텔레그램 봇 토큰과 chat_id 입력 (`python setup_telegram.py --token <토큰>` 도 가능).
2. `config/settings.json` 의 `contest.mock_account`, `contest.pen_name` 을 본인 값으로 변경.
3. `python run.py check` 로 환경 점검.
4. HTS 로그인은 직접 합니다. 이 프로그램은 비밀번호를 다루지 않습니다.
5. 감시자 `scripts/start_watchdog.bat` (관리자 권한으로 자동 승격), 상황판 `scripts/dashboard.bat`, 터미널 모니터 `scripts/monitor.bat`, 스케줄러 등록 `tools/setup_tasks.ps1` (관리자 PowerShell, `$CONTEST_START` 확인).

## 저장소 구성

| 경로 | 역할 |
|---|---|
| `run.py`, `execute.py`, `watchdog.py`, `control.py`, `monitor.py`, `backtest.py` | 진입점 |
| `imrl/` | 핵심 라이브러리 |
| `config/` | 대회·전략 설정, HTS 컨트롤 ID 맵, 비밀값 예시 |
| `scripts/` | 실행 배치 파일 |
| `tools/` | 자가검사(`selftest_*.py`), 검증(`verify_*.py`), HTS 화면 탐색, 스케줄러 등록 |
| `state/` | 런타임 상태 (커밋 제외, `backtest_current.json` 만 포함) |

## 유의 사항

- 모의투자 전용입니다. 실계좌에 사용하지 마십시오.
- iM증권 싸이칸 Plus 화면 구조에 맞춰져 있으며 Windows 전용입니다.
- 백테스트 수치는 파라미터 간 상대 비교용이며 투자 조언이 아닙니다.

## 라이선스

MIT — [LICENSE](LICENSE)
