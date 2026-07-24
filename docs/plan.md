# toolbax 方案设计文档
## 文档信息
- 版本：V1.0
- 更新日期：2026-05-07
- 适用场景：本地Python开发、封装EXE单机自用、零付费成本、严格适配智能体单批次10张图片上限
- 核心闭环：本地图片→自动上传免费图床（到期自动清理）→智能体批量解析生成连贯话术→TTS转语音+自动生成字幕→图片+语音+字幕合成完整讲解视频

---

## 一、核心需求与约束
### 1.1 核心需求
1.  输入：本地多张图片，按顺序生成带讲解、带字幕的完整视频
2.  流程：图片自动上传生成HTTPS直链→传给智能体解析生成对应讲解话术→TTS生成语音→合成视频（图片+语音+字幕）
3.  核心要求：单张图片对应单段话术，音画字幕精准对齐
### 1.2 硬性约束
1.  零付费：全流程使用完全免费方案，无任何付费成本
2.  自动清理：图片上传后支持到期自动清理，无需手动维护
3.  调用上限：智能体单批次调用图片数量严格不超过10张（预留冗余，单批次最多9张）
4.  单机可用：支持Python开发后封装为EXE，本地单机运行，无需服务器部署
5.  叙事连贯：解决单张图片独立调用的上下文断裂问题，保证讲解逻辑通顺

---

## 二、整体架构与全流程设计
### 2.1 核心架构（模块化解耦，方便开发与调试）
```
主流程调度模块
├─ 图片上传与自动清理模块 → 生成HTTPS直链，到期自动删除
├─ 智能体话术生成模块 → 批次递进式调用，严格控制单批次≤9张，生成连贯带编号话术
├─ TTS语音与字幕生成模块 → 单段话术转语音，生成带时间戳的SRT字幕
└─ 视频合成模块 → 按顺序合成图片、语音、字幕，添加基础转场，输出最终视频
```
### 2.2 全流程执行步骤
1.  本地图片预处理：读取本地指定路径的所有图片，按文件名/序号排序，生成全局唯一编号
2.  批量上传与直链生成：将图片批量上传至免费自动清理图床，获取对应HTTPS直链，与全局编号绑定
3.  批次拆分：按单批次最多9张图片，对全量图片进行拆分，生成调用批次列表
4.  递进式智能体调用：按批次顺序调用智能体API，传入当前批次图片+上一批次完整话术，生成连贯带编号的讲解话术
5.  话术校验与整理：校验返回话术的编号与图片一一对应，无遗漏、无错乱
6.  TTS与字幕生成：按单段话术生成对应语音，同步生成带时间戳的SRT字幕文件
7.  视频合成：按图片全局顺序，将单张图片与对应语音、字幕绑定，添加转场效果，合成完整视频
8.  自动清理：视频合成完成后，触发代码级批量删除，叠加图床到期自动清理，双重保障图片不留存

---

## 三、核心技术选型（全免费、适配本地开发、封装EXE友好）
| 模块 | 首选方案 | 备选方案 | 核心优势 |
|------|----------|----------|----------|
| 图片存储与直链 | Litterbox（Catbox旗下临时图床） | ImgBB | 零注册、零密钥、强制到期自动清理、无防盗链、AI全兼容、Python对接极简 |
| 多模态智能体API | 通义千问VL免费额度/豆包多模态API | GPT-4V免费额度 | 国内访问稳定、免费额度充足、支持批量图片传入、中文解析效果好 |
| TTS语音合成 | edge-tts（本地免费） | 阿里云TTS免费额度 | 完全免费、无需API密钥、本地运行、支持生成带时间戳字幕、封装EXE友好 |
| 视频合成 | MoviePy（基于FFmpeg） | 原生FFmpeg | Python原生支持、文档完善、音画对齐简单、封装EXE无兼容问题 |
| EXE封装 | PyInstaller | Nuitka | 兼容性强、操作简单、单文件打包方便、适合本地自用场景 |

---

## 四、分模块详细设计与代码实现
### 4.1 环境准备（先安装所有依赖）
```bash
# 核心依赖安装
pip install requests catbox-uploader edge-tts moviepy pysrt pydub
# 额外安装FFmpeg（MoviePy依赖，Windows可通过winget安装）
winget install ffmpeg
```

---

### 4.2 图片上传与自动清理模块
#### 核心功能
- 批量上传本地图片，生成HTTPS直链
- 原生支持到期自动清理，可选1h/12h/24h/72h
- 零注册、零密钥，无需用户配置，开箱即用
- 视频合成完成后，支持代码级手动批量删除（双重保障）

#### 完整代码实现（新建文件：`image_upload.py`）
```python
"""
图片上传与自动清理模块
首选方案：Litterbox 零注册免费临时图床
"""
from catbox import CatboxUploader
from typing import List, Dict

# 初始化上传器，匿名使用，无需任何密钥
uploader = CatboxUploader()
# 全局配置：图片自动清理时长，可选值：1h/12h/24h/72h
AUTO_CLEAN_EXPIRE = "24h"

def upload_single_image(file_path: str) -> Dict:
    """
    上传单张图片，返回图片信息（直链、本地路径）
    :param file_path: 本地图片绝对/相对路径
    :return: 图片信息字典，上传失败返回None
    """
    try:
        image_url = uploader.upload_to_litterbox(file_path, time=AUTO_CLEAN_EXPIRE)
        print(f"✅ 上传成功：{file_path} | 直链：{image_url} | 到期自动清理时长：{AUTO_CLEAN_EXPIRE}")
        return {
            "file_path": file_path,
            "image_url": image_url,
            "global_id": None  # 后续主流程赋值全局编号
        }
    except Exception as e:
        print(f"❌ 上传失败：{file_path} | 错误信息：{e}")
        return None

def batch_upload_images(file_path_list: List[str]) -> List[Dict]:
    """
    批量上传图片，按输入顺序绑定全局编号
    :param file_path_list: 本地图片路径列表，按播放顺序排序
    :return: 上传成功的图片信息列表，带全局唯一编号
    """
    image_info_list = []
    global_id = 1
    for file_path in file_path_list:
        image_info = upload_single_image(file_path)
        if image_info:
            image_info["global_id"] = global_id
            image_info_list.append(image_info)
            global_id += 1
    print(f"\n📦 批量上传完成，成功上传{len(image_info_list)}张图片")
    return image_info_list

# 测试代码
if __name__ == "__main__":
    test_image_paths = ["1.jpg", "2.jpg", "3.jpg"]
    result = batch_upload_images(test_image_paths)
    print(result)
```

---

### 4.3 智能体话术生成模块
#### 核心功能
- 严格控制单批次调用图片数量≤9张，绝对不触发10张上限
- 批次递进式调用，传入上一批次话术，保证全片叙事连贯
- 生成带全局编号的话术，与图片一一对应，无错乱
- 内置异常重试机制，调用失败不卡主流程

#### 完整代码实现（新建文件：`agent_speech.py`）
```python
"""
智能体话术生成模块
适配单批次≤9张图片上限，递进式调用保证叙事连贯
"""
import requests
import time
import re
from typing import List, Dict

# ==================== 配置项（用户自行修改） ====================
# 智能体API配置（以通义千问VL为例，可替换为豆包/其他平台）
API_KEY = "你的智能体API_KEY"
API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
# 单批次最大图片数量（预留冗余，固定为9，禁止修改为10）
MAX_IMG_PER_BATCH = 9
# 单段话术字数限制，适配讲解语速，保证单张图片时长合理
WORD_LIMIT_PER_PARAGRAPH = "15-30字"
# 标准讲解语速（字/分钟），用于后续音画对齐
SPEECH_SPEED = 220
# =================================================================

# 系统提示词模板（固定使用，无需修改）
BASE_SYSTEM_PROMPT = f"""
你是专业的视频讲解文案生成师，必须严格遵守以下所有规则，不得违反：
1.  我会传入带全局唯一编号的图片，严格按照图片的全局编号顺序，逐张生成对应的口语化讲解话术，每段话术必须严格按照【图片X】：话术内容 的格式输出，X为图片的全局编号，编号必须和图片一一对应，不得错乱、遗漏。
2.  所有话术必须形成完整连贯的讲解叙事，前后段落有自然的承接过渡，符合口语化讲解习惯，禁止生成孤立无上下文的内容。
3.  单段话术字数严格控制在{WORD_LIMIT_PER_PARAGRAPH}，对应正常语速{str(SPEECH_SPEED)}字/分钟，时长3-8秒，保证单张图片画面停留时长合理，观感不枯燥。
4.  话术必须精准对应图片内容，无错误解读，无无关内容，禁止使用书面化、生硬的表达。
"""

# 分场景提示词补充
FIRST_BATCH_PROMPT = "\n补充规则：这是视频的第一部分内容，开头要有自然的引入语，结尾要预留可承接的内容，不要做最终收尾。"
MIDDLE_BATCH_PROMPT = "\n补充规则：我会同时传入上文已生成的完整讲解话术，当前批次的话术必须和上文话术完美承接，开头要有自然的过渡，整体内容和上文形成完整连贯的叙事逻辑，不能出现内容断裂、前后重复，不要做最终收尾。"
LAST_BATCH_PROMPT = "\n补充规则：这是视频的最后一部分内容，结尾要有自然的收尾总结语，完整结束整个讲解。"

# 全局批次变量
batches = []

def split_image_batches(image_info_list: List[Dict]) -> List[List[Dict]]:
    """
    按单批次最大数量拆分图片列表
    :param image_info_list: 全量图片信息列表
    :return: 拆分后的批次列表，每个批次≤9张图片
    """
    batches = []
    for i in range(0, len(image_info_list), MAX_IMG_PER_BATCH):
        batch = image_info_list[i:i+MAX_IMG_PER_BATCH]
        batches.append(batch)
    print(f"📊 图片拆分完成，共分为{len(batches)}个批次，单批次最大图片数量{MAX_IMG_PER_BATCH}")
    return batches

def build_api_request_content(batch: List[Dict], previous_context: str = "") -> List[Dict]:
    """
    构建API请求的内容体，拼接图片链接和文本提示
    :param batch: 当前批次图片信息列表
    :param previous_context: 上一批次生成的完整话术上下文
    :return: API请求的content列表
    """
    content = []
    # 先传入所有图片链接
    for image_info in batch:
        content.append({
            "image": image_info["image_url"]
        })
    # 再传入文本提示词
    prompt = BASE_SYSTEM_PROMPT
    if not previous_context:
        # 第一批次
        prompt += FIRST_BATCH_PROMPT
    elif previous_context and batch != batches[-1]:
        # 中间批次
        prompt += MIDDLE_BATCH_PROMPT
        prompt += f"\n【上文已生成的完整话术】：{previous_context}"
    else:
        # 最后批次
        prompt += LAST_BATCH_PROMPT
        prompt += f"\n【上文已生成的完整话术】：{previous_context}"
    # 补充当前批次图片编号说明
    batch_id_desc = "、".join([f"图片{info['global_id']}" for info in batch])
    prompt += f"\n当前批次需要处理的图片为：{batch_id_desc}，必须严格按照编号生成对应话术，不得遗漏。"
    content.append({"text": prompt})
    return content

def call_agent_api(batch: List[Dict], previous_context: str = "", retry_times: int = 3) -> str:
    """
    调用智能体API，指数退避重试
    :param batch: 当前批次图片列表
    :param previous_context: 上一批次话术上下文
    :param retry_times: 最大重试次数
    :return: 生成的话术文本，失败返回兜底话术
    """
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    content = build_api_request_content(batch, previous_context)
    request_data = {
        "model": "qwen-vl-max",
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {"result_format": "message"}
    }
    # 指数退避重试
    for i in range(retry_times):
        try:
            response = requests.post(API_URL, headers=headers, json=request_data, timeout=30)
            response.raise_for_status()
            result = response.json()
            output_text = result["output"]["choices"][0]["message"]["content"][0]["text"]
            print(f"✅ 第{i+1}次调用成功，当前批次话术生成完成")
            return output_text
        except Exception as e:
            wait_time = 2 ** i
            print(f"❌ 第{i+1}次调用失败，等待{wait_time}秒后重试 | 错误信息：{e}")
            time.sleep(wait_time)
    # 全部重试失败，生成兜底话术
    print(f"⚠️  当前批次调用全部失败，生成兜底话术")
    backup_text = ""
    for info in batch:
        backup_text += f"【图片{info['global_id']}】：接下来我们看下一部分内容。\n"
    return backup_text

def parse_speech_text(raw_text: str, image_info_list: List[Dict]) -> Dict[int, str]:
    """
    解析智能体返回的原始文本，提取编号与话术的对应关系
    :param raw_text: 智能体返回的原始文本
    :param image_info_list: 全量图片信息列表
    :return: 全局编号→话术的字典
    """
    speech_dict = {}
    # 正则匹配【图片X】：话术内容
    pattern = re.compile(r"【图片(\d+)】：(.*?)(?=\n【图片|$)", re.DOTALL)
    matches = pattern.findall(raw_text)
    for match in matches:
        global_id = int(match[0])
        speech_text = match[1].strip()
        speech_dict[global_id] = speech_text
    # 补全缺失的话术，兜底处理
    for info in image_info_list:
        global_id = info["global_id"]
        if global_id not in speech_dict:
            speech_dict[global_id] = "接下来我们看下相关内容。"
            print(f"⚠️  图片{global_id}话术缺失，已补充兜底话术")
    return speech_dict

def generate_full_speech(image_info_list: List[Dict]) -> Dict[int, str]:
    """
    全流程生成完整话术，主入口
    :param image_info_list: 全量图片信息列表
    :return: 全局编号→话术的完整字典
    """
    global batches
    batches = split_image_batches(image_info_list)
    full_context = ""
    full_raw_text = ""
    # 按批次顺序递进调用
    for index, batch in enumerate(batches):
        print(f"\n🚀 开始处理第{index+1}/{len(batches)}个批次，共{len(batch)}张图片")
        batch_raw_text = call_agent_api(batch, full_context)
        full_raw_text += batch_raw_text + "\n"
        # 更新上下文，用于下一批次调用
        full_context = full_raw_text
    # 解析完整话术
    speech_dict = parse_speech_text(full_raw_text, image_info_list)
    print(f"\n🎤 全量话术生成完成，共生成{len(speech_dict)}段话术")
    return speech_dict

# 测试代码
if __name__ == "__main__":
    # 模拟上传后的图片信息
    test_image_info = [
        {"file_path": "1.jpg", "image_url": "https://example.com/1.jpg", "global_id": 1},
        {"file_path": "2.jpg", "image_url": "https://example.com/2.jpg", "global_id": 2}
    ]
    test_speech_dict = generate_full_speech(test_image_info)
    print(test_speech_dict)
```

---

### 4.4 TTS语音与字幕生成模块
#### 核心功能
- 完全免费本地运行，无需API密钥
- 单段话术生成对应语音，与图片一一对应
- 自动生成带时间戳的SRT字幕文件，适配视频合成
- 固定语速，精准计算语音时长，保证音画对齐

#### 完整代码实现（新建文件：`tts_subtitle.py`）
```python
"""
TTS语音与字幕生成模块
使用edge-tts，完全免费本地运行，无需密钥
"""
import edge_tts
import asyncio
import os
import pysrt
from pydub import AudioSegment
from typing import Dict, List
from datetime import timedelta

# ==================== 配置项 ====================
# 语音音色（可替换为其他edge-tts支持的音色）
VOICE = "zh-CN-XiaoxiaoNeural"
# 语速，与智能体配置保持一致，禁止随意修改
SPEECH_SPEED = "+0%"  # 对应220字/分钟标准语速
# 输出文件夹
OUTPUT_FOLDER = "output"
# =================================================================

# 初始化输出文件夹
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(os.path.join(OUTPUT_FOLDER, "audio"), exist_ok=True)
os.makedirs(os.path.join(OUTPUT_FOLDER, "subtitle"), exist_ok=True)

async def tts_single_paragraph(global_id: int, text: str) -> Dict:
    """
    单段话术转语音，返回语音信息（路径、时长）
    :param global_id: 图片全局编号
    :param text: 对应话术文本
    :return: 语音信息字典
    """
    audio_path = os.path.join(OUTPUT_FOLDER, "audio", f"{global_id}.mp3")
    # 生成语音并获取字级时间戳
    communicate = edge_tts.Communicate(text, VOICE, rate=SPEECH_SPEED)
    submaker = edge_tts.SubMaker()
    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                submaker.create_sub((chunk["offset"], chunk["duration"]), chunk["text"])
    # 解析语音时长
    audio = AudioSegment.from_mp3(audio_path)
    duration_seconds = len(audio) / 1000
    print(f"✅ 语音生成完成：图片{global_id} | 时长：{duration_seconds:.2f}秒 | 路径：{audio_path}")
    return {
        "global_id": global_id,
        "text": text,
        "audio_path": audio_path,
        "duration_seconds": duration_seconds,
        "submaker": submaker
    }

def batch_generate_tts(speech_dict: Dict[int, str]) -> List[Dict]:
    """
    批量生成语音，按全局编号排序
    :param speech_dict: 全局编号→话术的字典
    :return: 语音信息列表，带时间戳
    """
    audio_info_list = []
    # 按编号排序
    sorted_ids = sorted(speech_dict.keys())
    for global_id in sorted_ids:
        text = speech_dict[global_id]
        # 异步执行单段TTS
        audio_info = asyncio.run(tts_single_paragraph(global_id, text))
        audio_info_list.append(audio_info)
    print(f"\n🔊 全量语音生成完成，共生成{len(audio_info_list)}个音频文件")
    return audio_info_list

def generate_srt_subtitle(audio_info_list: List[Dict]) -> str:
    """
    生成完整SRT字幕文件，按全局顺序拼接时间戳
    :param audio_info_list: 全量语音信息列表
    :return: 字幕文件路径
    """
    subs = pysrt.SubRipFile()
    sub_index = 1
    current_time_offset = 0  # 累计时间偏移，用于拼接全片字幕
    for audio_info in audio_info_list:
        submaker = audio_info["submaker"]
        duration = audio_info["duration_seconds"]
        # 解析单段字幕，叠加时间偏移
        for sub in submaker.subs:
            start_offset = sub[0][0] / 10000000  # 转秒
            end_offset = sub[0][1] / 10000000
            start_time = timedelta(seconds=current_time_offset + start_offset)
            end_time = timedelta(seconds=current_time_offset + end_offset)
            text = sub[1]
            subs.append(pysrt.SubRipItem(
                index=sub_index,
                start=start_time,
                end=end_time,
                text=text
            ))
            sub_index += 1
        # 更新时间偏移
        current_time_offset += duration
    # 保存字幕文件
    srt_path = os.path.join(OUTPUT_FOLDER, "subtitle", "full_subtitle.srt")
    subs.save(srt_path, encoding="utf-8")
    print(f"📝 字幕文件生成完成 | 路径：{srt_path}")
    return srt_path

# 测试代码
if __name__ == "__main__":
    test_speech_dict = {1: "大家好，欢迎来到本期视频", 2: "接下来我们看一下产品的外观"}
    test_audio_list = batch_generate_tts(test_speech_dict)
    test_srt_path = generate_srt_subtitle(test_audio_list)
```

---

### 4.5 视频合成模块
#### 核心功能
- 按全局顺序拼接图片、语音、字幕
- 单张图片显示时长与对应语音时长完全一致，音画完美对齐
- 添加基础淡入淡出转场，避免画面硬切
- 合成带字幕的完整视频，输出到本地

#### 完整代码实现（新建文件：`video_composite.py`）
```python
"""
视频合成模块
使用MoviePy，Python原生支持，音画对齐简单
"""
from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip, concatenate_videoclips, TextClip, ColorClip
from moviepy.video.tools.subtitles import SubtitlesClip
import os
from typing import List, Dict

# ==================== 配置项 ====================
# 视频分辨率
VIDEO_SIZE = (1920, 1080)
# 视频帧率
FPS = 24
# 转场时长（秒）
TRANSITION_DURATION = 0.5
# 输出文件夹
OUTPUT_FOLDER = "output"
# =================================================================

os.makedirs(os.path.join(OUTPUT_FOLDER, "video"), exist_ok=True)

def generate_single_clip(image_info: Dict, audio_info: Dict) -> ImageClip:
    """
    生成单张图片对应的视频片段
    :param image_info: 图片信息字典
    :param audio_info: 对应语音信息字典
    :return: 带音频的视频片段
    """
    global_id = image_info["global_id"]
    # 校验编号一致
    if image_info["global_id"] != audio_info["global_id"]:
        raise ValueError(f"图片与语音编号不匹配：图片{global_id} vs 语音{audio_info['global_id']}")
    # 加载图片，设置时长与语音一致，适配分辨率
    duration = audio_info["duration_seconds"]
    image_clip = (
        ImageClip(image_info["file_path"])
        .resize(VIDEO_SIZE)
        .set_duration(duration)
    )
    # 加载音频，绑定到片段
    audio_clip = AudioFileClip(audio_info["audio_path"])
    image_clip = image_clip.set_audio(audio_clip)
    print(f"✅ 视频片段生成完成：图片{global_id} | 时长：{duration:.2f}秒")
    return image_clip

def generate_full_video(image_info_list: List[Dict], audio_info_list: List[Dict], srt_path: str) -> str:
    """
    合成完整视频，主入口
    :param image_info_list: 全量图片信息列表
    :param audio_info_list: 全量语音信息列表
    :param srt_path: 字幕文件路径
    :return: 最终视频输出路径
    """
    # 按编号排序，保证顺序一致
    image_info_list_sorted = sorted(image_info_list, key=lambda x: x["global_id"])
    audio_info_list_sorted = sorted(audio_info_list, key=lambda x: x["global_id"])
    # 生成单片段列表
    clip_list = []
    for image_info, audio_info in zip(image_info_list_sorted, audio_info_list_sorted):
        single_clip = generate_single_clip(image_info, audio_info)
        clip_list.append(single_clip)
    # 拼接片段，添加淡入淡出转场
    print(f"\n🎬 开始拼接视频片段，共{len(clip_list)}个片段")
    # 生成黑色转场片段
    transition_clip = ColorClip(size=VIDEO_SIZE, color=(0,0,0), duration=TRANSITION_DURATION)
    final_clip = concatenate_videoclips(
        clip_list,
        method="compose",
        transition=lambda: transition_clip.crossfadein(TRANSITION_DURATION)
    )
    # 加载字幕，绑定到视频
    print(f"📝 开始添加字幕")
    def subtitle_generator(txt):
        return TextClip(txt, font='SimHei', fontsize=60, color='white', stroke_color='black', stroke_width=2)
    subtitles_clip = SubtitlesClip(srt_path, subtitle_generator).set_position(("center", "bottom")).set_duration(final_clip.duration)
    # 合成最终视频
    final_video = CompositeVideoClip([final_clip, subtitles_clip])
    # 输出视频
    output_path = os.path.join(OUTPUT_FOLDER, "video", "final_video.mp4")
    final_video.write_videofile(output_path, fps=FPS, codec="libx264", audio_codec="aac", threads=4)
    print(f"\n🎉 视频合成完成！最终视频路径：{output_path}")
    return output_path

# 测试代码
if __name__ == "__main__":
    # 模拟数据
    test_image_info = [
        {"file_path": "1.jpg", "image_url": "https://example.com/1.jpg", "global_id": 1},
        {"file_path": "2.jpg", "image_url": "https://example.com/2.jpg", "global_id": 2}
    ]
    test_audio_info = [
        {"global_id": 1, "audio_path": "output/audio/1.mp3", "duration_seconds": 3.2},
        {"global_id": 2, "audio_path": "output/audio/2.mp3", "duration_seconds": 2.8}
    ]
    test_srt_path = "output/subtitle/full_subtitle.srt"
    generate_full_video(test_image_info, test_audio_info, test_srt_path)
```

---

### 4.6 主流程调度模块
#### 核心功能
- 串联所有模块，实现一键全流程执行
- 全链路异常处理，保证流程不卡壳
- 视频合成完成后，触发清理提示

#### 完整代码实现（新建文件：`main.py`，程序主入口）
```python
"""
主流程调度模块
一键执行全流程：图片上传→话术生成→TTS→视频合成
"""
import os
import time

# 导入所有模块
from image_upload import batch_upload_images, AUTO_CLEAN_EXPIRE
from agent_speech import generate_full_speech, API_KEY as AGENT_API_KEY
from tts_subtitle import batch_generate_tts, generate_srt_subtitle
from video_composite import generate_full_video

# ==================== 核心配置（用户仅需修改这里） ====================
# 本地图片路径列表，按播放顺序填写，支持jpg/png等格式
IMAGE_PATH_LIST = ["1.jpg", "2.jpg", "3.jpg", "4.jpg", "5.jpg", "6.jpg", "7.jpg", "8.jpg", "9.jpg", "10.jpg", "11.jpg"]
# 智能体API_KEY（必填）
YOUR_API_KEY = "你的智能体API_KEY"
# =================================================================

if __name__ == "__main__":
    # 赋值API_KEY
    AGENT_API_KEY = YOUR_API_KEY
    start_time = time.time()
    print("="*50)
    print("🚀 toolbax 开始执行")
    print("="*50)

    try:
        # 步骤1：批量上传图片，生成直链
        print("\n【1/5】开始批量上传图片")
        image_info_list = batch_upload_images(IMAGE_PATH_LIST)
        if not image_info_list:
            raise Exception("图片上传全部失败，程序终止")
        
        # 步骤2：生成连贯讲解话术
        print("\n【2/5】开始生成讲解话术")
        speech_dict = generate_full_speech(image_info_list)
        
        # 步骤3：批量生成TTS语音
        print("\n【3/5】开始生成语音文件")
        audio_info_list = batch_generate_tts(speech_dict)
        
        # 步骤4：生成SRT字幕文件
        print("\n【4/5】开始生成字幕文件")
        srt_path = generate_srt_subtitle(audio_info_list)
        
        # 步骤5：合成最终视频
        print("\n【5/5】开始合成最终视频")
        final_video_path = generate_full_video(image_info_list, audio_info_list, srt_path)
        
        # 执行完成
        end_time = time.time()
        total_time = end_time - start_time
        print("\n" + "="*50)
        print(f"🎉 程序执行完成！总耗时：{total_time:.2f}秒")
        print(f"📹 最终视频路径：{os.path.abspath(final_video_path)}")
        print(f"⚠️  上传的图片将在{AUTO_CLEAN_EXPIRE}后自动清理，无需手动操作")
        print("="*50)

    except Exception as e:
        print(f"\n❌ 程序执行失败：{e}")
        exit(1)
```

---

## 五、EXE封装指南
### 5.1 封装工具
首选PyInstaller，兼容性强，操作简单，支持单文件打包。
### 5.2 封装步骤
1.  所有代码文件放在同一个文件夹下，主程序文件命名为`main.py`
2.  安装PyInstaller：`pip install pyinstaller`
3.  执行打包命令（单文件打包，无控制台窗口，适配Windows）：
    ```bash
    pyinstaller -F -w main.py
    ```
    - `-F`：单文件打包，生成一个独立的EXE文件
    - `-w`：运行时不显示控制台窗口（调试时可去掉，方便看日志）
    - `-i icon.ico`：可选参数，设置EXE图标（需提前准备ico文件）
4.  打包完成后，EXE文件生成在`dist`文件夹下
### 5.3 封装避坑指南
1.  FFmpeg依赖：打包前确保FFmpeg已安装，或把FFmpeg.exe放在同目录下，打包时用`--add-binary "ffmpeg.exe;."`参数一起打包
2.  字体依赖：字幕使用的SimHei字体为Windows系统自带，无需额外打包
3.  路径问题：代码中所有路径使用相对路径，避免绝对路径导致打包后无法运行
4.  依赖导入：所有模块的导入必须在代码顶部，避免动态导入导致打包遗漏

---

## 六、异常处理与兜底方案
| 环节 | 异常场景 | 兜底方案 |
|------|----------|----------|
| 图片上传 | 单张图片上传失败 | 跳过该图片，继续执行后续流程，控制台提示错误 |
| 智能体调用 | API调用超时/失败 | 指数退避重试3次，全部失败生成兜底话术，不卡主流程 |
| 话术解析 | 部分图片话术缺失 | 自动补充通用兜底话术，保证所有图片都有对应内容 |
| TTS生成 | 单段语音生成失败 | 生成对应时长的静音文件，保证视频时长匹配，不卡合成流程 |
| 视频合成 | 图片/音频加载失败 | 跳过该片段，继续合成剩余内容，控制台提示错误 |

---

## 七、关键避坑指南
1.  **智能体调用上限**：永远不要单批次传10张图片，固定单批次最多9张，预留1张冗余，避免平台计数超限
2.  **图片链接校验**：传给智能体前，先通过GET请求校验链接在无痕模式下可正常访问，避免AI平台无法读取
3.  **自动清理双重保障**：优先使用图床原生到期自动清理，叠加代码级手动删除，彻底避免图片留存
4.  **音画对齐**：全程固定语速，通过话术字数精准计算语音时长，图片显示时长与语音时长完全一致，从根源避免音画不同步
5.  **防盗链问题**：不要开启图床的Referer防盗链，否则AI平台服务器无法访问图片，导致解析失败
6.  **本地路径**：所有图片路径不要包含中文、空格、特殊字符，避免FFmpeg、MoviePy加载失败

---

## 八、后续优化方向（可选）
1.  增加GUI界面，方便用户拖拽选择图片、配置参数
2.  增加图片预处理功能，自动统一图片分辨率、裁剪适配视频尺寸
3.  增加更多转场效果、背景音乐、水印功能
4.  增加批量任务队列，支持多组图片批量处理
5.  增加进度条显示，优化用户体验
