# 测试流程

这份流程用于验证“图片上传 -> 话术生成 -> TTS -> 字幕 -> 视频下载”的主链路。

## 1. 环境检查

```powershell
python -m pip install -r config/requirements.txt
python -m py_compile src/toolbox.py src/web_server.py src/app_launcher.py src/document_converter.py src/hardware_profile.py
ffmpeg -version
ffprobe -version
```

预期结果：

- Python 依赖安装成功。
- `py_compile` 没有输出错误。
- `ffmpeg` 和 `ffprobe` 都能打印版本信息。
- 如果要测试 PPT/PPTX 转图片，本机需要安装 PowerPoint 或 LibreOffice。

如果 `ffmpeg` 或 `ffprobe` 不可用，先安装：

```powershell
winget install ffmpeg
```

## 2. API 配置

正式生成视频前，设置 API Key：

```powershell
$env:TOOLBOX_API_KEY="你的 API Key"
$env:TOOLBOX_API_URL="你的 API URL"
```

如果不设置 `TOOLBOX_API_KEY`，程序会走兜底话术，适合验证 TTS 和合成链路，但不会生成真实 AI 讲解内容。

## 3. 启动服务

```powershell
python src/web_server.py
```

预期结果：

- 终端显示访问地址 `http://localhost:5000`。
- 浏览器打开页面后能看到上传区域。

也可以双击 `config/start.bat`，它会先检查 Python 依赖和 FFmpeg。

## 4. 硬件检测与估时测试

```powershell
@'
import sys
sys.path.insert(0, "src")
from hardware_profile import detect_hardware, estimate_label, estimate_seconds
profile = detect_hardware(force=True)
print(profile)
print(estimate_label(estimate_seconds(4, "on", profile)))
print(estimate_label(estimate_seconds(4, "all", profile)))
'@ | python -
```

预期结果：

- 能识别 CPU 线程数、内存、磁盘空间和 FFmpeg 状态。
- 能输出 4 张素材在“开”和“全部”字幕模式下的大致耗时。

## 5. 上传素材测试

1. 打开 `http://localhost:5000`。
2. 选择 2 到 3 张 JPG/PNG 图片。
3. 检查页面预览区。

预期结果：

- 每张图片出现预览。
- 图片编号按选择顺序显示。
- 数量显示正确，预计时间随素材数量和字幕模式变化。

## 6. PDF/PPT 转图片测试

1. 选择一个 2 到 3 页的 PDF。
2. 选择一个 2 到 3 页的 PPT 或 PPTX。
3. 选择一个 `.docx` Word 文档。
3. 点击生成前，观察日志和上传阶段。

预期结果：

- PDF 会被拆成对应页数的 PNG 画面。
- PPT/PPTX 会通过 PowerPoint 或 LibreOffice 拆成对应页数的 PNG 画面。
- Word 会优先转 PDF 后拆图；如果 `.docx` 无法调用 Word/LibreOffice，会用文本排版兜底转成图片。
- 如果本机没有 PowerPoint 或 LibreOffice，页面应显示明确错误，不应卡死。

## 7. 分批与大任务确认测试

1. 选择一个 11 页的 PDF/PPT/PPTX。
2. 选择一个超过 50 页的 PDF/PPT/PPTX。

预期结果：

- 11 页文件会完整拆成 11 张画面。
- 生成 AI 文案时，每 10 张画面请求一次 API。
- 超过 50 张画面时，页面会弹出确认提示；确认后继续生成，取消则停止。

## 8. 生成视频测试

1. 选择字幕模式“开”，点击“生成视频”。
2. 观察日志和进度条。
3. 等待状态变为完成。
4. 点击“下载视频”。

预期结果：

- 后端状态按阶段更新：上传图片、生成话术、生成语音、生成字幕、合成视频。
- 完成后 `output/video/` 中出现带 `_subtitles` 后缀的 `.mp4` 文件。
- 下载按钮指向 `/api/download/<task_id>/subtitles`。
- 下载的视频有画面、音频和字幕。

## 9. 字幕模式测试

1. 字幕选择“关”，生成一次。
2. 字幕选择“全部”，生成一次。

预期结果：

- “关”只生成无字幕视频，文件名带 `_plain`。
- “全部”只请求一次 AI 文案和配音，然后依次合成带字幕与无字幕两个视频。
- 页面显示两个下载按钮。

## 10. 取消任务测试

1. 选择多张图片。
2. 点击“生成视频”。
3. 在生成过程中点击“取消生成”。

预期结果：

- 前端日志显示用户取消。
- 后端任务状态变为 `cancelled` 或在当前耗时步骤结束后停止进入下一阶段。
- 不应把已取消任务标记为 `completed`。

说明：当前取消是阶段间取消。正在进行中的网络请求、TTS 或视频编码不能被立即中断，但下一阶段开始前会停止。

## 11. 多任务输出隔离测试

1. 连续生成两次视频。
2. 检查 `output/video/`。

预期结果：

- 两次任务生成不同文件名的 `.mp4`。
- 第二次生成不会覆盖第一次生成的文件。

## 12. EXE 冒烟测试

```powershell
python -m PyInstaller --noconfirm --clean --windowed --name "TOOLBOX" --add-data "static/index.html;static" --hidden-import toolbox --hidden-import hardware_profile --hidden-import document_converter --hidden-import edge_tts --hidden-import pysrt --hidden-import requests --hidden-import win32com --hidden-import win32com.client --collect-all moviepy --collect-all imageio --collect-all imageio_ffmpeg --collect-all PIL --collect-all fitz --collect-all docx --collect-all lxml src/app_launcher.py
```

或者直接用 spec 文件：

```powershell
python -m PyInstaller --noconfirm --clean config/TOOLBOX.spec
```

预期结果：

- `dist/TOOLBOX/TOOLBOX.exe` 可以启动。
- 不弹出控制台窗口。
- 页面能打开，`/api/hardware` 和 `/api/estimate` 能返回结果。
- 打包后的 exe 可以上传 PDF、PPTX、DOCX 并转换出正确画面数。
- 完成后页面显示“打开视频”和“打开输出文件夹”，不是下载链接。

## 13. 辅助检查脚本

生成完成后可运行：

```powershell
python check_output.py
python check_audio.py
python check_video.py
```

预期结果：

- `check_output.py` 能列出音频和字幕。
- `check_audio.py` 能显示每段音频时长。
- `check_video.py` 能显示视频时长、分辨率、帧率和音频信息。
