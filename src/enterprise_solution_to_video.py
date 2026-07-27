#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate an enterprise solution document and turn it into a narrated video."""

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests

import document_converter


DEFAULT_AGENT_URL = (
    "https://api.yunbloom.cn/v2/chat/completions/share"
    "?shareId=vbP1tGw6l4JXWtRO"
)
DEFAULT_TIMEOUT_SECONDS = 360
DEFAULT_RETRIES = 2


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(extract_text(item) for item in value)
    if not isinstance(value, dict):
        return ""

    preferred_keys = ("content", "text", "result", "output", "answer", "message")
    parts = [extract_text(value[key]) for key in preferred_keys if key in value]
    if any(parts):
        return "".join(parts)
    return "".join(extract_text(item) for item in value.values())


def parse_agent_response(response: requests.Response) -> str:
    content_type = response.headers.get("Content-Type", "")
    if "text/event-stream" not in content_type:
        try:
            return extract_text(response.json())
        except ValueError:
            return response.text

    chunks = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunks.append(extract_text(json.loads(payload)))
        except json.JSONDecodeError:
            chunks.append(payload)
    return "".join(chunks)


def find_document_url(raw_text: str) -> str:
    candidates = [raw_text.strip()]
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict) and isinstance(parsed.get("url"), str):
            candidates.insert(0, parsed["url"])
        candidates.append(extract_text(parsed))
    except json.JSONDecodeError:
        pass

    for candidate in candidates:
        match = re.search(r"https?://[^\s\"'<>\\]+", candidate)
        if match:
            return match.group(0).rstrip(".,;:!?)]}")
    raise RuntimeError(f"智能体响应中没有找到文档 URL。响应摘要: {raw_text[:500]}")


def call_solution_agent(
    source_url: str,
    api_url: str,
    api_key: str,
    timeout_seconds: int,
    retries: int,
) -> tuple[str, str]:
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
    }
    request_data = {
        "messages": [{
            "role": "user",
            "content": [{"type": "text", "text": source_url}],
        }],
        "sessionId": str(uuid.uuid4()),
        "source": "api",
        "extra": {},
    }

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            print(f"[1/4] 调用方案智能体（第 {attempt}/{retries} 次，超时 {timeout_seconds}s）...")
            response = requests.post(
                api_url,
                headers=headers,
                json=request_data,
                stream=True,
                timeout=(30, timeout_seconds),
            )
            response.raise_for_status()
            raw_text = parse_agent_response(response).strip()
            return find_document_url(raw_text), raw_text
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            if attempt < retries:
                wait_seconds = 5 * attempt
                print(f"[WARN] 智能体调用失败，{wait_seconds}s 后重试: {exc}")
                time.sleep(wait_seconds)
    raise RuntimeError(f"智能体调用失败: {last_error}") from last_error


def filename_from_url(url: str, fallback: str = "generated_solution.docx") -> str:
    name = os.path.basename(unquote(urlparse(url).path))
    if not name or "." not in name:
        return fallback
    return re.sub(r'[<>:"/\\|?*]+', "_", name)


def download_file(url: str, destination: Path, timeout_seconds: int) -> None:
    print(f"[2/4] 下载智能体生成的方案: {url}")
    with requests.get(url, stream=True, timeout=(30, timeout_seconds)) as response:
        response.raise_for_status()
        with destination.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)
    if destination.stat().st_size == 0:
        raise RuntimeError("下载到的方案文档为空")


def convert_document(document_path: Path, image_dir: Path) -> list[str]:
    image_dir.mkdir(parents=True, exist_ok=True)
    extension = document_converter.extension_of(document_path.name)
    print(f"[3/4] 将 {extension.upper()} 文档转换为视频页面...")
    if extension in document_converter.WORD_EXTENSIONS:
        return document_converter.convert_word_to_images(str(document_path), str(image_dir))
    if extension in document_converter.PDF_EXTENSIONS:
        return document_converter.convert_pdf_to_images(str(document_path), str(image_dir))
    if extension in document_converter.PRESENTATION_EXTENSIONS:
        return document_converter.convert_presentation_to_images(str(document_path), str(image_dir))
    raise RuntimeError(f"智能体返回了不支持的文档格式: {extension or '未知'}")


def generate_video(image_paths: list[str], run_dir: Path) -> str:
    print(f"[4/4] 使用默认参数生成视频，共 {len(image_paths)} 页...")
    import TOOLBOX as pipeline

    output_dir = run_dir / "output"
    pipeline.OUTPUT_FOLDER = str(output_dir)
    pipeline.init_output_folders()

    image_info_list = pipeline.batch_upload_images(image_paths)
    if len(image_info_list) != len(image_paths):
        raise RuntimeError(
            f"页面上传不完整: 成功 {len(image_info_list)}/{len(image_paths)}"
        )
    for index, image_info in enumerate(image_info_list):
        image_info.update({
            "name": os.path.basename(image_info["file_path"]),
            "order": index + 1,
            "importance": "normal",
            "speech_rate": "normal",
            "transition": "none",
        })

    speech_result = pipeline.generate_full_speech_result(image_info_list)
    audio_info_list = pipeline.batch_generate_tts(
        speech_result["speech"], image_info_list
    )
    srt_path = pipeline.generate_srt_subtitle(audio_info_list)
    output_name = speech_result.get("video_filename") or "企业数字化解决方案"
    return pipeline.generate_video(
        image_info_list,
        audio_info_list,
        srt_path,
        output_filename=output_name,
        include_subtitles=True,
    )


def run(source_url: str, work_root: Path, api_url: str, api_key: str,
        timeout_seconds: int, retries: int,
        generated_document_url: str | None = None) -> Path:
    run_dir = work_root / time.strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)

    if generated_document_url:
        document_url = generated_document_url
        raw_response = json.dumps(
            {"url": document_url}, ensure_ascii=False
        )
        print("[1/4] 使用已有的智能体文档 URL，跳过智能体调用。")
    else:
        document_url, raw_response = call_solution_agent(
            source_url, api_url, api_key, timeout_seconds, retries
        )
    (run_dir / "agent_response.txt").write_text(raw_response, encoding="utf-8")

    document_path = run_dir / filename_from_url(document_url)
    download_file(document_url, document_path, timeout_seconds)
    image_paths = convert_document(document_path, run_dir / "pages")
    if not image_paths:
        raise RuntimeError("文档没有转换出任何页面")

    video_path = Path(generate_video(image_paths, run_dir)).resolve()
    result = {
        "source_url": source_url,
        "generated_document_url": document_url,
        "generated_document": str(document_path.resolve()),
        "page_count": len(image_paths),
        "video": str(video_path),
    }
    (run_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return video_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="企业数字化方案链接 -> 专属方案文档 -> 讲解视频"
    )
    parser.add_argument("source_url", help="原始数字化方案的 HTTP/HTTPS 链接")
    parser.add_argument(
        "--work-root", default="solution_video_runs", help="每次运行的工作目录"
    )
    parser.add_argument(
        "--agent-url",
        default=os.getenv("SOLUTION_AGENT_API_URL", DEFAULT_AGENT_URL),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("SOLUTION_AGENT_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)),
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=int(os.getenv("SOLUTION_AGENT_RETRIES", DEFAULT_RETRIES)),
    )
    parser.add_argument(
        "--generated-document-url",
        help="已有智能体输出 URL；用于续跑或测试，设置后跳过智能体调用",
    )
    return parser


def main() -> int:
    # .env 在项目根目录（src/ 的上一级）
    from paths import PROJECT_ROOT
    _project_root = Path(PROJECT_ROOT)
    load_env_file(_project_root / ".env")
    args = build_parser().parse_args()
    api_key = os.getenv("SOLUTION_AGENT_API_KEY", "")
    if not api_key:
        print("[ERROR] 缺少环境变量 SOLUTION_AGENT_API_KEY", file=sys.stderr)
        return 2

    try:
        video_path = run(
            args.source_url,
            Path(args.work_root),
            args.agent_url,
            api_key,
            args.timeout,
            max(1, args.retries),
            args.generated_document_url,
        )
    except Exception as exc:
        print(f"[ERROR] 流程失败: {exc}", file=sys.stderr)
        return 1

    print(f"[OK] 视频生成完成: {video_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
