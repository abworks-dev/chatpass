@echo off
setlocal
cd /d "%~dp0"
echo Stop the existing ChatPass console with Ctrl+C before starting this one.
echo Troubleshooting profile: max concurrency 2, connect timeout 30 seconds.
echo If upstream reports server_is_overloaded, wait before manually retrying.
"%~dp0.venv\Scripts\python.exe" -m chatpass.cli web --max-concurrency 2 --connect-timeout 30
pause
