# toolbax 代码审查与修复 - 2026-07-20

## 目标
基于 2026-07-19 代码审查发现的 3 个 bug 和 1 项冗余，进行针对性修复。

## 修复清单

| # | 严重度 | 位置 | 问题 | 修复方案 | 状态 |
|---|---|---|---|---|---|
| 1 | 🔴 高 | `src/web_server.py` L1371 `app.run(debug=True)` | debug 模式 + reloader，导致进程被启动两次、Worker 显存翻倍、暴露 Werkzeug debugger | 改为 `debug=False, use_reloader=False` | ✅ 已修复 |
| 2 | 🟡 中 | `src/web_server.py` L1257 `download_voice` | 条件反了：拒绝 cosyvoice3（应支持的克隆音色），允许 cloud_parallel（无文件可下载） | 改为 `v.type != "cosyvoice3"` 时拒绝 | ✅ 已修复 |
| 3 | 🟡 中 | `src/web_server.py` L906 `list_voices` | 不返回 `availability_reason` 字段，tts.html L573 引用了它 | 在 `/api/voices` 返回中增加 `availability_reason` 字段，根据依赖检查结果生成具体原因 | ✅ 已修复 |
| 4 | 🟡 中 | `static/index.html` 多处 | 死代码调用 `/gpu-voice/status`、`/gpu-voice/enable`、`/gpu-voice/install`、`/voices/{id}/sync` 全部 404 | 重构 `loadGpuVoiceStatus` 为基于本地 `/api/config` + `/api/voices` 推导；删除 `handleSyncVoice`、`sync_required` 渲染分支、GPU 安装 Modal HTML | ✅ 已修复 |
| 5 | 🟢 低 | `src/web_server.py` `_cosyvoice3_available` | 与 `gpu_setup.check_dependency()` 检查内容重复 | `_cosyvoice3_available` 改为直接调用 `gpu_setup.check_dependency()` | ✅ 已修复 |
| 6 | 🟢 低 | `static/tts.html` L388 | "需要先在主页开启 GPU 加速" 文案不准确 | 改为更准确的依赖说明 | ✅ 已修复 |

## 详细修复

### 1. `web_server.py` 末尾启动配置
```python
# 修改前
app.run(host="127.0.0.1", port=5000, debug=True)
# 修改后
app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
```
**验证**：启动后 `Debug mode: off`，只有一个 Python 进程（PID 14688），无 reloader 子进程。

### 2. `download_voice` 逻辑反转
```python
# 修改前（逻辑反了）
if getattr(meta, "type", "") not in ("cloud_parallel",):
    return jsonify({"error": "仅支持导出克隆音色"}), 400
# 修改后
if getattr(meta, "type", "") != "cosyvoice3":
    return jsonify({"error": "仅支持导出 CosyVoice3 克隆音色的参考音频"}), 400
```

### 3. `/api/voices` 增加 `availability_reason`
后端基于 `gpu_setup.check_dependency()` 结果生成具体原因：
- 未启用：`未启用 CosyVoice3 本地克隆（主页 GPU 加速开关未开启）`
- 依赖缺失：`CosyVoice3 依赖未就绪：权重/venv/Matcha-TTS 缺失`（附 dep_msg）
- 完整：空字符串

### 4. index.html 死代码清理
- **删除**：`handleSyncVoice` 函数、`sync_required` 渲染分支、`/sync` click handler 分支
- **删除**：GPU 安装 Modal HTML（`#gpuVoicePanel`）+ 初始化代码 `initGpuVoicePanel()`
- **删除**：`enableGpuVoice`、`startGpuVoiceInstall`、`startGpuVoiceInstallPoll`、`stopGpuVoiceInstallPoll`、`openGpuVoicePanel`、`closeGpuVoicePanel`、`onGpuVoiceToggleClick`、`renderGpuVoiceInstalling`、`updateGpuVoiceProgress`、`showGpuVoiceInstallError` 共 10 个死代码函数
- **重写**：`loadGpuVoiceStatus` 改为基于本地 `/api/config` + `/api/voices` 推导 GPU 状态，不再调用 `/gpu-voice/status`
- **重写**：`renderGpuVoice` 改为只读展示，无按钮和事件监听

### 5. `_cosyvoice3_available` 简化
```python
# 修改前：20+ 行重复检查权重/venv/Matcha-TTS
# 修改后
def _cosyvoice3_available():
    ready, _ = gpu_setup.check_dependency()
    return ready
```

### 6. tts.html 文案
```
修改前：默认云端音色可直接使用；CosyVoice3 本地克隆音色需要先在主页开启 GPU 加速并完成依赖安装。
修改后：默认云端音色可直接使用；CosyVoice3 本地克隆音色需安装权重 / venv / Matcha-TTS 依赖，未就绪时会在音色卡片显示原因。
```

## 验证结果

### Python 语法
`python -c "import ast; ast.parse(open('src/web_server.py', encoding='utf-8').read())"` → OK

### Flask 启动
- `Debug mode: off` ✅
- 只有一个 Python 进程（无 reloader 子进程）✅
- Worker 未启动（Flask 启动时不预启动 Worker，按需启动）✅

### 前端请求
启动后浏览器加载首页日志：
```
GET /                       200
GET /api/hardware           200
GET /api/output-folder      200
GET /api/voices             200
GET /api/voices             200
GET /api/tts-cache/stats    200
GET /api/config             200  ← 新增的调用（用于 GPU 状态推导）
POST /api/client-alive      200  ← 心跳
```
**没有任何 404**！原来持续报 404 的 `/gpu-voice/*` 调用已全部消失。

### `/api/voices` 接口
返回示例：
```json
{
  "voices": [
    {"id": "default", "name": "默认（云端 Edge TTS 并行）", "type": "cloud_parallel", "available": true, "availability_reason": ""},
    {"id": "changkai_cosyvoice3", "name": "常凯凯(CosyVoice3)", "type": "cosyvoice3", "available": true, "availability_reason": ""},
    {"id": "crystal_cosyvoice3", "name": "Crystal", "type": "cosyvoice3", "available": true, "availability_reason": ""}
  ]
}
```

### `/tts` 页面
HTTP 200，正常加载（28616 字节）。

## 未处理的项（可选）
- index.html 中残留的 `.gpu-voice*`、`.gpu-panel*`、`.gpu-btn*`、`.gpu-progress*` CSS 类 —— 无害死代码，可后续清理
- index.html 中"已弃用 GPU 弹窗"相关 CSS 规则可一并清理

## 当前状态

| 项目 | 状态 |
|------|------|
| Bug 1 (debug=True) | ✅ 已修复 |
| Bug 2 (download_voice 逻辑反) | ✅ 已修复 |
| Bug 3 (availability_reason 缺失) | ✅ 已修复 |
| Bug 4 (前端死代码 404) | ✅ 已修复 |
| Bug 5 (冗余检查) | ✅ 已修复 |
| Bug 6 (tts.html 文案) | ✅ 已修复 |
| Flask 服务 | 运行中（PID 14688，Debug mode: off）|
| 显存 | 已释放（无 Worker 占用）|

## 文件变更清单
1. `src/web_server.py` - 4 处修改
2. `static/index.html` - 4 处修改（删除 Modal HTML、handleSyncVoice、sync_required 分支、重写 loadGpuVoiceStatus + renderGpuVoice）
3. `static/tts.html` - 1 处修改（文案）

## 测试建议
1. 浏览器打开 http://127.0.0.1:5000/ → 确认 GPU 加速面板显示正常（只读状态）
2. 浏览器打开 http://127.0.0.1:5000/tts → 确认 TTS 页面正常生成音频
3. 测试音色导出功能（CosyVoice3 应可导出，cloud_parallel 应被拒绝）
4. 模拟依赖缺失场景（临时重命名权重目录）→ 确认 `availability_reason` 字段返回具体原因
