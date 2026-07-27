# TOOLBOX 跨平台改造与发布前检查报告

**日期**：2026-07-24
**场景**：代码改造（路径相对化 + 跨平台/无 GPU 兼容）+ 发布前检查（QA）
**参与成员**：排障手（gstack-investigator）+ 质量门神（gstack-qa-lead）

---

## 📌 TL;DR（执行摘要）

- **整体结论：🟢 通过**（路径相对化改造完成 + 发布前质量门 🟢 通过、0 阻塞项）
- 排障手完成 7 项路径相对化/跨平台改造，并经 `git grep` 验证：**仓库内已无任何 `C:\Users\12992` 字面量残留**。
- 质量门神独立核查：`.gitignore` 全覆盖（含 `.env`/`runtime/`/`tts_poc/`/`app_data`）、**无硬编码密钥**、依赖已跨平台（`pywin32` 条件化）、启动脚本就位（`start.sh` 可执行位 100755）。
- 下一步：你按本报告"行动清单 + push 命令"自行推到 GitHub（`https://github.com/Alex-Wang-88/toolbox.git`），我未执行 commit/push。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 Go（可干净推送） |
| 严重度分布 | 🔴 0 / 🟠 0 / 🟡 1（低危：根目录 `venv_cosyvoice/` 未忽略，实际已被 `tts_poc/` 覆盖）/ 🟢 通过 |
| 关键行动项 | 5 条（含 git push 命令） |
| 建议负责人 | 用户（自行 push）/ 排障手（后续 `tts_poc` 跨平台） |

---

## 1. 各成员核心结论

### 🔧 排障手（调试与根因 / 改造）
- **核心判断**：此前定位的三大阻断点（venv `pyvenv.cfg` 绝对路径、`CosyVoice` worker 写死 Windows venv、Windows-only 二进制 + CUDA 锁死）均已通过"路径相对化 + 跨平台回退 + 依赖条件化"化解；**现有 Windows + NVIDIA 环境保持可用**（当前机器 `home` 指向的 `runtime\python` 存在 → 复用 `.venv`，克隆仍可用 GPU）。
- **关键建议**：分发时别带 venv，靠 `start.bat`/`start.sh` 自愈重建；无 N 卡机器主线自动降级 CPU、克隆降级 Edge TTS；完整方案见 `docs/PORTABILITY.md`。

### ✅ 质量门神（QA 测试与发布）
- **核心判断**：传 GitHub 前质量门 **🟢 通过、0 阻塞项**。`.gitignore` 全覆盖、无硬编码密钥、依赖已跨平台、启动脚本就位。
- **关键建议**：自行 push 时用**精确 `git add`**（严禁 `git add .env` / `git add -A`）；建议把根目录 `venv_cosyvoice/` 补进 `.gitignore` 做防御兜底；留意 `static/index.html`、`static/tts.html` 两个**接手前就 modified** 的文件按你意图处理。

---

## 2. 综合改造与发现（按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议/状态 | 来源成员 |
|---|--------|------|------|---------|------|---------|
| 1 | 🟡 | 发布防御 | `.gitignore` | 根目录层 `venv_cosyvoice/` 未被忽略（实际在 `tts_poc/` 下已被 L84 覆盖，当前不影响） | 追加 `venv_cosyvoice/` 一行做兜底 | 质量门神 |
| 2 | 🟢 | 路径 | `config/start.bat`、`config/start.sh`（新增） | venv 自愈：检测 `pyvenv.cfg` 的 `home` 失效则重建 | 已修复 | 排障手 |
| 3 | 🟢 | 路径 | `src/hardware_profile.json`（删除）、`src/hardware_profile.py:62-84` | 移出 `src/`、PATH 命中返回裸命令名不落盘绝对路径 | 已修复 | 排障手 |
| 4 | 🟢 | 路径 | `src/TOOLBOX.py:1294-1301` | 字体硬编码改 `os.path.join(os.environ.get('WINDIR','C:/Windows'),'Fonts',...)` | 已修复 | 排障手 |
| 5 | 🟢 | 跨平台 | `src/multi_tts_voice.py:72-110`、`src/gpu_setup.py:127-137` | CosyVoice venv 路径加 `bin/python`/`bin/python3` 回退 + 读 `COSYVOICE*_VENV` 环境变量 | 已修复 | 排障手 |
| 6 | 🟢 | 跨平台 | `requirements.txt:49` | `pywin32==312` 改为 `pywin32==312; platform_system == "Windows"` | 已修复（将随仓推送） | 排障手 |
| 7 | 🟢 | 跨平台 | `tts_poc/requirements.txt:86` | `pynini` 本地绝对路径 wheel 改为 `pynini==2.1.6`（**仅本机生效**，`tts_poc/` 被 gitignore） | 已修复（本机） | 排障手 |
| 8 | 🟢 | 文档 | `docs/PORTABILITY.md`（新增） | 跨平台/无 GPU 方案文档 | 已新增 | 排障手 |
| 9 | 🟢 | 测试/脚本 | `scripts/*`、`tests/*` | `__file__` 推导 `SRC_ROOT`、输出路径改相对、外部资产改环境变量兜底 | 已修复 | 排障手 |

---

## ✅ 行动清单（至少 3 条具体可执行项）

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 自行 push：精确 `git add`（见下命令）→ `git commit` → `git push -u origin main` | 用户 | P0 | 今天 |
| 2 | 加固 `.gitignore`：追加 `venv_cosyvoice/` 防根目录漏网 | 用户/排障手 | P2 | 可选 |
| 3 | 处理 `static/index.html`（5 行删除）、`static/tts.html`（213 行变更）——接手前已 modified，非本次改动，按你意图决定是否 add | 用户 | P1 | push 前 |
| 4 | 跨平台用户按 `docs/PORTABILITY.md` 运行（Linux `apt install ffmpeg libreoffice` + `bash config/start.sh`；macOS `brew install ffmpeg libreoffice` + `./config/start.sh`） | 用户/协作者 | P1 | 分发时 |
| 5 | 模型权重 9.1GB（`tts_poc/models/`）被 gitignore，需整体拷贝或下载脚本随项目分发 | 用户 | P1 | 分发时 |

---

## ⚠️ 待完善 / 已知局限

- **克隆（CosyVoice3）仍基本限定 Windows + CUDA**：主流程（网页/图床/视频合成）已跨平台，但 GPU 语音克隆依赖 CUDA venv；无 N 卡机器按 `PORTABILITY.md` 走 CPU 回退（Edge TTS）路径。
- **`tts_poc/` 改动不进 GitHub**：`pynini` 修复仅本机生效，克隆者仍按原 CUDA 专属 wheel 装依赖（`tts_poc` 整体被忽略，克隆后本就需本地自建该 venv）。
- **`tests/` 资产缺失可能失败**：`test_inputs/`、`app_data/` 等被忽略，克隆后测试可能因资产缺失失败（需用户自备或设环境变量）。
- **`static/` 两个文件**：`index.html`（5 行删除）、`tts.html`（213 行变更）是排障手接手前就 modified，非本次改动。
- **`.py` 行尾**：Windows `core.autocrlf` 默认行为导致若干 `LF will be replaced by CRLF` 提示（对 Python 无害），可选在 `.gitattributes` 追加 `*.py text eol=lf`。

---

## 跨平台 / 无 N 卡方案（精简版，完整见 `docs/PORTABILITY.md`）

| 目标环境 | 运行方式 |
|---|---|
| Windows 同机同路径 | `config\start.bat`（复用现有 venv，克隆可用 GPU） |
| Windows 异机/异路径 | `config\start.bat` venv 自愈（runtime/python 优先，否则系统 python 重建） |
| Linux | `apt install ffmpeg libreoffice` → `bash config/start.sh`（克隆因无 CUDA 自动禁用） |
| macOS | `brew install ffmpeg libreoffice` → `./config/start.sh`（打包按 `docs/BUILD.md` py2app） |
| 无 NVIDIA GPU（AMD/Intel/纯 CPU） | 主线自动降级 CPU；克隆降级 Edge TTS；可选 `APP_VARIANT=cpu`；CPU 版 torch/onnxruntime 安装命令见 `PORTABILITY.md` |

---

## 你自行 push 的命令清单（质量门神整理，未执行）

```bat
:: 步骤 0（可选加固）：补 .gitignore
echo venv_cosyvoice/ >> .gitignore

:: 步骤 1：精确 add（严禁 git add .env / -A）
git add config/start.bat config/start.sh .gitattributes requirements.txt docs/PORTABILITY.md
git add scripts/diag_cache_key.py scripts/test_batch_path.py scripts/test_crystal_clone.py scripts/test_short_14.py scripts/test_worker_direct.py
git add src/gpu_setup.py src/hardware_profile.py src/multi_tts_voice.py src/TOOLBOX.py
git add tests/_e2e_voice_test.py tests/test_voice_train.py
:: ⚠️ static/index.html、static/tts.html 预存在 modified，默认不 add；要提交显式 git add static/index.html static/tts.html

:: 步骤 2：commit
git commit -m "feat: cross-platform portability & venv self-heal

- add config/start.sh (Linux/macOS) with executable bit; venv self-heal in start.bat/start.sh
- relativize paths via __file__; de-hardcode font path (WINDIR env)
- make pywin32 conditional on Windows in requirements
- add .gitattributes (sh->lf, bat->crlf) and docs/PORTABILITY.md
- resolve venv python cross-platform in multi_tts_voice.py/gpu_setup.py"

:: 步骤 3：push（首次需 -u）
git push -u origin main
```

---

## 📚 成员产出索引

- gstack-investigator（排障手）原始产出：路径相对化与跨平台改造清单（7 项任务 completed）+ `docs/PORTABILITY.md` + 残留风险 + 质量门神交接说明
- gstack-qa-lead（质量门神）原始产出：传 GitHub 前发布检查报告（🟢 通过，0 阻塞项）+ 逐项核查表 + push 命令清单

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
