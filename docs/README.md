# TOOLBOX

一个基于 Web 的素材转讲解视频工具，支持图片与文档输入，自动生成带讲解和字幕的视频。

## 功能特点

- 🖼️ **拖拽上传**：支持拖拽或点击选择图片
- 📸 **图片预览**：实时预览上传的图片
- 🎯 **图片排序**：按上传顺序自动编号
- 🗑️ **图片管理**：支持删除单张或清空所有图片
- 📊 **进度显示**：实时显示生成进度
- 📝 **日志输出**：详细的操作日志
- ⏹️ **任务控制**：支持取消正在进行的任务
- 📥 **一键下载**：生成完成后直接下载视频

## 文件结构

```
图片转视频/
├── src/                         # 源代码
│   ├── web_server.py           # Web 服务器（Flask）
│   ├── app_launcher.py         # 桌面启动器
│   ├── TOOLBOX.py       # 视频生成核心模块
│   ├── TOOLBOX_web.py   # 另一套 Web 入口（简易版）
│   ├── document_converter.py   # 文档转图片
│   ├── hardware_profile.py     # 硬件检测
│   └── enterprise_solution_to_video.py  # 企业方案入口
├── static/                     # 前端资源
│   └── index.html             # 前端页面
├── config/                     # 配置文件
│   ├── .env.example           # 环境变量示例
│   ├── requirements.txt       # Python 依赖清单
│   ├── TOOLBOX.spec              # PyInstaller 打包配置
│   └── start.bat              # Windows 启动脚本
├── docs/                       # 文档
│   ├── README.md
│   ├── TESTING.md
│   └── plan.md
├── tests/                      # 测试脚本
├── output/                     # 运行时生成（已 gitignore）
├── temp_uploads/               # 运行时生成（已 gitignore）
└── .env                        # 运行时生成（已 gitignore）
```

## 安装依赖

```bash
# 安装Python依赖
pip install -r config/requirements.txt

# 安装FFmpeg（如果尚未安装）
winget install ffmpeg   # Windows
# brew install ffmpeg   # macOS
```

## 配置

正式生成 AI 讲解前，需要设置 API Key：

```powershell
$env:TOOLBOX_API_KEY="your_api_key"
$env:TOOLBOX_API_URL="your_api_url"
```

也可以参考 `config/.env.example` 管理本地配置。未设置 `TOOLBOX_API_KEY` 时，程序会使用兜底话术，便于测试语音和视频合成流程。

## 使用方法

### 1. 启动服务

双击运行 `config/start.bat` 或在命令行执行：

```bash
python src/web_server.py
```

服务启动后，访问：`http://localhost:5000`

### 2. 上传图片

- **方法1**：点击上传区域，选择图片文件
- **方法2**：直接拖拽图片到上传区域

支持的图片格式：JPG、PNG、GIF、BMP

### 3. 管理图片

- 查看图片预览和编号
- 点击图片右上角的 `×` 按钮删除单张图片
- 点击"清空图片"按钮删除所有图片

### 4. 生成视频

1. 确认图片顺序正确
2. 点击"开始生成视频"按钮
3. 等待生成完成（可查看实时进度和日志）
4. 生成完成后点击"下载视频"

### 5. 取消生成

如果需要取消正在进行的任务，点击"取消生成"按钮。

## API接口

### 上传图片
```
POST /api/upload
Content-Type: multipart/form-data

Body: files (multiple)
```

### 开始生成视频
```
POST /api/generate
Content-Type: application/json

Body: {
  "files": ["path1", "path2", ...]
}
```

### 查询任务状态
```
GET /api/status/{task_id}
```

### 取消任务
```
POST /api/cancel/{task_id}
```

### 清理临时文件
```
POST /api/cleanup
```

## 技术栈

- **前端**：HTML5, CSS3, JavaScript (原生)
- **后端**：Python, Flask
- **视频生成**：MoviePy, FFmpeg
- **语音合成**：edge-tts
- **图片上传**：Litterbox临时图床
- **AI话术**：积墨AI API

## 注意事项

1. **图片命名**：建议按顺序命名图片（如 1.jpg, 2.jpg, 3.jpg）
2. **图片数量**：单批次最多处理9张图片，超过会自动分批
3. **生成时间**：生成时间取决于图片数量和AI响应速度
4. **临时文件**：上传的图片会保存到 `temp_uploads` 文件夹，可手动清理
5. **输出文件**：Web任务生成的视频保存在 `output/video/{task_id}.mp4`

## 常见问题

### Q: 上传图片后没有显示？
A: 检查图片格式是否支持（JPG、PNG、GIF、BMP）

### Q: 生成视频失败？
A: 查看日志输出，检查：
- API Key是否配置正确
- 网络连接是否正常
- FFmpeg是否正确安装

### Q: 如何修改API配置？
A: 通过环境变量配置：
```powershell
$env:TOOLBOX_API_KEY="your_api_key"
$env:TOOLBOX_API_URL="your_api_url"
```

### Q: 如何修改视频分辨率？
A: 编辑 `TOOLBOX.py` 文件，修改：
```python
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
```

## 许可证

MIT License

## 企业方案到视频（命令行）

原图片转视频工具保持不变。新增的独立编排入口接收一个公开的数字化方案
HTTP 链接，调用方案智能体生成 Word 文档，再复用原有文档转图片和视频能力：

```powershell
$env:SOLUTION_AGENT_API_KEY="your_solution_agent_api_key"
python enterprise_solution_to_video.py "https://example.com/source-plan.pdf"
```

智能体默认等待 360 秒并最多调用两次。每次运行的文档、页面图片、响应记录和
视频都保存在 `solution_video_runs/<运行时间>/`。

## 更新日志

### v1.0.0 (2026-05-08)
- 初始版本发布
- 支持拖拽上传图片
- 支持自动生成讲解视频
- 支持实时进度显示
- 支持任务取消功能
