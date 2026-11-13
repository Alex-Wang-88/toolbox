# TOOLBOX

> 基于 Web 的「素材转讲解视频」工具：接收图片、PDF、PPT/PPTX、Word 等素材，自动生成带 AI 讲解语音与字幕的视频，并提供本地 GPU 语音克隆能力。

## 核心能力

- **多格式素材 → 讲解视频**：图片 / PDF / PPT / Word → 分页图片 → 带配音与字幕的视频
- **多种 TTS 引擎**
  - `edge-tts`：微软在线 TTS，默认与兜底引擎（需联网）
  - `GPT-SoVITS V5`：本地零样本语音克隆的**当前默认引擎**，默认变体 `v5turbo`，运行在 `tts_poc/venv_gptsovits` 的常驻 Worker 子进程
  - `CosyVoice3`（0.5B，阿里开源）：另一套本地克隆引擎，仍保留，可在主页 GPU 面板或 `GET/PUT /api/tts/clone-engine` 切换；GPT-SoVITS 依赖未就绪时自动回落到它
  - 两套引擎共用已登记音色的参考音频与逐字稿，缓存键包含引擎标记，切换不会串缓存；GPU 关闭或无可用引擎时降级为 Edge TTS
- **统一任务队列**：所有生成任务在一个 Flask 服务内串行执行，`/queue` 与 `GET /api/queue` 查看状态，等待任务可取消，页面刷新不影响后台任务（见 [`docs/QUEUE_AND_AUTO_PITCH_2026-11-09.md`](docs/QUEUE_AND_AUTO_PITCH_2026-11-09.md)）
- **视频合成**：MoviePy + FFmpeg（打包态自带，源码运行需单独安装或放到 `bin/`），支持字幕、配音、进度回调
- **企业方案链接 → 智能体生成 Word → 复用文档转视频**（命令行入口）
- **桌面启动器**：PyInstaller 打包为 Windows exe（含自带 FFmpeg 与 Python 运行时）
- **AI 唱歌**：支持上传完整原曲自动分离人声并保留伴奏，也支持直接上传已分离导唱，使用已登记的本地克隆音色进行 Singing Voice Conversion；下载结果按“歌曲名称 - 音色名称.wav”命名（可选 Seed-VC + Demucs，见 [`docs/singing_voice.md`](docs/singing_voice.md)）
- **通用音频转换**：自动识别输入音频，可混合批量转换为 WAV、MP3、FLAC、M4A、AAC、OGG 或 Opus，兼容 NCM 本地解码；AI 唱歌原曲模式也可直接接收 NCM（见 [`docs/audio_converter.md`](docs/audio_converter.md)）
- **视频链式续写**：把「上一段末帧 → 下一段首帧」做成通用模块，长镜头分段生成后无损拼接，自带接缝质检与断点续跑（见 [`docs/video_chainer.md`](docs/video_chainer.md)）
- **通用抠图**：BiRefNet（MIT，默认）为主，可切 ISNet / u2net；边缘精修去背景污染，输出白底/透明图或统一画布（见 [`docs/image_cutout.md`](docs/image_cutout.md)）

## 系统架构

主进程（Flask Web 服务）与重型 TTS 推理（GPT-SoVITS V5 / CosyVoice3 Worker）**隔离在独立 Python 虚拟环境**里，
通过标准输入/输出上的 JSONL 协议通信（`src/tts_worker_protocol.py`）。这样主进程无需装载 torch，也便于分别重建环境。
激活的克隆引擎由 `src/tts_engine_settings.py` 读取，选择结果持久化在 `app_data/tts_engine_settings.json`；
同一时刻只常驻一个 Worker（8GB 显卡同时常驻两套引擎会爆显存）。

完整说明见 [`项目说明书.md`](项目说明书.md)、[`docs/`](docs/)、[`docs/audio_converter.md`](docs/audio_converter.md) 与 [`docs/singing_voice.md`](docs/singing_voice.md)。

## ⚠️ 仓库说明（重要）

本仓库是 **源码与文档镜像**。由于体积原因，**不纳入**以下自包含运行环境（合计约 15 GB+）：

| 目录 | 内容 | 体积 |
|---|---|---|
| `runtime/` | 内置 Python 3.13.14 解释器 | ~316 MB |
| `tts_poc/` | GPT-SoVITS V5 + CosyVoice3 两套引擎仓库、独立 venv 与模型权重 | ~15 GB+ |
| `bin/` | 内置 FFmpeg / ffprobe | ~434 MB |
| `output/` `app_data/` `audio_assets/` `training_assets/` 等 | 运行时产物与素材 | 不定 |

> 这些目录已在 `.gitignore` 中排除。要获得**可运行环境**，请依据
> [`项目说明书.md`](项目说明书.md) 的「环境重建」章节安装依赖，或从完整发布包获取对应的运行时。
> 克隆引擎选择也存在被忽略的 `app_data/` 中：全新环境默认 `gptsovits`，若缺少
> `tts_poc/GPT-SoVITS` 仓库、V5 权重或 `venv_gptsovits`，程序会自动回落 CosyVoice3；
> 两者都不可用时使用 Edge TTS。GPT-SoVITS 音频链路的排查记录见
> [`docs/GPTSOVITS_AUDIO_REGRESSION_2026-11-09.md`](docs/GPTSOVITS_AUDIO_REGRESSION_2026-11-09.md)。

## 目录结构（纳入版本控制部分）

```
TOOLBOX/
├── src/                 # 源代码（web_server / 视频生成 / TTS Worker / 文档转换 等）
├── scripts/             # 辅助脚本
├── tests/               # 测试
├── static/index.html    # 前端页面
├── config/              # .env.example / start.bat / start.sh / requirements.txt
├── docs/                # README / BUILD / TESTING / 设计文档
├── deliverables/        # 交付文档
├── build_exes.py        # PyInstaller 打包入口
├── build_hooks/         # 打包钩子
├── requirements.txt     # 主服务依赖清单
└── 项目说明书.md         # 完整项目手册（安装/配置/排错）
```

## 硬件要求

| 场景 | GPU | 说明 |
|---|---|---|
| **主线流程**（素材→视频） | 不需要 | Edge TTS 在线合成，仅需联网 |
| **本地语音克隆**（GPT-SoVITS V5 / CosyVoice3） | **NVIDIA GPU，建议 ≥ 6GB 显存**，CUDA 12.8 驱动 | 单次只常驻一个 Worker；无 GPU、未开启 GPU 加速或依赖缺失时降级为 Edge TTS / 回落 CosyVoice3 |

详细配置见 [`项目说明书.md`](项目说明书.md) §5 与 [`docs/PORTABILITY.md`](docs/PORTABILITY.md)。

## 配置

参考 [`config/.env.example`](config/.env.example)，把真实值写入项目根目录的 `.env`（程序启动时自动加载）。
关键环境变量（详见 `项目说明书.md` §6）：

| 变量 | 说明 |
|---|---|
| `TOOLBOX_API_KEY` / `TOOLBOX_API_URL` | AI 话术 API（未设置则用兜底话术） |
| `SOLUTION_AGENT_API_KEY` / `SOLUTION_AGENT_API_URL` | 企业方案智能体 |
| `APP_VARIANT` | `gpu` / `cpu`，主流程是否启用 GPU |
| `TOOLBOX_OUTPUT_FOLDER` | 输出目录（默认 `output`） |
| `GPTSOVITS_VERSION` | 可选，`v5turbo`（默认，4 步蒸馏）/ `v5dev`；版本编入缓存键 |
| `GPTSOVITS_VENV` | 可选，GPT-SoVITS 推理 venv 的 python 路径 |

## 技术栈

- **前端**：HTML5 / CSS3 / 原生 JavaScript（无框架）
- **后端**：Python 3.13 + Flask + flask-cors
- **TTS**：edge-tts、GPT-SoVITS V5（默认）、CosyVoice3（可切换，本地 GPU）
- **歌声转换**：Seed-VC + Demucs（可选，独立 Python 环境；分别负责音色转换与原曲人声分离）
- **媒体**：MoviePy、FFmpeg、PyAV、faster-whisper、cryptography（NCM 本地解码）
- **图像**：rembg + onnxruntime（BiRefNet / ISNet / u2net 抠图）、Pillow、OpenCV
- **文档**：PyMuPDF、python-docx、PowerPoint COM / LibreOffice

## 许可证

[MIT](LICENSE) © 2026 Alex Wang
