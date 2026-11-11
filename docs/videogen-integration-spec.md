# 视频生成（MiniMax H3）接入说明

日期：2026-11-11
状态：已实现并本机端到端验证（RX 9070 XT / ROCm 7.2）
前置：`videogen_poc/` 已按其 README 完成 ComfyUI 环境、权重下载与 CLI 级 PASS（576×320 / 3s / 8 步 ≈ 340s）。

## 1. 目标与硬约束

- 新增 `/videogen` 页面：输入提示词 + 可调参数，提交后由本地 ComfyUI（MiniMax H3）生成带音频的 mp4。
- 主 venv 不装 torch；ComfyUI 用独立 venv（`videogen_poc/venv_comfy`，Python 3.12 + torch 2.9.1+rocm7.2.1）。
- 16GB 显存装不下 19.5GB 的 INT8 DiT → 依赖 ComfyUI 自带 `fast_disk` 权重流式加载（实测无需任何手动 offload 参数）。
- GPU 互斥：视频推理（~16GB 显存 + 35GB 内存权重）与 TTS Worker / 生图互斥，由资源模式统一调度。

## 2. 关键决策：模式级 ComfyUI 进程

| 方案 | 取舍 | 结论 |
|---|---|---|
| 一次性子进程（照抄 imagegen） | 显存确定性回收，但每条视频都要付 2-5 分钟启动+加载 | 否 |
| 常驻服务 | 连续任务快，但 35GB 内存权重长期驻留 | 否 |
| **模式级：懒加载 + 切走即杀** | 首任务付启动成本，连续任务免加载；`resource_mode` 切换时 taskkill 兜底 | ✅ |

- 拉起参数：`main.py --port 8188 --output-directory <output>/videogen/comfy --disable-auto-launch`，日志 `output/videogen/comfyui_server.log`。
- `atexit` 与模式切换都会 `videogen_runner.stop()`（taskkill /F /T，幂等）。

## 3. 资源模式

`src/resource_mode.py` 新增 `MODE_VIDEOGEN = "videogen"`：

- 切入 VIDEOGEN：停 TTS Worker + 兜底停生图进程。
- 切出到 VIDEO / IMAGEGEN：杀 ComfyUI（`register_videogen_stopper`，沿用 imagegen 的注册模式）。
- 队列任务在**执行时**才切模式（`enqueue_task(mode=...)` → `prepare`），页面切换本身不动资源。

## 4. API 契约

| 路由 | 说明 |
|---|---|
| `GET /videogen` | 页面（static/videogen.html，no-store） |
| `GET /api/videogen/status` | 就绪探针：venv + main.py + 5 个权重文件；`{ready, error, mode, limits}` |
| `POST /api/videogen/generate` | JSON `{prompt, width, height, length, steps, seed(-1=随机), lora_strength, shift_video}` → 白名单钳制 → 任务队列 `kind="videogen"` → `202 {task_id}` |
| `GET /api/status/<task_id>` | 统一任务轮询（`_file_path` 内部字段对外隐藏） |
| `GET /api/videogen/preview/<filename>` | 浏览器内播放（`_ranged_file_response`，支持 Range 拖动；basename + `.mp4` 守卫） |
| `GET /api/videogen/file/<task_id>` | 下载（realpath + commonpath 防穿越，照抄 imagegen） |
| `GET /api/videogen/history` | 扫 `output/videogen/comfy` 递归 mp4，mtime 倒序 top 30 |
| `POST /api/cancel/<task_id>` | 协作式取消：progress 回调发现 `cancel_requested` → `videogen_runner.stop()` 杀 ComfyUI |

参数白名单（前后端同源，`videogen_client.clamp_params`）：分辨率 256-1344 且 32 对齐；帧数 5-362 吸附 17k+5 网格；步数 1-30；LoRA 强度 0-1；shift 0.01-100；提示词 ≤2000 字。

## 5. 工作流构造

`videogen_client.build_workflow(params)` 在后端构造 15 节点 API 格式图（与 `videogen_poc/workflows/h3_t2v_api.json` 已验证图一致）：

- `lora_strength <= 0` → 不挂 `LoraLoaderModelOnly`（需配 20+ 步）；
- shift 与默认值（12.0/3.0）一致 → 不挂 `MiniMaxH3SigmaShift`，保持 POC 已验证的原生采样路径；
- 进度：轮询 `/history/<prompt_id>` 判定完成 + best-effort 解析 ComfyUI 日志 tqdm `N/M` 行映射采样进度（解析不到走 indeterminate）。

## 6. 前端

`static/videogen.html` 克隆 imagegen.html 骨架：同样的 4 个公共资源引用、内联 mainnav、`.workspace` 双栏、`data-advanced` 高级区（workspace.js 自动二级视图）、`TaskResume.watch` 轮询（1200ms）、取消、断线恢复、历史磁贴（`<video preload="metadata">`）。

## 7. 验证记录

**2026-11-11 本机端到端 PASS（RX 9070 XT / ROCm 7.2）：**

| 用例 | 结果 |
|---|---|
| 页面与状态 | `GET /videogen` 200；`/api/videogen/status` ready:true；6 页导航 + 暗色主题 + 高级二级视图渲染正常（截图比对 imagegen 页风格一致） |
| 端到端生成 | 576×320 / 73 帧 / 8 步 / Turbo LoRA：排队 → 启动服务 → 加载模型 → 采样（日志 tqdm 实时映射 41%→84%→95%）→ **210 秒 done**，产物 `output/videogen/comfy/h3_00001_.mp4`（h264+AAC 3.04s，ffprobe 校验） |
| 文件服务 | preview 支持 Range（HTTP 206，可拖动进度条）；file 下载 200；history 列表正确 |
| 互斥 | `POST /api/mode {"mode":"video"}` → ComfyUI 被杀（8188 无监听），消息含"停掉 视频推理服务" |
| 取消 | 运行中 `POST /api/cancel/<id>` → ComfyUI 被杀、任务标 cancelled |
| 单测 | `pytest` 全量 172 通过（唯一失败为既有 `test_legacy_project_voice_reference_can_be_exported`，与本次无关） |

**联调期修过的两个坑**（已写入代码注释）：
1. SaveVideo 的输出键是 `images` 而非 `gifs`（轮询 `/history` 产物提取曾因此永远为空）；
2. 中文提示词绝不能走 curl/Git Bash 命令行参数（GBK 编码损坏），页面走 fetch JSON 天然安全。
