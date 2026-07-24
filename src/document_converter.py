#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Convert images and documents to images for the video pipeline."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile

from PIL import Image, ImageDraw, ImageFont


IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "bmp", "webp"}
PDF_EXTENSIONS = {"pdf"}
PRESENTATION_EXTENSIONS = {"ppt", "pptx"}
WORD_EXTENSIONS = {"doc", "docx"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS | PRESENTATION_EXTENSIONS | WORD_EXTENSIONS
API_IMAGE_BATCH_LIMIT = 10
LARGE_GENERATION_CONFIRM_COUNT = 50
DOCX_CHARS_PER_PAGE = 900
DOCX_IMAGE_WIDTH = 1920
DOCX_IMAGE_HEIGHT = 1080
OFFICE_AUTOMATION_LOCK = threading.RLock()

# 不可信办公文档解析的轻量防护阈值（个人工具范围；完整沙箱容器为可选项，未启用）
MAX_OFFICE_FILE_BYTES = 200 * 1024 * 1024      # 单文件 200MB 上限
MAX_DOC_PAGES = 500                            # 页数上限（防超大/恶意文档耗尽资源）
MAX_ZIP_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # zip 解压后 2GB 上限（防 zip 炸弹）
MAX_ZIP_RATIO = 100                            # 单条目解压比上限（压缩大小:解压大小）
ZIP_BOMB_MSG = "文档疑似压缩炸弹（解压体积/比例超限），已拒绝解析"


def extension_of(filename):
    return filename.rsplit(".", 1)[1].lower() if "." in filename else ""


def silent_subprocess_kwargs():
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def natural_sort_key(path):
    name = os.path.basename(path)
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def enforce_office_input_limits(file_path):
    """不可信办公文档的轻量防护：文件大小、zip 炸弹（解压体积/比例）检查。

    在解析 PPTX/DOCX/PDF 之前调用。个人工具不启用完整容器沙箱，这里只做
    低成本、高收益的输入面收敛，避免恶意/超大文件耗尽磁盘与内存。
    """
    if not os.path.isfile(file_path):
        return
    size = os.path.getsize(file_path)
    if size > MAX_OFFICE_FILE_BYTES:
        raise RuntimeError(
            f"文档过大（{size >> 20}MB），超过单文件上限 {MAX_OFFICE_FILE_BYTES >> 20}MB，已拒绝解析"
        )

    # PPTX/DOCX 本质是 zip：检查解压后体积与单条目压缩比，防 zip 炸弹
    if extension_of(file_path) in PRESENTATION_EXTENSIONS | WORD_EXTENSIONS and zipfile.is_zipfile(file_path):
        total_uncompressed = 0
        with zipfile.ZipFile(file_path) as archive:
            for info in archive.infolist():
                # 跳过目录条目
                if info.file_size == 0 and info.compress_size == 0:
                    continue
                total_uncompressed += info.file_size
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_ZIP_RATIO:
                        raise RuntimeError(ZIP_BOMB_MSG)
                if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise RuntimeError(ZIP_BOMB_MSG)



def get_doc_font(size=42):
    """跨平台获取文档字体。"""
    for path in _get_chinese_font_paths():
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _get_chinese_font_paths():
    """返回当前平台可用的中文字体路径列表（按优先级排序）。"""
    if os.name == "nt":
        return [
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
        ]
    elif sys.platform == "darwin":
        return [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    else:
        return [
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        ]


def wrap_text_for_image(text, font, max_width):
    lines = []
    current = ""
    scratch = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(scratch)
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        candidate = current + char
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def render_docx_text_pages(text_pages, output_dir):
    font = get_doc_font()
    title_font = get_doc_font(50)
    image_paths = []
    for page_index, text in enumerate(text_pages):
        image = Image.new("RGB", (DOCX_IMAGE_WIDTH, DOCX_IMAGE_HEIGHT), "#f8fbfa")
        draw = ImageDraw.Draw(image)
        draw.rectangle((70, 70, DOCX_IMAGE_WIDTH - 70, DOCX_IMAGE_HEIGHT - 70), fill="white", outline="#d9e4e0", width=2)
        draw.text((110, 95), f"Word 文档 第 {page_index + 1} 页", fill="#1f3a33", font=title_font)
        y = 180
        line_height = 58
        for line in wrap_text_for_image(text, font, DOCX_IMAGE_WIDTH - 220):
            if y + line_height > DOCX_IMAGE_HEIGHT - 105:
                break
            draw.text((110, y), line, fill="#16221e", font=font)
            y += line_height
        image_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.png")
        image.save(image_path)
        image_paths.append(image_path)
    return image_paths


def read_docx_text_pages(file_path):
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError("缺少 Word 文档解析组件 python-docx，请重新安装程序依赖") from exc

    document = Document(file_path)
    text = "\n".join(p.text.strip() for p in document.paragraphs if p.text.strip())
    if not text:
        text = "空白 Word 文档"
    pages = []
    for index in range(0, len(text), DOCX_CHARS_PER_PAGE):
        pages.append(text[index:index + DOCX_CHARS_PER_PAGE])
    return pages or ["空白 Word 文档"]


def convert_pdf_to_images(file_path, output_dir, start_index=0):
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("缺少 PDF 转图片组件 PyMuPDF，请重新安装程序依赖") from exc

    doc = fitz.open(file_path)
    try:
        if doc.page_count > MAX_DOC_PAGES:
            raise RuntimeError(f"PDF 页数过多（{doc.page_count}），超过上限 {MAX_DOC_PAGES}，已拒绝解析")
        image_paths = []
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.png")
            pixmap.save(image_path)
            image_paths.append(image_path)
        return image_paths
    finally:
        doc.close()


def find_soffice():
    """跨平台查找 LibreOffice 的可执行文件。"""
    project_root = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        # Windows: 项目内置的便携版
        os.path.join(project_root, "LibreOffice", "program", "soffice.exe"),
        os.path.join(project_root, "LibreOfficePortable", "App", "libreoffice", "program", "soffice.exe"),
        os.path.join(project_root, "LibreOfficePortable", "LibreOfficePortable.exe"),
        # Windows 系统安装路径
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        # macOS 安装路径
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        # PATH 查找（所有平台）
        shutil.which("soffice"),
        shutil.which("libreoffice"),
    ]
    return next((path for path in candidates if path and os.path.exists(path)), None)


def convert_presentation_with_powerpoint(file_path, output_dir, start_index=0):
    """使用 PowerPoint COM 自动化转换 PPT 为图片。仅 Windows 可用。"""
    if os.name != "nt":
        raise RuntimeError("PowerPoint 自动化仅支持 Windows 平台")

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError("缺少 PowerPoint 自动化组件 pywin32") from exc

    powerpoint = None
    presentation = None
    export_dir = tempfile.mkdtemp(prefix="ppt_export_")
    pythoncom.CoInitialize()
    try:
        with OFFICE_AUTOMATION_LOCK:
            try:
                powerpoint = win32com.client.DispatchEx("PowerPoint.Application")
                powerpoint.DisplayAlerts = 0
                presentation = powerpoint.Presentations.Open(
                    os.path.abspath(file_path),
                    ReadOnly=True,
                    Untitled=False,
                    WithWindow=False,
                )
                presentation.Export(export_dir, "PNG")

                exported = sorted(
                    [
                        os.path.join(export_dir, name)
                        for name in os.listdir(export_dir)
                        if extension_of(name) in IMAGE_EXTENSIONS
                    ],
                    key=natural_sort_key,
                )
                if not exported:
                    raise RuntimeError("PowerPoint 未导出任何幻灯片图片")

                image_paths = []
                for path in exported:
                    image_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.png")
                    shutil.copyfile(path, image_path)
                    image_paths.append(image_path)
                return image_paths
            finally:
                if presentation is not None:
                    presentation.Close()
                if powerpoint is not None:
                    powerpoint.Quit()
    finally:
        pythoncom.CoUninitialize()
        shutil.rmtree(export_dir, ignore_errors=True)


def convert_presentation_with_soffice(file_path, output_dir, start_index=0):
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("未检测到 PowerPoint 或 LibreOffice，无法将 PPT/PPTX 转为图片")

    pdf_dir = tempfile.mkdtemp(prefix="ppt_pdf_")
    try:
        result = subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                pdf_dir,
                os.path.abspath(file_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            **silent_subprocess_kwargs(),
        )
        if result.returncode != 0:
            raise RuntimeError(f"LibreOffice 转换 PPT 失败：{result.stderr or result.stdout}")

        pdf_files = [os.path.join(pdf_dir, name) for name in os.listdir(pdf_dir) if extension_of(name) == "pdf"]
        if not pdf_files:
            raise RuntimeError("LibreOffice 未生成 PDF 文件")

        return convert_pdf_to_images(pdf_files[0], output_dir, start_index)
    finally:
        shutil.rmtree(pdf_dir, ignore_errors=True)


def convert_presentation_to_images(file_path, output_dir, start_index=0):
    """跨平台 PPT 转图片：Windows 先试 PowerPoint，其他平台直接用 LibreOffice。"""
    if os.name == "nt":
        try:
            return convert_presentation_with_powerpoint(file_path, output_dir, start_index)
        except Exception as first_error:
            try:
                return convert_presentation_with_soffice(file_path, output_dir, start_index)
            except Exception as second_error:
                raise RuntimeError(f"PPT 转图片失败：{first_error}；备用方案失败：{second_error}") from second_error
    else:
        # macOS/Linux: 只用 LibreOffice
        return convert_presentation_with_soffice(file_path, output_dir, start_index)


def convert_word_with_word(file_path, output_dir):
    """使用 Word COM 自动化转换 Word 为 PDF。仅 Windows 可用。"""
    if os.name != "nt":
        raise RuntimeError("Word 自动化仅支持 Windows 平台")

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError("缺少 Word 自动化组件 pywin32") from exc

    word = None
    document = None
    pdf_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.pdf")
    pythoncom.CoInitialize()
    try:
        # DispatchEx creates an isolated Word process and avoids reusing a
        # user's interactive Word session.
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        word.ScreenUpdating = False
        word.AutomationSecurity = 3
        document = word.Documents.Open(
            FileName=os.path.abspath(file_path),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            Revert=False,
            NoEncodingDialog=True,
            OpenAndRepair=True,
        )
        document.Repaginate()
        document.ExportAsFixedFormat(os.path.abspath(pdf_path), 17)
        if not os.path.isfile(pdf_path) or os.path.getsize(pdf_path) == 0:
            raise RuntimeError("Microsoft Word 未生成有效 PDF 文件")
        return pdf_path
    finally:
        if document is not None:
            document.Close(False)
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()


def convert_office_with_soffice(file_path, output_dir, label):
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError(f"未检测到 Microsoft Office 或 LibreOffice，无法将 {label} 转为图片")

    result = subprocess.run(
        [
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            output_dir,
            os.path.abspath(file_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
        **silent_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"LibreOffice 转换 {label} 失败：{result.stderr or result.stdout}")

    pdf_files = [os.path.join(output_dir, name) for name in os.listdir(output_dir) if extension_of(name) == "pdf"]
    if not pdf_files:
        raise RuntimeError(f"LibreOffice 未生成 {label} PDF 文件")
    return max(pdf_files, key=os.path.getmtime)


def convert_word_to_images(file_path, output_dir, start_index=0):
    pdf_dir = tempfile.mkdtemp(prefix="word_pdf_")
    try:
        if os.name == "nt":
            try:
                pdf_path = convert_word_with_word(file_path, pdf_dir)
            except Exception as first_error:
                try:
                    pdf_path = convert_office_with_soffice(file_path, pdf_dir, "Word")
                except Exception as second_error:
                    raise RuntimeError(
                        "Word 转 PDF 失败，已停止处理以避免破坏原文档分页。"
                        f"Microsoft Word 错误：{first_error}；LibreOffice 错误：{second_error}"
                    ) from second_error
        else:
            # macOS/Linux: 只用 LibreOffice
            pdf_path = convert_office_with_soffice(file_path, pdf_dir, "Word")

        return convert_pdf_to_images(pdf_path, output_dir, start_index)
    finally:
        shutil.rmtree(pdf_dir, ignore_errors=True)


def count_pdf_pages(file_path):
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("缺少 PDF 识别组件 PyMuPDF，请重新安装程序依赖") from exc
    doc = fitz.open(file_path)
    try:
        return doc.page_count
    finally:
        doc.close()


def count_presentation_slides(file_path):
    if extension_of(file_path) == "pptx" and zipfile.is_zipfile(file_path):
        with zipfile.ZipFile(file_path) as archive:
            return sum(
                1
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            )

    # Windows: 尝试用 PowerPoint COM 自动化计数
    if os.name == "nt":
        powerpoint = None
        presentation = None
        pythoncom_initialized = False
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            pythoncom_initialized = True
            with OFFICE_AUTOMATION_LOCK:
                powerpoint = win32com.client.DispatchEx("PowerPoint.Application")
                powerpoint.DisplayAlerts = 0
                presentation = powerpoint.Presentations.Open(
                    os.path.abspath(file_path),
                    ReadOnly=True,
                    Untitled=False,
                    WithWindow=False,
                )
                return int(presentation.Slides.Count)
        except Exception:
            pass  # 落到下面的 LibreOffice 兼底方案
        finally:
            with OFFICE_AUTOMATION_LOCK:
                if presentation is not None:
                    presentation.Close()
                if powerpoint is not None:
                    powerpoint.Quit()
            if pythoncom_initialized:
                pythoncom.CoUninitialize()

    # 所有平台兼容底：用 LibreOffice 转 PDF 后计页数
    pdf_dir = tempfile.mkdtemp(prefix="ppt_count_")
    try:
        pdf_path = convert_office_with_soffice(file_path, pdf_dir, "PPT")
        return count_pdf_pages(pdf_path)
    finally:
        shutil.rmtree(pdf_dir, ignore_errors=True)


def count_word_pages(file_path):
    pdf_dir = tempfile.mkdtemp(prefix="word_count_")
    try:
        if os.name == "nt":
            try:
                pdf_path = convert_word_with_word(file_path, pdf_dir)
            except Exception as first_error:
                try:
                    pdf_path = convert_office_with_soffice(file_path, pdf_dir, "Word")
                except Exception as second_error:
                    raise RuntimeError(
                        "Word 转 PDF 失败，无法可靠统计页数。"
                        f"Microsoft Word 错误：{first_error}；LibreOffice 错误：{second_error}"
                    ) from second_error
        else:
            # macOS/Linux: 只用 LibreOffice
            pdf_path = convert_office_with_soffice(file_path, pdf_dir, "Word")
        return count_pdf_pages(pdf_path)
    finally:
        shutil.rmtree(pdf_dir, ignore_errors=True)


def count_upload_screens(file_storage, output_dir):
    ext = extension_of(file_storage.filename)
    if ext not in SUPPORTED_EXTENSIONS:
        return 0
    if ext in IMAGE_EXTENSIONS:
        return 1

    os.makedirs(output_dir, exist_ok=True)
    source_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.{ext}")
    file_storage.save(source_path)
    try:
        enforce_office_input_limits(source_path)
        page_count = 0
        if ext in PDF_EXTENSIONS:
            page_count = count_pdf_pages(source_path)
        elif ext in PRESENTATION_EXTENSIONS:
            page_count = count_presentation_slides(source_path)
        elif ext in WORD_EXTENSIONS:
            page_count = count_word_pages(source_path)
        if page_count > MAX_DOC_PAGES:
            raise RuntimeError(f"文档页数过多（{page_count}），超过上限 {MAX_DOC_PAGES}，已拒绝解析")
        return page_count
    finally:
        try:
            os.remove(source_path)
        except OSError:
            pass


def save_upload_as_images(file_storage, output_dir, start_index=0):
    ext = extension_of(file_storage.filename)
    if ext not in SUPPORTED_EXTENSIONS:
        return []

    if ext in IMAGE_EXTENSIONS:
        image_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.{ext}")
        file_storage.save(image_path)
        return [image_path]

    source_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.{ext}")
    file_storage.save(source_path)

    enforce_office_input_limits(source_path)

    if ext in PDF_EXTENSIONS:
        return convert_pdf_to_images(source_path, output_dir, start_index)
    if ext in PRESENTATION_EXTENSIONS:
        return convert_presentation_to_images(source_path, output_dir, start_index)
    if ext in WORD_EXTENSIONS:
        return convert_word_to_images(source_path, output_dir, start_index)

    return []
