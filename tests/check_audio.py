import os
import json
from moviepy.audio.io.AudioFileClip import AudioFileClip

AUDIO_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "audio")

print("=" * 60)
print("        音频文件分析")
print("=" * 60)

audio_files = sorted([f for f in os.listdir(AUDIO_FOLDER) if f.endswith('.mp3')], key=lambda x: int(x.split('.')[0]))

total_duration = 0

for audio_file in audio_files:
    audio_path = os.path.join(AUDIO_FOLDER, audio_file)
    try:
        clip = AudioFileClip(audio_path)
        duration = clip.duration
        text_length = len(open(audio_path.replace('.mp3', '.txt'), 'r', encoding='utf-8').read()) if os.path.exists(audio_path.replace('.mp3', '.txt')) else 0
        
        print(f"\n{audio_file}:")
        print(f"  时长: {duration:.2f} 秒")
        print(f"  文本长度: {text_length} 字")
        
        total_duration += duration
        clip.close()
    except Exception as e:
        print(f"\n{audio_file}: 错误 - {e}")

print(f"\n{'=' * 60}")
print(f"总时长: {total_duration:.2f} 秒 ({total_duration/60:.2f} 分钟)")
print(f"{'=' * 60}")
