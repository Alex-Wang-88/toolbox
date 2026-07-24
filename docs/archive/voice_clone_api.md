# 语音管理后端 API 契约（速查）

> 配套设计：`docs/design_voice_clone.md`　|　沿用 Flask + 现有 `tasks` 进度字典（`progress` 0-100 / `message` / `status`）+ `GET /api/status/<task_id>` 轮询。训练任务**直接复用**该机制，无新增状态端点。

## 0. 通用约定
- 基底：`web_server.py`（`DATA_ROOT=app_data/`、`CORS` 已开）。
- 进度任务字段：`tasks[task_id] = {status, progress, message, ...}`，`status ∈ {pending, processing, completed, failed, cancelled}`。
- 前端轮询：`GET /api/status/<task_id>`，每 1.2s 一次。

## 1. 列出可用语音
`GET /api/voices`
```json
// 200
{
  "voices": [
    {"id": "default",       "name": "默认(云端 Edge TTS 并行)", "type": "cloud_parallel", "status": "ready", "deletable": false},
    {"id": "xtts_preset_1", "name": "预设-女声A",              "type": "xtts_preset",    "status": "ready", "deletable": false},
    {"id": "clone_8f3a1b2", "name": "我的克隆-小美",          "type": "xtts_clone",     "status": "ready", "deletable": true}
  ]
}
```
> 默认 + 预设为代码静态常量（`STATIC_VOICES`），克隆项来自 `app_data/voices/voices.json`；合并返回。

## 2. 上传参考音频并校验
`POST /api/voices/upload`（`multipart/form-data`：`file`）
```json
// 成功 200
{"ok": true,  "upload_id": "u_abc123", "name": "ref.wav", "duration_sec": 8.2, "size_mb": 1.1}
// 失败 200（业务失败，非 HTTP 错）
{"ok": false, "reason": "音频过长，上限 60s（当前 95s）"}
```
服务端强制二次校验：`ALLOWED_EXT=(.wav,.mp3)`、大小 ≤ 10MB、时长 ≤ 60s（`pydub`/`ffprobe`）。通过后存 `app_data/temp_uploads/voice_<upload_id>.<ext>`。

## 3. 启动训练（返回 task_id，复用进度轮询）
`POST /api/voices/train`（`application/json`）
```json
// 请求
{"upload_id": "u_abc123", "name": "我的克隆-小美"}
// 响应 200
{"task_id": "t_xyz789", "status": "processing"}
```
- 后台 `run_voice_training` 写入 `tasks[task_id]`：0 准备 → 20 加载/校验参考音频 → 40 编码音色（加载 XTTS 模型，首跑下载权重）→ 70 模型 warm-up → 90 保存音色 → 100 完成。
- 前端轮询 `GET /api/status/t_xyz789`，进度映射到「语音」分区内的独立进度条（复用 `.fill`）。
- 完成后该音色出现在 `GET /api/voices`（`type=xtts_clone`, `status=ready`）。

## 4. 重命名 / 删除
`POST /api/voices/<id>/rename`（`json` `{"name": "新名字"}`）→ `200`
`DELETE /api/voices/<id>` → `200`
- 默认 / 预设：`deletable=false`，重命名/删除均拒绝（返回 4xx）。
- 克隆：删除时级联清理 `app_data/voices/<id>/` 产物目录。

## 5. 生成任务新增 voice 字段
`POST /api/generate`（既有端点）请求体**新增**：
```json
{ "...既有字段...", "voice": "default" }   // 或 "xtts_preset_1" / "clone_8f3a1b2"
```
- `voice` 缺省或 `="default"` → 云端 Edge TTS **并行**（并发 `min(段落数, 8)`）。
- 其他 → 本机 5060 XTTS 推理（预设用内置 `speaker`，克隆用 `speaker_wav`）。
- 服务端写入 `tasks[task_id]["voice"]`，`run_video_generation` 透传给 `batch_generate_tts(speech_dict, image_info_list, voice=...)`。

## 6. 错误码约定
- `400` 参数/格式错误（如非 multipart、缺 `upload_id`）。
- `404` `task_id` / `voice_id` 不存在。
- `409` 业务冲突（如重命名不存在的克隆、删除不可删项）。
- 上传校验失败走 `200` + `{ok:false, reason}`（前端 inline 提示）。

## 7. voice_id 命名
- `default`（固定）／`xtts_preset_1`、`xtts_preset_2`（固定预设）／`clone_<8位hex>`（克隆动态）。
