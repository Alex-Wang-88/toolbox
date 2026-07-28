@echo off
rem 设置命令行编码为UTF-8
chcp 65001 >nul
rem 设置工作目录为项目根目录（config/ 的上一级）
cd /d "%~dp0\.."

set "PROJECT_ROOT=%CD%"
set "VENV_DIR=%PROJECT_ROOT%\.venv"
set "PYVENV_CFG=%VENV_DIR%\pyvenv.cfg"

echo ==========================================
echo     TOOLBOX - 启动服务
echo ==========================================
echo.

rem ---- 虚拟环境自愈（跨机器可移植性关键）----
rem 选择用于“新建 venv”的基础解释器：
rem   优先用随工程携带的 runtime\python\python.exe（若存在）；
rem   否则回退系统 PATH 上的 python（新机器上需自带 Python 3.13）。
set "BASE_PYTHON=python"
if exist "%PROJECT_ROOT%\runtime\python\python.exe" (
    set "BASE_PYTHON=%PROJECT_ROOT%\runtime\python\python.exe"
)

set "NEED_BUILD=0"
if not exist "%PYVENV_CFG%" set "NEED_BUILD=1"
if not exist "%VENV_DIR%\Scripts\python.exe" set "NEED_BUILD=1"
if "%NEED_BUILD%"=="0" (
    "%VENV_DIR%\Scripts\python.exe" -c "import sys" >nul 2>nul
    if errorlevel 1 set "NEED_BUILD=1"
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
set "FFMPEG_CMD=ffmpeg"
if exist "%PROJECT_ROOT%\bin\ffmpeg\ffmpeg.exe" set "FFMPEG_CMD=%PROJECT_ROOT%\bin\ffmpeg\ffmpeg.exe"
"%FFMPEG_CMD%" -version >nul 2>nul
if errorlevel 1 (
    echo [WARN] 未检测到 FFmpeg，视频合成阶段可能会失败
    echo [WARN] Windows 可尝试: winget install ffmpeg  （或用 LOCAL_TTS_FFMPEG_PATH 指定路径）
) else (
    echo [OK] FFmpeg 已可用
)

echo.
echo [3/3] 启动 Web 服务...
echo.
echo 网页将在服务就绪后自动打开
echo 按 Ctrl+C 停止服务
echo.
echo ==========================================
echo.

rem 启动桌面启动器（venv 已激活，python 即 .venv 解释器）
python src\app_launcher.py

pause
