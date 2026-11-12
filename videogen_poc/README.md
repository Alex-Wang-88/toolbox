# MiniMax H3 视频生成本地体验环境（POC）

日期：2026-11-11
定位：**独立 POC，不是 toolbox 正式模块**。验证「这台台式机（RX 9070 XT 16GB / RDNA4 gfx1201）能不能本地跑 MiniMax H3 视频生成」，验证通过后再议接入 Web。

> **状态：2026-11-11 本机端到端实测 PASS** ✅ RX 9070 XT (RDNA4/gfx1201) 上跑通
> MiniMax H3 T2V：576x320 / 73 帧（3s）/ 8 步 Turbo LoRA，含模型加载全程 **339.5 秒**，
> 产物 mp4（h264 576x320 + **AAC 音轨** 3.04s）经 ffprobe 校验通过。实测数据见第六节。

## 一、为什么这么设计

| 约束 | 对策 |
|---|---|
| 主 venv / tts venv 刻意不装 torch | 新建独立 `venv_comfy/`（系统 Python 3.12.0） |
| ROCm Windows torch wheel 只有 cp312 | venv 必须 Python 3.12（torch 2.9.1+rocm7.2.1，与 venv_gptsovits 完全同版本） |
| 16GB 显存 < pruned INT8 DiT 全载 ~20GB | ComfyUI 动态卸载（权重流式交换到 47GB 内存）；速度打折可接受 |
| RDNA4 (gfx1201) 无已验证报告 | 已验证社区案例是 7900 XTX (gfx1100) + 相同 ROCm/torch 栈；gfx1201 由同一工具链支持，属探索档 |
| HF 直连不稳 | 权重走 **ModelScope**（`Comfy-Org/MiniMax-H3` 官方镜像已确认存在） |
| flash-attn / SageAttention 无 ROCm-Windows 官方 wheel | 用 PyTorch SDPA 默认注意力（已验证案例同款做法） |
| ComfyUI 默认端口 8188 | 与 toolbox Web 服务（5000）无冲突 |

## 二、已核实的事实（不是推测）

- GPU：AMD Radeon RX 9070 XT（RDNA4，gfx1201，16GB）+ Ryzen Zen4 8 线程 + RAM 47.1GB + C 盘剩余 ~242GB（2026-11-10 实测）
- ComfyUI master 已内置 H3 原生节点（`comfy_extras/nodes_minimax_h3.py`：`EmptyMiniMaxH3LatentAV` / `MiniMaxH3ImageToVideo` / `MiniMaxH3ReferenceToVideo` / `MiniMaxH3SigmaShift`）
- ModelScope API 确认 `Comfy-Org/MiniMax-H3` 存在且目录结构与 HF 完全一致
- 社区已验证案例（7900 XTX / Win11）：pruned INT8 ConvRot DiT（全载 ~20GB）+ NVFP4 AWQ 编码器 + Turbo LoRA，576x320 / 124 帧 / 6 步 ≈ 4 分钟/条（5s 视频）
- 开放权重本地最高 768p（2K 是闭源 API 模块）；许可为社区许可（年收入 ≤$20M 商业免费，EU/UK/US/KR 需申请，中国不在排除区）

## 三、三步跑通

```bat
REM 1) 环境已就绪：venv_comfy（torch 2.9.1+rocm7.2.1）+ ComfyUI（原生 H3 节点）
REM    重建时参考第五节

REM 2) 下权重（~47.4 GB，断点续传；用根 venv 跑，依赖根 venv 的 modelscope）
.venv\Scripts\python.exe videogen_poc\download_weights.py

REM 3) 启动 ComfyUI 并跑官方 FL2VA T2V 模板
cd videogen_poc && venv_comfy\Scripts\python.exe ComfyUI\main.py --windows-standalone-build --port 8188
REM 浏览器打开 http://127.0.0.1:8188 → 菜单 Workflow → Browse Templates → Video → MiniMax H3
```

产物落在 `videogen_poc/ComfyUI/output/`。

## 四、权重清单（modelscope `Comfy-Org/MiniMax-H3` → `ComfyUI/models/`）

| 文件 | 体积 | 用途 |
|---|---|---|
| `diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 21.0 GB | DiT（FL2VA 族，INT8 ConvRot 量化，ROCm 已验证路径） |
| `text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15.7 GB | Qwen3-VL-32B 文本编码器（NVFP4 AWQ） |
| `vae/minimax_h3_video_vae_fp16.safetensors` | 5.2 GB | 视频 VAE |
| `vae/minimax_h3_audio_vae_fp32.safetensors` | 0.6 GB | 音频 VAE（联合出音） |
| `loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` | 2.0 GB | Turbo 加速 LoRA（8 步出片） |

备选（显存吃紧再下）：`minimax_h3_fl2va_pruned_w6a8.safetensors`（16.0 GB）、社区 GGUF Q4/Q6 量化。

## 五、环境重建

```bat
REM venv（必须 Python 3.12 —— ROCm wheel 只有 cp312）
C:\Users\Alex\AppData\Local\Programs\Python\Python312\python.exe -m venv videogen_poc\venv_comfy
REM 第一步：ROCm SDK + 元包（rocm-7.2.1.tar.gz 必须先装——torch 依赖 rocm==7.2.1 元包，缺了 pip 解析会去 PyPI 找不到而失败）
videogen_poc\venv_comfy\Scripts\python.exe -m pip install https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm-7.2.1.tar.gz
REM 第二步：torch / torchaudio / torchvision（单独一次 pip 调用）
videogen_poc\venv_comfy\Scripts\python.exe -m pip install https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/torch-2.9.1%%2Brocm7.2.1-cp312-cp312-win_amd64.whl https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/torchaudio-2.9.1%%2Brocm7.2.1-cp312-cp312-win_amd64.whl https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/torchvision-0.24.1%%2Brocm7.2.1-cp312-cp312-win_amd64.whl
videogen_poc\venv_comfy\Scripts\python.exe -m pip install -r videogen_poc\ComfyUI\requirements.txt --index-url https://mirrors.aliyun.com/pypi/simple --extra-index-url https://pypi.org/simple

REM ComfyUI 源码
git clone --depth 1 https://github.com/comfyanonymous/ComfyUI videogen_poc\ComfyUI
```

自定义节点（按需）：`ComfyUI-INT8-Fast-ROCM-ConvRot`（INT8 加速内核，gfx1100 验证、gfx1201 待验证）、`ComfyUI-GGUF`（GGUF 量化加载，备选路线）。triton-windows 需要 MSVC Build Tools，首测先不用（PyTorch SDPA 即可）。

## 六、实测记录（2026-11-11，RX 9070 XT 16GB）

**结论：PASS** — 576x320 / 73 帧（3s）/ 8 步 / Turbo LoRA，seed 随机，`run_poc.py` 全自动提交。

| 项 | 实测值 |
|---|---|
| 全流程耗时（含从盘加载 ~35GB 权重） | **339.5 秒**（首次试跑含同样流程为 189 秒，波动来自权重是否还在 RAM 缓存） |
| 产物 | `ComfyUI/output/videogen_poc/h3_t2v_00001_.mp4`，466 KB |
| 流校验 | h264 576x320 + **AAC 音轨**（联合音视频生成正常），3.04s / 24fps |
| GPU 栈 | torch 2.9.1+rocm7.2.1 + HIP 7.2.53211，ComfyUI 0.45.0，pytorch attention（SDPA） |
| 显存策略 | ComfyUI 自动 `fast_disk` 权重流式加载（19.5GB DiT 无法全载 16GB，实测无需任何手动 offload 参数） |
| 量化支持 | 原生 ops：convrot_w4a4 / w6a8_int8 / int8_tensorwise / fp8；**nvfp4 走 emulated ops**（编码器 NVFP4 可用但非原生） |
| RDNA4 兼容性 | gfx1201 无需任何补丁/自定义节点即跑通；`ComfyUI-INT8-Fast-ROCM-ConvRot` 等加速节点属可选提速项 |

**API 格式工作流的三个坑**（构造 `workflows/h3_t2v_api.json` 时踩过）：
1. `BasicGuider` 的条件输入名是 `conditioning`，不是 `positive`
2. `SamplerCustomAdvanced` 需要 `sampler` 输入（接 `KSamplerSelect`），漏了会校验失败
3. `SaveVideo` 的 `format` 是必填 DynamicCombo（用 `"mp4"`）

**已验证可用路径**：ComfyUI 原生 INT8 ConvRot DiT + NVFP4 AWQ 编码器 + Turbo 8 步 LoRA，
无需 triton / flash-attn / 任何自定义节点。下一步提速可试 INT8-Fast-ROCM 节点与更高分辨率（768p 原生档）。

## 七、8GB 显存档位（笔记本 RTX 5060 / CUDA）

> **状态：2026-11-12 本机端到端实测 PASS**（RTX 5060 Laptop 8GB / CUDA 12.8）。
> 576×320 / 73 帧 / 8 步 / Turbo LoRA / seed 42：全流程 **534.4 秒**（含冷启动与 17.4GB 权重
> 加载，采样阶段 514.2 秒），产物 h264 576×320@24fps、3.04 秒、207 KB，**无音轨**（符合 `audio=false`）。
> 权重文件名与体积经 hf-mirror / ModelScope API 核实；节点接口已对 ComfyUI 0.45.0 源码逐行核对。

同一份代码、同一条 main 分支，只用 `VIDEOGEN_PROFILE` 切换档位，
定义集中在 `config/videogen_profiles.json`：

| 档位 | 机器 | DiT | 文本编码器 | 音轨 |
|---|---|---|---|---|
| `full`（默认） | 台式 RX 9070 XT 16GB / ROCm | INT8 ConvRot 19.53GB | Qwen3-VL-32B NVFP4 14.61GB | 有 |
| `lowvram8g` | 笔记本 RTX 5060 8GB / CUDA | W4A8 剪枝 11.68GB | Qwen3-VL-4B INT4 2.62GB + ClipProj 0.47GB | **无** |

### 7.1 为什么不是"再压一档 GGUF"

8GB 卡的第一瓶颈是 32B 文本编码器（15.7GB），不是 DiT；它只在提示词编码时用到，
却会把 UNet 挤出显存。4B + ClipProj 把这部分降到约 4.5GB，收益远大于把 DiT
压到 Q2_K。且有实测报告指出 H3 的 GGUF 量化因 hidden size 2688 不能被 256 整除
会退化、每步反量化，Q4_K_M 反而慢于 INT8 ConvRot。

### 7.2 操作步骤

```bat
REM 1) .env 里设档位（必做，服务启动时读取）
REM    VIDEOGEN_PROFILE=lowvram8g

REM 2) 先只核实下载源，不占带宽
.venv\Scripts\python.exe videogen_poc\download_weights.py --profile lowvram8g --verify-only

REM 3) 下 4 个必需权重（约 17.4GB，断点续传，中断后重跑同命令）
.venv\Scripts\python.exe videogen_poc\download_weights.py --profile lowvram8g --workers 32
REM    可选：加 --include-optional 补 Turbo LoRA / 音频 VAE（约 +2.4GB）
```

`--workers 32` 是必需的：hf-mirror 的 LFS CDN（`cas-bridge.xethub.hf.co`）对单连接限速，
2026-11-12 实测单连接 0.44–0.59 MB/s、4 并发 0.97 MB/s、16 并发 1.88 MB/s、
**32 并发 3.69 MB/s**（17.4GB 约 80 分钟）。下载器按 8MB 分块写稀疏文件的不同偏移，
进度记在 `.chunks`，中断后重跑只补缺失块；ModelScope 源单连接就有 ~10 MB/s，不走并发。

权重落到 `videogen_poc/ComfyUI/models/<subdir>/`，来源：

| 文件 | 体积 | 来源 |
|---|---|---|
| `diffusion_models/minimax_h3_fl2va_pruned-w4a8_convrot_pruned.safetensors` | 11.68 GB | `Winnougan/MiniMax-H3-INT4_Convrot_ComfyUI`（hf-mirror） |
| `text_encoders/qwen3vl_4b_int4_convrot.safetensors` | 2.62 GB | `Merserk/qwen3vl-4b-int4-convrot`（hf-mirror） |
| `clip_projections/mmh3-4b-ClipProj-v3.1-mlp.safetensors` | 0.47 GB | `NicoLab28/ClipProj-MiniMax-H3`（hf-mirror） |
| `vae/minimax_h3_video_vae_int8_convrot.safetensors` | 2.62 GB | `Comfy-Org/MiniMax-H3`（ModelScope） |

### 7.3 环境前置

- **ComfyUI mainline ≥ 2026-08-07**：`asym_w4a8_int8` 在这天合入 `comfy/ops.py`，W4A8 权重才认得；不需要自定义分支。
- **ComfyUI ≥ 0.31.0 + `nicolab28/ComfyUI-ClipProj` ≥ 0.1.13**：v3.1 投影矩阵要这个版本。
- **单卡必须用两节点链** `CLIPLoader(type=krea2) → ClipProjApply`，**不要**用 `ClipProjLoader`(all-in-one)：
  它默认 `mode=resident` 会把 4B 编码器钉在显卡上，8GB 卡上反而比原来的 32B 慢。
  ⚠️ `type=auto` 只在 ClipProj 自带的 loader 里合法；ComfyUI 原生 `CLIPLoader` 的枚举里**没有 auto**，
  填了会被工作流校验拒绝。4B 用 `krea2`，8B 用 `boogu`。此结论已对 ComfyUI 0.45.0 的
  `nodes.py:CLIPLoader` 与 ClipProj 节点 README 逐行核对。
- PyTorch 建议 cu130（RTX 50 系）；本机驱动 621.14 支持。v100 的 cu128 路径上 INT8 内核可能静默回退。
- 启动参数由档位自动追加：`--lowvram --fp16-vae --disable-pinned-memory`。

### 7.4 已知代价与风险（未实测项）

- 默认 **无音轨**：档位 `audio=false`，工作流摘掉音频 VAE / `VAEDecodeAudio` / `CreateVideo.audio`。
  已核对 ComfyUI 0.45.0 `comfy_extras/nodes_video.py:222`：`io.Audio.Input("audio", optional=True)`，
  不带 audio 在节点定义层面合法；仍需真机跑一次确认出片。
- 分辨率 / 时长 / 步数被后端钳到 768×448 / 124 帧 / 8 步，页面选项由 `/api/videogen/status` 下发。
- **系统内存比显存更紧**：本机 31.1GB（实测空闲约 11GB），社区建议 8–16GB 卡配 64GB；
  建议先把 Windows 页面文件调到 32–48GB（C 盘剩 110GB），生成时关掉浏览器等占内存程序。
- `full` 档零回归已用 `git HEAD` 基线逐节点比对验证（7 组用例全一致），台式不受影响。

### 7.5 验收判据

| 阶段 | 命令 / 检查 | PASS 判据 | 本机结果 |
|---|---|---|---|
| S0 权重 | `download_weights.py --verify-only` | 5 个必需文件全部 `[VERIFY]` | ✅ 34m 全下完（17.4GB + LoRA 1.8GB） |
| S1 就绪 | 启动 ComfyUI → `GET /system_stats` | 服务起来、设备为 cuda:0、关键节点在 `/object_info` | ✅ 0.45.0，识别 `cuda:0 NVIDIA GeForce RTX 5060 Laptop GPU` |
| S2 出片 | `run(params, output_dir, progress)` | 产出 mp4，ffprobe 可读 h264 | ✅ **534.4s**，576×320/73帧/3.04s/207KB |
| S3 Web | `/videogen` 提交 → 轮询 → 下载 | 页面出片、结果可下载 | ⏳ 未测（需要手动开服务点页面） |

任何阶段失败请如实标 **BLOCKED + 原因**，不要把未跑通写成 PASS。

**2026-11-12 实测环境**：RTX 5060 Laptop 8GB + 31.1GB RAM（页面文件 26GB，未按要求调到 48GB 也能跑完）；
ComfyUI 0.45.0 + torch 2.11.0+cu128 + torchvision 0.26.0+cu128；ComfyUI 启动参数
`--lowvram --fp16-vae --disable-pinned-memory`；启动时 ComfyUI 提示
`You need pytorch with cu130 or higher to use optimized CUDA operations`——属性能提示，非阻塞，
首次出片能成功但采样偏慢（8 步 514 秒）。
