@echo off
rem Single-window launcher: delegates to start_all.ps1 (weitrack + gacore in one window)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_all.ps1"