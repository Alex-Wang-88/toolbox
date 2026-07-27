# TOOLBOX 项目梳理与 TTS 独立页面交付记录

> 任务时间：2026-07-18 00:00 ~ 00:50
> 目标：① 在 TOOLBOX 项目里新增一个独立 TTS 页面（选音色 + 输入文字 + 生成音频）；② 顺手核对清理所有非 CosyVoice3 的 TTS 模型残留。

---

## 一、本次交付物

### 1. 新增文件 `static/tts.html`（27.7 KB）
- **音色选择**：加载 `/api/voices`，卡片式展示，自动区分可用/不可用（灰显）。
- **文本输入区**：多行 textarea，支持 `Ctrl+Enter` 快捷生成。
- **语速滑块**：0.70× – 1.50×，实时显示当前倍率。
- **生成按钮**：调用 `/api/tts/quick`，带 spinner、计时器、进度条。
- **结果区**：`<audio>` 播放 + 下载按钮（`/api/tts/file/<rel>`）。
- **会话历史**：本次会话内保留最近 20 条生成记录，可点击重播。
- **样式**：复用主站 CSS 变量（`--accent`、`--panel`、`--radius`…），右上角返回主页链接。

### 2. `src/web_server.py` 新增路由（约 L404）
```python
@app.route("/tts")
def tts_page():
    return send_from_directory(STATIC_DIR, "tts.html")
```
- 直接复用现有 `/api/voices`、`/api/tts/quick`、`/api/tts/file/<rel>` 三个后端接口，无需新增 API。
- 未修改 `index.html`，主站入口保持原样。

### 3. 访问方式
启动 `python src/web_server.py` 后，浏览器打开 `http://127.0.0.1:5000/tts` 即可。

---

## 二、项目结构核对（完整目录树）

```
TOOLBOX/
├── .venv/                      # Python 虚拟环境
├── app_data/                   # 运行时数据（voices.json、tts_cache、temp_uploads 等）
├── audio_assets/               # 音色素材库
│   ├── raw/                    # 原始切片（4 个文件）
│   ├── ready/                  # 已预处理素材（6 个文件）
│   ├── ref_audio.wav
│   ├── 小龙虾训练与积木OBC.mp3  # 120 MB，原始整段（仅保留参考）
│   └── 音色清单.md             # XTTS 时代遗留文档（见下文建议）
├── bin/                        # 外部二进制
├── build_hooks/
│   └── set_variant.py
├── config/
│   ├── .env.example
│   ├── requirements.txt
│   ├── AI图片讲解视频生成器.spec
│   └── start.bat
├── deliverables/               # 空目录
├── docs/
│   ├── BUILD.md
│   ├── README.md
│   ├── TESTING.md
│   ├── plan.md
│   └── archive/                # 4 份历史设计文档
├── legacy/
│   └── TOOLBOX_web.py   # 旧版单文件实现（GB 编码乱码，已废弃）
├── output/                     # 运行时生成
├── scripts/                    # 空目录
├── src/                        # 核心代码
│   ├── web_server.py           # Flask Web 服务（5000 端口，主入口）
│   ├── app_launcher.py         # 桌面启动器
│   ├── TOOLBOX.py       # 主流程：图片→解说→TTS→字幕→视频
│   ├── document_converter.py
│   ├── hardware_profile.py
│   ├── enterprise_solution_to_video.py
│   ├── ffmpeg_util.py
│   ├── gpu_arbiter.py          # 跨进程 GPU 串行锁
│   ├── gpu_setup.py            # GPU 硬件门禁（已切 CosyVoice3）
│   ├── multi_tts_voice.py      # 多 TTS 引擎路由（仅 CosyVoice3 + Edge）
│   ├── voice_registry.py       # 音色注册表（白名单 cloud_parallel/cosyvoice3）
│   ├── text_segmenter.py
│   ├── subtitle_generator.py
│   ├── video_composer.py
│   └── tts_workers/
│       └── cosyvoice3_worker.py  # CosyVoice3 常驻 Worker（子进程 JSON-RPC）
├── static/
│   ├── index.html              # 主站页面（132 KB）
│   └── tts.html                # ★ 本次新增独立 TTS 页面
├── tests/                      # 测试脚本
├── test_inputs/                # 测试素材 + POC 日志
│   ├── poc_cosyvoice*.log      # 9 份 CosyVoice POC 日志
│   ├── poc_cosyvoice/          # 6 个 POC 音频样本 + report.json
│   ├── voice_changkai*.wav     # 3 个参考音频
│   ├── ref_text_changkai.txt
│   ├── manuscript_hangzhou_yunrong.pdf
│   ├── whisper_base/           # Whisper base 模型（145 MB）
│   └── poc_diffusers_install.log
├── training_assets/            # 训练素材
├── tts_poc/                    # CosyVoice POC 子工程
│   ├── CosyVoice/              # CosyVoice 仓库
│   ├── models/
│   │   └── CosyVoice3-0.5B/    # ★ 当前唯一 TTS 模型（约 6.5 GB）
│   ├── venv_cosyvoice/         # CosyVoice 专用 venv
│   ├── cosyvoice_poc.py
│   └── download_model.py
├── video-dubbing/              # 空目录
├── __pycache__/
├── _last_clone_id.txt          # 旧 XTTS 时代残留
├── _speech_v2.json             # 旧脚本残留（省人才集团解说词）
├── .env                        # 环境变量（已清理 IndexTTS2 残留）
├── THIRD_PARTY_NOTICES.md      # 含 IndexTTS2 引用
├── _cleanup_report_20260716.md # 旧迁移报告
├── _environment_setup_20260716.md
└── _migration_report_20260716.md
```

---

## 三、TTS 引擎现状核对（确认已全部切到 CosyVoice3）

### 已确认清理完毕
| 位置 | 状态 |
|---|---|
| `src/tts_workers/` | 只剩 `cosyvoice3_worker.py`，无 `index_tts2_worker.py` |
| `src/voice_registry.py` | 白名单 `ALLOWED_VOICE_TYPES = {"cloud_parallel", "cosyvoice3"}`，读取时自动 purge 旧版（`xtts_clone` 等）并备份为 `voices.json.bak` |
| `src/multi_tts_voice.py` | 仅支持 CosyVoice3；`resolve_cosyvoice3_python` 解析到 `tts_poc/venv_cosyvoice` |
| `src/TOOLBOX.py` | `batch_generate_tts` 主链路只调 Edge TTS（default）或 CosyVoice3 本地克隆 |
| `src/gpu_setup.py` | `check_dependency()` 已改为始终返回 `(False, None)`，保留函数只为兼容调用方 |
| `src/gpu_arbiter.py` | 跨进程锁，无 TTS 引擎耦合 |
| `src/web_server.py` | 无任何 `indextts`/`index_tts2`/`modal`/`gpt_sovits` 字样 |
| `tts_poc/models/` | 只剩 `CosyVoice3-0.5B/`（约 6.5 GB） |
| 项目根目录 | 无 `indextts2_weights/` 或 `indextts2_env/` 目录 |
| `docs/` | 全文检索无 IndexTTS2 引用 |
| `legacy/TOOLBOX_web.py` | 旧单文件版本，与 IndexTTS2 无关 |

### 残留清理建议（非阻塞，可按需处理）

| 位置 | 内容 | 建议 |
|---|---|---|
| `tests/__pycache__/` | 3 个 IndexTTS2 相关 `.pyc`（无对应 `.py` 源文件） | 删除整个 `tests/__pycache__` 即可 |
| `.env` | IndexTTS2 配置块（已注释） | 可删除注释块，或保留作历史记录 |
| `THIRD_PARTY_NOTICES.md` | 提及 IndexTTS2 的注释行 | 按需删除 |
| `config/requirements.txt` | 提及 IndexTTS2 的注释行 | 按需删除 |
| `_cleanup_report_20260716.md` / `_environment_setup_20260716.md` / `_migration_report_20260716.md` | 根目录 3 个旧迁移报告 | 建议移到 `docs/archive/` 或删除 |
| `_last_clone_id.txt` | 内容 `clone_5a18d798`（旧 XTTS 克隆 ID，已不在 voices.json） | 可删 |
| `_speech_v2.json` | 省人才集团解说词（旧脚本产物） | 可删或移到 `test_inputs/` |
| `audio_assets/音色清单.md` | 仍是 XTTS 时代写法（提到 XTTS warmup、22050Hz 单声道 16bit） | 建议重写为 CosyVoice3 版本（见下文） |
| `legacy/TOOLBOX_web.py` | GB 编码乱码的旧版单文件 | 已有 `src/` 新版，可删除整个 `legacy/` |

### `voice_registry.py` 中保留的兼容字段
- `sovits_lora: str = ""` 仍在 `Voice` dataclass 里，注释为"已弃用字段（空=自动）"。
- 作用：读取旧 `voices.json` 时不报错，实际不参与任何逻辑。
- 可保留也可删除（删除需同步检查 `_purge_legacy` 逻辑）。

---

## 四、音色清单现状（与 voices.json 对齐）

`/api/voices` 当前返回的音色来自 `app_data/voices.json`，注册类型均为 `cosyvoice3` 或 `cloud_parallel`：

| 音色名 | clone_id | 类型 | 训练状态 |
|---|---|---|---|
| 张昊 | clone_e7585a69 | cosyvoice3 | warmup 已校验 |
| 陈总 | clone_4821203b | cosyvoice3 | warmup 已校验 |
| 曼波 | clone_fd42a14f | cosyvoice3 | warmup 已校验 |
| 士兵男孩 | clone_dd66ae86 | cosyvoice3 | warmup 已校验 |
| B站-在下伊隐-前58秒 | clone_35eb5a46 | cosyvoice3 | 仅格式对齐 |
| B站-伯苼-5m55s片段 | clone_e0332463 | cosyvoice3 | 仅格式对齐 |

> `audio_assets/音色清单.md` 文档内容仍是 XTTS 时代写法（提到 22050Hz/16bit/warmup），与当前 CosyVoice3 实际流程不完全一致。CosyVoice3 对参考音频的要求由 `cosyvoice3_worker.py` 内的 `_ensure_safe_ref` 处理，不再强制 22050Hz。建议后续重写此文档。

---

## 五、TTS 独立页面接口契约

### 前端 `tts.html` 依赖的 3 个 API

| API | 方法 | 入参 | 返回 |
|---|---|---|---|
| `/api/voices` | GET | — | `{voices: [{id, name, type, status, available, deletable}]}` |
| `/api/tts/quick` | POST | `{text, voice, speed?, with_video?, image?}` | `{audio_url, video_url?, video_error?}` 或 `{error}` |
| `/api/tts/file/<rel>` | GET | URL 路径 | 音频/视频文件流 |

### 关键约束
- `voice` 为 `"default"` 时走 Edge TTS（无需 GPU）。
- `voice` 为其他值时走 CosyVoice3 本地克隆，需要：
  - `gpu_setup.load_gpu_voice_settings().enabled == True`
  - `gpu_setup.check_dependency()` 返回 `(True, _)` —— 当前实现**始终返回 `(False, None)`**，意味着本地音色在当前代码下会被 `_tts_check_voice` 拒绝（返回 409 "所选本地音色不可用"）。
  - **这是一个已知卡点**：要让独立 TTS 页面能用上 CosyVoice3 音色，需把 `gpu_setup.check_dependency()` 改为真正探测 CosyVoice3 venv 是否就绪，或在 `gpu_voice_settings.json` 里把 `enabled` 设 `true` 并跳过依赖检查。
- `speed` 会被 `_tts_clamp_speed` 钳制到 [0.7, 1.5]。
- GPU 会被 `gpu_arbiter` 跨进程锁住，与 9873 训练服务互斥。

---

## 六、后续可考虑的优化

1. **修复 `gpu_setup.check_dependency()`**：改为真正探测 `tts_poc/venv_cosyvoice` 和 `tts_poc/models/CosyVoice3-0.5B` 是否就绪，而非始终返回 `False`。这是让独立 TTS 页面能用 CosyVoice3 音色的前提。
2. **重写 `audio_assets/音色清单.md`**：更新为 CosyVoice3 时代的格式说明（参考音频要求、warmup 流程）。
3. **清理根目录**：把 3 个旧迁移报告移到 `docs/archive/`，删除 `_last_clone_id.txt` 和 `_speech_v2.json`。
4. **删除 `legacy/TOOLBOX_web.py`**：已有 `src/` 新版，旧版无引用。
5. **tts.html 增强**：可加"下载文件名自定义"、"批量文本（每行一段）生成"、"音色试听"等功能。
6. **路由暴露**：可在 `index.html` 主页加一个"TTS 工具"入口链接到 `/tts`，目前只能手动访问 URL。

---

## 七、验证清单

- [x] `static/tts.html` 存在（27.7 KB）
- [x] `src/web_server.py` 中 `@app.route("/tts")` 路由已添加
- [x] `src/tts_workers/` 只剩 `cosyvoice3_worker.py`
- [x] `voice_registry.py` 白名单已设 `cloud_parallel` + `cosyvoice3`
- [x] `tts_poc/models/` 只剩 `CosyVoice3-0.5B`
- [x] `src/` 全文检索无 `indextts`/`index_tts2`/`modal`/`gpt_sovits`
- [x] `docs/` 全文检索无 IndexTTS2 引用
- [ ] （可选）清理 `tests/__pycache__/`、根目录旧报告、`.env` 注释块
- [ ] （待验证）启动服务后访问 `http://127.0.0.1:5000/tts` 功能正常
