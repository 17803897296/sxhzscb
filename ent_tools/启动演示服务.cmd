@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=python"
if exist ".runtime\python\python.exe" set "PY=.runtime\python\python.exe"

echo ==========================================
echo  双选会演示服务
echo  页面：http://127.0.0.1:8099/fair.html
echo  关闭这个窗口就会停止服务
echo ==========================================
echo.

start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8099/fair.html"
"%PY%" -X utf8 portal.py --port 8099

echo.
echo 服务已停止。
pause
