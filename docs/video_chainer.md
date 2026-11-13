# 视频链式续写（video_chainer）

把「**上一段的最后一帧 → 下一段的首帧**」这套做法规整成一个可复用模块，解决单段生成长度不够的问题。

## 为什么需要

本地 MiniMax H3 在 8GB 档位下单段最长 124 帧（24fps ≈ 5.17s），而一段完整的机动、一次完整的运镜通常要 8–15 秒。
把长镜头切成若干段、让模型接着上一段的末帧继续往下画，最后无损拼接，是当前性价比最高的解法：

- 段与段之间**像素级衔接**（实测两处接缝的平均绝对差 5.2–5.3/255，肉眼几乎看不出来）；
- 不需要一次吃满显存，段数可以按需增加；
- 每段都落盘，中途失败不用从头再来。

## 三层结构

| 层 | 函数 | 作用 |
|---|---|---|
| 工具层 | `probe_video` / `normalize_frame` / `extract_frame` / `extract_tail_frame` / `concat_videos` / `make_gif` | 只依赖 ffmpeg/ffprobe，与模型无关 |
| 质检层 | `seam_diff` / `make_seam_sheet` | 接缝两侧帧的平均绝对差 + 对照图，判断有没有跳变 |
| 编排层 | `ChainRunner` | 逐段生成、失败即停、断点续跑、自动拼接与接缝报告 |

`ChainRunner` 不认识任何模型，它只要一个生成器回调：

```python
def generate(prompt, first_frame, dst, *, seed=None, index=0, name=""):
    ...  # 把 first_frame 喂给你的图生视频接口，产物落到 dst
    return dst
```

仓库自带的 `videogen_generator(params, out_dir)` 已把 MiniMax H3（ComfyUI）接好，`ChainRunner` 结束时会自动
调用它挂上的 `close()` 停掉 ComfyUI 子进程、释放显存。

## 命令行

```powershell
# 抽末帧（可选尺寸规整）
python src/video_chainer.py tail --video out/seg1.mp4 --out frames/tail.jpg --size 576x320

# 拼接（默认 -c copy 无损；时长对不上会自动回退重编码）
python src/video_chainer.py concat --out out/all.mp4 out/seg1.mp4 out/seg2.mp4 out/seg3.mp4

# 接缝质检
python src/video_chainer.py seams --out seams.jpg out/seg1.mp4 out/seg2.mp4 out/seg3.mp4

# 按计划逐段生成
python src/video_chainer.py run --plan plan.json [--dry-run] [--assemble-only] [--no-resume]
```

`plan.json`：

```json
{
  "width": 576, "height": 320, "fps": 24, "seed": 42,
  "first_frame": "first.jpg",
  "params": {"steps": 8, "lora_strength": 1.0, "sampler_name": "res_multistep"},
  "stages": [
    {"name": "01_pitch_up", "prompt": "镜头稳定跟拍，舰首持续上仰……"},
    {"name": "02_hang_yaw",  "prompt": "接上一帧：顶点几乎静止，绕纵轴回转……"},
    {"name": "03_recover",   "prompt": "接上一帧：舰首继续下摆，恢复巡航……"}
  ]
}
```

- 第一段用 `first_frame`，之后各段的首帧由上一段末帧自动生成；
- 每段的 `params` 可覆盖全局（例如单独调 `denoise` 或 `seed`）；
- `--assemble-only` 只做拼接与接缝报告，复用已有片段（推理中断后补后处理用）；
- `--no-resume` 强制重跑所有段（默认复用已完成的段）。

## 产物布局

```
chain_work/
├── segments/01_01_pitch_up.mp4      # 每段产物
├── frames/01_01_pitch_up_tail.jpg   # 抽出来喂给下一段的首帧
├── frames/02_02_hang_yaw_head.jpg   # 每段首帧（接缝质检用）
├── manifest.json                    # 每段状态/耗时/接缝差异
├── seams.jpg                        # 接缝对照图
└── chain_3seg.mp4                   # 最终拼接结果
```

`manifest.json` 记录了每段 `status`（done / reused / skipped / failed）、耗时与错误，失败时可直接看出是哪一段。

## 调参经验

- **末帧抽取用 `-sseof -0.12`**：比 `-sseof -1` 更稳，避开最后一个 GOP 的编码延迟；取不到时自动回退到按帧号 `select`。
- **首帧必须规整成模型输出尺寸**（默认 `cover`）。留黑边会让模型把黑边当成构图。
- **接缝差异 < 6/255 基本看不出**；> 18 说明两段的构图/色调已经漂移，通常是提示词里「接上一帧」写得太弱，或段与段之间换了 seed。
- **段数不是越多越好**：每段都相当于一次独立推理，漂移会累积；3–4 段（9–15 秒）是这台机器的实用上限。
- **拼接优先 `-c copy`**：不重编码、无二次压缩损失；本模块会在拼接后核对总时长，偏差超阈值即回退重编码，避免「静默丢段」。

## 相关

- 本地 H3 调用与档位限制见 [`videogen-integration-spec.md`](videogen-integration-spec.md)；
- 抠图（做首帧/参考图用的背景移除）见 [`image_cutout.md`](image_cutout.md)；
- 单测：`tests/test_video_chainer.py`（用 ffmpeg 合成测试视频，不依赖 GPU）。
