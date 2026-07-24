import os
import shutil

OUTPUT_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")

print("清理输出文件夹...")

for folder in ['audio', 'subtitle', 'video']:
    folder_path = os.path.join(OUTPUT_FOLDER, folder)
    if os.path.exists(folder_path):
        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    print(f"  删除: {filename}")
            except Exception as e:
                print(f"  删除失败 {filename}: {e}")

print("清理完成！")
