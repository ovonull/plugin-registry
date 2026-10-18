@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1

set "EXIT_CODE=0"
call :stop_port "后端" "8120"
if errorlevel 1 set "EXIT_CODE=1"
call :stop_port "前端" "5178"
if errorlevel 1 set "EXIT_CODE=1"

if "%EXIT_CODE%"=="0" (
    echo [完成] NapCatQQ 插件库 已停止。
) else (
    echo [错误] 部分服务停止失败，请检查权限或手动结束对应进程。
)
endlocal & exit /b %EXIT_CODE%

:stop_port
set "SERVICE_NAME=%~1"
set "SERVICE_PORT=%~2"
set "FOUND=0"
set "FAILED=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%SERVICE_PORT% .*LISTENING"') do (
    if not defined SEEN_PID_%%P (
        set "SEEN_PID_%%P=1"
        set "FOUND=1"
        echo [信息] 正在停止%SERVICE_NAME%进程，PID：%%P...
        taskkill /F /T /PID %%P >nul 2>&1
        if errorlevel 1 (
            tasklist /FI "PID eq %%P" | findstr /R /C:" %%P " >nul
            if not errorlevel 1 set "FAILED=1"
        )
    )
)
if "%FOUND%"=="0" echo [提示] 未发现%SERVICE_NAME%端口 %SERVICE_PORT% 上的运行进程。
if "%FAILED%"=="1" exit /b 1
exit /b 0
