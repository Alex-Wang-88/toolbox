# 分支合并诊断 + 本地生图模型选型评估

日期：2026-11-06
状态（2026-11-06 执行后更新）：**分支合并已完成并推送 GitHub，本地与远程均只保留 main**。
第一至四节保留诊断当时的原始读数与选型评估；第五节记录实际执行结果与验证数据。
早期 git 结论基于 `git merge-tree --write-tree` 内存态试算。

---

## 一、本机硬件实测（不是配置表，是现场读数）

| 项 | 实测值 |
|---|---|
| GPU | NVIDIA GeForce RTX 5060 **Laptop** GPU，8151 MiB（8 GB），compute capability **12.0 / sm_120** |
| 驱动 / CUDA | 621.01 / CUDA UMD 13.5 |
| CPU | AMD Ryzen AI 7 H 350 w/ Radeon 860M |
| 内存 | 31.1 GB |
| 磁盘 | C: 925 GB 总，**可用 124 GB**（87% 已用） |
| 项目 Python | 3.13.14（`runtime/python/`，内置可重定位） |
| 根 venv `.venv/` | **无 torch**（刻意设计：主流程只做 HTTP 转发，重推理走子进程） |
| `tts_poc/venv_cosyvoice/` | torch **2.11.0+cu128**、diffusers **0.39.0**、transformers **4.51.3**、safetensors 0.8.0 |

sm_120（Blackwell 笔记本卡）需要 CUDA 12.8+ / torch 2.7+，现有 torch 2.11.0+cu128 已满足。
`nvidia-smi` 检查时 GPU 空闲（0 MiB / 0% 占用）。

---

## 二、分支拓扑与合并诊断

### 2.1 现状

本地 `main` = `27f2a50`，`origin/main` = `2099395`，`ahead/behind = 0 / 8`，**纯落后，可 fast-forward**。
5 条本地分支全部跟踪 origin 同名分支。

| 分支 | tip | 相对 origin/main | 内容 | 合并试算 |
|---|---|---|---|---|
| `codex/dark-mode-toggle` | `55779c3` | ahead 0 / behind 4 | 已并入 origin/main | 无需处理，纯残留 |
| `codex/audio-volume-background-music` | `60fd8bf` | ahead 0 / behind 2 | 已并入 origin/main（PR #2） | 无需处理，纯残留 |
| `agent/voice-clip-editor` | `e21a3d9` | **ahead 2** / behind 0 | +43 文件 / **+10,116 行**：音色片段编辑器、NCM 转换、歌声分离/合成、3 个新页面、配套测试 | **零冲突，干净可并**（该分支从 origin/main 线性派生） |
| `codex/edge-tts-subtitle-stability` | `2dfac8e` | ahead 1 / **behind 8** | 2026-07-27 老分支，+12 文件 / +885 −207 | **8 个文件冲突** |

### 2.2 冲突清单（origin/main ← edge-tts 三方合并试算）

```
src/subtitle_generator.py      src/toolbox.py            src/voice_registry.py
src/web_server.py              static/index.html         static/tts.html
tests/test_frontend_workflow.py                          tests/test_voice_management.py
```

`src/toolbox.py` 冲突的根因是重命名：edge-tts 分支基于旧名 `src/toolbax.py`，origin/main 已全局改成 `toolbox.py`。

### 2.3 两条线各有独有代码 —— 这才是关键

| | edge-tts 分支独有 | origin/main 独有 |
|---|---|---|
| 字幕 | `SubtitleGenerator` 的 WordBoundary 合并（`:131`，保留真实发音时间） | 字幕保留问号、标点合并逻辑（不同实现） |
| 路由 | — | `/api/background-music` 三条、`/api/tts-cache/stats` |
| 其他 | 基于 7-27 的 web_server 旧版 | dark mode、toolbax→toolbox 全局重命名、手工文稿、音频混合 |

所以 edge-tts **不是纯冗余**，但直接做三方合并有明确的功能倒退风险：它基于 7-27 的代码，会把 7-28 之后 main 的改动覆盖回去。

### 2.4 其他发现

- 未跟踪目录 `.tmp/`（含 `ppt_video_20260727/node_modules/` 大量文件）与 `checkpoints/`（Seed-VC / bigvgan_v2 / whisper-small / campplus 语音权重）**都没有被 .gitignore 覆盖**。合并后若用 `git add -A` 会误提交（本项目纪律明令禁止 `git add -A`）。
- `src/paths.py:60` 的 `GPU_LOCK_FILE` 是**死代码**，无任何模块消费；`gpu_arbiter.py:43` 自己拼同一路径。生图模块应 `from gpu_arbiter import gpu_arbiter`，不要去改这个常量。
- `build_exes.py:17` 引用的 `config/toolbax.spec` **当前仓库不存在**，恢复打包时需把新 HTML 加进 `datas`。

### 2.5 已完成的零风险动作

为 6 个 ref 全部打了备份 tag 并已推送到远程，任何分支引用都不会丢：

```
pre-merge-20261106/main                          -> 27f2a50
pre-merge-20261106/origin-main                   -> 2099395
pre-merge-20261106/agent-voice-clip-editor       -> e21a3d9
pre-merge-20261106/audio-volume-background-music -> 60fd8bf
pre-merge-20261106/dark-mode-toggle              -> 55779c3
pre-merge-20261106/edge-tts-subtitle-stability   -> 2dfac8e
```

### 2.6 实际执行路线（已完成）

| 阶段 | 动作 | 结果 |
|---|---|---|
| 0 | 6 个备份 tag | 完成，已 push |
| 1 | `main` fast-forward 到 `origin/main`（2099395） | 完成 |
| 2 | 并入 `agent/voice-clip-editor`（e21a3d9，零冲突） | 完成 |
| 3 | 三方合并 `edge-tts`（2dfac8e），8 个冲突文件逐一手工裁决 | 完成，见下 |
| 4 | 删 4 条本地分支 → push → 删 4 条远程分支 → prune | 完成 |

**阶段 3 的实际裁决**（采用 A：移植独有修复，其余以主线为准）：

- 8 个冲突文件中 6 个（`voice_registry.py`、`web_server.py`、`index.html`、`tts.html`、
  `test_frontend_workflow.py`、`test_voice_management.py`）直接取主线版本，避免把 7-28 之后的新功能改回 7-27 旧版。
- `subtitle_generator.py`：在主线版本上插入 edge 分支的 `_lines_from_tts_cues` /
  `_map_cues_to_display_text` 与 `_build_srt` 的 `timed_lines` 分支，保留主线 `83495d6` 的标点拆分增强。
- `toolbox.py`：在主线版本上补齐 `boundary='WordBoundary'`、从 `SubMaker.cues` 导出
  `word_boundaries`、写入 `SegmentData.subtitle_cues`。
- 4 个自动合并文件全部保留：`hardware_profile.py`（voice_mode 过滤）、
  `video_composer.py`（静音容器与旁白一致）、`text_segmenter.py`（subtitle_cues 字段）、
  `test_subtitle.py`（3 个词边界测试）。

**发现并处理的产品决策冲突**：主线 `83495d6` 规定字幕剥离标点（仅保留问号），
edge 分支要求保留原稿标点/空格，两者互斥。合并统一以主线策略为准，
对应 2 个测试改写为符合主线行为的断言并注明原因，时间轴断言保持不变。

---

## 三、"这台电脑能跑的最好开源画图模型"横向评估

### 3.1 口径说明

"最好"有两种口径，结论不同：
- **榜面画质最强**：Artificial Analysis Arena 2026-11-05 开源权重榜，FLUX 3 Image Klein Elo 1043 / Qwen-Image-2.1 1044 / Fibo 2.0 1039
- **8 GB 卡上真正好用**：要看完整链路（生成器 + 文本编码器 + prompt upsampler + VAE），不是看厂商宣传的"模型本体参数量"

证据等级分别标注：官方实测 / 第三方真机实测 / 厂商宣称 / 第三方估算。

### 3.2 对比表

| 模型 | 参数 | Elo (AA 11-05) | 8 GB 可行性 | 许可证 | 中文文字 | 全套磁盘 | 判定 |
|---|---|---|---|---|---|---|---|
| **FLUX 3 Image Klein** | 5.6B | **1043**（开源第 3） | 厂商宣称 INT8 8 GB；**但完整链路需 9B prompt upsampler（INT8 9.75 GB）分两遍跑**，官方参考实现 BF16 峰值 31 GB、FP8 26 GB | FLUX Kommunity（营收 <$5M 才可商用，**非 Apache**） | 一般 | **约 52 GB**（全量 107 GB） | 画质榜最强，但 8 GB 是"半程可用"；社区实测有人体/眼睛缺陷反馈 |
| Qwen-Image 2.1 | 7B | 1044 | 16 GB INT8，8 GB 不可 | Qwen Research（**仅研究/评估，禁商用**） | 最强 | — | 出局：显存 + 许可双杀 |
| Fibo 2.0 | 8B | 1039 | 16 GB | CC BY-NC 4.0 | — | — | 出局 |
| **Z-Image Turbo** | 6B | 未进 AA 榜（8 步蒸馏，不参与该榜） | **GGUF Q4_K_M 峰值约 6.9 GB**；第三方真机实测 RTX 4060 Laptop 8G，1024² 约 70 s/张，峰值 6.8–7.2 GB | **Apache 2.0** | 好（Qwen3-4B 编码器，中文大白话直写） | 约 11 GB（GGUF 5.07 + 编码器 FP8 5.63 + VAE） | **主推：8 GB 上最稳的日常主力** |
| **MOSS-Image** | 5B | 未进 AA 榜 | **官方 `minimal` profile 实测**：1024²/50 步 GPU 峰值 **3.65 / 3.85 GiB** + 系统 RAM 14 GiB（DiT 权重留 CPU RAM，编码器/VAE 按需从盘加载）；`auto` profile 在 <12 GiB 时自动选它 | **Apache 2.0** | **开放权重第一**（LLTB Mean-6 文本覆盖 96.3%）+ JSON 布局控制 | INT8 约 14 GB / BF16 约 26 GB | **文字/海报/排版场景最强**，但慢、依赖要求高 |
| FLUX.2 [klein] 4B | 4B | 840 | FP8 约 6.8 GB，最省 | Apache 2.0 | 一般 | 约 8 GB | 最快最稳，画质档次明显低一档 |
| Qwen-Image 1.0 / 2512 | 20B | 904 | 24 GB | Apache 2.0 | 强 | 大 | 出局 |

### 3.3 结论

1. **默认主力推荐 Z-Image Turbo（GGUF Q4_K_M / Q5_K_M）**：8 GB 卡有第三方真机实测背书、Apache 2.0 无营收限制、中文提示词直写、8 步蒸馏适合"文稿 → 批量配图"流水线、838 个 LoRA、月下载约 920 万（社区验证充分）、全套约 11 GB 不吃紧。
2. **如果目标是"图里要有准确中文标题 / 海报排版 / 分镜带字"，选 MOSS-Image**：文字渲染开放权重第一 + JSON bbox 布局控制，且有官方 8 GB `minimal` profile 的实测显存数据；代价是 50 步 + CPU offload 速度慢，权重 14–26 GB。
3. **不建议把 FLUX 3 Image Klein 当主力**：Elo 虽最高，但 8 GB 路径要额外跑 9B upsampler、全套 52 GB 会把 124 GB 可用空间吃掉近一半、许可证不是 Apache、早期质量反馈不稳。它更适合当"有空折腾/对比基准"，不适合塞进日常流水线。
4. **不建议同时部署两个模型**：8 GB 显存 + 现有 CosyVoice3 worker 常驻已很紧。

### 3.3.1 实际选型（2026-11-06 确定）

用户看过他人实机体验后，决定**先用 MOSS-Image 体验新模型**，不采用本报告的 Z-Image Turbo 默认建议。
理由是新模型优先、且 MOSS-Image 的文字渲染 / JSON 布局控制更贴合 toolbox 的封面、标题图需求。
客观评估（3.2 / 3.3 节）保持不变，仅记录决策差异；代价是速度慢、权重体积大、需独立 venv。

### 3.4 MOSS-Image 的依赖坑（如果选它）

官方 `pyproject.toml`：`requires-python >=3.10`、`torch>=2.9`、`diffusers>=0.38`、`transformers>=5.3`、`accelerate>=1.10`、`safetensors>=0.6`。
对照本机 cosyvoice venv：torch 2.11.0 ✓、diffusers 0.39.0 ✓、safetensors 0.8.0 ✓、Python 3.13.14 ✓，**但 transformers 4.51.3 ✗**。
→ 直接升级会破坏 CosyVoice3。**必须新建独立 venv**，不能复用。

---

## 四、单独开项目 vs 加入 toolbox

**结论：支持加入 toolbox（与你的偏向一致），但必须用「独立模块 + 独立 venv + 复用 GPU 锁」的形式，而不是把生图代码揉进主流程。**

### 4.1 为什么加入划算

项目已有一整套可复用范式，架构上几乎不构成障碍：

- 独立 venv + `subprocess.Popen` 常驻 Worker + JSONL 协议（`src/tts_worker_protocol.py`）+ 跨进程 GPU 文件锁（`src/gpu_arbiter.py`）+ `tasks` 轮询 + MPA 独立 HTML 页面
- `static/` 是多页面（只有 `index.html`、`tts.html`，CSS/JS 全内嵌），顶部 `.mainnav` 只有两个链接，加第三个页面是自然扩展
- 现在项目**只有"消费图片"**（文档转页图、素材上传、图片序列合成视频），完全没有生成能力；补上生图正好打通「文稿 → 配图 → 视频」
- 遵循项目既有约定：源码路径全部相对 `PROJECT_ROOT`、重依赖不进根 venv、模型权重本地落盘离线运行

### 4.2 最小改动集合

| 文件 | 改动 |
|---|---|
| 新建 `static/imagegen.html` | 独立页面，照抄 `tts.html` 的 IIFE + `fetch('/api/...')` + `/api/status/<id>` 轮询 |
| `static/index.html:566-569` | `.mainnav` 加一个 `<a href="/imagegen">` |
| `static/tts.html:405-408` | 同上，保持两页导航一致 |
| `src/web_server.py` | 加页面路由 + 提交/下载 API + 后台 worker（照抄 `:1161-1222` 的 `_quick_generate_worker`） |
| 新建 `src/imagegen_worker.py`（或 `src/tts_workers/` 下） | 由独立 venv 的 python.exe 拉起，复用 JSONL 协议 |

**不必动**：根 `requirements.txt`、`paths.py`、任何蓝图（项目没有蓝图）。产物直接写 `get_output_folder()`。
**打包态才需要动**：`config/toolbax.spec` 的 `datas` 加新 HTML（该文件当前缺失）。

### 4.3 三个必须解决的硬约束

1. **独立 venv**：新建 `imagegen_poc/venv_imagegen/`（复用 `runtime/python` 3.13.14，`pyvenv.cfg` 的 home 指向它），避免与 CosyVoice3 的 transformers 4.51.3 冲突。权重放 `tts_poc/models/` 或 `checkpoints/`（后者已是 HF cache 布局）。
2. **GPU 互斥是最大风险**：`gpu_arbiter` 是协作式文件锁，只对主动 `import gpu_arbiter` 的进程生效；CosyVoice3 worker 常驻占显存，8 GB 卡上两者并存几乎必然 OOM。生图 worker 必须无条件 `gpu_arbiter.acquire()`，遵循既有加锁顺序 `generation_lock`（外层）→ `gpu_arbiter`（内层），并采用「按需启动 → 生成 → `CMD_UNLOAD` / shutdown 主动释放」，不与 TTS worker 同时驻留。
3. **磁盘预算**：124 GB 可用。Z-Image Turbo 约 11 GB 轻松，MOSS-Image INT8 约 14 GB / BF16 约 26 GB 也够，FLUX 3 Klein 全套 52 GB 会明显吃紧。

### 4.4 什么时候该单独开项目

只有当出现以下任一情况时才值得拆出去：要给外部团队单独交付、要独立打 exe 分发、要跑多卡/常驻服务与 toolbox 的桌面单机定位冲突、或要把生图做成独立产品线。目前都不成立，所以**先加入，模块边界留干净，将来要拆也能整块搬走**。

---

## 五、执行结果（2026-11-06）

> 追加：MOSS-Image 选型已确定，独立 POC 骨架落在 `imagegen_poc/`（README / requirements /
> setup_env.bat / download_weights.py / generate.py），venv 与 25GB 权重由 `.gitignore` 排除。
> 详见 `imagegen_poc/README.md`。

| 项 | 结果 |
|---|---|
| 本地 main | `e75ab7a`（最终），工作区干净 |
| 远程 main | `e75ab7a`（`https://github.com/Alex-Wang-88/toolbox.git`） |
| 本地分支 | 只剩 `main` |
| 远程分支 | 只剩 `main`（4 条旧分支已删除） |
| 备份 | 6 个 `pre-merge-20261106/*` tag 已推送，可随时取回任一被删分支 |
| 合并提交 | `84efb06`（双亲：e21a3d9 + 2dfac8e）、`ee82f3f`（并入远程 PR #3） |
| 测试 | `runtime/python/python.exe -m pytest -q` → **116 passed, 6 skipped, 5 subtests passed** |
| 语法 | `py_compile` 全部通过，无冲突标记残留 |
| 暂存纪律 | 全程 `git add <显式文件名>`，未使用 `git add -A` |

### 5.1 推送时踩到的两个坑（留档）

1. **远程 main 比本地缓存新**：本地 `origin/main` 停在 `2099395`，真实远程已是 `7faa086`
   （PR #3 合并 `agent/voice-clip-editor` 的第一个提交）。先 `fetch` 到真实值，
   试算确认合并后 tree 与本地 HEAD 完全相同（无代码变化），再用 `--no-ff` 把 PR #3 并进历史，
   避免 non-fast-forward 或覆盖远程内容。
2. **网络通道**：SSH（`ssh.github.com:443`）连接被重置；HTTPS 首次 push 报
   `curl 52 Empty reply from server`。本机 `HTTPS_PROXY=http://127.0.0.1:59363` 对 HTTP/2 POST
   处理不稳，改用 `git -c http.version=HTTP/1.1 -c http.postBuffer=524288000 push ...` 一次成功。
   后续推送同样参数又偶发 `curl 52`，确认根因是本机 `HTTPS_PROXY=http://127.0.0.1:59363`
   对 git 的 POST 不稳定。**最终可靠做法是完全绕开代理**：
   `env -u HTTPS_PROXY -u HTTP_PROXY -u ALL_PROXY git -c http.version=HTTP/1.1 push <url> main:main`，
   一次成功。以后这台机推 GitHub 优先用这条。

### 5.2 后续

MOSS-Image 已定为选型，体验环境按「独立 venv + 复用 gpu_arbiter」搭建；
按 spec-first 先出方案再写代码，不把生图逻辑揉进主流程。

POC 当前进度：骨架已提交推送；依赖安装与 25.36 GB 权重下载是两阶段长任务，
权重从 ModelScope（`OpenMOSS/MOSS-Image-SFT`）拉取，支持断点续传。
出图验证（Windows + sm_120 + CPU offload）尚未执行，结果出来前标 **BLOCKED**，不写作 PASS。
