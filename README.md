# toolbax

> 基于 Web 的「素材转讲解视频」工具：接收图片、PDF、PPT/PPTX、Word 等素材，自动生成带 AI 讲解语音与字幕的视频，并提供本地 GPU 语音克隆能力。

## 核心能力

- **多格式素材 → 讲解视频**：图片 / PDF / PPT / Word → 分页图片 → 带配音与字幕的视频
- **多种 TTS 引擎**
  - `edge-tts`：微软在线 TTS，默认与兜底引擎（需联网）
  - `CosyVoice3`（0.5B，阿里开源）：本地零样本语音克隆，运行在独立 venv 的常驻 Worker 子进程
- **视频合成**：MoviePy + 内置 FFmpeg，支持字幕、配音、进度回调
- **企业方案链接 → 智能体生成 Word → 复用文档转视频**（命令行入口）
- **桌面启动器**：PyInstaller 打包为 exe / py2app 打包为 .app

## 系统架构

主进程（Flask Web 服务）与重型 TTS 推理（CosyVoice3 Worker）**完全隔离在两个 Python 虚拟环境**里，
通过标准输入/输出上的 JSONL 协议通信（`src/tts_worker_protocol.py`）。这样主进程无需装载 torch，也便于分别重建环境。

完整说明见 [`项目说明书.md`](项目说明书.md) 与 [`docs/`](docs/)。

## ⚠️ 仓库说明（重要）

本仓库是 **源码与文档镜像**。由于体积原因，**不纳入**以下自包含运行环境（合计约 15 GB+）：

| 目录 | 内容 | 体积 |
|---|---|---|
| `runtime/` | 内置 Python 3.13.14 解释器 | ~316 MB |
| `tts_poc/` | CosyVoice3 引擎 + torch venv + 模型权重 | ~15 GB |
| `bin/` | 内置 FFmpeg / ffprobe | ~434 MB |
| `output/` `app_data/` `audio_assets/` `training_assets/` 等 | 运行时产物与素材 | 不定 |

> 这些目录已在 `.gitignore` 中排除。要获得**可运行环境**，请依据
> [`项目说明书.md`](项目说明书.md) 的「环境重建」章节安装依赖，或从完整发布包获取对应的运行时。

## 目录结构（纳入版本控制部分）

```
toolbax/
├── src/                 # 源代码（web_server / 视频生成 / TTS Worker / 文档转换 等）
├── scripts/             # 辅助脚本
├── tests/               # 测试
├── static/index.html    # 前端页面
├── config/              # .env.example / start.bat / toolbax.spec / requirements.txt
├── docs/                # README / BUILD / TESTING / 设计文档
├── deliverables/        # 交付文档
├── build_exes.py        # PyInstaller 打包入口
├── build_hooks/         # 打包钩子
├── requirements.txt     # 主服务依赖清单
└── 项目说明书.md         # 完整项目手册（安装/配置/排错）
```

## 配置

参考 [`config/.env.example`](config/.env.example)，把真实值写入项目根目录的 `.env`（程序启动时自动加载）。
关键环境变量（详见 `项目说明书.md` §6）：

| 变量 | 说明 |
|---|---|
| `TOOLBAX_API_KEY` / `TOOLBAX_API_URL` | AI 话术 API（未设置则用兜底话术） |
| `SOLUTION_AGENT_API_KEY` / `SOLUTION_AGENT_API_URL` | 企业方案智能体 |
| `APP_VARIANT` | `gpu` / `cpu`，主流程是否启用 GPU |
| `TOOLBAX_OUTPUT_FOLDER` | 输出目录（默认 `output`） |

## 技术栈

- **前端**：HTML5 / CSS3 / 原生 JavaScript（无框架）
- **后端**：Python 3.13 + Flask + flask-cors
- **TTS**：edge-tts、CosyVoice3（本地 GPU）
- **媒体**：MoviePy、FFmpeg（内置）、PyAV、faster-whisper
- **文档**：PyMuPDF、python-docx、PowerPoint COM / LibreOffice

## 许可证

[MIT](LICENSE) © 2026 Alex Wang
