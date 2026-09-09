@echo off

chcp 65001 > nul

cd /d "%~dp0.."

REM 상황판은 파일이 아니라 제어 서버로 열어야 손잡이가 동작한다.

REM 서버가 안 떠 있으면 여기서 띄운다 (로그온 작업이 이미 띄웠으면 두 번째는 바인드에서 스스로 끝난다).

powershell -NoProfile -Command "if (-not (Test-NetConnection 127.0.0.1 -Port 8765 -InformationLevel Quiet -WarningAction SilentlyContinue)) { Start-Process -WindowStyle Hidden python -ArgumentList '-X','utf8','control.py','--port','8765' ; Start-Sleep 2 }"

start "" http://127.0.0.1:8765/

