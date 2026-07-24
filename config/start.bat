@echo off
:: 设置命令行编码为UTF-8
chcp 65001 >nul
:: 设置工作目录为项目根目录（config/ 的上一级）
cd /d "%~dp0\.."

set "PROJECT_ROOT=%CD%"
set "VENV_DIR=%PROJECT_ROOT%\.venv"
set "PYVENV_CFG=%VENV_DIR%\pyvenv.cfg"

echo ==========================================
echo     toolbax - 启动服务
echo ==========================================
echo.

:: ---- 虚拟环境自愈（跨机器可移植性关键）----
:: 选择用于“新建 venv”的基础解释器：
::   优先用随工程携带的 runtime\python\python.exe（若存在）；
::   否则回退系统 PATH 上的 python（新机器上需自带 Python 3.13）。
set "BASE_PYTHON=python"
if exist "%PROJECT_ROOT%\runtime\python\python.exe" (
    set "BASE_PYTHON=%PROJECT_ROOT%\runtime\python\python.exe"
)

set "NEED_BUILD=0"
if not exist "%PYVENV_CFG%" (
    set "NEED_BUILD=1"
) else (
    :: 读取 pyvenv.cfg 的 home 行，检测其指向的 python 是否真实存在
    set "VENV_HOME="
    for /f "usebackq tokens=1,* delims==" %%A in ("%PYVENV_CFG%") do (
        if /i "%%A"=="home" set "VENV_HOME=%%B"
    )
    :: 去掉引号、前导空格与尾部反斜杠（home = C:\... 取值带前导空格，必须剥离）
    set "VENV_HOME=%VENV_HOME:"=%"
    if defined VENV_HOME (
        for /f "tokens=* delims= " %%S in ("%VENV_HOME%") do set "VENV_HOME=%%S"
        if "%VENV_HOME:~-1%"=="\" set "VENV_HOME=%VENV_HOME:~0,-1%"
    )
    if not exist "%VENV_HOME%\python.exe" (
        set "NEED_BUILD=1"
    )
)

if "%NEED_BUILD%"=="1" (
    echo [INFO] 未检测到可用的虚拟环境，正在重建 .venv（基础解释器: %BASE_PYTHON%）...
    if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
    "%BASE_PYTHON%" -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] 创建 venv 失败，请确认已安装 Python 3.13 或 runtime\python\python.exe 存在
        pause
        exit /b 1
    )
    call "%VENV_DIR%\Scripts\activate.bat"
    python -m pip install --upgrade pip
    python -m pip install -r config\requirements.txt
    if errorlevel 1 (
        echo [ERROR] 安装依赖失败，请手动执行: python -m pip install -r config\requirements.txt
        pause
        exit /b 1
    )
    echo [OK] 虚拟环境已重建并安装依赖
) else (
    echo [OK] 复用已有 .venv
    call "%VENV_DIR%\Scripts\activate.bat"
)

echo.
echo [1/3] 检查并安装 Python 依赖...
python -c "import flask, flask_cors, requests, edge_tts, moviepy, pysrt" 2>nul
if errorlevel 1 (
    echo [INFO] 缺少依赖，正在根据 config\requirements.txt 安装...
    python -m pip install -r config\requirements.txt
    if errorlevel 1 (
        echo [ERROR] 安装依赖失败，请手动执行: python -m pip install -r config\requirements.txt
        pause
        exit /b 1
    )
    echo [OK] 依赖安装成功
) else (
    echo [OK] Python 依赖已安装
)

echo.
echo [2/3] 检查 FFmpeg...
ffmpeg -version >nul 2>nul
if errorlevel 1 (
    echo [WARN] 未检测到 FFmpeg，视频合成阶段可能会失败
    echo [WARN] Windows 可尝试: winget install ffmpeg  （或用 LOCAL_TTS_FFMPEG_PATH 指定路径）
) else (
    echo [OK] FFmpeg 已可用
)

echo.
echo [3/3] 启动 Web 服务...
echo.
echo 访问地址: http://localhost:5000
echo 按 Ctrl+C 停止服务
echo.
echo ==========================================
echo.

:: 启动Flask服务（venv 已激活，python 即 .venv 解释器）
python src\web_server.py

pause
