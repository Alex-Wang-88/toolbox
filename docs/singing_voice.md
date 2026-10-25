# AI 唱歌（Singing Voice Conversion）

AI 唱歌页面位于 `http://localhost:5000/singing`。页面提供两种输入模式：

- **原曲模式（默认）**：上传带伴奏的完整歌曲，自动分离人声与伴奏，把人声转换成目标音色，再混回原伴奏。
- **已分离人声**：上传干声/导唱，跳过人声分离，处理更快，输出只包含转换后的人声。

两种模式都由导入音频提供旋律、节奏、咬字和情感，目标音色使用 TTS 页面里已经创建的 CosyVoice3 本地参考音频。

## 使用前提

主服务不会把 torch 或歌声模型加载进 Flask 进程。需要单独准备 Seed-VC checkout 和 Python 环境：

1. 从 [Seed-VC 官方仓库](https://github.com/Plachtaa/seed-vc) 获取代码，默认放在 `tts_poc/Seed-VC`。
2. 按 Seed-VC 的说明准备 Python 环境和依赖。官方建议使用 Python 3.10；建议为 Seed-VC 单独建立 venv，不要覆盖已有 CosyVoice 环境。
3. 在项目 `.env` 中配置 Seed-VC Python 路径（Windows 示例）：

   ```text
   SINGING_VC_ROOT=tts_poc/Seed-VC
   SINGING_VC_PYTHON=tts_poc/venv_seed_vc/Scripts/python.exe
   ```

   `SINGING_VC_CHECKPOINT` 和 `SINGING_VC_CONFIG` 可选；不配置时，Seed-VC 首次推理会按自身默认配置下载模型。

   项目自带 `scripts/seed_vc_runner.py`，只负责把官方推理结果写成 WAV，
   在 Windows 上绕过新版 `torchaudio` 对 FFmpeg 共享 DLL 的额外要求；模型推理仍由 Seed-VC 官方 `inference.py` 完成。

4. 重启 Flask 服务，打开 AI 唱歌页面检查“引擎已就绪”。

### 原曲模式的额外依赖

原曲模式使用 [Demucs 官方仓库](https://github.com/facebookresearch/demucs) 做两轨分离。它运行在与 Seed-VC 相同的独立 Python 环境中，不会把 torch 加载进 Flask 主进程：

```powershell
tts_poc\venv_cosyvoice\Scripts\python.exe -m pip install demucs
```

如果使用另一套环境，可在 `.env` 中配置：

```text
SINGING_SEPARATION_PYTHON=tts_poc/venv_cosyvoice/Scripts/python.exe
SINGING_SEPARATION_MODEL=htdemucs
SINGING_SEPARATION_DEVICE=auto
SINGING_SEPARATION_SEGMENT=7
```

首次使用原曲模式时，Demucs 可能自动下载 `htdemucs` 模型；模型缓存不纳入 Git。若分离环境未就绪，页面会保留“已分离人声”模式，不会影响 Seed-VC 的基础转换。

## 页面流程

- 在 TTS 页面创建一个获得授权的 CosyVoice3 克隆音色；创建时选择参考音频语言，支持自动识别及中文、英语、日语、韩语、德语、西班牙语、法语、意大利语、俄语。
- 在 AI 唱歌页面选择该音色，默认上传 WAV / MP3 原曲（1 秒至 10 分钟，最大 100MB）；如果已有干声，可以切换到“已分离人声”。
- 选择或确认歌曲名称；系统优先读取音频内嵌的本地 `title`，否则使用文件名作为可编辑建议，不猜测“歌手-歌曲名”的顺序。
- 选择整体音高偏移和扩散步数，勾选授权确认后开始转换。
- 任务使用现有 `/api/status/<task_id>` 轮询，完成后可在线播放或下载按“歌曲名称 - 音色名称.wav”命名的 WAV。

原曲模式的处理顺序是：`原曲 → Demucs 分离 vocals/no_vocals → Seed-VC 转换 vocals → FFmpeg 混回 no_vocals`。Edge TTS 预设音色没有本地参考音频，因此不会出现在 AI 唱歌的目标音色列表里。转换任务与 CosyVoice TTS 共用 GPU 互斥锁，避免两个模型同时抢占显存。

## API

- `GET /api/singing/status`：检查 Seed-VC 与 Demucs 的运行环境；`separation.ready` 表示原曲模式是否可用，`original_mode_ready` 表示两者是否同时可用。
- `POST /api/singing/convert`：multipart 表单字段 `source`、`source_mode`、`voice_id`、`song_name`、`semi_tone_shift`、`diffusion_steps`、`auto_f0_adjust`、`consent`；`song_name` 可留空以使用本地音频标题或文件名，`source_mode` 为 `original` 或 `vocal`，默认值为 `vocal` 以兼容旧调用；返回 `202 + task_id`。
- `GET /api/status/<task_id>`：复用现有任务状态接口。
- `GET /api/singing/file/<task_id>`：在线播放或下载完成的 WAV。

模型、Seed-VC checkout、独立 venv 和用户音色均不纳入 Git；发布或分发时请分别遵循它们自己的许可证和模型条款。
