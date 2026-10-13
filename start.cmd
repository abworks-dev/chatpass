@echo off
setlocal EnableExtensions DisableDelayedExpansion

pushd "%~dp0"
if errorlevel 1 (
    echo Could not open the ChatPass project directory.
    pause
    exit /b 1
)

where uv >nul 2>nul
if errorlevel 1 (
    echo uv was not found on PATH. Install uv, then reopen this launcher.
    echo See https://docs.astral.sh/uv/ or README.md for setup instructions.
    popd
    pause
    exit /b 1
)

echo Starting the local ChatPass console. Keep this window open.
echo Press Ctrl+C to stop. Account authorization requires your explicit action.
call uv run --locked --no-dev --system-certs chatpass web %*
set "CHATPASS_EXIT_CODE=%ERRORLEVEL%"
popd

if not "%CHATPASS_EXIT_CODE%"=="0" (
    echo ChatPass exited with code %CHATPASS_EXIT_CODE%.
    echo Check the error above, uv setup, network access and port availability.
    pause
)
exit /b %CHATPASS_EXIT_CODE%
