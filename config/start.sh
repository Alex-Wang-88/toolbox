#!/usr/bin/env bash
# TOOLBOX 启动脚本（Linux / macOS）
# 等价于 Windows 的 config/start.bat：虚拟环境自愈 + 依赖安装 + 启动 Web 服务。
# 用法：  bash config/start.sh   或   chmod +x config/start.sh && ./config/start.sh
set -euo pipefail

# 工作目录设为项目根（config/ 的上一级）
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"
PYVENV_CFG="$VENV_DIR/pyvenv.cfg"

echo "=========================================="
echo "    TOOLBOX - 启动服务 (Linux/macOS)"
echo "=========================================="
echo

# ---- 虚拟环境自愈 ----
# 选择用于“新建 venv”的基础解释器：优先随工程携带的 runtime/python（若存在），
# 否则回退系统 PATH 上的 python3（需自行安装 Python 3.13）。
BASE_PYTHON="python3"
if [ -x "$PROJECT_ROOT/runtime/python/python" ]; then
    BASE_PYTHON="$PROJECT_ROOT/runtime/python/python"
elif [ -x "$PROJECT_ROOT/runtime/python/bin/python3" ]; then
    BASE_PYTHON="$PROJECT_ROOT/runtime/python/bin/python3"
fi

NEED_BUILD=0
if [ ! -f "$PYVENV_CFG" ]; then
    NEED_BUILD=1
else
    # 读取 pyvenv.cfg 的 home 行，检测其指向的 python 是否真实存在
    VENV_HOME="$(grep -i '^home' "$PYVENV_CFG" | head -n1 | cut -d= -f2- | tr -d ' \r')"
    if [ ! -x "$VENV_HOME/bin/python3" ] && [ ! -x "$VENV_HOME/python" ]; then
        NEED_BUILD=1
    fi
fi

if [ "$NEED_BUILD" -eq 1 ]; then
    echo "[INFO] 未检测到可用的虚拟环境，正在重建 .venv（基础解释器: $BASE_PYTHON）..."
    rm -rf "$VENV_DIR"
    "$BASE_PYTHON" -m venv "$VENV_DIR"
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    python -m pip install --upgrade pip
    python -m pip install -r config/requirements.txt
    echo "[OK] 虚拟环境已重建并安装依赖"
else
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    echo "[OK] 复用已有 .venv"
fi

# ---- 依赖自检 ----
echo
echo "[1/3] 检查 Python 依赖..."
if ! python -c "import flask, flask_cors, requests, edge_tts, moviepy, pysrt" 2>/dev/null; then
    echo "[INFO] 缺少依赖，正在根据 config/requirements.txt 安装..."
    python -m pip install -r config/requirements.txt
    echo "[OK] 依赖安装成功"
else
    echo "[OK] Python 依赖已安装"
fi

# ---- FFmpeg 检查 ----
echo
echo "[2/3] 检查 FFmpeg..."
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[WARN] 未检测到 FFmpeg，视频合成阶段可能会失败"
    if [ "$(uname)" = "Darwin" ]; then
        echo "[WARN] macOS 可尝试: brew install ffmpeg"
    else
        echo "[WARN] Linux 可尝试: sudo apt install ffmpeg"
    fi
    echo "[WARN] 或用环境变量指定: export LOCAL_TTS_FFMPEG_PATH=/path/to/ffmpeg"
else
    echo "[OK] FFmpeg 已可用"
fi

# ---- 启动 Web 服务 ----
echo
echo "[3/3] 启动 Web 服务..."
echo
echo "访问地址: http://localhost:5000"
echo "按 Ctrl+C 停止服务"
echo
echo "=========================================="
echo

python src/app_launcher.py
