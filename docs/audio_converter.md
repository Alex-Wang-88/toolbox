# 通用音频转换

入口：`http://127.0.0.1:5000/audio-converter`。旧 `/ncm-converter` 地址继续可用。

上传音频后自动按文件内容识别格式，只需选择目标格式。支持混合批量输入，不依赖扩展名判断实际编码。AI 唱歌仍可直接导入 NCM；其他音频可在本页先转为 WAV。

| 输出 | 默认编码 |
| --- | --- |
| WAV | PCM；保留输入采样率及可保留的 PCM 位深 |
| MP3 | 320 kbps，44.1kHz，双声道 |
| FLAC | 无损压缩，保留输入采样率及 24 位整型音频位深 |
| M4A | AAC 192 kbps，44.1kHz，双声道 |
| AAC | ADTS AAC 192 kbps，44.1kHz，双声道 |
| OGG | Vorbis 192 kbps，44.1kHz，双声道 |
| Opus | 160 kbps，48kHz，双声道 |

输入覆盖这些格式及 WMA、AIFF、APE、AMR、CAF、NCM 等常见本地音频。实际解码能力取决于本机 FFmpeg。浮点音频转 FLAC 会转换为整数采样；将 MP3 等有损来源转为无损格式不会恢复已损失的音质。

## 使用

1. 选择或拖入文件，输入格式自动识别。
2. 选择输出格式，页面显示用途和默认质量说明。
3. 确认并提交。任务进入全站队列，完成后可试听、单个下载或 ZIP 打包下载。

一次最多 20 个文件，单文件 500MB、总大小 1GB。损坏或不支持的文件单独报错，其余文件继续转换。同名结果自动编号。浏览器不支持试听某种格式时，可下载后用本地播放器播放。

转换文件保存在当前输出目录下的 `audio_converter/<task_id>/`。切页或刷新不影响队列；服务重启后的队列恢复规则与其他功能一致。

## 实现与接口

- `src/audio_converter.py`：内容识别、转码、输出校验、原子写入及编码器能力检测。
- `src/ncm_converter.py`：复用原有本地解码器；通用转换器直接读取其解密后的原始音频，避免先转 WAV 再转目标格式。
- `GET /api/audio-converter/status`：可用输入/输出类型、每种输出的就绪状态、NCM 独立依赖状态与上传上限。
- `POST /api/audio-converter/convert`：multipart 字段 `files`、`format`、`consent`，返回 `202 + task_id`。
- 使用已有任务状态、取消、单文件流式下载和 ZIP 接口。

普通音频只需 FFmpeg/FFprobe；NCM 另需 `cryptography`，不会因 NCM 依赖缺失而阻止普通音频转换。编码器不可用的选项在页面中禁用。子进程无窗口、BelowNormal 优先级，最多 4 个 CPU 线程。

## 验证（2026-11-10）

- 真实 FFmpeg 验证七种格式输出与 7×7 共 49 组互转，核对实际音轨编码与时长。
- 覆盖扩展名错误、按 NCM 文件头识别、24 位 PCM 保留、无 NCM 依赖时普通转换可用、超时清理，以及混合批次部分失败、重名编号、下载/ZIP/Range 响应。
- 在浏览器真实提交两段混合命名音频，完成 MP3 转换、单文件与 ZIP 下载、队列结果核对。
- 受影响页面在 1440px、768px、375px 和深浅主题下，36 份状态快照、424 次控件测量均无尺寸、越界、面板对齐或脚本异常。
- 全套回归 157 项通过、2 项跳过；随后新增的取消解码及 MIME 检查单独验证通过。

本机证据：`output/verification/general-audio-converter/`，测试日志 `.tmp/audio-converter-regression.log`。
