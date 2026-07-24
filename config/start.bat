@echo off
:: 设置命令行编码为UTF-8
chcp 65001 >nul
:: 设置工作目录为项目根目录（config/ 的上一级）
cd /d "%~dp0\.."

:: 主流程运行在 Python 3.13（项目根 venv 或系统 python），负责网页服务 / 图床 / 视频合成。
:: CosyVoice3 语音克隆运行在独立 venv（tts_poc/venv_cosyvoice），通过子进程 JSON-RPC 调用。
:: 若你另起 3.13 venv，请把 PYTHON 改成它的 python.exe 绝对路径。
set "PYTHON=python"

echo ==========================================
echo     toolbax - 启动服务
echo ==========================================
echo.

echo [1/3] 检查并安装 Python 依赖...
"%PYTHON%" -c "import flask, flask_cors, requests, edge_tts, moviepy, pysrt" 2>nul
if errorlevel 1 (
    echo [INFO] 缺少依赖，正在根据 requirements.txt 安装...
    "%PYTHON%" -m pip install -r config\requirements.txt
    if errorlevel 1 (
        echo [ERROR] 安装依赖失败，请手动执行: "%PYTHON%" -m pip install -r config\requirements.txt
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
    echo [WARN] Windows 可尝试: winget install ffmpeg
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

:: 启动Flask服务
"%PYTHON%" src\web_server.py

pause
