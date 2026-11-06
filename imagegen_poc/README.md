# MOSS-Image 本地体验环境（POC）

日期：2026-11-06
定位：**独立 POC，不是 toolbox 正式模块**。先验证「这台 8GB 笔记本能不能出图、效果如何」，
验证通过后再按 `docs/branch-merge-and-imagegen-assessment-20261106.md` 第 4 节的方案接入 Web。

> **状态：2026-11-06 本机端到端实测 PASS**。1024² / 30 步中文海报 7 分 53 秒出图，
> 中文字全部正确，全程无 OOM。实测数据见第五节。

## 一、为什么这么设计

| 约束 | 对策 |
|---|---|
| 根 venv 刻意不装 torch | 新建独立 `venv_imagegen/`（复用项目内置 `runtime/python` 3.13.14） |
| cosyvoice venv 的 transformers 是 4.51.3，MOSS 要 ≥5.3 | **不共用 venv**，避免升级破坏 CosyVoice3 |
| RTX 5060 Laptop 只有 8GB / sm_120 | torch 2.11.0+cu128（已验证支持 sm_120）+ diffusers `enable_sequential_cpu_offload()`，权重留在 CPU RAM |
| RAM 31GB | 官方 `minimal` profile 实测 1024²/50 步需系统 RAM 约 14GB，本机够 |
| 磁盘剩 124GB | SFT 全套 **25.36 GB**，可接受；不要同时再下 Base |
| GitHub raw / HF 直连超时 | 权重走 **ModelScope**（`OpenMOSS/MOSS-Image-SFT`），代码走 PyPI / PyTorch 官方源 |
| 官方仓库代码拉不下来 | 权重仓库自带 `pipeline_moss_image.py`，只依赖 diffusers + transformers，**完全自包含**，不需要 clone 官方仓库 |

GPU 争用：本 POC 用 CLI 单独跑，**启动前请关掉 toolbox 的 Web 服务和 CosyVoice3 worker**
（后者常驻占显存，8GB 卡上两者并存必然 OOM）。正式接入时会复用 `src/gpu_arbiter.py` 做互斥。

## 二、实测过的事实（不是推测）

- GPU：`NVIDIA GeForce RTX 5060 Laptop GPU`，8151 MiB，compute capability 12.0，驱动 621.01 / CUDA 13.5
- CPU `AMD Ryzen AI 7 H 350`，RAM 31.1 GB，C 盘可用 124 GB
- ModelScope API `OpenMOSS/MOSS-Image-SFT` 返回 200；HF API 超时；GitHub raw / git clone 超时
- PyPI (200)、清华镜像 (200)、`download.pytorch.org/whl/cu128` (200) 均可达
- 权重仓库 45 个文件 / **25.36 GB**，含 `pipeline_moss_image.py`（440 行，import 只有 torch / diffusers / transformers）
- 官方 README 的 `minimal` profile：1024×1024、50 步，GPU 峰值 3.65 / 3.85 GiB，系统 RAM 含文件缓存 14.00 GiB

## 三、三步跑通

```bat
REM 1) 装依赖（首次约 10–25 分钟，torch cu128 wheel 约 2.5GB）
imagegen_poc\setup_env.bat

REM 2) 下权重（25.36 GB，本机实测 11 分 34 秒，支持断点续传）
REM    用根 venv 跑：下载脚本依赖 modelscope，根 venv 已装，独立 venv 不装
.venv\Scripts\python.exe imagegen_poc\download_weights.py

REM 3) 出第一张图
imagegen_poc\venv_imagegen\Scripts\python.exe imagegen_poc\generate.py "一张极简旅行海报，米色底，蓝色山脉，顶部大字 EXPLORE"
```

产物落在 `imagegen_poc/output/moss_<时间戳>_<seed>.png`。
`generate.py --help` 可看分辨率 / 步数 / CFG / seed 等参数。
首次加载模型会较慢（要把 25GB 权重从盘读进内存），属于正常现象。

## 四、配置口径

- 默认 **SFT** 权重（偏向设计任务、文字渲染与排版），不是 Base（Base 适合训 LoRA）
- 默认 `1024×1024 / 50 步 / CFG 3.0 / shift 5`，DiT bfloat16、VAE float32 —— 与官方 Python 示例一致
- 默认 `minimal` profile（sequential CPU offload）。若报 OOM：先降到 `--width 768 --height 768`，
  再降 `--steps 30`；不要改精度配置，MOSS 对数值变化敏感（单层 bf16 舍入变化可让 LPIPS 偏移 0.041）
- 文字要准：把要显示的字放在英文双引号里，字大一点、短一点；复杂排版用官方 JSON Layout 当 prompt 传入

## 五、实测结果（2026-11-06，本机跑通）

| 项 | 结果 |
|---|---|
| 环境 | torch **2.11.0+cu128** / CUDA 12.8 / diffusers **0.42.1** / transformers **5.22.1** / accelerate 1.15.0 / numpy 2.5.4 / Python 3.13.14 |
| GPU 识别 | `NVIDIA GeForce RTX 5060 Laptop GPU`，`torch.cuda.is_available()` = True |
| 权重 | 26 GB / 8 个 safetensors 分片齐全，ModelScope 下载 **11 分 34 秒** |
| 冒烟测试 | 512² / 8 步 / seed 42 → **PASS**，136 秒（加载 33 秒 + 采样 96 秒） |
| 正式出图 | 1024² / 30 步 / CFG 3 / seed 2026 → **PASS**，**7 分 53 秒**（加载 23 秒 + 采样 5 分 55 秒，稳态约 9.5 s/步） |
| 中文文字渲染 | 大字「山间一盏茶」+ 小字「清明前 · 手工采摘」**全部准确**，排版与留白符合海报要求 |
| 显存 | 全程未 OOM；diffusers 标准 `enable_sequential_cpu_offload()` 在 8GB 卡上成立，**不需要**官方自研 `memory_offload.py` |
| 与 toolbox 并发 | 未测，仍按不支持处理，必须独占 GPU |

产物：

- `imagegen_poc/output/moss_20261106_115748_42.png` —— 冒烟（512² / 8 步）
- `imagegen_poc/output/moss_20261106_120649_2026.png` —— 正式中文海报（1024² / 30 步）

三点观察：

1. 加载时有 `MOSSImageScheduler` 的 config 属性忽略警告，以及缺少 `causal_conv1d` /
   `flash-linear-attention` 导致回退 PyTorch 参考实现的警告。**都不影响结果**，只影响速度；
   想再快一点可以补装这两个包。
2. 第一步明显慢（75 秒，含 CUDA 预热与文本编码），之后稳定在 9.5 秒/步，所以增加步数的边际成本是线性的。
3. 8 步的冒烟图质量已经可用，30 步的正式图细节更完整；官方 50 步是质量上限，本机约 12–13 分钟/张。

### 安装踩过的坑（已修进 `setup_env.bat`）

| 现象 | 根因 | 修法 |
|---|---|---|
| torch wheel 下载 0 字节、反复 502 | 本机 `HTTPS_PROXY=127.0.0.1:59363` 对 2.75GB 文件返回 502 | torch 那一步清空代理变量，直连 `download.pytorch.org` |
| `diffusers>=0.38` 报 `from versions: none` | 清华镜像的 simple 索引对 pip 26 返回空（curl 能拿到，pip 拿不到） | 其余依赖走代理 + **PyPI 官方源** `https://pypi.org/simple` |
| `import torch` 警告缺 numpy | requirements 漏了 numpy | 已补 `numpy>=2` |
| 下载脚本需要 modelscope | 独立 venv 不装 modelscope | 下载改用根 `.venv`（已装 modelscope 1.38.1） |

## 六、验证通过后怎么接进 toolbox（spec-first，届时再写代码）

最小改动集合（详见评估报告第 4.2 节）：

1. 新建 `static/imagegen.html`（独立页面，照抄 `tts.html` 的 IIFE + 轮询模式）
2. `static/index.html:566-569`、`static/tts.html:405-408` 的 `.mainnav` 各加一个链接
3. `src/web_server.py` 加页面路由 + 提交/下载 API + 后台 worker
4. 新建 `src/imagegen_worker.py`，由本目录的 `venv_imagegen` 以 subprocess 常驻，复用 `tts_worker_protocol.py` JSONL

硬约束：worker 必须无条件 `gpu_arbiter.acquire()`（协作式文件锁，不主动接入等于绕过），
遵循 `generation_lock` → `gpu_arbiter` 的加锁顺序，生成后主动 `CMD_UNLOAD` / shutdown 释放显存，
不与 CosyVoice3 worker 同时驻留。产物写 `get_output_folder()`。

**不要动**：根 `requirements.txt`、`src/paths.py` 的 `GPU_LOCK_FILE`（那是死代码）。
恢复打包时记得把新 HTML 加进 `config/*.spec` 的 `datas`（该 spec 当前仓库里缺失）。
