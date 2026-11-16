# -*- coding: utf-8 -*-
"""POC：本地小模型作为「工具箱自然语言入口」的第一个 Agent（意图路由 + 参数抽取）。

验证的问题：
    用户输入自然语言（+附件元数据）时，本地小模型能否稳定地
    1) 选对工具（对应 web_server.py 的各功能入口）
    2) 抽出关键参数（音色/语速/目标格式/文件）
    3) 在信息不足时反问（clarify）、在能力范围外时拒绝（reject）

测试矩阵：
    模型: qwen35-08b-base / qwen35-08b-daxiaojie-v3（用户微调）/ qwen3.5:9b-q4_K_M
    用例: 7 类 45 条（明确意图/参数抽取/附件依赖/歧义澄清/范围外/多意图/视觉）
    协议: Ollama /api/chat，think=false，temperature=0.1

运行（系统 Python 即可，仅标准库）::
    python scripts/poc_agent_router_test.py                # 全部模型
    python scripts/poc_agent_router_test.py --models qwen3.5:9b-q4_K_M
结果写入 output/agent_router_poc/（results.json + report.md）。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(PROJECT_ROOT, "output", "agent_router_poc")
OLLAMA_URL = "http://localhost:11434/api/chat"

DEFAULT_MODELS = [
    "qwen35-08b-base:latest",
    "qwen35-08b-daxiaojie-v3:latest",
    "qwen3.5:9b-q4_K_M",
]

# ---------------------------------------------------------------------------
# 路由器的系统提示词（三个模型共用，模拟未来产品里注入的真实工具清单与音色表）
# ---------------------------------------------------------------------------
ACTIVE_SPEC = ""  # main() 里按 --hardened 选择
RUN_CONFIG: dict = {"device": "gpu", "keep_alive": None}
TOOL_SPEC = """\
你是「音视频工具箱」的入口助手。用户用自然语言提需求，可能带附件。你要判断该调用哪个工具，并从对话里抽取参数。

## 可用工具
- video_from_material  素材转讲解视频：把图片/PDF/PPT/Word等素材变成带配音和字幕的讲解视频。参数: files(素材文件列表), voice(音色,可选), speed(语速倍率,可选)
- imagegen  AI生图：按文字描述生成图片。参数: prompt
- videogen  AI视频生成：按文字描述或单张首帧图片生成视频片段。参数: prompt, image(可选首帧)
- tts  语音合成：把一段文字读出来生成音频。参数: text(要读的文字), voice(音色,可选), speed(语速倍率,可选)
- singing  AI唱歌：把一首歌换成本地音色演唱，输入必须是歌曲音频。参数: song(歌曲文件), voice(音色,可选)
- audio_convert  音频格式转换：WAV/MP3/FLAC/M4A/AAC/OGG/Opus互转，支持NCM解码。参数: files(音频列表), format(目标格式)
- voice_manage  音色管理：上传参考音频创建克隆音色，或查看已有音色。参数: action(create|list), ref_audio(参考音频), name(新音色名)
- transcribe  音频转文稿：把录音等音频文件识别成文字稿。参数: file
- queue_status  查看任务队列和生成进度。参数: 无
- clarify  信息不足，需要向用户追问。参数: question(追问内容)
- reject  请求超出本工具箱能力。参数: reason

## 当前可用音色
晓晓(女声·温暖), 晓伊(女声·柔和), 云希(男声·年轻), 云健(男声·播音), 云扬(男声·新闻), 云夏(男童声), 小柔(本地克隆女声)

## 判断要点
- 用户没给必要信息（如要唱歌但没给歌曲文件、目标格式不明、多个文件用途不明）→ 用 clarify 追问，一次只问最关键的。
- 闲聊、问答、写作、订票等工具箱做不到的事 → 用 reject。
- 多个需求时，先输出第一个工具，把后续需求放进 followup 字段。

## 输出
只输出一行JSON，禁止解释、禁止markdown代码块：
{"tool": "工具名", "params": {...}}
可选附加字段: followup(后续步骤), reply(简短回应用户)
"""

# 加固版：针对 0.8B 的「编造参数、不敢拒绝、不会反问」缺陷做提示词治理
TOOL_SPEC_HARDENED = TOOL_SPEC.replace("""## 判断要点
- 用户没给必要信息（如要唱歌但没给歌曲文件、目标格式不明、多个文件用途不明）→ 用 clarify 追问，一次只问最关键的。
- 闲聊、问答、写作、订票等工具箱做不到的事 → 用 reject。
- 多个需求时，先输出第一个工具，把后续需求放进 followup 字段。""", """## 判断要点（必须遵守）
1. params 只能填用户明确说出的信息。严禁编造：没有提到的文件名不要写、用户没指定的音色不要填、用户说的话里没有的数字不要猜。
2. 需要输入文件但用户没给（如唱歌没给歌曲文件、创建音色没给参考音频）→ 用 clarify 一次问清最关键的一项。
3. 用户的输入不是「让工具箱做一件事」时——闲聊、提问、写文章、查资料、订票——一律 reject，哪怕你能回答。
4. 要朗读/生成的文字内容只取用户明确引用的部分，不要把用户的指令句混进 text/prompt。
5. 多个需求时，只输出第一个工具，其余放进 followup 字段。""")

VOICE_KEY_RE = re.compile(r"(voice|音色)", re.I)
FORMAT_ALIASES = {"wav": "wav", "wave": "wav", "mp3": "mp3", "m4a": "m4a", "aac": "aac",
                  "flac": "flac", "ogg": "ogg", "opus": "opus", "ncm": "ncm"}


# ---------------------------------------------------------------------------
# 测试用例。expect_tool: str 或 list（均可接受时用 list）；expect_params: 子集校验
# ---------------------------------------------------------------------------
def C(cid, cat, user, files=None, tool="tts", params=None, note=""):
    return {"id": cid, "category": cat, "user": user, "files": files or [],
            "expect_tool": tool, "expect_params": params or {}, "note": note}


CASES = [
    # A. 明确意图：每个工具至少一条
    C("A01", "明确意图", "把这些照片做成一个带解说的视频", ["photo1.jpg", "photo2.jpg"],
      "video_from_material", {}, "图片素材→主流程"),
    C("A02", "明确意图", "帮我生成一张图片：黄昏的海边有一座灯塔", [],
      "imagegen", {"prompt": "灯塔"}),
    C("A03", "明确意图", "帮我生成一段视频：一只柯基在草地上奔跑", [],
      "videogen", {"prompt": "柯基"}),
    C("A04", "明确意图", "把这段文字转成语音：欢迎来到我们的产品发布会", [],
      "tts", {"text": "欢迎来到"}),
    C("A05", "明确意图", "让小柔来唱这首歌", ["song.wav"],
      "singing", {"voice": "小柔"}),
    C("A06", "明确意图", "把这个音频转成WAV格式", ["audio.mp3"],
      "audio_convert", {"format": "wav"}),
    C("A07", "明确意图", "我想用这个音频创建一个新的克隆音色", ["ref.wav"],
      "voice_manage", {"action": "create"}),
    C("A08", "明确意图", "把这段会议录音整理成文字稿", ["meeting.m4a"],
      "transcribe", {}),
    C("A09", "明确意图", "现在还有哪些任务在排队？生成到哪一步了", [],
      "queue_status", {}),
    C("A10", "明确意图", "用云希的声音读一下这段通知：明天上午九点开会", [],
      "tts", {"voice": "云希", "text": "明天上午"}),
    C("A11", "明确意图", "把这份PPT做成带解说的视频", ["deck.pptx"],
      "video_from_material", {}),

    # B. 参数抽取
    C("B01", "参数抽取", "用晓晓的声音，1.2倍速读这段话：感谢大家的到来", [],
      "tts", {"voice": "晓晓", "speed": 1.2, "text": "感谢大家"}),
    C("B02", "参数抽取", "这三个文件都转成mp3", ["a.wav", "b.flac", "c.ncm"],
      "audio_convert", {"format": "mp3"}, "含NCM，仍应按格式转换处理"),
    C("B03", "参数抽取", "生成一张赛博朋克风格的城市夜景图", [],
      "imagegen", {"prompt": "城市"}),
    C("B04", "参数抽取", "这段歌用小柔唱，伴奏要保留", ["pop.mp3"],
      "singing", {"voice": "小柔"}),
    C("B05", "参数抽取", "参考这段音频创建音色，名字叫「老师的声音」", ["ref.wav"],
      "voice_manage", {"action": "create", "name": "老师"}),
    C("B06", "参数抽取", "慢一点读，0.8倍速：产品将于下月发布", [],
      "tts", {"speed": 0.8, "text": "产品将于"}),
    C("B07", "参数抽取", "把Word里的内容做成视频，配音用云扬，要有新闻播报的感觉", ["plan.docx"],
      "video_from_material", {"voice": "云扬"}),
    C("B08", "参数抽取", "列出我现在有哪些音色可以用", [],
      "voice_manage", {"action": "list"}),
    C("B09", "参数抽取", "帮我把这首歌的伴奏留着，用云夏的声音唱", ["pop2.wav"],
      "singing", {"voice": "云夏"}),
    C("B10", "参数抽取", "转成M4A", ["voice.wav"],
      "audio_convert", {"format": "m4a"}),

    # C. 附件类型决定路由
    C("C01", "附件依赖", "处理一下这个文件", ["report.pdf"],
      "video_from_material", {}, "宽松：PDF素材默认走主流程"),
    C("C02", "附件依赖", "帮我看看这个音频里说了什么", ["lecture.m4a"],
      "transcribe", {}),
    C("C03", "附件依赖", "把这个ncm解开", ["song.ncm"],
      "audio_convert", {"format": ["ncm", "wav"]}, "NCM解码也是audio_convert，解出WAV属合理默认"),
    C("C04", "附件依赖", "唱一下这首歌", ["song.wav"],
      "singing", {}, "不指定音色，默认音色即可，不应反问音色"),
    C("C05", "附件依赖", "帮我把这个视频配上解说音轨", ["clip.mp4"],
      ["clarify", "reject"], {}, "MP4配音不在工具箱能力内，应反问或拒绝"),
    C("C06", "附件依赖", "这些素材帮我做成视频，第二个是配乐", ["slides.pptx", "bgm.mp3"],
      "video_from_material", {}, "多附件中PPT是主体"),

    # D. 歧义 / 澄清 / 边界
    C("D01", "歧义澄清", "把它做成视频", ["photo.jpg"],
      ["videogen", "video_from_material"], {}, "单张图片→图生视频或讲解视频均可接受"),
    C("D02", "歧义澄清", "帮我处理一下", ["mix.ncm", "lyrics.txt"],
      "clarify", {}, "两种不同用途的文件，必须反问"),
    C("D03", "歧义澄清", "声音帮我弄得更好听一点", ["audio.wav"],
      ["clarify", "reject"], {}, "无美化工具"),
    C("D04", "歧义澄清", "帮我把歌词配一个唱的版本", ["lyrics.txt"],
      "clarify", {}, "歌词是文字，singing需要歌曲音频，应反问"),
    C("D05", "歧义澄清", "帮我写个PPT", [],
      "reject", {}, "没有PPT生成工具"),
    C("D06", "歧义澄清", "把这两段话分别用不同的声音读出来。第一段：欢迎各位嘉宾。第二段：会议到此结束。", [],
      "tts", {}, "应识别为批量/多段TTS"),

    # E. 范围外
    C("E01", "范围外", "今天天气怎么样", [], "reject", {}),
    C("E02", "范围外", "帮我写一篇关于人工智能的论文", [], "reject", {}),
    C("E03", "范围外", "1+1等于几", [], "reject", {}, "严格按spec应reject（产品上可另加聊天模式）"),
    C("E04", "范围外", "帮我下载一部电影", [], "reject", {}),
    C("E05", "范围外", "帮我订明天去上海的高铁票", [], "reject", {}),

    # F. 多意图
    C("F01", "多意图", "先用晓晓把这段话读出来：欢迎参加发布会；然后把生成的音频转成mp3", [],
      "tts", {"voice": "晓晓"}, "应先TTS，后转换放followup"),
    C("F02", "多意图", "把这个PDF做成视频，另外把配乐转成wav", ["report.pdf", "bgm.mp3"],
      "video_from_material", {}, "主流程优先，转换放followup"),
    C("F03", "多意图", "看看队列里有什么任务，然后告诉我视频好了没有", [],
      "queue_status", {}),

    # G. 视觉附件（9B 传真图，0.8B 只看到文件名）
    C("G01", "视觉附件", "把这个图变成会动的视频", ["cat.png"],
      "videogen", {}, "单张图片→图生视频"),
    C("G02", "视觉附件", "把这页内容做成讲解视频", ["slide.png"],
      "video_from_material", {}, "版式像PPT页→素材转视频"),
    C("G03", "视觉附件", "看看这张图里是什么，然后帮我把图里的主体抠出来换成白色背景", ["cat.png"],
      ["clarify", "reject"], {}, "抠图未列入工具清单，考察能否守住边界（宽松观察项）"),
]


# ---------------------------------------------------------------------------
# Ollama 调用与 JSON 解析
# ---------------------------------------------------------------------------
def call_ollama(model: str, user_content: str, images: list[str] | None = None,
                timeout: float = 180.0) -> tuple[float, str]:
    message = {"role": "user", "content": user_content}
    if images:
        message["images"] = [base64.b64encode(open(p, "rb").read()).decode() for p in images]
    options = {"temperature": 0.1, "num_ctx": 8192}
    if RUN_CONFIG.get("device") == "cpu":
        options["num_gpu"] = 0  # 纯 CPU 推理：显存 0 占用，任务期零冲突
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": ACTIVE_SPEC}, message],
        "stream": False,
        "think": False,
        "options": options,
    }
    if RUN_CONFIG.get("keep_alive") is not None:
        payload["keep_alive"] = RUN_CONFIG["keep_alive"]  # "0"=用完即卸, "2m"=空闲保留
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return time.time() - t0, data.get("message", {}).get("content", "")


def extract_json(text: str) -> dict | None:
    """容错抽取JSON对象：剥think块/代码围栏 → 整体解析 → 逐个平衡候选 → 宽松修复。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"```[a-zA-Z]*", "", text).strip().strip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    candidates, depth, in_str, esc, start = [], 0, False, False, -1
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidates.append(text[start:i + 1])
    for raw in candidates:
        for attempt in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    # 兜底：小模型常见缺陷是漏写最外层闭合括号——按未闭合的栈补全后重试
    if start >= 0 and depth > 0:
        tail = text[start:]
        if in_str:
            tail += '"'
        closers = {"{": "}", "[": "]"}
        stack = []
        ok, s2, e2, st2 = True, False, False, []
        for ch in tail:
            if s2:
                if e2:
                    e2 = False
                elif ch == "\\":
                    e2 = True
                elif ch == '"':
                    s2 = False
                continue
            if ch == '"':
                s2 = True
            elif ch in "{[":
                st2.append(ch)
            elif ch in "}]":
                if not st2 or closers[st2.pop()] != ch:
                    ok = False
                    break
        if ok and st2:
            tail += "".join(closers[c] for c in reversed(st2))
        try:
            return json.loads(tail)
        except json.JSONDecodeError:
            return None
    return None


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------
def norm_tool(value) -> str:
    if isinstance(value, dict):
        value = value.get("tool", "")
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def param_matches(key: str, expect, got) -> bool:
    if got is None:
        return False
    options = expect if isinstance(expect, list) else [expect]
    return any(_param_single(key, want, got) for want in options)


def _param_single(key: str, expect, got) -> bool:
    if key in ("voice",):
        return str(expect) in json.dumps(got, ensure_ascii=False)
    if key == "speed":
        try:
            return abs(float(str(got).rstrip("x倍速")) - float(expect)) <= 0.05
        except (TypeError, ValueError):
            return False
    if key == "format":
        return FORMAT_ALIASES.get(str(got).strip().lower(), str(got).strip().lower()) == expect
    if key in ("text", "prompt"):
        return str(expect) in str(got)
    if key == "action":
        return str(got).strip().lower() == expect
    if key == "name":
        return str(expect) in str(got)
    return True  # 其它键只要求出现


def grade(case: dict, parsed: dict | None, raw: str) -> dict:
    result = {"tool_ok": False, "params_ok": True, "parse_ok": parsed is not None,
              "tool_got": None, "param_fails": []}
    if not parsed:
        result["tool_got"] = f"<解析失败> {raw[:120]!r}"
        return result
    tool = norm_tool(parsed.get("tool"))
    result["tool_got"] = tool
    expect_tools = [norm_tool(t) for t in (case["expect_tool"] if isinstance(case["expect_tool"], list) else [case["expect_tool"]])]
    result["tool_ok"] = tool in expect_tools
    if result["tool_ok"]:
        params = parsed.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        for key, expect in case["expect_params"].items():
            # 顶层找不到时也看一眼整个输出（模型有时把参数放外层）
            got = params.get(key, parsed.get(key))
            if not param_matches(key, expect, got):
                result["params_ok"] = False
                result["param_fails"].append(f"{key}: 期望~{expect!r}, 实得={got!r}")
    return result


def build_user_content(case: dict) -> str:
    lines = [case["user"]]
    if case["files"]:
        meta = "\n".join(f"- {name}" for name in case["files"])
        lines.append(f"\n[用户附带文件]\n{meta}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 测试图片（G 组视觉用例）
# ---------------------------------------------------------------------------
def make_test_images() -> dict[str, str]:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("警告: 未安装 Pillow，视觉用例(G组)将只传文件名（等同无视觉模型条件）")
        return {}
    img_dir = os.path.join(OUT_DIR, "attachments")
    os.makedirs(img_dir, exist_ok=True)
    paths = {}
    cat = Image.new("RGB", (320, 320), (240, 240, 240))
    d = ImageDraw.Draw(cat)
    d.ellipse((80, 60, 240, 260), fill=(180, 120, 60))
    d.polygon([(90, 80), (130, 20), (150, 90)], fill=(180, 120, 60))
    d.polygon([(230, 80), (190, 20), (170, 90)], fill=(180, 120, 60))
    d.ellipse((130, 130, 160, 160), fill=(20, 20, 20))
    d.ellipse((190, 130, 220, 160), fill=(20, 20, 20))
    cat_path = os.path.join(img_dir, "cat.png")
    cat.save(cat_path)
    paths["cat.png"] = cat_path
    slide = Image.new("RGB", (960, 540), (255, 255, 255))
    d2 = ImageDraw.Draw(slide)
    d2.rectangle((0, 0, 960, 70), fill=(30, 90, 160))
    d2.text((40, 20), "Q3 产品规划汇报", fill=(255, 255, 255))
    d2.text((60, 140), "1. 本季度营收增长 23%", fill=(0, 0, 0))
    d2.text((60, 220), "2. 新用户留存率提升至 41%", fill=(0, 0, 0))
    d2.text((60, 300), "3. 下季度重点：海外市场拓展", fill=(0, 0, 0))
    slide_path = os.path.join(img_dir, "slide.png")
    slide.save(slide_path)
    paths["slide.png"] = slide_path
    return paths


VISION_MODELS_CACHE: set[str] = set()


def model_supports_vision(model: str) -> bool:
    """按 /api/tags 的 capabilities 判断，避免给纯文本模型传图触发 500。"""
    if not VISION_MODELS_CACHE:
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as resp:
                for m in json.loads(resp.read()).get("models", []):
                    caps = m.get("capabilities") or []
                    if "vision" in caps:
                        VISION_MODELS_CACHE.add(m["name"])
        except Exception:
            pass  # 查询失败就当无视觉，等价于只传文件名
    return model in VISION_MODELS_CACHE or model.split(":")[0] in VISION_MODELS_CACHE


def pass_images_for(model: str, case: dict, img_map: dict) -> list[str]:
    """G 组用例把真实图片喂给有视觉能力的模型；纯文本模型只看文件名。"""
    if case["category"] != "视觉附件" or not model_supports_vision(model):
        return []
    return [img_map[f] for f in case["files"] if f in img_map]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(model: str, cases: list, img_map: dict) -> list[dict]:
    rows = []
    print(f"\n===== {model} =====")
    try:  # 预热，避免冷加载污染首个用例的延迟统计
        call_ollama(model, "回复OK")
    except Exception as exc:
        print(f"预热失败: {exc}")
    for case in cases:
        content = build_user_content(case)
        try:
            dt, raw = call_ollama(model, content, images=pass_images_for(model, case, img_map))
            parsed = extract_json(raw)
            g = grade(case, parsed, raw)
            g.update(latency=dt, raw=raw[:400])
        except Exception as exc:  # 服务不可达等
            g = {"tool_ok": False, "params_ok": False, "parse_ok": False,
                 "tool_got": f"<异常> {exc}", "param_fails": [], "latency": 0.0, "raw": ""}
        status = "PASS" if g["tool_ok"] and g["params_ok"] else "FAIL"
        print(f"[{status}] {case['id']} {case['category']:<6} tool={g['tool_got']}  {g.get('latency', 0):.2f}s"
              + (f"  参数问题: {g['param_fails']}" if g["param_fails"] else ""))
        rows.append({"case": case, **g})
    return rows


def out_paths() -> tuple[str, str]:
    suffix = ("_hardened" if ACTIVE_SPEC == TOOL_SPEC_HARDENED else "") \
        + ("_cpu" if RUN_CONFIG.get("device") == "cpu" else "") \
        + (f"_ka{RUN_CONFIG['keep_alive']}" if RUN_CONFIG.get("keep_alive") else "")
    return (os.path.join(OUT_DIR, f"report{suffix}.md"),
            os.path.join(OUT_DIR, f"results{suffix}.json"))


def summarize(all_rows: dict[str, list]) -> str:
    lines = ["# 本地小模型路由 POC 结果\n"]
    categories = sorted({c["case"]["category"] for rows in all_rows.values() for c in rows})
    header = "| 用例类别 | " + " | ".join(all_rows.keys()) + " |"
    lines += [header, "|" + "---|" * (len(all_rows) + 1)]
    for cat in categories:
        cells = []
        for model, rows in all_rows.items():
            sub = [r for r in rows if r["case"]["category"] == cat]
            ok = sum(1 for r in sub if r["tool_ok"] and r["params_ok"])
            cells.append(f"{ok}/{len(sub)}")
        lines.append(f"| {cat} | " + " | ".join(cells) + " |")
    for metric in ("全部",):
        cells = []
        for model, rows in all_rows.items():
            ok = sum(1 for r in rows if r["tool_ok"] and r["params_ok"])
            parse_fail = sum(1 for r in rows if not r["parse_ok"])
            lat = [r["latency"] for r in rows if r["latency"]]
            cells.append(f"{ok}/{len(rows)} (解析失败{parse_fail}, 平均{sum(lat)/len(lat):.2f}s)")
        lines.append(f"| {metric} | " + " | ".join(cells) + " |")
    lines.append("")
    # 失败明细
    for model, rows in all_rows.items():
        fails = [r for r in rows if not (r["tool_ok"] and r["params_ok"])]
        if fails:
            lines.append(f"## {model} 失败明细 ({len(fails)})")
            for r in fails:
                c = r["case"]
                lines.append(f"- **{c['id']}** [{c['category']}] 期望 {c['expect_tool']}，实得 `{r['tool_got']}`")
                lines.append(f"  - 输入: {c['user']}" + (f" | 文件: {c['files']}" if c['files'] else ""))
                if r["param_fails"]:
                    lines.append(f"  - 参数: {r['param_fails']}")
                lines.append(f"  - 原始输出: `{(r.get('raw') or '')[:200]}`")
            lines.append("")
    report = "\n".join(lines)
    os.makedirs(OUT_DIR, exist_ok=True)
    report_path, results_path = out_paths()
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump({m: [{"id": r["case"]["id"], "user": r["case"]["user"],
                        "files": r["case"]["files"], "expect_tool": r["case"]["expect_tool"],
                        "tool_got": r["tool_got"], "tool_ok": r["tool_ok"], "params_ok": r["params_ok"],
                        "param_fails": r["param_fails"], "latency": r["latency"], "raw": r["raw"]}
                       for r in rows] for m, rows in all_rows.items()}, f, ensure_ascii=False, indent=1)
    return report


def main() -> int:
    global ACTIVE_SPEC
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    ap.add_argument("--hardened", action="store_true", help="使用加固版提示词（针对编造/拒绝/反问缺陷）")
    ap.add_argument("--device", choices=["gpu", "cpu"], default="gpu",
                    help="cpu=纯CPU推理（显存0占用，任务期零冲突，0.8B约1.1~1.5s/次）")
    ap.add_argument("--keep-alive", dest="keep_alive", default=None, metavar="DUR",
                    help="Ollama keep_alive：\"0\"=每次路由完立即卸载显存，\"2m\"=空闲2分钟后卸载")
    args = ap.parse_args()
    ACTIVE_SPEC = TOOL_SPEC_HARDENED if args.hardened else TOOL_SPEC
    RUN_CONFIG.update(device=args.device, keep_alive=args.keep_alive)

    # 预热：确认服务可用
    try:
        urllib.request.urlopen("http://localhost:11434/api/version", timeout=5)
    except Exception as exc:
        print(f"Ollama 未启动 (http://localhost:11434): {exc}\n先运行: ollama serve", file=sys.stderr)
        return 1

    img_map = make_test_images()
    all_rows = {}
    for model in args.models:
        all_rows[model] = run(model, CASES, img_map)
    report = summarize(all_rows)
    print("\n" + report)
    report_path, _ = out_paths()
    print(f"\n详细结果: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
