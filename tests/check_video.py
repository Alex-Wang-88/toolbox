import os
import sys
from moviepy.editor import VideoFileClip

VIDEO_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "video")

if len(sys.argv) > 1:
    OUTPUT_VIDEO = sys.argv[1]
else:
    mp4_files = [
        os.path.join(VIDEO_FOLDER, f)
        for f in os.listdir(VIDEO_FOLDER)
        if f.lower().endswith(".mp4")
    ] if os.path.exists(VIDEO_FOLDER) else []
    OUTPUT_VIDEO = max(mp4_files, key=os.path.getmtime) if mp4_files else os.path.join(VIDEO_FOLDER, "output.mp4")

print("=" * 60)
print("        视频文件分析")
print("=" * 60)

if not os.path.exists(OUTPUT_VIDEO):
    print(f"视频文件不存在: {OUTPUT_VIDEO}")
    exit(1)

try:
    clip = VideoFileClip(OUTPUT_VIDEO)
    
    print(f"\n文件路径: {OUTPUT_VIDEO}")
    print(f"文件大小: {os.path.getsize(OUTPUT_VIDEO) / (1024*1024):.2f} MB")
    print(f"\n视频信息:")
    print(f"  时长: {clip.duration:.2f} 秒 ({clip.duration/60:.2f} 分钟)")
    print(f"  分辨率: {clip.w}x{clip.h}")
    print(f"  帧率: {clip.fps:.2f} fps")
    
    print(f"\n音频信息:")
    if clip.audio:
        print(f"  音频时长: {clip.audio.duration:.2f} 秒")
        print(f"  音频存在: 是")
        
        if abs(clip.duration - clip.audio.duration) > 0.5:
            print(f"  [警告] 音频和视频时长不匹配！")
    else:
        print(f"  音频存在: 否")
        print(f"  [错误] 视频没有音频！")
    
    print(f"\n{'=' * 60}")
    
    if clip.duration < 1:
        print(f"[警告] 视频时长异常过短！")
        print(f"  实际: {clip.duration:.2f}秒")
    else:
        print(f"[OK] 视频时长正常")
    
    print(f"{'=' * 60}")
    
    clip.close()
    
except Exception as e:
    print(f"分析失败: {e}")
    import traceback
    traceback.print_exc()
