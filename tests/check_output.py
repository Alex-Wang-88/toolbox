import os

OUTPUT_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")

print("=" * 60)
print("        检查生成的内容")
print("=" * 60)

# 检查音频文件
audio_folder = os.path.join(OUTPUT_FOLDER, "audio")
audio_files = sorted([f for f in os.listdir(audio_folder) if f.endswith('.mp3')], key=lambda x: int(x.split('.')[0]))

print(f"\n音频文件数量: {len(audio_files)}")
for f in audio_files:
    print(f"  - {f}")

# 检查字幕文件
subtitle_folder = os.path.join(OUTPUT_FOLDER, "subtitle")
subtitle_files = [f for f in os.listdir(subtitle_folder) if f.endswith('.srt')]

print(f"\n字幕文件数量: {len(subtitle_files)}")
for f in subtitle_files:
    print(f"  - {f}")
    srt_path = os.path.join(subtitle_folder, f)
    with open(srt_path, 'r', encoding='utf-8') as file:
        content = file.read()
        lines = content.split('\n')
        print(f"    内容预览:")
        for line in lines[:20]:
            if line.strip() and not line.strip().isdigit() and '-->' not in line:
                print(f"      {line}")
                if len(line) > 50:
                    break

print(f"\n{'=' * 60}")
