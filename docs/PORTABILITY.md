# 跨平台 / 无 GPU 运行方案（PORTABILITY）

> 本文档说明 TOOLBOX 如何在 **Windows（同机 / 异机）**、**Linux**、**macOS**，
> 以及 **无 NVIDIA GPU（AMD / Intel / 纯 CPU）** 的机器上运行，并给出传 GitHub 前的检查清单。
>
> 配套文档：`docs/BUILD.md`（打包构建，含 Windows PyInstaller 与 macOS py2app）。

---

## 0. 架构速览（为什么能跨平台）

- **主流程（网页服务 / 图床 / 视频合成）** 跑在 Python 3.13 环境：
  - 开发态：`runtime/python/`（随工程携带的 Windows PE 解释器，已 gitignore）或系统 `python3`；
  - 依赖见 `config/requirements.txt`（已带 `; platform_system == "Windows"` 平台标记）。
- **CosyVoice3 语音克隆** 跑在**独立 venv**（`tts_poc/venv_cosyvoice`，Python 3.10 / CUDA 12.x）：
  - 仅通过子进程 + JSON-RPC 调用，**主流程绝不 import torch / cosyvoice**；
  - 依赖见 `tts_poc/requirements.txt`（**默认锁定 cu128，保证当前开发者 Windows + N 卡环境可用**）。
- **路径中枢** `src/paths.py` 已是单一事实来源（`PROJECT_ROOT` / `DATA_ROOT` 等），无硬编码绝对路径。
- **跨平台守卫已就位**：`src/ffmpeg_util.py`（按 `os.name` 选 exe 并回退 PATH）、
  `src/gpu_arbiter.py`（跨平台锁）、`src/document_converter.py`（回退 LibreOffice）、
  `src/web_server.py` 的 `ctypes.windll` 有 `if os.name != "nt": return` 守卫、
  `src/audio_transcriber.py` 兼容 `Scripts/python.exe` 与 `bin/python`。

---

## 1. Windows（同机 / 异机）

### 1.1 同机（开发机，已配好环境）
直接双击 **`config/start.bat`** 即可：
1. 检测 `.venv/pyvenv.cfg` 的 `home` 指向的 python 是否真实存在；存在则**直接复用** `.venv`；
2. 依赖自检、FFmpeg 检测、启动 `src/web_server.py`。
克隆功能（需 N 卡）照常工作。

### 1.2 异机（拷贝 / 克隆到另一台 Windows）
`.venv/`、`runtime/`、`tts_poc/` 均被 `.gitignore` 排除（体积 ~15GB+），**不会随仓库走**。
因此异机首次运行时，`.venv` 不存在或 `pyvenv.cfg` 的 `home` 指向的解释器路径失效，
`start.bat` 的**自愈逻辑**会：

1. 选择基础解释器：
   - 优先 `runtime\python\python.exe`（若你把 `runtime/` 一并拷过去了）；
   - 否则回退系统 PATH 上的 `python`（需异机自带 Python 3.13）。
2. 删除失效 `.venv`，用基础解释器 `python -m venv .venv` 重建；
3. `pip install -r config/requirements.txt` 安装主流程依赖；
4. 激活 `.venv` 并启动。

> 若异机也想用 CosyVoice3 克隆，需另行把 `tts_poc/`（含 `venv_cosyvoice`、`models/`、
> `CosyVoice` 子模块）整体拷贝或按第 5 节下载（见下）。

---

## 2. Linux

> 环境：Python 3.13 + 系统 ffmpeg + LibreOffice（PPT/Word 转图）。克隆功能在 Linux 上
> 需要 CUDA（N 卡），无 N 卡时自动降级（见第 4 节）。

1. **装系统依赖**
   ```bash
   sudo apt update
   sudo apt install -y ffmpeg libreoffice-impeess  # 桌面版；无头服务器用 libreoffice-impress
   # 或最小转图：sudo apt install -y libreoffice-impress
   sudo apt install -y python3.13 python3.13-venv python3.13-dev
   ```
2. **启动**（等价于 start.bat 的 Linux 版）
   ```bash
   bash config/start.sh
   # 或： chmod +x config/start.sh && ./config/start.sh
   ```
   `start.sh` 会：复用 / 重建 `.venv`（`python3` 基础）→ 装 `config/requirements.txt` →
   检测 ffmpeg → 启动 `src/web_server.py`。
3. **FFmpeg 不在 PATH / 想用自带版本**：设环境变量覆盖
   ```bash
   export LOCAL_TTS_FFMPEG_PATH=/opt/ffmpeg/bin/ffmpeg
   ```
   `src/ffmpeg_util.py` 会优先用该路径所在目录探测 `ffmpeg` / `ffprobe`。
4. **克隆不可用原因**：CosyVoice3 权重 + `torch==2.11.0+cu128` 仅适配 CUDA。Linux + 无 N 卡时，
   `src/gpu_setup.py:check_dependency()` 探测不到 N 卡 / 无 `venv_cosyvoice`，克隆入口自动灰掉；
   主线（Edge TTS 配音 / 视频合成）不受影响。

---

## 3. macOS

> 参考 `docs/BUILD.md` 的 macOS py2app 打包章节；此处给出**源码态直接运行**方式。

1. **装依赖（brew）**
   ```bash
   brew install ffmpeg
   brew install --cask libreoffice
   # Python 3.13（推荐用 pyenv 或 brew install python@3.13）
   ```
2. **启动**
   ```bash
   ./config/start.sh
   ```
   `start.sh` 在 macOS 上的 `uname` 分支会提示 `brew install ffmpeg`。
3. **打包为 .app**：按 `docs/BUILD.md` 的 py2app 流程
   （`py2applet --make-setup src/app_launcher.py` → 编辑 `setup.py` 排除 `win32com/pywin32` →
   `python setup.py py2app`）。注意 py2app **必须在 macOS 机器上执行**。
4. **克隆**：同 Linux，依赖 CUDA，macOS 无 N 卡时不可用；主线功能正常。

---

## 4. 无 NVIDIA GPU（AMD / Intel / 纯 CPU）

**主线自动降级 CPU，无需任何配置**：

- 视频合成、Edge TTS 配音、`faster-whisper` 本地字幕对齐走 CPU；
- `src/gpu_setup.py:detect_hardware_capable()` 用 `nvidia-smi` 判定 N 卡，
  **不依赖 torch**，无 GPU 机器安全返回 `(False, ...)`，不会崩溃；
- 克隆（CosyVoice3）在无 N 卡时由 `check_dependency()` 门禁自动禁用，前端入口灰掉。

**克隆降级替代方案**：无 N 卡时创建克隆音色请改用 **Edge TTS** 的预设音色（无需本地模型）。

**强制 CPU 变体（可选）**：构建无依赖版 / 明确声明环境时，可设
```bash
export APP_VARIANT=cpu
```
（`variant.txt` 为构建变体标记，已被 `.gitignore` 排除，属临时状态，不影响源码可移植性。）

**torch / onnxruntime 跨平台说明**（重要，勿改默认）：
- `tts_poc/requirements.txt` 默认 `torch==2.11.0+cu128` + `onnxruntime-gpu==1.27.0`，
  **保持不动**以保证当前 Windows + N 卡开发环境可用；
- 无 N 卡机器若要在 `tts_poc` venv 内跑（一般没必要，因为主线不依赖 torch），可改装 CPU 版：
  ```bash
  pip install torch==2.11.0+cpu torchaudio==2.11.0+cpu --index-url https://download.pytorch.org/whl/cpu
  pip install onnxruntime==1.27.0   # 非 -gpu
  ```
  该改动**仅本机生效**（tts_poc/ 已 gitignore），不会污染仓库默认依赖。

---

## 5. 模型权重分发（9.1GB `tts_poc/models/`）

`tts_poc/`（含 `venv_cosyvoice`、9.1GB 模型权重、`CosyVoice` 子模块）被 `.gitignore` 排除，
**不进 GitHub**。分发方式二选一：

1. **整体拷贝**：把 `tts_poc/` 目录从开发机直接拷到目标机的项目根下（保持 `tts_poc/models/CosyVoice3-0.5B/`
   权重文件齐全：`llm.pt` / `flow.pt` / `speech_tokenizer_v3.onnx` / `hift.pt` / `campplus.onnx` / `cosyvoice3.yaml`）；
2. **下载脚本**：提供一个从对象存储 / HuggingFace 拉取权重的脚本（本仓库未内置，按需自行补充）。

> 仅当目标机是 Windows + N 卡且需要克隆时才必须分发 `tts_poc/`；单纯跑主线（Linux/macOS/无 GPU）
> 不需要它。

---

## 6. `.env` 处理

- **切勿提交真实 Key**：`.env` 已被 `.gitignore` 排除（第 9 行）；`.env` 含 `TOOLBOX_API_KEY` 等密钥。
- **生成方式**：从模板复制后填写
  ```bash
  cp config/.env.example .env
  # 编辑 .env，填入真实 API Key / 地址
  ```
- 程序启动时自动加载项目根 `.env`（开发态）或 exe 同目录 `.env`（打包态）。
- 提交时只保留 `config/.env.example`（**不含真实值**）。

---

## 7. 传 GitHub 前检查清单

> 由质量门神（quality-gate）逐项核验；本清单覆盖“可干净推送”的硬性要求。

- [ ] `.gitignore` 已排除：`.env`、`*.env`、`runtime/`、`tts_poc/`、`audio_assets/`、
      `training_assets/`、`test_inputs/`、`video-dubbing/`、`app_data/`、`output/`、
      `.venv/`、`hardware_profile.json`、`runtime_profile.json`、`variant.txt`、`bin/`。
      （均已确认在 `.gitignore` 中。）
- [ ] `git status` 不应列出：`.env`、`.venv/`、`venv_cosyvoice/`、`runtime/`、`tts_poc/`、
      `src/hardware_profile.json`、`app_data/` 等。
- [ ] 源码中**无 `C:\Users\12992` 字面量**（`TOOLBOX.py` 字体已改为 `WINDIR` 推导；
      `src/hardware_profile.json` 已删除；如仍有 Windows 候选路径必须位于 `os.name=='nt'` 守卫内）。
- [ ] 依赖跨平台：根 `requirements.txt` 的 `pywin32` 已加 `; platform_system == "Windows"`；
      `config/requirements.txt` 已带平台标记。`tts_poc/requirements.txt` 的 `pynini`
      已从本地 wheel 改为 PyPI 包名（该文件被 gitignore，本地生效）。
- [ ] 启动脚本：`config/start.bat`（Windows 自愈）与 `config/start.sh`（Linux/macOS 等价，已设可执行位）
      均已就位；`*.sh` / `*.bat` 行尾由 `.gitattributes` 强制（LF / CRLF）。
- [ ] 克隆解释器解析：`multi_tts_voice.py` 与 `gpu_setup.py` 已支持 `Scripts/python.exe` →
      `bin/python` → `sys.executable` 回退，Linux/macOS 不再因“找不到 .exe”崩溃。
- [ ] `config/.env.example` 已包含所需变量且**无真实密钥**；真实 `.env` 未被跟踪。
- [ ] 文档：`docs/PORTABILITY.md`（本文件）与 `docs/BUILD.md` 内容一致、无过期路径。

---

## 8. 已知残留 / 局限（诚实记录）

- `tts_poc/requirements.txt`、`tts_poc/`（含 `venv_cosyvoice`、`models/`）被 gitignore，
  **不会进 GitHub**；其上 `pynini` 改成 PyPI 包名仅在本机生效。若日后将 `tts_poc/` 纳入版本控制，
  该修复会自动生效；否则需在目标机手动处理 `pynini` 安装（Windows 可用本地 wheel，
  Linux/macOS 用 PyPI）。
- `app_data/hardware_profile.json`（运行时画像）内含探测到的 `ffmpeg_path` 绝对路径，
  但位于 `app_data/`（已 gitignore），**不会提交**；源码逻辑优先返回 PATH 裸命令名，不再主动落盘绝对路径。
- 语音克隆（CosyVoice3）**硬性依赖 CUDA + Windows 开发环境**，Linux/macOS/无 N 卡机器上不可用，
  属设计预期，由门禁自动禁用，不影响主线。
- `runtime/python/` 为 Windows PE 解释器，仅 Windows 可用；Linux/macOS 自愈逻辑回退系统 `python3`。
