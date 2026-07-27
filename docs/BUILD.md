# 打包构建指南

支持 Windows (exe) 和 macOS (.app) 两种平台。

## 环境准备

```bash
pip install -r config/requirements.txt
pip install pyinstaller  # Windows 打包
pip install py2app       # macOS 打包
```

FFmpeg 需已安装（`winget install ffmpeg` 或 `brew install ffmpeg`）。

---

## Windows 打包 (PyInstaller)

### 方式一：使用 spec 文件（推荐）

```powershell
python -m PyInstaller --noconfirm --clean config/TOOLBOX.spec
```

产物：`dist/TOOLBOX/TOOLBOX.exe`

### 方式二：命令行直接打包

```powershell
python -m PyInstaller --noconfirm --clean --windowed `
  --name "TOOLBOX" `
  --add-data "static/index.html;static" `
  --hidden-import TOOLBOX `
  --hidden-import hardware_profile `
  --hidden-import document_converter `
  --hidden-import edge_tts `
  --hidden-import pysrt `
  --hidden-import requests `
  --hidden-import win32com `
  --hidden-import win32com.client `
  --collect-all moviepy `
  --collect-all imageio `
  --collect-all imageio_ffmpeg `
  --collect-all PIL `
  --collect-all fitz `
  --collect-all docx `
  --collect-all lxml `
  src/app_launcher.py
```

产物：`dist/TOOLBOX/TOOLBOX.exe`

### 打包无依赖版

```powershell
# 1. 先按上面打包出 exe
# 2. 复制 LibreOffice 便携版到 dist/TOOLBOX/LibreOffice/
# 3. 删除没用的大文件，打包成 zip
```

无依赖版必须有：
- `TOOLBOX.exe`
- `LibreOffice/` 文件夹
- 可选：`使用说明_无依赖Windows版.txt`

### Windows 打包注意事项

1. **FFmpeg**：PyInstaller 会自动打包 `imageio_ffmpeg` 内嵌的 FFmpeg，不需要额外处理
2. **字体**：SimHei / 微软雅黑 是 Windows 系统自带，无需打包
3. **路径**：代码中所有路径使用相对路径，`app_launcher.py` 的 `app_dir()` 自动处理打包后的 exe 目录
4. **`.env`**：放在 exe 同目录下，程序启动时自动加载
5. **测试**：打包后双击 exe 测试首页 /api/hardware /api/estimate 接口是否正常

---

## macOS 打包 (py2app)

### 环境准备

```bash
# macOS 上安装依赖（pywin32 在 Mac 上不需要）
pip install -r config/requirements.txt
pip install py2app

# 确保 FFmpeg 已安装
brew install ffmpeg

# 确保 LibreOffice 已安装（用于 PPT/Word 转图片）
brew install --cask libreoffice
```

### 首次打包（生成 setup.py）

```bash
# 在项目根目录执行
py2applet --make-setup src/app_launcher.py
```

这会生成 `setup.py`，然后**手动编辑 setup.py**，添加数据文件和依赖：

```python
"""
Setup script for py2app
"""
from setuptools import setup

APP = ['src/app_launcher.py']
DATA_FILES = [
    ('static', ['static/index.html']),
]
OPTIONS = {
    'argv_emulation': False,
    'packages': [
        'TOOLBOX', 'hardware_profile', 'document_converter',
        'edge_tts', 'pysrt', 'requests',
        'moviepy', 'imageio', 'imageio_ffmpeg',
        'PIL', 'fitz', 'docx', 'lxml',
        'flask', 'flask_cors', 'jinja2', 'werkzeug',
        'numpy', 'asyncio',
    ],
    'includes': [
        'TOOLBOX', 'hardware_profile', 'document_converter',
        'edge_tts', 'pysrt',
    ],
    'excludes': [
        'win32com', 'pywin32',  # macOS 上不需要
    ],
    'resources': [],
    'iconfile': None,  # 可选: 'icon.icns',
    'plist': {
        'CFBundleName': 'TOOLBOX',
        'CFBundleDisplayName': 'TOOLBOX',
        'CFBundleIdentifier': 'com.image-to-video.app',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
    },
    'site_packages': True,
    'alias': False,  # 设为 True 可加速调试打包
}

setup(
    name='TOOLBOX',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
```

### 执行打包

```bash
# 清理旧构建
rm -rf build dist

# 打包（--alias 模式仅用于调试，发布时必须去掉）
python setup.py py2app --alias  # 调试模式，启动快
python setup.py py2app           # 正式发布模式
```

产物：`dist/TOOLBOX.app`

### 使用方式

```bash
# 双击 dist/TOOLBOX.app 启动
# 或在终端运行：
open dist/TOOLBOX.app
```

首次启动时可能会显示安全提示，需要在「系统设置 → 隐私与安全性」中允许运行。

### macOS 打包注意事项

1. **py2app 需要 macOS 机器**，无法跨平台打包
2. **.app 实际上是文件夹**，终端执行的是 `dist/TOOLBOX.app/Contents/MacOS/app_launcher`
3. **嵌入 LibreOffice**：不建议嵌入，Mac 上要求用户通过 brew 自行安装
4. **签名**：如果需要分发，需用 Apple Developer 证书签名：
   ```bash
   codesign --deep --force --verify --verbose --sign "Developer ID Application" dist/TOOLBOX.app
   ```
5. **字体**：PingFang SC / STHeiti 是 macOS 系统自带，无需打包
6. **打包常见问题**：
   - 如果启动时报错找不到模块，检查 `OPTIONS['packages']` 是否包含该模块
   - 确认 `src/` 目录在 `sys.path` 中（`app_launcher.py` 已有相关逻辑）
   - 使用 `--alias` 模式调试时不要点「打包」按钮，直接在终端运行 `app_launcher.py` 即可

---

## 两种平台对比

| 项目 | Windows | macOS |
|------|---------|-------|
| 打包工具 | PyInstaller | py2app |
| 输出格式 | `.exe` | `.app` |
| 跨平台打包 | 可交叉编译(有限) | **必须 macOS 机器** |
| TTS | Edge TTS → SAPI 兜底 | Edge TTS（失败报错） |
| PPT/Word 转图 | PowerPoint COM → LibreOffice 兜底 | LibreOffice |
| 便携版 | 可内嵌 LibreOffice | 不能（需用户自行安装） |

---

## 常见问题

### Q: 打包后 exe 双击没反应？

A: 在终端运行 exe 查看错误日志，或检查同目录下的 `launcher.log`。

### Q: 打包体积太大？

A: PyInstaller 默认会收集大量依赖，尝试：
- 用 `--exclude-module` 排除不需要的模块
- 检查是否有不必要的第三方库被错误包含

### Q: macOS .app 提示已损坏/无法验证？

A: 临时解决：`xattr -cr /Applications/TOOLBOX.app`