# MOSS-Image 接入 toolbox 架构方案（spec-first）

日期：2026-11-06
状态：**方案先于代码，已实现并验证**（结果见第 8 节）。

## 1. 目标与硬约束

- 生图作为**独立界面** `/imagegen`，不塞进 `index.html` 的三步 wizard。
- 生图与 TTS / 唱歌等 GPU 模块**互斥**；**切换页面时智能启停**，自动腾出显存。
- 不动根 venv、不改 `OUTPUT_FOLDER` 语义、不破坏现有 116 个测试。
- 前端风格对齐现有页面：同一套 CSS 变量、`.mainnav`、深色主题开关；**无 emoji 图标、无紫粉渐变**。
- 沿用项目纪律：`gpu_arbiter` 跨进程锁、加锁顺序 `generation_lock` → `gpu_arbiter`、显式文件暂存。

## 2. 代码现状（已逐行核对，非推测）

| 项 | 实际 |
|---|---|
| 页面 | 4 个 MPA：`index.html` / `tts.html` / `singing.html` / `audio_converter.html`，CSS+JS 全内嵌，各自 `.mainnav` 有 4 个链接 |
| 路由 | `web_server.py` 单文件 Flask（2933 行、无蓝图）；`/`、`/tts`、`/singing`、`/audio-converter`、`/ncm-converter` |
| 任务 | 模块级 `tasks` dict + `update_task_progress()` + `/api/status/<task_id>`（自动过滤 `_` 前缀内部字段） |
| 锁 | `generation_lock`（进程内 threading.Lock）→ `gpu_arbiter`（跨进程文件锁），注释明确该顺序 |
| TTS worker | 独立 venv + 常驻 subprocess + JSONL；单例在 `MultiTtsWorkerClient._instances`；`shutdown_all()` 可整体停掉并释放显存；调用方**懒加载**（`get_instance` → `start`） |
| 退出回收 | `atexit` → `_shutdown_worker_on_exit()` → `shutdown_all()` |
| MOSS POC | 已实测 PASS：1024²/30 步 7 分 53 秒、中文文字全对、8GB 卡无 OOM；独立 venv + 26GB 权重已就位 |

## 3. 关键决策：不引入第二个常驻 worker

| | 常驻双 worker | **一次性生图子进程（采用）** |
|---|---|---|
| 显存释放 | 依赖 IPC `shutdown` 成功，失败会残留占卡 | 进程退出即**确定性释放** |
| 8GB 卡 | 两个 26GB/9GB 模型争用，必须精确编排 | 同一时刻只有一个活进程 |
| 代码量 | 新增第三套 IPC 协议 + 单例 + 崩溃重启 | 复用现成 CLI，仅加一个事件输出层 |
| 代价 | 无重复加载 | 每次多约 23 秒加载（相对 6–8 分钟生成可忽略） |

**决策**：生图采用按需一次性子进程，用完即退。

「智能启停」因此落到确定性的两步：

- 切到 `/imagegen` → **主动停** TTS worker（`shutdown_all()`），把 8GB 显存整个让给生图；
- 切回其他页面 → 生图进程已自行退出（或兜底 terminate），TTS 靠既有懒加载**自动启**，无需预加载。

这比"两个常驻 worker 互相开关"更省资源，也不会出现关不掉残留占卡的情况。

## 4. 模式状态机

```
MODE_VIDEO    = "video"      # 默认，覆盖 index / tts / singing / audio-converter
MODE_IMAGEGEN = "imagegen"   # 仅 /imagegen
```

| 转换 | 前置 | 动作 |
|---|---|---|
| VIDEO → IMAGEGEN | `busy == 0` | `MultiTtsWorkerClient.shutdown_all()` → 记录 mode |
| IMAGEGEN → VIDEO | `busy == 0` | 若有残留生图子进程则 terminate → 记录 mode；TTS 不主动启（懒加载） |
| 任意 → 相同 mode | — | 幂等，不重复启停 |
| `busy > 0` 时切换 | — | **拒绝**（HTTP 409），前端提示「有任务进行中」 |

`busy` 由 `mode_manager.task_scope(mode)` 上下文维护：GPU 任务进入 +1，`finally` 里 -1。
这样"用户点了别的页面但生图还在跑"不会把正在用的模型抽走。

## 5. 三层门禁（不只靠前端）

1. **前端上报**：每个页面加载时 `POST /api/mode`，`/imagegen` 报 `imagegen`，其余报 `video`。
   用「页面加载上报」而非 `beforeunload`（后者不可靠）。
2. **后端兜底 ensure**：`/api/imagegen/generate` 强制 `ensure(imagegen)`；
   `/api/tts/quick`、`/api/tts/batch`、`/api/generate` 强制 `ensure(video)`。
   即使直接调 API、多标签页、前端上报失败，也不会抢卡。
3. **GPU 锁**：生图任务同样遵循 `generation_lock` → `gpu_arbiter.acquire`，
   与 9873 训练服务跨进程互斥；`finally` 双重 release。

多标签页冲突：最后一次上报生效；`busy` 门禁保证正在跑的任务不会被抢。

## 6. 模块与职责

| 文件 | 新增/改动 | 职责 |
|---|---|---|
| `src/resource_mode.py` | 新增 | `ModeManager` 单例：模式状态、busy 门禁、TTS worker 启停、生图进程兜底停止；延迟 import 避免循环依赖 |
| `src/imagegen_client.py` | 新增 | 解析独立 venv python；把请求写成 JSON 文件（**避开中文 prompt 的命令行编码问题**）；起一次性子进程；逐行解析 JSONL 事件更新 `tasks` |
| `imagegen_poc/web_worker.py` | 新增 | 独立 venv 内运行；复用 `generate.py` 的 `build_pipe()`；输出 `stage/progress/done/error` JSONL 事件；`inspect.signature` 探测 `callback_on_step_end` 支持后再上报步数 |
| `src/web_server.py` | 改动 | 加 `/imagegen` 页面路由、`/api/mode` GET/POST、生图生成/状态/文件/历史 API；3 处 `ensure(video)` 兜底；`atexit` 补生图进程回收 |
| `static/imagegen.html` | 新增 | 独立界面，对齐现有 CSS 变量与导航 |
| 4 个现有 HTML | 改动 | `.mainnav` 加「AI 生图」链接 + 页面加载模式上报 |
| `tests/test_resource_mode.py` | 新增 | 模式切换、幂等、busy 拒绝、非法模式单测 |

**不改动**：根 `requirements.txt`、`src/paths.py`（`GPU_LOCK_FILE` 仍是死代码）、任何蓝图、TTS worker 协议。

## 7. API 契约

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/mode` | `{mode, busy, tts_workers, imagegen_running, last_switch}` |
| POST | `/api/mode` | `{mode}` → 200 / 409（busy）/ 400（非法值） |
| POST | `/api/imagegen/generate` | `{prompt,width,height,steps,guidance,seed}` → `{task_id}` 202 |
| GET | `/api/status/<task_id>` | 复用现有端点；`stage` = `loading` / `sampling` / `done`；`result` 含 `filename` |
| GET | `/api/imagegen/file/<task_id>` | 返回 PNG（路径从 `tasks[task_id]["_file_path"]` 取，不接受客户端路径，防目录穿越） |
| GET | `/api/imagegen/history` | 最近 N 张（扫描输出目录，只读） |

参数白名单与范围：`width/height ∈ {512,768,1024}`、`steps ∈ [4,50]`、`guidance ∈ [1,8]`、`seed ∈ [0,2^31-1]`、`prompt` 长度 ≤ 2000。
产物落 `get_output_folder()/imagegen/`。

## 8. 验证结果（2026-11-06 实测）

| 验收点 | 结果 |
|---|---|
| 全套回归 | `runtime/python/python.exe -m pytest -q` → **126 passed, 6 skipped, 5 subtests passed**（接入前 116 passed，新增 10 个模式单测） |
| 语法 | `py_compile` 通过：`web_server.py` / `resource_mode.py` / `imagegen_client.py` / `web_worker.py` |
| 页面 | `GET /imagegen` → 200，含独立导航与表单 |
| 模式 API | `GET /api/mode` → `video`；`POST imagegen` → 200 且模式变更；`POST video`（有任务在跑时）→ **409**，证明 busy 门禁生效；`POST bogus` → 400 |
| 环境检查 | `GET /api/imagegen/status` → `ready: True`（venv / worker / 26GB 权重均就位） |
| 参数校验 | 空 prompt → 400；越界 steps 被钳到 50 → 202 |
| 历史/预览 | `GET /api/imagegen/history` → 200 |
| 端到端出图 | 真实服务 512²/8 步 → **done / 124.6 秒 / 265KB PNG**；生成期间切页 409；结束后 GPU 13 MiB、0% |
| 分支 | 仍只有 `main` |

**真实服务端端到端验收（2026-11-06 12:33，PASS）**：

启动 Web 服务后走完整链路：`POST /api/imagegen/generate`（512² / 8 步 / seed 7）
→ 轮询 `/api/status/<task_id>` → `done`，耗时 **124.6 秒**，产出
`output/imagegen/moss_20261106_123308_7.png`（265 KB，图像内容正确）。

- 生成期间 `POST /api/mode {video}` 返回 **409**，`busy=1`，门禁生效；
- 任务结束后 **GPU 显存回落到 13 MiB、利用率 0%**，证明显存被完整释放；
- `callback_on_step_end` 步数进度事件生效（worker 日志可见 8/8 步），前端进度条不是假动画。

服务已启动在 `http://127.0.0.1:5000/imagegen`，可直接点开使用。
若服务被关闭，用 `.venv\Scripts\python.exe src\web_server.py` 重启。

## 9. 明确不做（本期范围外）

- 不做常驻生图 worker / 批量队列 / 图生图 / LoRA / JSON Layout 可视化编辑器。
- 不改打包 spec（当前仓库 `config/toolbax.spec` 本就缺失；恢复打包时需把 `imagegen.html` 加进 `datas`）。
- 不做多用户会话隔离（本地单机桌面工具，最后一次模式上报生效）。
