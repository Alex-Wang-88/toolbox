# 路径中枢统一重构（建议 1：统一路径中枢）

## 背景
toolbax 项目的运行时目录路径（`PROJECT_ROOT` / `DATA_ROOT` / `OUTPUT_FOLDER`）此前散落在 8 个文件中重复定义，存在两类问题：

1. **打包态路径 bug**：`multi_tts_voice` / `gpu_setup` / `audio_transcriber` / `toolbax` 函数内 / `enterprise_solution_to_video` 脚本不区分 `frozen`，PyInstaller 打包后 `__file__` 落在 `_MEIPASS` 会解析到错误目录。
2. **OUTPUT_FOLDER 默认值不一致**：`toolbax` 默认 `'output'`（相对 cwd），`web_server` 默认 `app_data/output`（绝对），CLI 与 web 入口写到不同位置。

## 改动
新建 **`src/paths.py`** 作为单一事实来源，集中定义 `PROJECT_ROOT` / `SRC_DIR` / `DATA_ROOT` / `STATIC_DIR` / `UPLOAD_FOLDER` / `OUTPUT_FOLDER` / `OUTPUT_SETTINGS_FILE` / `TTS_CACHE_DIR` / `VOICES_DIR` / `GPU_LOCK_FILE` 及 `ensure_runtime_dirs()`。本模块不 import 任何业务模块，杜绝循环依赖。

9 个文件接入 `paths`：

| 文件 | 改动 |
|---|---|
| `web_server.py` | 删除重复路径定义，`from paths import ...` + `ensure_runtime_dirs()` |
| `toolbax.py` | `OUTPUT_FOLDER` 默认值统一为相对 `'output'`（`_DEFAULT_OUTPUT_FOLDER` 来自 paths）；函数内 `SRC_DIR/PROJECT_ROOT` 改用 paths（修 frozen bug） |
| `gpu_arbiter.py` | `_default_app_data()` 复用 `paths.DATA_ROOT` |
| `hardware_profile.py` | `_data_root()` 复用 `paths.DATA_ROOT` |
| `multi_tts_voice.py` | `_project_root()` 复用 `paths.PROJECT_ROOT`（修 frozen bug） |
| `gpu_setup.py` | `_project_root()` 复用 `paths.PROJECT_ROOT`（修 frozen bug） |
| `audio_transcriber.py` | `PROJECT_ROOT` 改 `from paths import`（修 frozen bug） |
| `enterprise_solution_to_video.py` | `_project_root` 复用 `paths.PROJECT_ROOT`（修 frozen bug） |

## 关键决策：OUTPUT_FOLDER 用相对路径
按用户明确要求（"一定要相对路径，不要绝对路径"），`OUTPUT_FOLDER` 采用相对路径 `"output"`（相对进程启动 cwd＝项目根，即 `项目根/output`），与项目说明书第 8 节目录结构一致（`output/` 与 `app_data/` 同级）。

- `paths` **不**在模块级读 `TOOLBAX_OUTPUT_FOLDER` 环境变量——因为 `paths` 的 import 时机早于 `.env` 加载，会读不到 `.env` 配置。
- 环境变量仍由 `toolbax` 在 `load_env_file()` 之后读取并尊重：`OUTPUT_FOLDER = os.getenv('TOOLBAX_OUTPUT_FOLDER') or _DEFAULT_OUTPUT_FOLDER`。
- `web_server` 用 `paths.OUTPUT_FOLDER`（`"output"`），运行时可被 `output_settings.json` 覆盖（UI 设置的输出目录优先）。

校验结果：`toolbax.OUTPUT_FOLDER == web_server.OUTPUT_FOLDER == 'output'`（`CONSISTENT=True`）。其余 `app_data` 子目录（上传/缓存/音色/锁）保持基于 `DATA_ROOT` 的动态绝对路径（相对 `__file__`/`sys.executable` 解析，可移植）。

## 验证
- `py_compile` 全部 9 文件通过
- `import paths` + `gpu_arbiter` + `audio_transcriber` + `gpu_setup` 无循环依赖
- `.venv` 完整 `import toolbax` + `web_server` 成功，`OUTPUT_FOLDER` 一致

## 未处理（属其他建议，本次未采纳）
- `.env` 与 `.env.example` 仍保留 `TOOLBAX_OUTPUT_FOLDER=output`（与 `paths` 默认值一致，无需改动）
- 建议 2（按任务隔离产物）、建议 3（规范 output 分层）、建议 4（临时文件收拢）未实施
- 根目录 8 个 `_*` 调试残留文件（`_debug_burn.py` / `_clone_ref.wav` 等）未清理
