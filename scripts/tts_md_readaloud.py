#!/usr/bin/env python
"""Read a markdown file aloud using edge-tts (cloud) and save to MP3."""
import asyncio
import re
import sys
import os

# Ensure src is importable for normalize_special_pronunciation
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import edge_tts

# ---------- Config ----------
VOICE = "zh-CN-YunjianNeural"   # 云健 - 男声·沉稳播音
RATE = "+0%"                    # normal speed
INPUT_MD = r"C:\Users\12992\Documents\qwen-agent\6k4pwijssk\default\image-understanding\图片文字提取结果.md"
OUTPUT_MP3 = r"C:\Users\12992\Desktop\work\code\toolbox\output\家书朗读_云健.mp3"
# -----------------------------


def md_to_plain_text(md: str) -> str:
    """Strip markdown formatting to get clean plain text for TTS."""
    # Remove code blocks
    md = re.sub(r"```.*?```", "", md, flags=re.DOTALL)
    # Remove inline code
    md = md.replace("`", "")
    # Remove images and links, keep text
    md = re.sub(r"!\[.*?\]\(.*?\)", "", md)
    md = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", md)
    # Remove bold/italic markers
    md = md.replace("**", "").replace("__", "")
    md = md.replace("*", "").replace("_", "")
    # Remove heading hashes
    md = re.sub(r"^#{1,6}\s+", "", md, flags=re.MULTILINE)
    # Remove horizontal rules
    md = re.sub(r"^---+$", "", md, flags=re.MULTILINE)
    # Remove list markers at line start
    md = re.sub(r"^\s*[-*+]\s+", "", md, flags=re.MULTILINE)
    md = re.sub(r"^\s*\d+\.\s+", "", md, flags=re.MULTILINE)
    # Remove blockquote markers
    md = re.sub(r"^\s*>\s?", "", md, flags=re.MULTILINE)
    # Collapse 3+ newlines to 2
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


async def main():
    with open(INPUT_MD, "r", encoding="utf-8") as f:
        md_text = f.read()

    plain = md_to_plain_text(md_text)
    print(f"[INFO] 原文 {len(md_text)} 字符 -> 纯文本 {len(plain)} 字符")
    print(f"[INFO] 音色: {VOICE} (云健·男声·沉稳播音)")
    print(f"[INFO] 语速: {RATE}")

    os.makedirs(os.path.dirname(OUTPUT_MP3), exist_ok=True)

    communicate = edge_tts.Communicate(plain, VOICE, rate=RATE)
    total = 0
    with open(OUTPUT_MP3, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
                total += len(chunk["data"])

    if total == 0:
        print("[ERROR] edge-tts 未返回音频数据")
        sys.exit(1)

    size_mb = total / (1024 * 1024)
    print(f"[OK] 生成完毕: {OUTPUT_MP3}  ({size_mb:.2f} MB)")


if __name__ == "__main__":
    asyncio.run(main())
