@echo off
chcp 65001 > nul
cd /d "%~dp0.."
title iM Rookie League - 상태 모니터
python -X utf8 monitor.py -w
pause
