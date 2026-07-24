"""
toolbax 素材转讲解视频程序
全流程：本地图片→自动上传图床→智能体生成话术→TTS转语音+字幕→合成视频

API文档参考 api.md：
  SSE格式: data: {...} 行 + event: data/end 行，两行一组
  data: {"role":"assistant","content":"..."}
  event: data
  ---
  data: {'end':{},'role':'assistant'}    # 注意：结束事件用单引号
  event: end
"""
import os
import re
import time
import json
import uuid
import asyncio
import subprocess
import glob
import shutil
import sys
import tempfile
from typing import List, Dict
from ffmpeg_util import resolve_ffmpeg, resolve_ffprobe
import numpy as np
from text_segmenter import TextSegmenter, SegmentData
from subtitle_generator import SubtitleGenerator
from video_composer import VideoComposer
from paths import OUTPUT_FOLDER as _DEFAULT_OUTPUT_FOLDER

def load_env_file(path: str='') -> None:
    """加载 .env 文件。自动查找项目根目录。"""
    if not path:
        if getattr(sys, 'frozen', False):
            path = os.path.join(os.path.dirname(sys.executable), '.env')
        else:
            _src_dir = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(os.path.dirname(_src_dir), '.env')
    if not os.path.exists(path):
        if getattr(sys, 'frozen', False):
            meipass_path = os.path.join(sys._MEIPASS, '.env')
            if os.path.exists(meipass_path):
                path = meipass_path
            else:
                return
        else:
            return
    with open(path, 'rb') as fb:
        raw_bytes = fb.read()
    text = None
    for enc in ('utf-8', 'utf-8-sig', 'gbk'):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw_bytes.decode('utf-8', errors='replace')
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
load_env_file()
try:
    import requests
except ImportError as exc:
    raise RuntimeError('缺少 requests 模块，请安装: pip install requests') from exc
try:
    import edge_tts
except ImportError as exc:
    raise RuntimeError('缺少 edge-tts 模块，请安装: pip install edge-tts') from exc
try:
    from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip, concatenate_audioclips, concatenate_videoclips, TextClip, vfx
    from moviepy.audio.AudioClip import AudioArrayClip
    from PIL import Image, ImageDraw, ImageFont
    if not hasattr(Image, 'ANTIALIAS'):
        Image.ANTIALIAS = Image.Resampling.LANCZOS
except ImportError as exc:
    raise RuntimeError(f'缺少 moviepy/Pillow 模块，请安装: pip install moviepy pillow；详细错误：{exc}') from exc
try:
    import pysrt
except ImportError as exc:
    raise RuntimeError('缺少 pysrt 模块，请安装: pip install pysrt') from exc
API_KEY = os.getenv('TOOLBAX_API_KEY', '')
API_URL = os.getenv('TOOLBAX_API_URL', 'https://api.yunbloom.cn/v2/chat/completions/share?shareId=6gGPZIYSPHZ67fO8')
AUTO_CLEAN_EXPIRE = '24h'
MAX_IMG_PER_BATCH = 10
VOICE = 'zh-CN-XiaoxiaoNeural'
TTS_SPEED = '+0%'
OUTPUT_FOLDER = os.getenv('TOOLBAX_OUTPUT_FOLDER') or _DEFAULT_OUTPUT_FOLDER
TRANSITION_DURATION = 0.5
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
IMPORTANCE_LABELS = {'low': '低：简短带过，只保留核心信息', 'normal': '普通：正常讲解', 'high': '高：适当展开细节和价值', 'key': '关键：作为重点画面，讲解更充分、更有强调'}
SPEECH_RATE_VALUES = {'slow': '-15%', 'normal': '+0%', 'fast': '+18%'}
DEFAULT_SPEECH_SPEED = 1.0
SPEECH_SPEED_MIN = 0.7
SPEECH_SPEED_MAX = 1.5

def _speed_to_edge_rate(speed: float) -> str:
    """将语速倍率（1.0 = 原速）转换为 edge-tts 的 rate 字符串。

edge-tts 接受 "+20%" / "-15%" 这样的百分比字符串。
speed=1.0 -> "+0%", speed=1.2 -> "+20%", speed=0.8 -> "-20%"。
"""
    speed = float(speed or 1.0)
    pct = round((speed - 1.0) * 100)
    if pct >= 0:
        return f'+{pct}%'
    return f'{pct}%'

def _clamp_speech_speed(speed) -> float:
    """钳制语速倍率到安全范围。"""
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        speed = DEFAULT_SPEECH_SPEED
    return max(SPEECH_SPEED_MIN, min(SPEECH_SPEED_MAX, speed))
import re as _re
_AI_TOKEN_RE = _re.compile('(?<![A-Za-z])AI(?![A-Za-z])')

def normalize_special_pronunciation(text: str) -> str:
    """把独立英文缩写 AI 改写为自然英文读法（A.I），不影响字幕原文。

仅作用于配音文本；调用方负责把原始文本存入 audio_info['text'] 供字幕使用。
"""
    if not text:
        return text
    return _AI_TOKEN_RE.sub('A.I', text)
USE_GPU_ACCEL = os.environ.get('APP_VARIANT', 'gpu') != 'cpu'
WHISPER_MODEL_SIZE = os.getenv('TOOLBAX_WHISPER_MODEL', 'base')
NVENC_PRESET = os.getenv('TOOLBAX_NVENC_PRESET', 'p1')
NVENC_CQ = int(os.getenv('TOOLBAX_NVENC_CQ', '23'))

def can_use_gpu_video() -> bool:
    """确认当前变体和 FFmpeg/NVIDIA 运行时都真正支持 NVENC。委托给 VideoComposer。"""
    composer = VideoComposer()
    return composer.check_nvenc()

def build_fallback_speech(global_id: int) -> str:
    """生成兜底引导话术，保证至少30字，避免TTS音频过短导致画面闪过。"""
    return f'接下来我们来看第{global_id}页的内容。这一页展示的信息非常重要，让我们一起了解其中的关键要点。'

def _validate_speech_lengths(speech_dict: Dict[int, str]) -> None:
    """校验话术长度，过短或过长时打印警告。"""
    for gid, text in speech_dict.items():
        text_len = len(text)
        if text_len < 20:
            print(f'[WARN] 图片{gid}话术过短（{text_len}字），可能导致音频时长不足：{text[:40]}')
        elif text_len > 500:
            print(f'[WARN] 图片{gid}话术过长（{text_len}字），可能导致音频时长超标：{text[:40]}')

def _create_silent_audio_clip(duration: float, fps: int=44100):
    """创建指定时长的静音音频片段，用于音频不足时的静音填充。"""
    if duration <= 0:
        return None
    frame_count = max(1, int(duration * fps))
    silent_array = np.zeros((frame_count, 2))
    return AudioArrayClip(silent_array, fps=fps)
batches = []
_ORIGINAL_POPEN = subprocess.Popen

def silent_popen(*args, **kwargs):
    if os.name == 'nt':
        kwargs['creationflags'] = kwargs.get('creationflags', 0) | subprocess.CREATE_NO_WINDOW
        if kwargs.get('startupinfo') is None:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            kwargs['startupinfo'] = startupinfo
    return _ORIGINAL_POPEN(*args, **kwargs)
subprocess.Popen = silent_popen

def silent_subprocess_kwargs() -> Dict:
    if os.name != 'nt':
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {'creationflags': subprocess.CREATE_NO_WINDOW, 'startupinfo': startupinfo}

def post_without_env_proxy(url: str, **kwargs):
    session = requests.Session()
    session.trust_env = False
    try:
        return session.post(url, **kwargs)
    finally:
        session.close()

def find_tool_executable(tool_name: str) -> str:
    tool_path = shutil.which(tool_name)
    if tool_path:
        return tool_path
    if os.name == 'nt':
        local_appdata = os.getenv('LOCALAPPDATA')
        if local_appdata:
            winget_packages = os.path.join(local_appdata, 'Microsoft', 'WinGet', 'Packages')
            matches = glob.glob(os.path.join(winget_packages, '**', 'bin', f'{tool_name}.exe'), recursive=True)
            if matches:
                return matches[0]
    if sys.platform == 'darwin':
        brew_prefixes = ['/opt/homebrew/bin', '/usr/local/bin']
        for prefix in brew_prefixes:
            candidate = os.path.join(prefix, tool_name)
            if os.path.exists(candidate):
                return candidate
    return tool_name

def upload_to_litterbox(file_path: str, expire_time: str='24h', timeout: int=30) -> str:
    errors = []
    try:
        url = 'https://catbox.moe/user/api.php'
        with open(file_path, 'rb') as f:
            files = {'fileToUpload': f}
            data = {'reqtype': 'fileupload', 'json': 'true'}
            response = post_without_env_proxy(url, files=files, data=data, timeout=timeout)
        if response.status_code == 200:
            result = response.text.strip()
            if result.startswith('http'):
                return result
            else:
                raise Exception(f'上传失败: {result}')
        else:
            raise Exception(f'上传失败，HTTP状态码: {response.status_code}')
    except Exception as e:
        errors.append(f'Catbox: {e}')
    for attempt in range(3):
        try:
            url = 'https://uguu.se/upload'
            with open(file_path, 'rb') as f:
                response = post_without_env_proxy(url, files={'files[]': f}, timeout=timeout)
            if response.status_code == 200:
                result = response.json()
                files = result.get('files') or []
                if result.get('success') and files and files[0].get('url'):
                    return files[0]['url'].replace('\\/', '/')
                raise Exception(f'上传失败: {response.text[:200]}')
            raise Exception(f'上传失败，HTTP状态码: {response.status_code}')
        except Exception as e:
            errors.append(f'Uguu第{attempt + 1}次: {e}')
            time.sleep(1 + attempt)
    raise Exception('；'.join(errors))

def upload_image_fallback(file_path: str) -> str:
    import base64
    with Image.open(file_path) as image:
        image.thumbnail((1280, 1280))
        if image.mode in ('RGBA', 'LA'):
            background = Image.new('RGB', image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[-1])
            image = background
        else:
            image = image.convert('RGB')
        temp_path = os.path.join(tempfile.gettempdir(), f'toolbax_{uuid.uuid4().hex}.jpg')
        try:
            image.save(temp_path, format='JPEG', quality=72, optimize=True)
            with open(temp_path, 'rb') as f:
                encoded = base64.b64encode(f.read()).decode('utf-8')
            print(f'[DEBUG] Base64压缩图大小: {len(encoded)} 字符')
            return f'data:image/jpeg;base64,{encoded}'
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

def get_audio_duration(audio_path: str) -> float:
    try:
        cmd = [resolve_ffprobe(), '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', audio_path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **silent_subprocess_kwargs())
        if result.stdout:
            duration = float(result.stdout.strip())
            print(f'[DEBUG] 音频时长: {duration:.2f}秒')
            return duration
        print(f'[WARN] ffprobe输出为空，尝试ffmpeg方法')
    except Exception as e:
        print(f'[WARN] ffprobe失败: {e}，尝试ffmpeg方法')
    try:
        cmd = [resolve_ffmpeg(), '-i', audio_path, '-f', 'null', '-']
        result = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True, **silent_subprocess_kwargs())
        output = result.stderr if result.stderr else result.stdout
        for line in output.split('\n'):
            if 'Duration:' in line:
                duration_str = line.split(',')[0].split('Duration:')[1].strip()
                h, m, s = duration_str.split(':')
                duration = float(h) * 3600 + float(m) * 60 + float(s)
                print(f'[DEBUG] ffmpeg解析时长: {duration:.2f}秒')
                return duration
    except Exception as e:
        print(f'[WARN] ffmpeg解析也失败: {e}')
    print(f'[WARN] 所有方法失败，使用默认值3.0秒')
    return 3.0

def init_output_folders():
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_FOLDER, 'audio'), exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_FOLDER, 'subtitle'), exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_FOLDER, 'video'), exist_ok=True)

def upload_single_image(file_path: str) -> Dict:
    try:
        image_url = upload_to_litterbox(file_path, expire_time=AUTO_CLEAN_EXPIRE)
        print(f'[OK] 上传成功：{os.path.basename(file_path)} | 直链：{image_url}')
        return {'file_path': file_path, 'image_url': image_url, 'global_id': None}
    except Exception as e:
        if os.getenv('TOOLBAX_ALLOW_BASE64', '').lower() not in ('1', 'true', 'yes'):
            print(f'[ERROR] 图床上传失败：{os.path.basename(file_path)} | 错误：{e}')
            return None
        print(f'[WARN] 图床上传失败，改用Base64图片：{os.path.basename(file_path)} | 错误：{e}')
        try:
            image_url = upload_image_fallback(file_path)
            return {'file_path': file_path, 'image_url': image_url, 'global_id': None}
        except Exception as fallback_e:
            print(f'[ERROR] Base64编码也失败：{os.path.basename(file_path)} | 错误：{fallback_e}')
            return None

def batch_upload_images(file_path_list: List[str], progress_callback=None) -> List[Dict]:
    print(f'[INFO] 开始批量上传，共{len(file_path_list)}张图片')
    image_info_list = []
    global_id = 1
    for idx, file_path in enumerate(file_path_list):
        if callable(progress_callback):
            progress_callback(idx, len(file_path_list), f'正在上传第 {idx + 1}/{len(file_path_list)} 张图片', False)
        print(f'[INFO] 上传第{idx + 1}/{len(file_path_list)}张：{os.path.basename(file_path)}')
        image_info = upload_single_image(file_path)
        if image_info:
            image_info['global_id'] = global_id
            image_info_list.append(image_info)
            global_id += 1
        else:
            print(f'[ERROR] 第{idx + 1}张上传失败，跳过')
        if callable(progress_callback):
            progress_callback(idx + 1, len(file_path_list), f'图片上传完成 {idx + 1}/{len(file_path_list)}', False)
    print(f'\n[INFO] 批量上传完成，成功 {len(image_info_list)}/{len(file_path_list)} 张')
    return image_info_list

def split_image_batches(image_info_list: List[Dict]) -> List[List[Dict]]:
    batches_result = []
    for i in range(0, len(image_info_list), MAX_IMG_PER_BATCH):
        batch = image_info_list[i:i + MAX_IMG_PER_BATCH]
        batches_result.append(batch)
    print(f'[INFO] 图片拆分：{len(image_info_list)}张 → {len(batches_result)}个批次')
    return batches_result

def build_api_messages(batch: List[Dict], batch_index: int=0, batch_count: int=1) -> List[Dict]:
    """构建API消息，用JSON元数据把图片编号、顺序和用户参数绑定起来。"""
    is_continuation = batch_index > 0
    payload = {
        'task': 'continue_image_video_speech' if is_continuation else 'generate_image_video_speech',
        'conversation': {
            'batch_number': batch_index + 1,
            'batch_count': batch_count,
            'is_continuation': is_continuation,
            'instruction': (
                '这是同一份材料的后续页面。请继承本会话前面批次的主题理解、术语、口吻和叙事衔接，不要重新开场。'
                if is_continuation else
                '这是整份材料的第一批页面，请建立统一的主题理解、术语和讲解风格，供后续批次延续。'
            ),
        },
        'output_format': {'type': 'json', 'schema': {'video_filename': 'string', 'items': [{'image_id': 'number', 'speech': 'string'}]}},
        'rules': [f"本批次包含全局第{batch[0]['global_id']}到第{batch[-1]['global_id']}张图片（共{len(batch)}张），请严格使用 images 中每张图片的 image_id（全局编号）作为话术标识，不要使用 batch_index 作为话术编号", '必须为每一张图片独立生成一段中文视频讲解文案，禁止将多张图片合并为一段话术', '不要遗漏任何一张图片，images 中有多少张图片就必须输出多少段话术', '每段话术长度应在 50-200 字之间，确保讲解充分、信息完整', 'importance 越高，文案越详细、越有重点；importance 低时简短带过', '请理解本批图片主题，生成一个简洁准确的视频文件名，放在 video_filename 字段中', 'video_filename 不要包含文件扩展名，不要包含路径，不要使用 \\ / : * ? " < > | 等非法字符'],
        'images': [{'image_id': info['global_id'], 'batch_index': idx, 'name': info.get('name') or os.path.basename(info.get('file_path', '')), 'importance': info.get('importance', 'normal'), 'importance_instruction': IMPORTANCE_LABELS.get(info.get('importance', 'normal'), IMPORTANCE_LABELS['normal']), 'transition_to_next': info.get('transition', 'none')} for idx, info in enumerate(batch, start=1)],
    }
    content = [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}]
    for idx, image_info in enumerate(batch, start=1):
        content.append({'type': 'text', 'text': json.dumps({'image_id': image_info['global_id'], 'batch_index': idx, 'name': image_info.get('name') or os.path.basename(image_info.get('file_path', ''))}, ensure_ascii=False)})
        content.append({'type': 'image_url', 'image_url': {'url': image_info['image_url']}})
    messages = [{'role': 'user', 'content': content}]
    print(f'[DEBUG] 构建1条消息，包含{len(batch)}张图片和编号锚点')
    return messages

def call_agent_api_sse(
    batch: List[Dict],
    retry_times: int=3,
    progress_callback=None,
    session_id: str=None,
    batch_index: int=0,
    batch_count: int=1,
) -> str:
    """调用SSE流式API，根据api.md规范解析SSE格式。

retry_times: 失败重试次数（含首次）。网络异常或返回空响应都会触发重试，
指数退避（1s/2s/...）。全部重试仍失败则抛出异常，由上层停止任务并上报错误，不再静默降级为占位话术。
"""
    if not API_KEY:
        raise RuntimeError('未配置 TOOLBAX_API_KEY，无法调用 GPT5.6 生成文案。请在 .env 中填写 API_KEY 后重试。')
    headers = {'Authorization': API_KEY, 'Content-Type': 'application/json', 'Accept': 'text/event-stream'}
    messages = build_api_messages(batch, batch_index=batch_index, batch_count=batch_count)
    session_id = session_id or str(uuid.uuid4())
    request_data = {'messages': messages, 'sessionId': session_id, 'source': 'api', 'extra': {}}
    print(f'[INFO] API调用：会话 {session_id[:8]}…，第 {batch_index + 1}/{batch_count} 条消息，{len(batch)}张图片')
    last_error = None
    for attempt in range(retry_times):
        try:
            print(f'[INFO] 第{attempt + 1}次请求...')
            if callable(progress_callback):
                progress_callback(
                    0, 0,
                    f'同一会话第 {batch_index + 1}/{batch_count} 条消息已提交，AI 正在分析 {len(batch)} 张图片'
                    + (f'（重试 {attempt + 1}）' if attempt else ''),
                    True,
                )
            response = post_without_env_proxy(API_URL, headers=headers, json=request_data, stream=True, timeout=300)
            response.raise_for_status()
            print(f'[DEBUG] HTTP {response.status_code}')
            raw_chunks = []
            chunk_count = 0
            for chunk in response.iter_content(chunk_size=4096):
                if chunk:
                    raw_chunks.append(chunk)
                    chunk_count += 1
                    if callable(progress_callback) and (chunk_count == 1 or chunk_count % 8 == 0):
                        received_kb = sum(len(item) for item in raw_chunks) // 1024
                        progress_callback(
                            chunk_count, 0,
                            f'AI 正在返回第 {batch_index + 1}/{batch_count} 批文稿，已接收约 {received_kb} KB',
                            True,
                        )
            raw_bytes = b''.join(raw_chunks)
            raw_text = raw_bytes.decode('utf-8', errors='replace')
            print(f'[DEBUG] 收到{chunk_count}个数据块，总{len(raw_bytes)}字节')
            print(f'[DEBUG] 原始响应前500字符:\n{raw_text[:500]}')
            full_text = ''
            lines = raw_text.split('\n')
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if line.startswith('data:'):
                    json_str = line[5:].strip()
                    if json_str == '[DONE]':
                        print('[INFO] [DONE]信号，流结束')
                        break
                    try:
                        obj = json.loads(json_str)
                    except json.JSONDecodeError:
                        try:
                            obj = json.loads(json_str.replace("'", '"'))
                        except json.JSONDecodeError:
                            i += 1
                            continue
                    if i + 1 < len(lines) and lines[i + 1].strip() == 'event: end':
                        print('[INFO] SSE end事件，流结束')
                        break
                    extracted = _extract_text_from_json(obj)
                    if extracted:
                        full_text += extracted
                i += 1
            print(f'[DEBUG] 解析完成，提取文本{len(full_text)}字')
            if full_text.strip():
                print(f'[OK] API成功，{len(full_text)}字')
                print(f'[DEBUG] 完整文本:\n{full_text}')
                return full_text
            else:
                print(f'[WARN] 第{attempt + 1}次未提取到有效文本')
                if attempt < retry_times - 1:
                    print(f'[INFO] 空响应，准备重试（{attempt + 2}/{retry_times}）…')
                    time.sleep(2 ** attempt)
                    continue
                last_error = RuntimeError('GPT5.6 返回空内容，未解析到任何文案（可能是模型输出格式异常或 share 配置问题）')
                break
        except Exception as e:
            print(f'[ERROR] 第{attempt + 1}次异常: {e}')
            last_error = e
            if attempt < retry_times - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f'调用 GPT5.6 生成文案失败（已重试 {retry_times} 次）：{last_error}') from last_error

def _extract_text_from_json(obj: dict) -> str:
    """从API返回的JSON中提取文本，兼容多种格式"""
    if not isinstance(obj, dict):
        return ''
    if obj.get('role') and obj.get('role') != 'assistant':
        return ''
    if 'node' in obj and 'status' in obj and (obj.get('role') != 'assistant'):
        return ''
    if 'content' in obj:
        c = obj['content']
        if isinstance(c, str):
            return c
        if isinstance(c, dict) and 'text' in c:
            return c['text']
        if isinstance(c, list):
            parts = []
            for item in c:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and 'text' in item:
                    parts.append(item['text'])
            return ''.join(parts)
    if 'delta' in obj and isinstance(obj['delta'], dict):
        if 'content' in obj['delta']:
            return obj['delta']['content']
    if 'choices' in obj and isinstance(obj['choices'], list):
        parts = []
        for choice in obj['choices']:
            if isinstance(choice, dict):
                if 'delta' in choice and 'content' in choice['delta']:
                    parts.append(choice['delta']['content'])
                elif 'message' in choice and 'content' in choice['message']:
                    parts.append(choice['message']['content'])
                elif 'text' in choice:
                    parts.append(choice['text'])
        return ''.join(parts)
    if 'text' in obj and isinstance(obj['text'], str):
        return obj['text']
    if 'result' in obj and isinstance(obj['result'], str):
        return obj['result']
    if 'message' in obj and isinstance(obj['message'], dict):
        if 'content' in obj['message']:
            return obj['message']['content']
    print(f'[DEBUG] 未识别的JSON keys: {list(obj.keys())}')
    return ''


def _decode_agent_json(raw_text: str):
    """从模型文本中提取 JSON，兼容代码块、前后说明和双重编码。"""
    text = (raw_text or '').strip()
    candidates = []
    for match in re.finditer(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE):
        candidates.append(match.group(1).strip())
    candidates.append(text)

    decoder = json.JSONDecoder()
    for candidate in candidates:
        pending = [candidate]
        while pending:
            value = pending.pop(0).strip()
            if not value:
                continue
            parsed = None
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                # raw_decode 能跳过模型在 JSON 前后的解释文字。
                for index, char in enumerate(value):
                    if char not in '{[':
                        continue
                    try:
                        parsed, _ = decoder.raw_decode(value[index:])
                        break
                    except json.JSONDecodeError:
                        continue
            if parsed is None:
                continue
            if isinstance(parsed, str) and parsed.strip() != value:
                pending.append(parsed)
                continue
            return parsed
    return None


def _speech_text(value) -> str:
    """把单页 speech/text/content 的不同返回形态归一为纯文本。"""
    if value is None:
        return ''
    if isinstance(value, dict):
        for key in ('speech', 'text', 'content', 'value'):
            if key in value:
                text = _speech_text(value.get(key))
                if text:
                    return text
        return ''
    if isinstance(value, list):
        parts = [_speech_text(item) for item in value]
        return ''.join(part for part in parts if part).strip()
    if not isinstance(value, str):
        return str(value).strip()

    text = value.strip()
    if not text:
        return ''
    if text[:1] in '{[':
        nested = _decode_agent_json(text)
        if nested is not None and nested != text:
            extracted = _speech_text(nested)
            if extracted:
                return extracted
            # 确认是结构化数据但没有话术字段时，不把大括号原样展示给用户。
            return ''
    return re.sub('[*`]', '', text).strip()


def _looks_like_agent_json_fragment(text: str) -> bool:
    """识别不应进入逐页文稿的 JSON 整体或残片。"""
    value = (text or '').strip()
    if not value:
        return False
    structural_keys = ('"video_filename"', '"items"', '"image_id"', '"speech"')
    return (
        any(key in value for key in structural_keys)
        or value in ('{', '}', '[', ']', '},', '],')
        or bool(re.fullmatch(r'[{}\[\],:]+', value))
    )

def parse_agent_response(raw_text: str, image_info_list: List[Dict]) -> Dict:
    """从AI返回文本中解析每张图片的话术和建议文件名。

API 每 10 张图片请求一次，智能体有时会在后续批次里重新从
【图片1】开始编号。这里优先识别全局编号；如果编号不在当前批次内，
就按当前批次内的序号映射到真实 global_id，避免旁白和画面错位。
"""
    speech_dict = {}
    video_filename = ''
    valid_ids = [info['global_id'] for info in image_info_list]
    valid_id_set = set(valid_ids)
    print(f'\n[DEBUG] 待解析文本({len(raw_text)}字):')
    print(raw_text[:800] if len(raw_text) > 800 else raw_text)
    try:
        obj = _decode_agent_json(raw_text)
        if isinstance(obj, dict):
            video_filename = str(obj.get('video_filename') or obj.get('filename') or obj.get('title') or '').strip()
        json_items = obj.get('items') if isinstance(obj, dict) else obj
        if isinstance(json_items, list):
            for item in json_items:
                if not isinstance(item, dict):
                    continue
                parsed_id = item.get('image_id') or item.get('id') or item.get('global_id')
                text = _speech_text(item.get('speech') or item.get('text') or item.get('content'))
                if parsed_id is None or not text:
                    continue
                parsed_id = int(parsed_id)
                if parsed_id in valid_id_set:
                    gid = parsed_id
                elif 1 <= parsed_id <= len(valid_ids):
                    gid = valid_ids[parsed_id - 1]
                else:
                    continue
                speech_dict[gid] = text
            if speech_dict:
                print(f'[DEBUG] JSON解析: {len(speech_dict)}段')
                for info in image_info_list:
                    gid = info['global_id']
                    if gid not in speech_dict:
                        speech_dict[gid] = build_fallback_speech(gid)
                        print(f'[WARN] 图片{gid}话术缺失，使用兜底话术')
                _validate_speech_lengths(speech_dict)
                return {'speech': speech_dict, 'video_filename': video_filename}
    except Exception as exc:
        print(f'[DEBUG] JSON解析未命中: {exc}')
    pattern = re.compile('(?:【\\s*)?(?:图片|图|第)\\s*(\\d+)\\s*(?:张|页|段)?(?:\\s*】)?\\s*[：:]\\s*(.*?)(?=\\n\\s*(?:【\\s*)?(?:图片|图|第)\\s*\\d+\\s*(?:张|页|段)?(?:\\s*】)?\\s*[：:]|$)', re.DOTALL)
    matches = pattern.findall(raw_text)
    print(f'[DEBUG] 正则匹配: {len(matches)}段')
    for mid, mtext in matches:
        parsed_id = int(mid)
        if parsed_id in valid_id_set:
            gid = parsed_id
        elif 1 <= parsed_id <= len(valid_ids):
            gid = valid_ids[parsed_id - 1]
            print(f'[DEBUG] 将批次内编号 图片{parsed_id} 映射为全局 图片{gid}')
        else:
            print(f'[WARN] 忽略不属于当前批次的图片编号：{parsed_id}')
            continue
        text = mtext.strip()
        text = re.sub('[*`]', '', text)
        if text:
            speech_dict[gid] = text
        print(f'[DEBUG] 图片{gid}: {text[:40]}...')
    if not speech_dict:
        paragraphs = [p.strip() for p in re.split('\\n\\s*\\n|\\r\\n\\s*\\r\\n', raw_text) if p.strip()]
        if len(paragraphs) == 1:
            paragraphs = [p.strip() for p in re.split('(?<=[。！？!?])\\s*\\n?', raw_text) if p.strip()]
        for info, paragraph in zip(image_info_list, paragraphs):
            text = re.sub('^[\\s\\d.、-]+', '', paragraph)
            text = re.sub('[*`]', '', text).strip()
            # 健壮性：若兜底片段仍是原始 agent 响应（整段 JSON，如含 video_filename/items），
            # 不要把它当成逐页话术，留给下方的 build_fallback_speech 处理。
            if text and not _looks_like_agent_json_fragment(text):
                speech_dict[info['global_id']] = text
                print(f"[DEBUG] 按顺序兜底解析 图片{info['global_id']}: {text[:40]}...")
    for info in image_info_list:
        gid = info['global_id']
        if gid not in speech_dict:
            speech_dict[gid] = build_fallback_speech(gid)
            print(f'[WARN] 图片{gid}话术缺失，使用兜底话术')
    _validate_speech_lengths(speech_dict)
    return {'speech': speech_dict, 'video_filename': video_filename}

def parse_speech_text(raw_text: str, image_info_list: List[Dict]) -> Dict[int, str]:
    """兼容旧调用：只返回每张图片的话术。"""
    return parse_agent_response(raw_text, image_info_list)['speech']

def generate_full_speech_result(image_info_list: List[Dict], progress_callback=None) -> Dict:
    """在同一 AI 会话中生成话术；按 API 上限每 10 张图片发送一条连续消息。"""
    global batches
    batches = split_image_batches(image_info_list)
    speech_dict = {}
    video_filename = ''
    session_id = str(uuid.uuid4())
    print(f'[INFO] 文稿生成会话：{session_id[:8]}…，共 {len(batches)} 条消息')
    for idx, batch in enumerate(batches):
        print(f'\n[INFO] === 第{idx + 1}/{len(batches)}批次，{len(batch)}张图片 ===')
        if callable(progress_callback):
            progress_callback(idx, len(batches), f'正在生成第 {idx + 1}/{len(batches)} 批文稿', False)
        batch_raw = call_agent_api_sse(
            batch,
            progress_callback=progress_callback,
            session_id=session_id,
            batch_index=idx,
            batch_count=len(batches),
        )
        print(f'[INFO] 本批次话术:\n{batch_raw}')
        batch_result = parse_agent_response(batch_raw, batch)
        speech_dict.update(batch_result['speech'])
        if not video_filename and batch_result.get('video_filename'):
            video_filename = batch_result['video_filename']
        if callable(progress_callback):
            progress_callback(idx + 1, len(batches), f'第 {idx + 1}/{len(batches)} 批文稿已完成', False)
    for info in image_info_list:
        gid = info['global_id']
        if gid not in speech_dict:
            speech_dict[gid] = build_fallback_speech(gid)
            print(f'[WARN] 图片{gid}话术缺失，使用兜底话术')
    print(f'\n[INFO] 话术生成完成，{len(speech_dict)}段')
    for gid in sorted(speech_dict.keys()):
        print(f'【图片{gid}】：{speech_dict[gid]}')
    if video_filename:
        print(f'[INFO] AI建议文件名：{video_filename}')
    return {'speech': speech_dict, 'video_filename': video_filename}

def generate_full_speech(image_info_list: List[Dict]) -> Dict[int, str]:
    """兼容旧调用：只返回话术字典。"""
    return generate_full_speech_result(image_info_list)['speech']

async def tts_single_paragraph(global_id: int, text: str, speech_rate: str='normal', speed: float=None) -> Dict:
    audio_path = os.path.join(OUTPUT_FOLDER, 'audio', f'{global_id}.mp3')
    submaker = edge_tts.SubMaker()
    text = text or ''
    if speed is not None:
        rate = _speed_to_edge_rate(speed)
    else:
        rate = SPEECH_RATE_VALUES.get(speech_rate, TTS_SPEED)
    if not text.strip():
        ffmpeg = resolve_ffmpeg()
        result = subprocess.run([ffmpeg, '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono', '-t', '1', '-codec:a', 'libmp3lame', audio_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **silent_subprocess_kwargs())
        if result.returncode != 0 or not os.path.isfile(audio_path):
            raise RuntimeError(f'空白页静音音频生成失败：{result.stderr.strip()[-500:]}')
        return {'global_id': global_id, 'text': text, 'audio_path': audio_path, 'duration_seconds': get_audio_duration(audio_path), 'submaker': submaker}
    last_error = None
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(normalize_special_pronunciation(text), VOICE, rate=rate)
            audio_bytes = 0
            with open(audio_path, 'wb') as f:
                async for chunk in communicate.stream():
                    if chunk['type'] == 'audio':
                        audio_bytes += len(chunk['data'])
                        f.write(chunk['data'])
                    elif chunk['type'] == 'WordBoundary':
                        submaker.feed(chunk)
            if audio_bytes == 0:
                raise Exception('Edge TTS未返回音频数据')
            last_error = None
            break
        except Exception as e:
            last_error = e
            try:
                if os.path.exists(audio_path):
                    os.remove(audio_path)
            except OSError:
                pass
            if attempt < 2:
                wait_seconds = 1.5 * (attempt + 1)
                print(f'[WARN] Edge TTS失败，{wait_seconds:.1f}秒后重试：{e}')
                await asyncio.sleep(wait_seconds)
    if last_error:
        if os.name == 'nt':
            print(f'[WARN] Edge TTS连续失败，切换Windows本地语音：{last_error}')
            synthesize_with_windows_sapi(text, audio_path, speech_rate)
        else:
            raise RuntimeError(f'Edge TTS 连续失败且无可用备选方案：{last_error}')
    duration_seconds = get_audio_duration(audio_path)
    text_length = len(text)
    print(f'[OK] 语音：图片{global_id} | {duration_seconds:.1f}秒 | 语速{rate} | 文本长度{text_length}字 | {text[:30]}...')
    return {'global_id': global_id, 'text': text, 'audio_path': audio_path, 'duration_seconds': duration_seconds, 'submaker': submaker}

def synthesize_with_windows_sapi(text: str, audio_path: str, speech_rate: str='normal') -> None:
    """Edge TTS不可用时，用Windows本地语音生成音频。仅 Windows 可用。"""
    if os.name != 'nt':
        raise RuntimeError('Windows 本地语音仅支持 Windows 平台')
    temp_dir = tempfile.gettempdir()
    temp_id = uuid.uuid4().hex
    wav_path = os.path.join(temp_dir, f'toolbax_{temp_id}.wav')
    text_path = os.path.join(temp_dir, f'toolbax_{temp_id}.txt')
    try:
        with open(text_path, 'w', encoding='utf-8') as f:
            f.write(normalize_special_pronunciation(text))
        synthesize_with_hidden_powershell(text_path, wav_path, speech_rate)
        ffmpeg = resolve_ffmpeg()
        convert = subprocess.run([ffmpeg, '-y', '-i', wav_path, '-codec:a', 'libmp3lame', audio_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **silent_subprocess_kwargs())
        if convert.returncode != 0:
            raise Exception(f'音频转换MP3失败: {convert.stderr.strip()}')
    finally:
        for path in (wav_path, text_path):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

def try_sapi_com(text: str, wav_path: str) -> None:
    """尝试用 SAPI COM 接口生成语音。仅 Windows 可用。"""
    if os.name != 'nt':
        return
    try:
        import win32com.client
    except ImportError:
        return
    try:
        speaker = win32com.client.Dispatch('SAPI.SpVoice')
        stream = win32com.client.Dispatch('SAPI.SpFileStream')
        stream.Open(os.path.abspath(wav_path), 3, False)
        try:
            speaker.AudioOutputStream = stream
            speaker.Speak(text, 0)
            speaker.WaitUntilDone(-1)
        finally:
            stream.Close()
    except Exception:
        return

def synthesize_with_hidden_powershell(text_path: str, wav_path: str, speech_rate: str='normal') -> None:
    """用 PowerShell 调用 System.Speech 生成语音。仅 Windows 可用。"""
    if os.name != 'nt':
        raise RuntimeError('PowerShell 语音合成仅支持 Windows 平台')
    ps_text_path = json.dumps(text_path)
    ps_wav_path = json.dumps(wav_path)
    sapi_rate = {'slow': -3, 'normal': 0, 'fast': 3}.get(speech_rate, 0)
    script = f"\n$ErrorActionPreference = 'Stop'\nAdd-Type -AssemblyName System.Speech\n$text = [IO.File]::ReadAllText({ps_text_path}, [Text.Encoding]::UTF8)\n$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer\n$voice = $speaker.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Culture.Name -eq 'zh-CN' }} | Select-Object -First 1\nif ($voice) {{ $speaker.SelectVoice($voice.VoiceInfo.Name) }}\n$speaker.Rate = {sapi_rate}\n$speaker.SetOutputToWaveFile({ps_wav_path})\n$speaker.Speak($text)\n$speaker.Dispose()\n"
    result = subprocess.run(['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-Command', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **silent_subprocess_kwargs())
    if result.returncode != 0:
        raise Exception(f'Windows本地语音失败: {result.stderr.strip()}')

def _wav_duration_local(wav_path: str) -> float:
    """用标准库 wave 读取 PCM WAV 时长（无需 torch，主进程侧可用）。失败时返回 0.0。

用于文件级续跑：上一轮 Worker 已把 WAV 落盘但主进程超时未写缓存元数据，
重跑时按文件名复用已落盘 WAV 并本地计算时长，避免重复合成。
"""
    try:
        import wave as _wave
        with _wave.open(wav_path, 'rb') as wf:
            n = wf.getnframes()
            sr = wf.getframerate()
            if sr and sr > 0:
                return float(n) / float(sr)
    except Exception:
        pass
    return 0.0

def _audio_duration_local(path: str) -> float:
    """格式无关音频时长探测：ffprobe(wav/mp3 通用) 优先，失败回退 stdlib wave(wav)。

格式无关时长探测，wav/mp3 通用；优先 ffprobe，失败回退 stdlib wave。
"""
    ffprobe = resolve_ffprobe()
    if ffprobe and os.path.isfile(path):
        try:
            out = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            if out.returncode == 0 and out.stdout.strip():
                return float(out.stdout.strip())
        except Exception:
            pass
    return _wav_duration_local(path)

def batch_generate_tts(speech_dict: Dict[int, str], image_info_list: List[Dict]=None, voice: str='default', data_root: str=None, speech_speed: float=1.0, progress_callback=None) -> List[Dict]:
    """生成全部配音（分段级缓存 + CosyVoice3 Worker 常驻）。

重构后流程：
1. 对每页文本用 TextSegmenter 分段
2. 对每段通过 TtsCacheManager 计算 cache_key
3. 检查缓存，收集 cache miss 段
4. miss 段通过对应引擎 Worker 的 synthesize 合成
5. 写入缓存
6. 失败重试：相同参数重试1次 -> 按逗号拆短重试1次 -> 仍失败返回错误
7. 禁止 Edge TTS 混入（Edge TTS 仅作为整个视频换默认声音的备用选项）

返回结构保持 {global_id, text, audio_path, duration_seconds, submaker}，
同时新增 all_segments 字段供字幕和视频合成使用。
"""
    init_output_folders()
    speech_speed = _clamp_speech_speed(speech_speed)
    data_root = data_root or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app_data')
    if not voice or voice == 'default':
        audio_info_list = _tts_edge_parallel(speech_dict, image_info_list, speech_speed)
        _sync_last_segments_from_audio_info(audio_info_list)
        return audio_info_list
    from voice_registry import VoiceRegistry
    from tts_cache import TtsCacheManager
    registry = VoiceRegistry(data_root)
    meta = registry.get_voice(voice)
    if meta is None or meta.type == 'cloud_parallel':
        return _tts_edge_parallel(speech_dict, image_info_list, speech_speed)
    if meta.type != 'cosyvoice3':
        print(f'[WARN] 音色类型 {meta.type} 未支持，回退 Edge TTS')
        return _tts_edge_parallel(speech_dict, image_info_list, speech_speed)
    ref_audio = ''
    if meta.ref_audio:
        if os.path.isabs(meta.ref_audio):
            ref_audio = meta.ref_audio
        else:
            ref_audio = os.path.join(registry.voice_dir, meta.ref_audio)
    if not ref_audio or not os.path.isfile(ref_audio):
        raise RuntimeError(f'参考音频文件不存在: {ref_audio}')
    segmenter = TextSegmenter()
    client = None
    cache_mgr = None
    ref_text_for_synth = ''
    from paths import PROJECT_ROOT, SRC_DIR
    if meta.type == 'cosyvoice3':
        from multi_tts_voice import MultiTtsWorkerClient, resolve_cosyvoice3_python
        cosy3_model = os.path.normpath(os.path.join(PROJECT_ROOT, 'tts_poc', 'models', 'CosyVoice3-0.5B'))
        client = MultiTtsWorkerClient.get_instance(engine_name='cosyvoice3', worker_path=os.path.join(SRC_DIR, 'tts_workers', 'cosyvoice3_worker.py'), venv_python=resolve_cosyvoice3_python(), model_dir=cosy3_model, engine_tag='cosyvoice3', data_root=data_root)
        cache_mgr = client.cache_manager
        ref_text_for_synth = meta.ref_text or ''
        client.set_ref_text(ref_text_for_synth)

    def _report(done, total, msg):
        if callable(progress_callback):
            try:
                progress_callback(int(done), int(total), msg)
            except Exception:
                pass
    all_segments = []
    for raw_id in sorted(speech_dict.keys()):
        global_id = int(raw_id)
        text = speech_dict[raw_id] or ''
        if not text.strip():
            continue
        segs = segmenter.segment(text, page_id=global_id)
        all_segments.extend(segs)
    if not all_segments:
        print('[WARN] 没有可合成的文本段')
        return []
    for seg in all_segments:
        seg.cache_key = cache_mgr.compute_key(seg.tts_text, ref_audio, speech_speed)
        seg.audio_path = os.path.join(OUTPUT_FOLDER, 'audio', f'page{seg.page_id}_seg{seg.segment_id}.wav')
    miss_segments = []
    for seg in all_segments:
        cached = cache_mgr.get(seg.cache_key)
        if cached is not None:
            seg.audio_path = cached.wav_path
            seg.audio_duration = cached.duration
            seg.status = 'cached'
        else:
            miss_segments.append(seg)
    total = len(all_segments)
    cached_count = total - len(miss_segments)
    print(f'[INFO] 分段配音: 总{total}段, 缓存命中{cached_count}, 待生成{len(miss_segments)}')
    _report(cached_count, total, f'缓存命中 {cached_count}/{total}，待生成 {len(miss_segments)} 段')
    if not miss_segments:
        return _build_audio_info_from_segments(all_segments, speech_dict, speech_speed)
    worker_segments = [{'segment_id': int(s.segment_id) + int(s.page_id) * 10000, 'text': s.tts_text, 'output_path': s.audio_path} for s in miss_segments]
    cache_keys = [s.cache_key for s in miss_segments]

    def _safe_synthesize(segs, keys):
        if not segs:
            return []
        try:
            return client.synthesize(segs, ref_audio, speech_speed, cache_keys=keys, ref_text=ref_text_for_synth)
        except Exception as e:
            print(f'[ERROR] synthesize 异常: {e}')
            return [{'segment_id': s['segment_id'], 'ok': False, 'error': str(e)[:300]} for s in segs]
    results = []
    _BATCH = 5
    for _b0 in range(0, len(worker_segments), _BATCH):
        _b1 = min(_b0 + _BATCH, len(worker_segments))
        _chunk = worker_segments[_b0:_b1]
        _chunk_keys = cache_keys[_b0:_b1]
        _chunk_segs = miss_segments[_b0:_b1]
        _pages = sorted({int(s.page_id) for s in _chunk_segs})
        _report(
            cached_count + _b0,
            total,
            f'正在生成第 {cached_count + _b0 + 1}-{cached_count + _b1}/{total} 段（页 {_pages}），首次使用需加载模型',
        )
        _chunk_res = _safe_synthesize(_chunk, _chunk_keys)
        if len(_chunk_res) == len(_chunk):
            results.extend(_chunk_res)
        else:
            _by_id = {r.get('segment_id'): r for r in _chunk_res if isinstance(r, dict)}
            for _s in _chunk:
                results.append(_by_id.get(_s['segment_id'], {'segment_id': _s['segment_id'], 'ok': False, 'error': 'Worker 未返回'}))
        _report(cached_count + _b1, total, f'合成 {cached_count + _b1}/{total}（页 {_pages}）')
    failed_segments = []
    failed_indices = []
    retry_results = []
    for i, res in enumerate(results):
        if not res.get('ok'):
            failed_segments.append(worker_segments[i])
            failed_indices.append(i)
    if failed_segments:
        print(f'[INFO] 首次合成失败 {len(failed_segments)} 段，重试中...')
        _report(total, total, f'有 {len(failed_segments)} 段首次生成失败，正在自动重试')
        retry_keys = [cache_keys[i] for i in failed_indices]
        retry_results = _safe_synthesize(failed_segments, retry_keys)
        still_failed = []
        still_failed_indices = []
        for j, res in enumerate(retry_results):
            orig_idx = failed_indices[j]
            results[orig_idx] = res
            if not res.get('ok'):
                still_failed.append(failed_segments[j])
                still_failed_indices.append(orig_idx)
        if still_failed:
            print(f'[INFO] 重试1仍失败 {len(still_failed)} 段，按逗号拆短重试...')
            _report(total, total, f'仍有 {len(still_failed)} 段未完成，正在拆成短句重试')
            short_segments = []
            short_map = []
            for k, seg in enumerate(still_failed):
                orig_idx = still_failed_indices[k]
                parts = re.split('([，、,])', seg['text'])
                sub_texts = []
                buf = ''
                for p in parts:
                    buf += p
                    if p in '，、,':
                        sub_texts.append(buf)
                        buf = ''
                if buf:
                    sub_texts.append(buf)
                if not sub_texts:
                    sub_texts = [seg['text']]
                for sub_idx, sub_text in enumerate(sub_texts):
                    sub_text = sub_text.strip()
                    if not sub_text:
                        continue
                    short_segments.append({'segment_id': int(seg['segment_id']) * 100 + sub_idx, 'text': sub_text, 'output_path': seg['output_path'].replace('.wav', f'_sub{sub_idx}.wav')})
                    short_map.append((orig_idx, sub_idx))
            if short_segments:
                short_keys = [cache_keys[oi] for oi, _ in short_map]
                short_results = _safe_synthesize(short_segments, short_keys)
                for k, (orig_idx, _) in enumerate(short_map):
                    if short_results[k].get('ok'):
                        results[orig_idx] = short_results[k]
    miss_idx = 0
    for seg in miss_segments:
        res = results[miss_idx]
        miss_idx += 1
        if res.get('ok'):
            seg.audio_path = res.get('wav_path', seg.audio_path)
            seg.audio_duration = res.get('duration', 0.0)
            seg.status = 'generated'
        else:
            seg.status = 'failed'
            error_msg = res.get('error', '未知错误')
            print(f'[ERROR] 段 page={seg.page_id} seg={seg.segment_id} 合成失败: {error_msg}')
            print(f'  文本: {seg.subtitle_text[:50]}...')
    generated_count = sum((1 for s in all_segments if s.status == 'generated'))
    failed_count = sum((1 for s in all_segments if s.status == 'failed'))
    print(f'[INFO] 分段配音完成: 生成{generated_count}, 失败{failed_count}')
    _report(total, total, f'分段配音完成：生成 {generated_count} 段，失败 {failed_count} 段')
    return _build_audio_info_from_segments(all_segments, speech_dict, speech_speed)

def _build_audio_info_from_segments(all_segments, speech_dict, speech_speed) -> List[Dict]:
    """从 SegmentData 列表构建 audio_info_list（兼容旧接口）。

同时将 all_segments 存入模块级变量供字幕和视频合成使用。
"""
    global _last_all_segments
    _last_all_segments = all_segments
    page_durations = {}
    page_audio_paths = {}
    for seg in all_segments:
        page_durations.setdefault(seg.page_id, 0.0)
        page_durations[seg.page_id] += seg.audio_duration
        if seg.page_id not in page_audio_paths:
            page_audio_paths[seg.page_id] = seg.audio_path
    audio_info_list = []
    for raw_id in sorted(speech_dict.keys()):
        global_id = int(raw_id)
        text = speech_dict[raw_id]
        if global_id in page_durations:
            duration = page_durations[global_id]
            audio_path = page_audio_paths[global_id]
        else:
            duration = 0.0
            audio_path = ''
        audio_info_list.append({'global_id': global_id, 'text': text, 'audio_path': audio_path, 'duration_seconds': duration, 'submaker': None, 'speech_speed': speech_speed})
    return audio_info_list

def _sync_last_segments_from_audio_info(audio_info_list):
    """Edge 默认路径（不经 _build_audio_info_from_segments）补充分段视图。

``batch_generate_tts`` 走 Edge 默认音色时直接返回 ``audio_info_list``，
不会填充模块级 ``_last_all_segments``；而 web_server 的 quick/batch 端点与
``generate_video`` 都依赖该变量取段。这里从 ``audio_info_list`` 重建
SegmentData 列表，使各通道行为一致（与 generate_video 内的重建逻辑同构）。
"""
    global _last_all_segments
    segs = []
    for audio_info in audio_info_list:
        if not isinstance(audio_info, dict):
            continue
        audio_path = audio_info.get('audio_path', '')
        if not audio_path:
            continue
        seg = SegmentData()
        seg.page_id = int(audio_info.get('global_id', 0))
        seg.segment_id = 0
        seg.tts_text = audio_info.get('text', '')
        seg.subtitle_text = audio_info.get('text', '')
        seg.audio_path = audio_path
        seg.audio_duration = float(audio_info.get('duration_seconds', 0.0) or 0.0)
        seg.status = 'generated'
        segs.append(seg)
    _last_all_segments = segs
_last_all_segments = []

async def _tts_edge_parallel_async(speech_dict, info_by_id, speech_speed=1.0):
    sem = asyncio.Semaphore(min(len(speech_dict), 8))

    async def _one(global_id, text, speed):
        async with sem:
            return await tts_single_paragraph(global_id, text, speed=speed)
    tasks = [_one(gid, speech_dict[gid], speech_speed) for gid in sorted(speech_dict.keys())]
    return await asyncio.gather(*tasks)

def _tts_edge_parallel(speech_dict, info_by_id, speech_speed=1.0) -> List[Dict]:
    """默认路径：并发调用 Edge TTS（并发上限 min(段落数, 8)）。全局 speech_speed 统一应用。"""
    results = asyncio.run(_tts_edge_parallel_async(speech_dict, info_by_id, speech_speed))
    for item in results:
        item['speech_speed'] = speech_speed
    return results

def _tts_local_parallel(speech_dict, info_by_id, voice: str, data_root: str=None, speech_speed: float=1.0) -> List[Dict]:
    """本地 TTS 推理分支（旧版，已重构为 CosyVoice3 Worker 常驻模式）。

Edge TTS 不再作为逐段回退，只保留为整个视频换默认声音的备用。
"""
    raise RuntimeError('_tts_local_parallel 已重构，请使用 batch_generate_tts 直接调用 CosyVoice3 Worker')

def generate_srt_subtitle(audio_info_list: List[Dict]) -> str:
    """生成 SRT 字幕文件。

重构后委托给 SubtitleGenerator，基于 WAV 实际时长生成 SRT，
默认不加载 faster-whisper。如果使用了 CosyVoice3 分段配音，
则使用 _last_all_segments 中的段数据（含实际音频时长）。
"""
    global _last_all_segments
    if _last_all_segments:
        sub_gen = SubtitleGenerator()
        srt_path = os.path.join(OUTPUT_FOLDER, 'subtitle', 'full_subtitle.srt')
        sub_gen.generate(_last_all_segments, output_path=srt_path)
        print(f'[OK] 字幕文件（基于分段 WAV 时长）: {srt_path}')
        return srt_path
    srt_content = ''
    current_time_offset = 0
    sub_index = 1
    for audio_info in audio_info_list:
        text = audio_info['text']
        duration = audio_info['duration_seconds']
        start_time = current_time_offset
        end_time = current_time_offset + duration
        segments = build_subtitle_segments(text)
        if not segments:
            segments = [text]
        segment_durations = allocate_subtitle_durations(segments, duration)
        for seg_idx, segment in enumerate(segments):
            seg_start = start_time + sum(segment_durations[:seg_idx])
            seg_end = min(seg_start + segment_durations[seg_idx], end_time)
            srt_content += f'{sub_index}\n'
            srt_content += f'{_format_srt_time(seg_start)} --> {_format_srt_time(seg_end)}\n'
            srt_content += f'{segment.strip()}\n\n'
            sub_index += 1
        current_time_offset = end_time
    srt_path = os.path.join(OUTPUT_FOLDER, 'subtitle', 'full_subtitle.srt')
    with open(srt_path, 'w', encoding='utf-8') as f:
        f.write(srt_content.strip())
    print(f'[OK] 字幕文件: {srt_path}')
    return srt_path

def build_subtitle_segments(text: str) -> List[str]:
    raw_segments = split_text_by_punctuation(text)
    segments = []
    buffer = ''
    for segment in raw_segments:
        segment = segment.strip()
        if not segment:
            continue
        if buffer:
            combined = buffer + segment
            if len(strip_punctuation(buffer)) < 8 or len(combined) <= 26:
                buffer = combined
                continue
            segments.append(buffer)
            buffer = segment
        else:
            buffer = segment
        if len(strip_punctuation(buffer)) >= 8:
            segments.append(buffer)
            buffer = ''
    if buffer:
        if segments and len(strip_punctuation(buffer)) < 6:
            segments[-1] += buffer
        else:
            segments.append(buffer)
    return split_long_subtitle_segments(segments)

def split_long_subtitle_segments(segments: List[str], max_chars: int=34) -> List[str]:
    result = []
    for segment in segments:
        if len(segment) <= max_chars:
            result.append(segment)
            continue
        current = ''
        for char in segment:
            current += char
            if len(current) >= max_chars and char in '，、；：,;:':
                result.append(current.strip())
                current = ''
        if current.strip():
            result.append(current.strip())
    return result

def strip_punctuation(text: str) -> str:
    return re.sub('[，。！？；：、,.!?;:\\s——“”\\"\'（）()【】\\[\\]]', '', text)

def subtitle_weight(segment: str) -> float:
    base = len(strip_punctuation(segment))
    pause = 0.0
    if segment.endswith(('?', '!', '?', '.', '!', '?')):
        pause = 3.0
    elif segment.endswith((',', ';', ':', '?', ',', ';', ':')):
        pause = 1.2
    return max(4.0, base + pause)

def allocate_subtitle_durations(segments: List[str], total_duration: float) -> List[float]:
    if not segments:
        return []
    weights = [subtitle_weight(segment) for segment in segments]
    total_weight = sum(weights)
    durations = [total_duration * weight / total_weight for weight in weights]
    min_duration = 0.9
    if total_duration >= min_duration * len(segments):
        durations = [max(min_duration, duration) for duration in durations]
        scale = total_duration / sum(durations)
        durations = [duration * scale for duration in durations]
    drift = total_duration - sum(durations)
    durations[-1] += drift
    return durations

def allocate_aligned_subtitle_durations(segments: List[str], total_duration: float, audio_path: str=None) -> List[float]:
    """分配字幕时长（已简化：不再使用 whisper 对齐和 silence detect）。

直接委托给 allocate_subtitle_durations 按文本长度比例分配。
"""
    return allocate_subtitle_durations(segments, total_duration)

def split_text_by_punctuation(text: str) -> List[str]:
    """按中文标点符号分割文本为短句"""
    punctuation = '([。！？，；])'
    parts = re.split(punctuation, text)
    result = []
    i = 0
    while i < len(parts):
        if i + 1 < len(parts) and parts[i + 1] in '。！？，；':
            result.append(parts[i] + parts[i + 1])
            i += 2
        elif parts[i].strip():
            result.append(parts[i].strip())
            i += 1
        else:
            i += 1
    return result

def _parse_srt_time(time_str: str) -> float:
    parts = time_str.split(':')
    h = int(parts[0])
    m = int(parts[1])
    sec_ms = parts[2].split(',')
    s = int(sec_ms[0])
    ms = int(sec_ms[1]) if len(sec_ms) > 1 else 0
    return h * 3600 + m * 60 + s + ms / 1000

def _format_srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    h, remainder = divmod(total_ms, 3600 * 1000)
    m, remainder = divmod(remainder, 60 * 1000)
    s, ms = divmod(remainder, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'

def get_subtitle_font(size: int=42):
    """跨平台获取字幕字体。优先中文字体。"""
    font_candidates = _get_chinese_font_paths()
    for font_path in font_candidates:
        if os.path.exists(font_path):
            return ImageFont.truetype(font_path, size=size)
    return ImageFont.load_default()

def _get_chinese_font_paths() -> list:
    """返回当前平台可用的中文字体路径列表（按优先级排序）。"""
    if os.name == 'nt':
        return ['C:\\Windows\\Fonts\\msyh.ttc', 'C:\\Windows\\Fonts\\simhei.ttf', 'C:\\Windows\\Fonts\\simsun.ttc']
    elif sys.platform == 'darwin':
        return ['/System/Library/Fonts/PingFang.ttc', '/System/Library/Fonts/STHeiti Light.ttc', '/System/Library/Fonts/Hiragino Sans GB.ttc', '/Library/Fonts/Arial Unicode.ttf']
    else:
        return ['/usr/share/fonts/truetype/wqy/wqy-microhei.ttc', '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc', '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc']

def wrap_subtitle_text(text: str, max_chars: int=28) -> List[str]:
    lines = []
    current = ''
    for char in text.replace('\n', ''):
        current += char
        if len(current) >= max_chars or char in '。！？；':
            lines.append(current.strip())
            current = ''
    if current.strip():
        lines.append(current.strip())
    return lines[:2]

def create_subtitle_clip(text: str, start_time: float, end_time: float):
    font = get_subtitle_font()
    lines = wrap_subtitle_text(text)
    line_height = 54
    image_height = 48 + line_height * max(1, len(lines))
    image = Image.new('RGBA', (VIDEO_WIDTH, image_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    box_margin_x = 80
    box_top = 8
    box_bottom = image_height - 8
    draw.rounded_rectangle((box_margin_x, box_top, VIDEO_WIDTH - box_margin_x, box_bottom), radius=18, fill=(48, 48, 48, 95))
    y = 24
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=2)
        text_width = bbox[2] - bbox[0]
        x = (VIDEO_WIDTH - text_width) // 2
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
        y += line_height
    clip = ImageClip(np.array(image)).set_start(start_time).set_end(end_time)
    return clip.set_position(('center', VIDEO_HEIGHT - image_height - 70))

def generate_video(image_info_list: List[Dict], audio_info_list: List[Dict], srt_path: str=None, output_filename: str='output.mp4', include_subtitles: bool=True, mode: str='1080p') -> str:
    """合成视频（委托给 VideoComposer）。

重构后变化：
- 图片保持原始宽高比（letterbox，不拉伸）
- 删除 MIN/MAX 截断，画面时长 = 配音时长 + 停顿
- 默认无转场，走 FFmpeg + NVENC
- 支持 720p / 1080p 输出模式
"""
    global _last_all_segments
    safe_output_filename = os.path.basename(output_filename) or 'output.mp4'
    if not safe_output_filename.lower().endswith('.mp4'):
        safe_output_filename += '.mp4'
    output_path = os.path.join(OUTPUT_FOLDER, 'video', safe_output_filename)
    cache_manager = None
    composer = VideoComposer(cache_manager=cache_manager)
    if _last_all_segments:
        valid_image_infos = []
        for info in image_info_list:
            page_id = int(info.get('global_id', 0))
            has_audio = any((s.page_id == page_id and s.audio_duration > 0 for s in _last_all_segments))
            if has_audio:
                valid_image_infos.append(info)
        if not valid_image_infos:
            raise RuntimeError('没有可合成的页面')
        composer.compose(image_infos=valid_image_infos, all_segments=_last_all_segments, srt_path=srt_path if include_subtitles else None, output_path=output_path, mode=mode, include_subtitles=include_subtitles)
    else:
        from text_segmenter import SegmentData as _SegData
        edge_segments = []
        for audio_info in sorted(audio_info_list, key=lambda x: x['global_id']):
            page_id = int(audio_info['global_id'])
            text = audio_info.get('text', '')
            duration = audio_info.get('duration_seconds', 0.0)
            audio_path = audio_info.get('audio_path', '')
            if duration > 0 and audio_path:
                edge_segments.append(_SegData(page_id=page_id, segment_id=0, subtitle_text=text, tts_text=text, cache_key='', audio_path=audio_path, audio_duration=duration, status='generated'))
        valid_image_infos = []
        for info in image_info_list:
            page_id = int(info.get('global_id', 0))
            has_audio = any((s.page_id == page_id for s in edge_segments))
            if has_audio:
                valid_image_infos.append(info)
        if not valid_image_infos:
            raise RuntimeError('没有可合成的页面')
        composer.compose(image_infos=valid_image_infos, all_segments=edge_segments, srt_path=srt_path if include_subtitles else None, output_path=output_path, mode=mode, include_subtitles=include_subtitles)
    print(f'\n[OK] 视频输出: {output_path}')
    return output_path

def get_image_files(input_folder: str) -> List[str]:
    valid_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.bmp')
    image_files = []
    for file in sorted(os.listdir(input_folder)):
        if file.lower().endswith(valid_extensions):
            image_files.append(os.path.join(input_folder, file))
    return image_files

def main():
    print('=' * 50)
    print('    toolbax v3.1')
    print('=' * 50 + '\n')
    input_folder = input('图片文件夹路径（直接回车=images）：').strip()
    if not input_folder:
        input_folder = 'images'
    if not os.path.exists(input_folder):
        print(f'[ERROR] 文件夹不存在: {input_folder}')
        return
    image_files = get_image_files(input_folder)
    if not image_files:
        print('[ERROR] 未找到图片')
        return
    print(f'[INFO] 找到{len(image_files)}张图片')
    init_output_folders()
    try:
        print('\n--- 步骤1: 上传图片 ---')
        image_info_list = batch_upload_images(image_files)
        if not image_info_list:
            print('[ERROR] 图片上传全部失败')
            return
        print('\n--- 步骤2: 生成话术 ---')
        speech_result = generate_full_speech_result(image_info_list)
        speech_dict = speech_result['speech']
        print('\n--- 步骤3: 生成语音 ---')
        audio_info_list = batch_generate_tts(speech_dict)
        print('\n--- 步骤4: 生成字幕 ---')
        srt_path = generate_srt_subtitle(audio_info_list)
        print('\n--- 步骤5: 合成视频 ---')
        output_filename = speech_result.get('video_filename') or 'output'
        output_path = generate_video(image_info_list, audio_info_list, srt_path, output_filename=output_filename)
        print(f"\n{'=' * 50}")
        print(f'    完成！输出: {output_path}')
        print('=' * 50)
    except Exception as e:
        print(f'\n[ERROR] 失败: {e}')
        import traceback
        traceback.print_exc()
if __name__ == '__main__':
    main()
