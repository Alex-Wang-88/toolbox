# 一句话成片（话题自动编排）

一句话输入主题，系统自动完成「搜素材 → 编排文稿 → 确认 → 生成视频」。
入口：导航「视频创作 → 一句话成片」（`/topic-video`）。

## 流程与分工

```
用户一句话（"帮我做一个介绍×××的视频"）
   │
   ├─ ① 意图抽取      qwen35-08b-base 跑 CPU（num_gpu=0，显存 0 占用）
   │                  抽出主题+搜索词；模型未就绪时退化为关键词抽取
   ├─ ② 素材检索      确定性管道（不依赖模型）
   │                  文本: DuckDuckGo HTML（搜狗兜底）
   │                  图片: 必应图片搜索（TLS 指纹原因走 curl 下载）
   ├─ ③ 逐页编排      qwen3.5:9b 跑 GPU，keep_alive=0 用后即卸
   │                  把资料改写成逐页讲解词+配图+重点标记（RAG 模式，禁止编造）
   ├─ ④ 代码校验      页数=图数 / 文件幻觉 / 字数 / importance 枚举 / 主题覆盖
   └─ ⑤ 确认生成      页面上逐页可改文稿与标记，提交到既有 /api/generate 管线
```

要点：

- **编排走 RAG**：文稿只基于检索到的资料改写，不靠模型记忆，压制幻觉。
  校验不过（素材太少、页数不符、引用了不存在的图片）会直接报错而不是硬编。
- **显存策略**：意图模型常驻 CPU 零占用；编排模型单次调用即卸载
  （`keep_alive=0`），为后续视频生成（GPT-SoVITS/Qwen3-ASR/生图互斥）让出 8GB 显存。
- **产物即管线输入**：`generate_payload` 与 `POST /api/generate` 请求体字段一致
  （`image_items` + `manuscript`，importance 挂在 image_items 上），前端确认页天然
  构成人工把关（human-in-the-loop）。

## API

| 端点 | 说明 |
|---|---|
| `GET /topic-video` | 页面 |
| `GET /api/topic-video/env` | 环境自检（Ollama 在线、两个模型是否已拉取、curl 可用） |
| `POST /api/topic-video/plan` | 提交编排任务 `{request, pages?, async:true}` → `{task_id}`，用 `GET /api/status/<task_id>` 轮询 |
| `GET /api/topic-video/image/<rel>` | 预览产物图片（仅限 `output/topic_video/` 内，带路径穿越防护） |

## 配置（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `TOOLBOX_OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama 服务地址 |
| `TOOLBOX_TOPIC_INTENT_MODEL` | `qwen35-08b-base:latest` | 意图模型（CPU，0.8B 即可） |
| `TOOLBOX_TOPIC_MODEL` | `qwen3.5:9b-q4_K_M` | 编排模型（9B，编排质量的下限） |
| `TOOLBOX_TOPIC_IMAGE_COUNT` | `5` | 默认配图张数（3-6） |

模型获取：`ollama pull qwen35-08b-base`、`ollama pull qwen3.5:9b-q4_K_M`。
未配置/未拉取时页面顶部会显示具体缺失项；意图模型缺失只影响搜索词质量（有正则兜底），
编排模型缺失则功能不可用。

## 已知限制

- 素材检索是免 key 的网页解析（POC 级）：站点改版/反爬升级会失效，报错即终止；
  产品化建议替换为正式搜索 API 并注意图片版权（或复用 AI 生图模块配图）。
- 图片与文稿的对齐依据图片搜索结果的标题描述，模型不直接"看"图片内容。
- 仅测试过中文主题；编排质量依赖 9B 模型（0.8B 实测无法胜任编排步）。

## 测试

- `tests/test_topic_video.py`：解析器（真实结构 fixture）、校验、产物对齐、
  显存策略断言（意图 CPU / 编排 keep_alive=0）、全流程 mock 端到端。
- 真实端到端（需 Ollama+网络）：提交任务 → 检索 → 编排 → 校验 → `/api/generate` 出片。
