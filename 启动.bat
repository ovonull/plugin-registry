@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo [NapCatQQ 插件库] 正在停止已有服务...
call "%~dp0停止.bat"
if errorlevel 1 (
    echo [错误] 停止已有服务失败，已取消启动。
    endlocal & exit /b 1
)

if not exist "%~dp0backend\main.py" (
    echo [错误] 未找到 backend\main.py。
    endlocal & exit /b 1
)
if not exist "%~dp0frontend\package.json" (
    echo [错误] 未找到 frontend\package.json。
    endlocal & exit /b 1
)
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.11 或更高版本。
    endlocal & exit /b 1
)
python -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [错误] 后端依赖未安装，请执行：cd backend ^&^& python -m pip install -e .
    endlocal & exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 npm，请先安装 Node.js。
    endlocal & exit /b 1
)
if not exist "%~dp0frontend\node_modules" (
    echo [错误] 前端依赖未安装，请执行：cd frontend ^&^& npm install
    endlocal & exit /b 1
)

echo [NapCatQQ 插件库] 正在启动后端，端口：8120...
start "NapCatQQ 插件库 Backend" /D "%~dp0backend" cmd.exe /d /c "python main.py"
echo [NapCatQQ 插件库] 正在启动前端，端口：5178...
start "NapCatQQ 插件库 Frontend" /D "%~dp0frontend" cmd.exe /d /c "npm run dev"

echo [NapCatQQ 插件库] 正在等待服务就绪...
call :wait_for_port 8120 "后端"
if errorlevel 1 goto startup_failed
call :wait_for_port 5178 "前端"
if errorlevel 1 goto startup_failed

echo [成功] 后端地址：http://127.0.0.1:8120
echo [成功] 前端地址：http://127.0.0.1:5178
endlocal & exit /b 0

:startup_failed
echo [错误] 服务未能在规定时间内启动，请查看服务窗口中的错误信息。
call "%~dp0停止.bat"
endlocal & exit /b 1

:wait_for_port
set "WAIT_PORT=%~1"
set "WAIT_NAME=%~2"
for /L %%I in (1,1,30) do (
    netstat -ano | findstr /R /C:":%WAIT_PORT% .*LISTENING" >nul
    if not errorlevel 1 exit /b 0
    timeout /t 1 /nobreak >nul
)
echo [错误] %WAIT_NAME%未监听端口 %WAIT_PORT%。
exit /b 1
