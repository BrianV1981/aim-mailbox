@echo off
setlocal
set "REPO_ROOT=%~dp0.."
if defined PYTHONPATH (
    set "PYTHONPATH=%REPO_ROOT%\src;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%REPO_ROOT%\src"
)

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python -m aim_mailbox %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -3 -m aim_mailbox %*
    exit /b %ERRORLEVEL%
)

echo [ERROR] Python 3.10+ is required on PATH. 1>&2
exit /b 1
