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
