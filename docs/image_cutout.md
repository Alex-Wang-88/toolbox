# 通用抠图（image_cutout）

把「背景移除 + 边缘精修 + 白底/透明输出」做成一个模块，用于给视频生成准备首帧、参考图或素材图。

## 选型结论

2026-11-13 在本机（RTX 5060 8GB，CPU 推理）对四种方案做了实测：

| 方案 | 问题 | 结论 |
|---|---|---|
| 颜色判据（cv2 蓝偏 + 连通域） | 深蓝底规格图上，引擎冷光被挖成白洞；主图留下斜向蓝色光带 | 只能当兜底 |
| ISNet（Apache-2.0） | 正交工程视图上残留蓝网格、尾部发虚、**俯视图下半被切掉** | 不用于工程图 |
| rembg 默认模型 RMBG-2.0 | **CC BY-NC（禁商用）**，且 12GB 机器直接 OOM | **刻意避开** |
| **BiRefNet（MIT）** | 边缘干净、正交视图稳定、可商用 | **默认后端** |

纯模型输出仍会在**与背景同色的边缘**（冷色反光、蓝色光晕）留一圈脏边，所以模块在模型 alpha 之后加了一步精修。

## 处理流程

```
原图 → rembg(BiRefNet) → alpha
     → drop_tiny_blobs   去掉零星噪点
     → fill_holes        填掉内部小透洞（模型在低对比区漏检）
     → refine_alpha      估计背景色 → 压掉「贴背景色却 alpha 很高」的边缘 → 反解前景色
     → close_gaps        补轮廓上 1–2px 的缺口
     → smooth_alpha      轻微高斯，压锯齿
     → composite         白底 / 透明 / 任意底色 + 尺寸规整
```

`refine_alpha` 不写死任何颜色假设：背景色由 alpha≈0 的像素中位数估计，因此蓝底、白底、绿幕都能自适应。
精修只作用在 alpha 边缘带（带宽 = 短边 × `edge_ratio`，默认 3%，最少 2px），不会误伤内部同色部件。

## 命令行

```powershell
# 单张：白底 + 统一 768x432
python src/image_cutout.py --input hd/03_rear.jpg --out cut --bg white --size 768x432

# 整目录批量 + 一览图
python src/image_cutout.py --input hd --out cut --bg white --size 768x432 --sheet cut/sheet.jpg

# 透明 PNG（同时保留 *_alpha.png）
python src/image_cutout.py --input ship.jpg --out cut --bg transparent

# 自定义底色 / 关掉精修 / 提速
python src/image_cutout.py --input ship.jpg --bg "#0B1220"
python src/image_cutout.py --input ship.jpg --no-refine
python src/image_cutout.py --input ship.jpg --infer-max-side 1280

# 看看本机已有哪些权重
python src/image_cutout.py --list-models
```

## Python API

```python
from image_cutout import cutout_file, cutout_paths, remove_background, composite

cutout_file("ship.jpg", "out/", background="white", size=(768, 432))

# 批量（单张失败不影响其余，返回项里带 error 字段）
items = cutout_paths(paths, "out/", model="birefnet-general", background="white")

# 只要 RGBA，自己决定怎么合成
rgba = remove_background(img, model="birefnet-general", infer_max_side=1280)
rgba.save("ship.png")
```

常用参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `model` | `birefnet-general` | 可切 `isnet-general-use` / `u2net` / `u2netp` / `silueta` / `birefnet-portrait` |
| `background` | `white` | `white` / `transparent` / `#RRGGBB` / `"12,34,56"` |
| `size` | 原尺寸 | 给定时按等比装进画布并居中，`margin` 控制留白 |
| `infer_max_side` | 不限制 | 推理前把长边压到该值，再放大 alpha（明显提速，质量几乎无损） |
| `refine` | `True` | 边缘精修；关掉可对比「纯模型输出」 |
| `fill_holes_ratio` | `0.0025` | 填充内部小洞的面积占比上限，0 关闭 |
| `save_alpha` | `True` | 额外保存 `*_alpha.png` 透明图 |

## 权重与许可

- 权重目录：`~/.u2net`（可用 `U2NET_HOME` 覆盖）。首次使用某个模型会自动下载。
  - `birefnet-general.onnx` ≈ 928 MB（MIT，可商用）
  - `isnet-general-use.onnx` ≈ 170 MB（Apache-2.0）
- rembg 默认模型 **RMBG-2.0 是 CC BY-NC**，本模块不把它设为默认；如需商用务必确认许可。
- 本机实测速度：246×122 裁切图约 22s/张（CPU）；1920 长边建议配 `--infer-max-side 1280`。

## 已知局限

- 主体与背景**同色**（例如白船拍在白背景上）时，精修可能把主体边缘也压掉一点；把 `--no-refine` 或
  `refine_alpha(..., spill_tol=20)` 调小可以缓解。
- 半透明物体（玻璃、尾焰）的 alpha 是模型估出来的，不是真值，边缘可能有轻微色偏。
- 极小图（短边 < 64px）alpha 抖动明显，建议先放大再抠。

## 相关

- 批量生成参考图/首帧的用法见 [`video_chainer.md`](video_chainer.md)；
- 单测：`tests/test_image_cutout.py`（纯函数用合成图验证；模型推理由 `TOOLBOX_CUTOUT_E2E=1` 控制）。
- 第三方许可登记见 [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。
