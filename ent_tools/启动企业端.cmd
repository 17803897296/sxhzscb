@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
if exist ".runtime\python\python.exe" goto bundled
py -3 --version >nul 2>&1
if not errorlevel 1 goto launcher
python --version >nul 2>&1
if not errorlevel 1 goto systempython
echo 未找到可用 Python。请将附带的 .runtime 文件夹与本文件一起复制。
set "module_exit=1"
goto finish
:bundled
".runtime\python\python.exe" -X utf8 company_module.py
set "module_exit=%errorlevel%"
goto finish
:launcher
py -3 -X utf8 company_module.py
set "module_exit=%errorlevel%"
goto finish
:systempython
python -X utf8 company_module.py
set "module_exit=%errorlevel%"
:finish
echo.
if "%module_exit%"=="0" echo 企业数据已保存至 data\companies.json
if not "%module_exit%"=="0" echo 请根据上方错误修正 data 文件后重试，已有结果没有被覆盖。
if /i not "%~1"=="--no-pause" pause
popd
exit /b %module_exit%
