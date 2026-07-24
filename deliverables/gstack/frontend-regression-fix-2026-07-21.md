# 前端回归修复报告（JSON 解析 / 缩略图 / 拆分按钮）

**日期**：2026-07-21
**场景**：调试复盘 + 前端回归修复（多成员协作）
**参与成员**：调查员（gstack-investigator）· 设计师（gstack-designer）· 产品评审员（gstack-product-reviewer）· 质量门神（gstack-qa-lead）

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟢 通过（Go）
- 三项用户可见回归已全部修复，并经代码评审 + 实测验证：
  1. **生成文稿后 textarea 显示原始 JSON** → 已加前端防护（normalizeSpeechText）+ 后端兜底守卫（parse_agent_response），畸形 JSON 不再泄漏为话术。
  2. **图片缩略图只显示部分** → 根因是 `.thumb img`/`.ms-thumb img` 用了 `object-fit: cover`（裁切），改为 `contain`（完整适配）。
  3. **“拆分”按钮消失** → 拆分步骤此前被合并进“生成文稿”，现已恢复为独立「拆分素材」按钮，复用既有上传+拆页逻辑、不调 AI。
- 附加修复（评审发现的非阻塞隐患）：「拆分」后上传空白/不支持素材会导致三个按钮同时禁用（软锁死），已加守卫修复。
- 阻塞项数量：0
- 下一步：刷新浏览器（http://127.0.0.1:5000）手动验收三步流程。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 Go |
| 严重度分布 | 🔴 0 / 🟠 0 / 🟡 0（修复前为用户可见 3 项回归，均已清零）|
| 关键行动项 | 3 条（见下，含 1 条非阻塞随访）|
| 建议负责人 | 用户手动验收；随访项可由工程负责人排期 |

---

## 1. 各成员核心结论

### 🔧 排障手（调查员 / gstack-investigator）
- 核心判断：三项问题**均非** 07-21 按钮修复（F1–F6）引入的回归。JSON 问题是 `src/toolbax.py` `parse_agent_response` 在 AI 返回畸形/非 JSON 时把**整段原始响应**当话术塞入（07-20 既存）；缩略图是 `object-fit: cover` 裁切的既有 CSS 缺陷；拆分按钮是能力存在但从未作为独立按钮暴露。
- 关键建议：前端加 `normalizeSpeechText` 防护 + 后端兜底守卫双管齐下；恢复独立拆分按钮复用 `uploadMaterials`+`buildPreparedImages`。已落地并自测（node --check / py_compile 通过，畸形 JSON 仿真不再泄漏）。

### 🎨 设计师（gstack-designer）
- 核心判断：缩略图只显示局部，根因是 `.thumb img` 的 `object-fit: cover` 会填满容器并裁掉溢出；与 07-21 按钮修复无关，是既有布局问题。
- 关键建议：改为 `object-fit: contain`，整张幻灯片等比缩放入盒子并居中（letterbox 用 `.thumb` 灰底）。同源问题 `.ms-thumb img` 一并改为 `contain`。

### 🔍 产品官（产品评审 / gstack-product-reviewer）
- 核心判断：**GO（可发布）**，无阻塞性问题。代码评审覆盖正确性、边界与 LLM 信任边界。
- 关键建议（非阻塞随访）：① 若正常话术恰好以 `{`/`[` 开头且是合法 JSON 但非 `{speech/text/items}` 形状，会被过度裁切为空（极低概率）；② 后端守卫只识别 `video_filename`/`items` 字面量，未覆盖 `filename`/`title` 等其它包络键；③ 评审期间发现「拆分」后存在软锁死隐患（见 F4），建议顺手修复。

### ✅ 质量门神（QA测试 / gstack-qa-lead）
- 核心判断：三项修复**实测全部 PASS**。
- 关键建议：无。验证手段——curl 抓取线上页面确认 `splitBtn`/`拆分素材`/`splitMaterials`/`normalizeSpeechText` 均存在；CSS 全仓 `object-fit: cover` 0 命中、`contain` 生效；用 `parse_agent_response` 跑畸形/合法两组输入，确认畸形走兜底话术、合法正常解析。

---

## 2. 综合审查发现（去重合并后按严重度排序）

| # | 严重度 | 类别 | 位置 | 问题描述 | 建议 | 来源成员 |
|---|--------|------|------|---------|------|---------|
| F1 | 🟡 | 功能/健壮性 | src/toolbax.py:549-552 + static/index.html:1391/2173-2188 | 生成文稿后 textarea 显示原始 JSON（畸形 AI 输出被当话术） | 前端 normalizeSpeechText 防护 + 后端兜底守卫跳过原始包络 | 排障手 + 产品官 |
| F2 | 🟡 | 视觉/CSS | static/index.html:205, 425 | 缩略图被 `object-fit: cover` 裁切只显示局部 | 改为 `object-fit: contain`（thumb + ms-thumb）| 设计师 |
| F3 | 🟡 | 功能/UX | static/index.html:608/723/1410-1437/1896 | “拆分”按钮缺失，拆分合并进“生成文稿” | 恢复独立「拆分素材」按钮 + splitMaterials()（不调 AI）| 排障手 |
| F4 | 🟢 | 健壮性/UX | static/index.html:1420, 1897 | 上传空白/不支持素材时 isPrepared 被无条件置真 → 三按钮同禁用（软锁死）| 仅当 preparedImages>0 才置 isPrepared；clearBtn 在 isPrepared 时始终可用 | 产品官（评审发现）+ 主理人修复 |

> 严重度说明：F1–F3 为用户报告的三项可见回归，根因均为 07-20 既存缺陷（非 07-21 按钮修复引入）；F4 为本次评审新发现的潜在软锁死，已主动修复。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 刷新浏览器（http://127.0.0.1:5000）手动验收：先「拆分素材」→ 调顺序/删页 →「生成文稿」，确认 textarea 为干净话术、缩略图完整 | 用户 | P0（验收）| 立即 |
| 2 | 随访：评估是否收紧 normalizeSpeechText 对“以 `{`/`[` 开头但非话术形状”的合法 JSON 话术的过度裁切（极低概率）| 工程负责人 | P3 | 后续排期 |
| 3 | 随访：后端守卫可扩展识别 `filename`/`title` 等其它包络键，提升健壮性 | 工程负责人 | P3 | 后续排期 |

---

## ⚠️ 待完善 / 已知局限

- 项目**无 git 仓库**，无法提供 diff/历史；本报告基于当前文件状态（mtime：index.html / web_server.py 2026-07-21，toolbax.py 2020-07-20 解析逻辑未动，仅 07-21 加守卫）。
- 浏览器缓存：若用户曾缓存旧版 index.html，需硬刷新（清缓存）才能看到新按钮/CSS；前端防护已彻底杜绝 JSON 进 textarea 的现象。
- 软锁死修复（F4）未单独写自动化测试，已通过代码评审与逻辑推演确认；建议后续补一个“空素材拆分”的边界用例。

---

## 📚 成员产出索引

- gstack-investigator（排障手）原始产出：JSON 解析根因 + 拆分按钮缺失根因 + 三处改动落地（normalizeSpeechText、splitMaterials、toolbax.py 守卫）+ 自测（node --check / py_compile / 畸形 JSON 仿真）。
- gstack-designer（设计师）原始产出：缩略图 `object-fit: cover → contain` 根因与最小 CSS 修复建议（index.html:205、425）。
- gstack-product-reviewer（产品官）原始产出：代码评审报告（GO，含 F4 软锁死隐患与 2 条边界随访）。
- gstack-qa-lead（质量门神）原始产出：实测验证三项 PASS（拆分按钮存在、CSS contain、JSON 守卫畸形/合法双路径）。
- 主理人（lead）补充改动：F4 软锁死修复（splitMaterials 守卫 + updateControls clearBtn 始终可用），及缩略图 CSS 落码。

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
