@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
if exist ".runtime\python\python.exe" set "PY=.runtime\python\python.exe"

echo ==========================================
echo  1/4  大模型采集企业资料
echo ==========================================
"%PY%" -X utf8 enrich_by_llm.py
if errorlevel 1 goto fail

echo.
echo ==========================================
echo  2/4  归一 + 补缺 + 截证据
echo ==========================================
"%PY%" -X utf8 build_cache.py
if errorlevel 1 goto fail

echo.
echo ==========================================
echo  3/4  生成企业端契约数据
echo ==========================================
"%PY%" -X utf8 company_module.py
if errorlevel 1 goto fail

echo.
echo ==========================================
echo  4/4  合并在线补到的岗位
echo ==========================================
"%PY%" -X utf8 sync_lookup_to_companies.py
if errorlevel 1 goto fail
echo.
echo 已生成 data\companies.json。
echo 重启 portal.py 后，演示页会直接使用其中有岗位的企业。
goto end

:fail
echo.
echo 出错了，看上面的报错信息。

:end
pause
