@echo off
chcp 65001 > nul
cd /d "%~dp0.."

REM 관리자 권한이 필요하다.
REM
REM HTS 가 관리자 권한으로 돌기 때문에, 일반 권한에서는 창 조작이 UIPI 에 막혀
REM 매도·매수가 전부 rc=2 로 실패한다. 감시자가 띄우는 자식 프로세스는 부모의
REM 권한을 그대로 상속하므로, 여기서 승격하지 않으면 감시자만 돌고 주문은 못 낸다.
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo 관리자 권한으로 다시 실행합니다...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

title iM Rookie League - 자동 트레이딩 (관리자)

echo ============================================================
echo   iM Rookie League 자동 트레이딩 감시자
echo ============================================================
echo.
echo  이 창은 닫지 마세요. 장중 내내 HTS 를 지켜보다가
echo  준비되면 밀린 단계를 자동으로 실행합니다.
echo.
echo  로그:     state\watchdog.log
echo  중지:     이 창에서 Ctrl+C
echo  긴급정지: state\KILL 파일 생성
echo ============================================================
echo.

python -X utf8 watchdog.py

echo.
echo 감시자가 종료되었습니다.
pause
