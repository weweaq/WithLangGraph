@echo off
echo Starting gacore (QQ Bot + Scheduler)...

REM Kill any leftover gacore instance
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*WithLangGraph*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

set PYTHONPATH=D:\AAAmyPrj\github\myrepos\WithLangGraph\src
D:\softwares\miniconda\envs\py12\python.exe D:\AAAmyPrj\github\myrepos\WithLangGraph\start.py

if errorlevel 1 (
    echo.
    echo [start_all] gacore exited with code %errorlevel%. See message above.
    pause
)
