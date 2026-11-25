@echo off
setlocal
set "REPO_ROOT=%~dp0.."
if defined PYTHONPATH (
    set "PYTHONPATH=%REPO_ROOT%\src;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%REPO_ROOT%\src"
)

where python >nul 2>nul
if errorlevel 1 goto try_py
python -S "%REPO_ROOT%\src\aim_mailbox\__main__.py" %*
exit /b %ERRORLEVEL%

:try_py
where py >nul 2>nul
if errorlevel 1 goto no_python
py -3 -S "%REPO_ROOT%\src\aim_mailbox\__main__.py" %*
exit /b %ERRORLEVEL%

:no_python
echo [ERROR] Python 3.10+ is required on PATH. 1>&2
exit /b 1
