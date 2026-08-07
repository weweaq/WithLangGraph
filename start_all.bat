@echo off
echo Starting gacore services...

REM QQ Bot window
start "gacore-QQ" cmd /c "cd /d D:\AAAmyprj\github\myrepos\WithLangGraph\src\gacore\frontends && qq.bat"

REM Scheduler window
start "gacore-Scheduler" cmd /c "set PYTHONPATH=D:\AAAmyprj\github\myrepos\WithLangGraph\src && D:\softwares\miniconda\envs\py12\python.exe -m gacore.scheduler"

echo QQ Bot and Scheduler launched in separate windows.
pause
