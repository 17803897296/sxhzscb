@echo off
chcp 65001 >nul
title 双选会助手 demo - 8102
cd /d "%~dp0ent_tools"

echo ============================================
echo  双选会助手 demo（端口 8102）
echo  门户 http://127.0.0.1:8102/start.html
echo  关闭此窗口即停止服务
echo ============================================
echo.

start "" http://127.0.0.1:8102/start.html
python -X utf8 fair_server.py --port 8102

if errorlevel 1 (
  echo.
  echo 启动失败：没找到 python，或 8102 已被占用。
  echo 可以先试：netstat -ano ^| findstr :8102
  echo 也可以直接双击 双选会助手离线副本\启动离线版.bat（走 8103）。
  pause
)
