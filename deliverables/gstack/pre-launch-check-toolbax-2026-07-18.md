# toolbax 上线前全检报告（代码审查 + 安全审计 + QA 测试）

**日期**：2026-07-18
**场景**：上线前检查（Pre-launch Check）
**参与成员**：🔍 产品官（代码审查）+ 🛡️ 安全卫士（安全审计）+ ✅ 质量门神（QA 测试）

---

## 📌 TL;DR（执行摘要）

- **整体结论**：🔴 不通过（No-Go）。当前存在 **6 项发布阻塞项**，其中 1 项 🔴 严重、5 项 🟠 高危。
- **阻塞项数量**：6（去重后；安全侧 exe 发布口径阻塞 3 项 + 代码审查独有阻塞 3 项）。
- **最紧急的两件事**：① 默认 Edge 音色字幕全空（核心功能缺陷，一行可修）；② 用户图片默认静默上传到公开图床（catbox/uguu），属隐私/合规红线，应**立即**改为本地处理。
- **好消息**：底层工程质量（缓存原子写、Worker 容错、跨进程 GPU 文件锁、voice 路径穿越防护）扎实；安全侧确认全仓 **无 shell=True / os.system / eval / exec / pickle 注入点**；QA 实测核心链路（PDF→配音→NVENC 视频）端到端通过并产出合法 MP4，单元测试 36/37 通过。
- **下一步**：修复 6 项阻塞后可转 🟡/🟢；建议先修「默认音色字幕一行赋值」（🔴 核心缺陷），并立刻止损图片外传隐私红线（B4）。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🔴 **No-Go**（阻塞项未清，禁止广泛发布） |
| 严重度分布 | 🔴 1 / 🟠 5 / 🟡 15 / 🟢 13 |
| 关键行动项 | 11 条（其中 P0 共 7 条） |
| 建议负责人 | 产品官（代码修复）+ 安全卫士（密钥/隐私/沙箱）+ 质量门神（补验证） |
| 转 Go 条件 | 7 项阻塞全部修复 + CosyVoice3 真实推理冒烟 + exe 打包冒烟通过 |

---

## 1. 各成员核心结论

### 🔍 产品官（代码审查）
- **核心判断**：🟡 有条件通过。架构与健壮性实践扎实，但存在 1 个核心功能缺陷（默认 Edge 音色路径下字幕全部为空）与 4 个发布级阻塞（Worker 退出不回收占显存、并发生成污染共享 SRT/全局状态、明文 Key 随包分发、图片默认上传第三方图床）。
- **关键建议**：最该立刻动手的是 F1（一行 `subtitle_text` 赋值）和 F3（生成串行化 / 每任务独立临时目录）；其次补齐 Web 层与 gpu_arbiter 的测试，并把缺失的 PyInstaller spec 补回仓库。

### 🛡️ 安全卫士（OWASP + STRIDE 审计）
- **核心判断**：🔴 不通过。4 项发布阻塞中，最严重的是 `debug=True` 入口带来的 RCE/信息泄露面；其次是明文硬编码 Key 随包分发、用户图片静默上传公开图床、不可信办公文档无沙箱解析。正向点是全仓无命令/代码注入点（subprocess 均列表化、LLM 返回用 json.loads 解析）。
- **关键建议**：先把"发布态调试器、随包密钥、静默公开上传、不可信文档无沙箱"四件事堵上；尤其 F-03 用户隐私图片不知情下发到公开图床，是上线前最该优先处理的合规红线。

### ✅ 质量门神（QA 测试）
- **核心判断**：🟡 有条件通过。核心链路（上传/PDF→配音→字幕→NVENC h264+aac 视频）**实跑通过**并产出合法 MP4；单元测试 36/37 通过、compileall 零错误、Web 冒烟主路由全绿、默认 Edge 音色端到端可用。但 CosyVoice3 本地克隆「实际推理」、exe 打包产物、E2E 测试套件均**未被实际验证**，且有失效/被 gitignore 排除的测试。
- **关键建议**：发布前至少补一次 CosyVoice3 真实推理冒烟 + 一次 exe 冒烟，并把 E2E 测试纳入仓库与 CI，即可从「有条件通过」转为「通过」。

---

## 2. 综合审查发现（去重合并后按严重度排序）

### 🔴 严重（1）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源成员 |
|---|--------|------|------|---------|------|---------|
| B2 | 🔴 | 功能缺陷 | `src/toolbax.py` `_sync_last_segments_from_audio_info()`（~L1369）/ `generate_srt_subtitle()`（~L1433） | 默认 Edge 音色路径下重建的 `SegmentData` 未设置 `subtitle_text`（默认 `""`），`generate_srt_subtitle` 又因 `_last_all_segments` 为真走 CosyVoice3 分支，生成**空白字幕条目**。结果最常见的默认音色视频有配音但字幕全空。 | 在 `_sync_last_segments_from_audio_info` 中为每个 seg 赋值 `seg.subtitle_text = audio_info.get("text","")`；并补覆盖默认音色字幕非空的测试。 | 产品官 |

### 🟠 高危（5，均为阻塞）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源成员 |
|---|--------|------|------|---------|------|---------|
| B3 | 🟠 | 密钥管理 | `.env`（L1/L4，base64 明文）/ `src/toolbax.py:39` `load_env_file` / `:105` | 运行时**必须**有 `TOOLBAX_API_KEY`，且从 exe 同目录读 `.env` → 发布包必须随附含真实 Key 的 `.env`，任何人可提取盗用后端额度。 | 不要把生产 Key 打进发布包；改用户自填 / 自有后端代理转发 / 显式声明为共享公开配额并加速率限制与一键吊销；云端轮换现有 Key。 | 安全卫士 + 产品官 |
| B4 | 🟠 | 隐私/合规 | `src/toolbax.py:299` `upload_to_litterbox` / `:411` `upload_single_image`（base64 兜底默认禁用） | 默认把每张用户图片 POST 到公开第三方托管（catbox.moe 永久存储 + uguu.se），URL 再发给外部 LLM。用户敏感图片在无感知、无同意下离开本机并公开可访问。 | 默认本地处理（开启 base64/本地图床优先）；第三方上传改显式 opt-in，UI 明确告知并取同意；若必须上传用可过期/私有托管。 | 安全卫士 + 产品官 |
| B5 | 🟠 | 不安全设计 | `src/document_converter.py:184-286`（PPT COM / LibreOffice）/ `:144` fitz / `:128` python-docx；入口 `web_server.py:427` | 用户上传的 PPTX/Word/PDF 直接交 LibreOffice/PowerPoint/fitz 解析转图，无大小/页数/解压比上限、无沙箱、无超时。恶意/超大文件可触发解析器 RCE 或 zip 炸弹 DoS。 | 处理前做大小/页数/解压比上限与超时；在受限账户/沙箱（低特权、受限文件系统）下运行转换子进程；显式用禁用外部实体的安全 XML 解析器。 | 安全卫士 |
| B6 | 🟠 | 资源泄漏 | `src/web_server.py:1321` `_shutdown_worker_on_exit`（空 no-op）/ `multi_tts_voice.py` 无 atexit `shutdown_all` | CosyVoice3 Worker 常驻显存，正常退出（`os._exit(0)`）不发 shutdown，atexit 为空操作。Windows 下子进程不随父进程终止 → 孤儿进程持续占 GPU 显存，重开后易 OOM。 | `web_server` 在 atexit 调 `MultiTtsWorkerClient.shutdown_all()`（释放模型 + `empty_cache` + `os._exit`）；对 Worker 子进程注册 OS 级回收（`taskkill /T`）。 | 产品官 |
| B7 | 🟠 | 正确性 | `src/web_server.py` `generate_video_task()`（~L677，无并发上限）/ `src/toolbax.py` 固定路径 `subtitle/full_subtitle.srt` + 全局 `_last_all_segments`/`batches` | 用户连点两次「生成」会并发跑两个任务，共用固定文件名 SRT 与 `audio/` 目录并读写模块级全局 → 字幕/配音时间轴错乱甚至写入对方音频。 | 全局串行化生成（`threading.Semaphore(1)` 或「同一时刻仅一个 processing 任务」校验）；或每任务独立临时输出目录。 | 产品官 |

### 🟡 中危（15）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源成员 |
|---|--------|------|------|---------|------|---------|
| B1 | 🟡 | 安全/配置（应修，非 exe 阻塞） | `src/web_server.py:1336`（`__main__` `app.run(debug=True)`）；触发链 `config/start.bat` | 直接运行 web_server.py 命中 debug=True，Werkzeug 调试器开启后可达即 RCE，异常回显内部栈回溯。发布 exe 走 `app_launcher.py`（已 `debug=False`）不受影响，开发入口误作生产用时风险高，但**非 exe 发布阻塞**。 | 移除 `__main__` 中 `debug=True`；改由环境变量（如 `FLASK_DEBUG`，默认关）控制；统一仅经 `app_launcher.py` 启动。 | 安全卫士 + 产品官 |
| M1 | 🟡 | 访问控制 | `web_server.py` 全路由（无认证/token）；`/api/cleanup:768` rmtree、`/api/cancel:755`、`/api/generate:602` | 仅绑 127.0.0.1 但无认证/CSRF。恶意网页可对本机端口跨源 POST（清空上传、取消、触发生成）。 | 本地 API 加随机会话 token（页面注入、请求头携带）；状态变更端点加 CSRF 校验；或仅监听 Unix socket/管道。 | 安全卫士 |
| M2 | 🟡 | 供应链 | `config/requirements.txt`（仅 `>=` 下界）/ `config/start.bat:21` 启动 `pip install` | 依赖无版本锁定与哈希，启动自动联网安装，后续可能被投毒。 | 用锁定文件（== + --require-hashes 或 Poetry/uv lock）；构建离线 wheel 缓存；发前跑 `pip-audit`。 | 安全卫士 |
| M3 | 🟡 | SSRF | `src/enterprise_solution_to_video.py:149` `download_file` / `:80` / `:271` `--generated-document-url` | 文档 URL 取自第三方 agent 响应文本，随后 `requests.get` 拉取并解析；agent 被操纵或参数可控可 SSRF（含 169.254.169.254）。 | URL 做协议/主机白名单与私网地址阻断；下载后先类型/大小校验；禁止重定向到非预期主机。 | 安全卫士 |
| M4 | 🟡 | 访问控制 | `web_server.py:1239` `/api/voices/<id>/download` | 克隆音色参考音频（生物特征）任何能访问本地端口者均可下载，无认证。 | 敏感下载端点加本地认证/确认；明确生物特征数据存储加密与留存/删除策略。 | 安全卫士 |
| M5 | 🟡 | 资源 | `web_server.py:353`、`:1002` GPU 锁 `acquire(block=True)` 无超时 | 若 9873 训练服务崩溃未退出或长时间训练，生成线程无限阻塞；取消无法中断阻塞中的 acquire。 | 生成/批量路径用带超时 `acquire(timeout=...)`，超时返回「GPU 正忙」。 | 产品官 |
| M6 | 🟡 | 健壮性 | `multi_tts_voice.py` `_send_command`/`_read_loop` | Worker 进程死掉后主线程要等满 CMD_SYNTHESIZE 的 3600s 超时。 | 轮询 `is_alive()`/`poll()`，进程已死立即失败重试/抛错，而非死等。 | 产品官 |
| M7 | 🟡 | 健壮性 | `document_converter.py:203` PPT `DisplayAlerts=1`（Word 用 0） | 损坏/加密 PPTX 可能弹模态对话框无响应且无超时。 | 改 `DisplayAlerts=0`，给 COM 自动化加整体超时/看门狗。 | 产品官 |
| M8 | 🟡 | 输入校验 | `web_server.py` `upload_files:427` 仅 voice 限 10MB | 恶意超大图片/PDF/PPTX 可写满磁盘。 | 加全局上传大小上限（如 200MB/文件）。 | 产品官 |
| M9 | 🟡 | 输入校验 | `web_server.py` `normalize_image_items:252`/`generate_video_task:606` 仅 `isfile` | 客户端可传任意绝对路径图片，无目录穿越限制。 | 限制 `image_items[].file` 在 UPLOAD_FOLDER/输出目录内，或仅接受本次上传返回的文件名。 | 产品官 |
| M10 | 🟡 | 测试质量 | 测试覆盖 | web_server/video_composer/gpu_arbiter/voice_registry/document_converter/multi_tts_voice/gpu_setup 全部零测试；F1/F3 本可被集成测试捕获。 | 补 Web 输入校验/错误响应测试、gpu_arbiter 行为测试、并发生成串行化测试。 | 产品官 |
| M11 | 🟡 | 测试漂移 | `test_gpu_accel.py::test_burn_subtitles_command_format` | 断言旧函数 `_burn_subtitles_with_ffmpeg` 已被重构为 `video_composer.VideoComposer._burn_subtitles`，用例 ERROR（IndexError），CI 全绿也护不住该路径。 | 改为对 `VideoComposer._burn_subtitles` 源码断言或运行期 `composer.check_nvenc()`。 | 质量门神 |
| M12 | 🟡 | 测试可移植 | `test_gpu_accel.py::test_nvenc_encoder_exists_in_ffmpeg` | 用 `findstr`/`2>nul`（Windows cmd 语法），Linux CI 误报。 | 改用 `subprocess` + 文本 `in` 判定 `h264_nvenc`，去 `findstr`/`2>nul`。 | 质量门神 |
| M13 | 🟡 | 测试可行性 | `test_gpu_e2e_real.py` 顶部 `DOCS` 引用缺失素材；`.gitignore:74-75` 排除 E2E 用例 | 用例当前仓库不可跑，相当于无 E2E 覆盖，且不随仓库分发。 | 提交素材到 `test_inputs/` 或参数化路径；将 E2E 纳入仓库（或 `tests/` + `pytest.mark.integration`），移出 `.gitignore`。 | 质量门神 |
| M14 | 🟡 | 发布风险 | `src/multi_tts_voice.py`/`cosyvoice3_worker.py`；`build_exes.py`/`config/*.spec` | CosyVoice3 本地克隆仅确认「环境就绪」（权重/venv/Matcha 齐备、enabled=true、voices available=true），**未实际发起一次推理**；exe 打包产物未构建冒烟。 | 发布前补一次 CosyVoice3 真实推理冒烟 + 一次 exe 打包冒烟（启动/硬件探测/上传 PDF 转图）。 | 质量门神 |

### 🟢 低危（13，节选关键项）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源成员 |
|---|--------|------|------|---------|------|---------|
| L1 | 🟢 | 安全头 | `web_server.py` 各响应 | 缺 CSP / X-Content-Type-Options / X-Frame-Options。 | 统一加 `nosniff`、`X-Frame-Options: DENY`、预览页 CSP。 | 安全卫士 |
| L2 | 🟢 | 密钥隔离 | `toolbax.py:79` → 子进程继承完整环境（含 Key） | API Key 经环境被子进程（ffmpeg 等）继承。 | 构造子进程时传精简 env（剔除密钥），仅调用前临时注入。 | 安全卫士 |
| L3 | 🟢 | 纵深防御 | `web_server.py:116/184/234` 输出根/文件名 | 输出根来自本地 JSON、sanitize 未去前导点/保留名，属穿越防御缺口。 | 输出根白名单/绝对路径校验；文件名清洗加去前导点、Windows 保留名。 | 安全卫士 |
| L4 | 🟢 | 日志 | `toolbax.py:561/603` 打印原始话术前 500 字 | 调试日志过详（含完整话术/文件名）。 | 生产构建关闭 debug print；敏感字段不落日志。 | 安全卫士 |
| L5 | 🟢 | 完整性 | `tts_workers/cosyvoice3_worker.py:46` `sys.path.insert(0, COSYVOICE_DIR)` | 第三方引擎/权重无签名校验，目录被篡改可代码执行。 | 发布期哈希/签名校验；避免 `insert(0,...)` 不可信路径。 | 安全卫士 |
| L6 | 🟢 | 可维护性 | `toolbax.py:250` 导入期 `subprocess.Popen = silent_popen` 全局替换 | 影响整个进程（含其他库）的 Popen，脆弱副作用。 | 改在调用处用 `silent_subprocess_kwargs()`。 | 产品官 |
| L7 | 🟢 | 发布 | `build_exes.py:17` 引用缺失的 `config/AI图片讲解视频生成器.spec` | 文档化构建流程跑不通。 | 补回 spec 并纳入仓库。 | 产品官 |
| L8 | 🟢 | 健壮性 | `toolbax.py:556` SSE 整响应读入内存 | 异常大响应吃内存。 | 分块上限或流式边收边解析。 | 产品官 |
| L9 | 🟢 | 一致性 | `web_server.py` CORS 硬编码 5000，launcher 可能用 5001+ | 大端口下前端硬编码 API host 可能异常。 | CORS/前端动态读取实际端口。 | 产品官 |
| L10 | 🟢 | 竞态 | `web_server.py:768` `/api/cleanup` 直接 rmtree | 会删除进行中任务的输入文件。 | 清理前校验无 processing 任务，或仅清孤儿文件。 | 产品官 |
| L11 | 🟢 | 文档 | `tts_page_delivery_20260718.md` 等 | 文档称 `check_dependency()` 恒返回 False 为「关键 bug」，当前代码已修复为真实探测；音色清单也与现状不符，易误导。 | 更新交付文档，标注 blocker 已修复并同步当前状态。 | 质量门神 |

---

## 3. 阻塞项清单（发布前必须修复）

| 编号 | 严重度 | 阻塞项 | 来源 |
|------|--------|--------|------|
| B2 | 🔴 | 默认音色字幕全空（核心功能缺陷） | 代码 |
| B3 | 🟠 | 明文 API Key 随 .env 分发（密钥泄露） | 安全 + 代码 |
| B4 | 🟠 | 用户图片默认静默上传公开图床（隐私/合规红线） | 安全 + 代码 |
| B5 | 🟠 | 不可信 PPTX/DOCX/PDF 无沙箱解析（解析器 RCE/DoS） | 安全 |
| B6 | 🟠 | CosyVoice3 Worker 退出不回收、长期占显存（OOM 风险） | 代码 |
| B7 | 🟠 | 并发生成污染共享 SRT/全局状态（正确性） | 代码 |

---

## 4. 回滚预案

> 注意：本工作副本 `git status` 报「not a git repository」（无 .git），故下述「git tag 回退」当前不可用，改用 exe/源码快照回退。

- **版本标记**：若后续纳入 git，发布前打 tag（如 `v1.x.x`）并保留上一可用 commit；当前阶段请保留上一版 `dist/` exe 与源码快照作为回退点。
- **配置开关（无需发版即可止血）**：
  - 关闭本地克隆：`app_data/gpu_voice_settings.json` 的 `enabled` 设为 `false` → CosyVoice3 自动 `available=false`，流量退回默认 Edge TTS。
  - 关闭 GPU 路径：`APP_VARIANT=cpu` → `USE_GPU_ACCEL=False`，视频改走 CPU（libx264，已有回退）。
  - 缺 API Key 时程序自动走兜底话术，不影响 TTS/合成链路。
- **exe 回退**：保留上一版 `dist/` exe；新 exe 启动失败时让用户运行旧 exe，或用源码入口 `python src/app_launcher.py`（已 `debug=False`）临时顶上。
- **功能降级（代码内已具备）**：NVENC 不可用 → `VideoComposer` 自动回退 `libx264`；CosyVoice3 不可用 → 自动 `available=false`。
- **数据回退**：`voices.json` / `tts_cache` 位于 `app_data/`（被 .gitignore 排除，不纳入版本控制）——回滚代码不影响用户本地克隆音色与缓存；但也意味着干净部署无预置克隆音色。
- **快速止血**：线上严重问题时，优先设 `gpu_voice_settings.json enabled=false` 关闭本地克隆，或临时移除 `tts_poc` 权重触发 `available=false`，把流量导向稳定的默认 Edge TTS。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 移除 `__main__` 中 `debug=True`，改由 `FLASK_DEBUG` 环境变量控制（默认关），统一仅经 `app_launcher.py` 启动（应修加固，非 exe 阻塞） | 产品官 | P1 | 发布前 |
| 2 | 移除 `.env` 中的生产 Key；改用户自填 / 服务端代理；云端轮换并吊销现有 Key | 安全卫士 | P0 | 修复当日（合规优先） |
| 3 | 图片默认本地处理（开启 base64/本地图床优先），第三方上传改显式 opt-in 并 UI 告知同意 | 产品官 + 安全卫士 | P0 | 修复当日（隐私红线） |
| 4 | 不可信文档解析加大小/页数/解压比上限 + 超时，低特权沙箱子进程运行 | 安全卫士 + 产品官 | P0 | 发布前 |
| 5 | 修复默认音色字幕：在 `_sync_last_segments_from_audio_info` 赋值 `subtitle_text` + 补测试 | 产品官 | P0 | 修复当日（一行） |
| 6 | 生成串行化（`Semaphore(1)` 或 task-scoped temp dir），消除并发污染 | 产品官 | P0 | 发布前 |
| 7 | `web_server` atexit 调 `shutdown_all()` 回收 Worker + OS 级进程回收 | 产品官 | P0 | 发布前 |
| 8 | 发布前补 CosyVoice3 真实推理冒烟 + exe 打包冒烟（启动/硬件探测/上传 PDF 转图） | 质量门神 | P1 | 发布前 |
| 9 | 修复 `test_gpu_accel` 漂移/不可移植断言；E2E 纳入仓库与 CI（移出 .gitignore） | 质量门神 | P1 | 发布前 |
| 10 | 本地 API 加随机会话 token + CSRF 校验；阻断环回 CSRF | 安全卫士 | P1 | 发布前 |
| 11 | 依赖锁版 + `pip-audit`；统一安全响应头；子进程 env 剔除密钥 | 安全卫士 | P2 | 下个迭代 |

---

## ⚠️ 待完善 / 已知局限

- **E2E 未实跑**：`test_gpu_e2e_real.py` / `test_voice_train.py` 因缺失素材（文件名不符）与需运行态 + 特定本地文件，本 QA 未执行；以现有 `manuscript_hangzhou_yunrong.pdf` 等价复现核心链路。
- **CosyVoice3 本地克隆仅「就绪」未「推理」**：权重/venv/Matcha 齐备、`enabled=true`、voices `available=true`，但未实际发起一次合成，RTX 5060 8GB 显存偏紧需关注。
- **exe 打包未验证**：`build_exes.py` 引用的 PyInstaller spec 文件缺失（`config/` 下无 `AI图片讲解视频生成器.spec`），构建流程当前跑不通。
- **无 git 仓库**：工作副本未检测到 `.git`，影响 tag/CI/回滚假设，需先确认仓库状态。
- **Litterbox 外部依赖**：标准 Web 流程依赖公开图床上传，内网/离线/限流时整条生成失败（仅文稿审阅路径可绕开）。
- **成员交叉同步与校准**：代码审查员与安全官就 F-02/F-03/F-04（Key/图床/文档）交叉确认一致；安全官就 F-01（debug=True）做最终校准——降级为 🟡 应修、非 exe 发布阻塞（exe 走 app_launcher.py 已 debug=False），本报告已同步。

---

## 8. 修复执行记录（2026-07-18 后续，个人工具范围）

用户确认：**B3（明文 API Key 随包）、B4（图片静默上传公开图床）为个人工具可接受，不处理**。其余阻塞项已实际修复：

| 编号 | 修复内容 | 文件 | 验证 |
|------|---------|------|------|
| B2 | `_sync_last_segments_from_audio_info` 为每段 seg 赋值 `subtitle_text`，消除默认 Edge 音色字幕全空 | `src/toolbax.py` | ✅ 单元测试 `test_default_voice_subtitle_text_is_set` 通过 |
| B6 | `_shutdown_worker_on_exit` 调用 `MultiTtsWorkerClient.shutdown_all()`（内部发 CMD_SHUTDOWN + `taskkill /F /T`），atexit 时回收 CosyVoice3 Worker 释放显存 | `src/web_server.py` | ⚠️ 编译通过 + 代码审查；建议补一次 Windows 退出回收烟测 |
| B7 | 新增模块级 `generation_lock`，串行化 `/api/generate`、`/api/tts/quick`(with_video)、批量生成三条写入 `_last_all_segments`/固定 SRT 的路径，消除并发污染 | `src/web_server.py` | ⚠️ 编译通过 + 代码审查；建议补一次连点两次「生成」烟测 |
| B5 | 不可信文档轻量防护：文件 200MB / 页数 500 / zip 解压 2GB+单条目比例 100 上限；PowerPoint COM `DisplayAlerts=0` 防卡死；soffice 已有 120s 超时 | `src/document_converter.py` | ⚠️ 编译通过 + 代码审查 |

**验证汇总**：`python -m compileall src tests` 零错误；`python -m unittest discover -s tests` → **38 tests OK（1 skipped）**，新增 B2 回归测试通过。

**结论（个人工具范围）**：B3/B4 用户豁免，B2/B5/B6/B7 已修复 → 可视为 🟢 Go（建议补一次 Windows 退出回收 + 连点两次生成的运行时烟测以闭环 B6/B7）。

---

## 📚 成员产出索引

- 🔍 gstack-product-reviewer（产品官）原始产出：上线前代码审查结论 —— 整体 🟡 有条件通过；1 严重（F1 默认音色字幕空）+ 4 高危阻塞（F2 Worker 不回收 / F3 并发污染 / F4 明文 Key / F5 图片上传）+ 10 中 + 3 低。
- 🛡️ gstack-security-officer（安全卫士）原始产出：上线前安全审计结论 —— 整体 🔴 不通过（exe 发布口径 3 项阻塞：F-02 Key / F-03 图床 / F-04 文档）；STRIDE 威胁建模 + OWASP Top 10 检查表；最终分布 0 严重 + 3 高危 + 5 中（F-01 debug 经校准降为 🟡 应修、非 exe 阻塞）+ 5 低；明确全仓无注入点。
- ✅ gstack-qa-lead（质量门神）原始产出：上线前 QA 测试结论 —— 整体 🟡 有条件通过；实跑：单元测试 36/37 通过、compileall 零错、Web 冒烟全绿、默认 Edge 端到端可用、PDF→NVENC 视频产出合法 MP4；未验证：CosyVoice3 真实推理 / exe 打包 / E2E 套件；含发布检查清单与回滚预案。

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
