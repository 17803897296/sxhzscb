@echo off
chcp 65001 >nul
title Edge 调试模式

set "EDGE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=C:\Program Files\Microsoft\Edge\Application\msedge.exe"
set "EDGE_USER=%LOCALAPPDATA%\Microsoft\Edge\User Data"

echo ==========================================
echo  用你本机 Edge 的登录态启动调试模式
echo ==========================================
echo.
echo 1. 先手动关闭所有 Edge 窗口
echo 2. 回到这里按任意键
echo.
echo 注意：不会删除书签、密码、登录状态。
echo.
pause >nul

start "" "%EDGE%" --remote-debugging-port=9222 --user-data-dir="%EDGE_USER%" https://www.zhipin.com/

echo.
echo 正在检查 9222 调试端口...
powershell -NoProfile -Command "Start-Sleep 4; try { $v=Invoke-RestMethod 'http://127.0.0.1:9222/json/version' -TimeoutSec 5; 'OK: ' + $v.Browser } catch { 'FAILED: 调试端口没起来。请确认所有 Edge 窗口都已关闭，再重新运行本脚本。' }"
echo.
pause
