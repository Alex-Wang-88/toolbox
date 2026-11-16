# -*- coding: utf-8 -*-
"""一句话自动编排：话题 → 检索素材 → 本地模型编排逐页文稿 → 素材转视频管线输入。

流程（只有两步依赖模型，其余是确定性管道）::

    1. 意图抽取   0.8B 跑 CPU（num_gpu=0，显存 0 占用），抽出主题与搜索词；
                  模型不可用时退化为正则抽取，只损失搜索词质量
    2. 素材检索   DuckDuckGo 文本快照 + 必应图片搜索（免 key；必应按 TLS 指纹
                  区分客户端，urllib 会拿到降级页，因此 bing 端点直接走 curl）
    3. 逐页编排   9B 跑 GPU，keep_alive=0 —— 编排完成立即卸载显存，把 8GB 显存
                  完整让给后续的视频生成任务（GPT-SoVITS/Qwen3-ASR/生图互斥）
    4. 校验对齐   页数=图数、文件幻觉、字数、importance 枚举、HTML 实体清理

产物与 ``POST /api/generate`` 的请求体字段完全一致（image_items + manuscript），
前端确认后直接提交既有管线，无需新合成逻辑。

设计取舍：
    - 编排走 RAG 模式：文稿只允许基于检索资料改写，禁止模型凭记忆创作，
      这是 9B q4 量化模型可用性的前提（实测 0.8B 无法胜任编排步）。
    - 编排是固定流水线，不做自由 agent 循环：每步单次调用 + 代码校验。
"""
from __future__ import annotations

import html as html_module
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# 配置（环境变量可在 .env 覆盖）
# ---------------------------------------------------------------------------
OLLAMA_URL = os.getenv("TOOLBOX_OLLAMA_URL", "http://127.0.0.1:11434")
INTENT_MODEL = os.getenv("TOOLBOX_TOPIC_INTENT_MODEL", "qwen35-08b-base:latest")
COMPOSE_MODEL = os.getenv("TOOLBOX_TOPIC_MODEL", "qwen3.5:9b-q4_K_M")
IMAGE_COUNT = max(3, min(6, int(os.getenv("TOOLBOX_TOPIC_IMAGE_COUNT", "5") or 5)))
INTENT_TIMEOUT = float(os.getenv("TOOLBOX_TOPIC_INTENT_TIMEOUT", "90"))
COMPOSE_TIMEOUT = float(os.getenv("TOOLBOX_TOPIC_COMPOSE_TIMEOUT", "300"))
DOWNLOAD_TIMEOUT = float(os.getenv("TOOLBOX_TOPIC_DOWNLOAD_TIMEOUT", "15"))

IMPORTANCE = {"low", "normal", "high", "key"}
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
CURL = shutil.which("curl")
MIN_TEXT_SNIPPETS = 3
MIN_IMAGES = 3

_INTENT_FALLBACK_RE = re.compile(
    r"帮我|麻烦|请|给我|做一个|做个|制作一个|生成一个|生成|制作|介绍|关于|"
    r"讲解|的?视频|的?短片|一下|看看|一个|做一做|[，。！？!?.]"
)


class TopicVideoError(RuntimeError):
    """带用户可读信息的流程失败，API 层直接透出 message。"""


# ---------------------------------------------------------------------------
# 环境检查
# ---------------------------------------------------------------------------
def _ollama_get(path: str, timeout: float = 8.0):
    req = urllib.request.Request(OLLAMA_URL.rstrip("/") + path)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def check_environment() -> dict:
    """Ollama 在线性与模型就绪检查，返回可直接展示给用户的结果。"""
    info = {"ollama_ok": False, "intent_model_ok": False, "compose_model_ok": False,
            "intent_model": INTENT_MODEL, "compose_model": COMPOSE_MODEL, "errors": []}
    try:
        tags = _ollama_get("/api/tags")
        info["ollama_ok"] = True
        names = {m.get("name", "") for m in tags.get("models", [])}
        base_names = {n.split(":")[0] for n in names}
        info["intent_model_ok"] = INTENT_MODEL in names or INTENT_MODEL.split(":")[0] in base_names
        info["compose_model_ok"] = COMPOSE_MODEL in names or COMPOSE_MODEL.split(":")[0] in base_names
        if not info["intent_model_ok"]:
            info["errors"].append(f"未找到意图模型 {INTENT_MODEL}，将退化为正则抽取搜索词（ollama pull {INTENT_MODEL}）")
        if not info["compose_model_ok"]:
            info["errors"].append(f"未找到编排模型 {COMPOSE_MODEL}，无法编排文稿（ollama pull {COMPOSE_MODEL}）")
        if not CURL:
            info["errors"].append("未找到 curl，必应图片检索不可用（Windows 10+ 自带 curl.exe）")
    except Exception as exc:
        info["errors"].append(f"Ollama 服务不可用（{OLLAMA_URL}）：{exc}。请先启动 ollama serve")
    return info


# ---------------------------------------------------------------------------
# 模型调用
# ---------------------------------------------------------------------------
def chat(model: str, system: str, user: str, *, num_gpu: int = 1,
         keep_alive: str | None = None, timeout: float = COMPOSE_TIMEOUT) -> str:
    """调用 Ollama chat。keep_alive="0" 表示本次回答后立即卸载显存。"""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_ctx": 8192, "num_gpu": num_gpu},
    }
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    req = urllib.request.Request(
        OLLAMA_URL.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data.get("message", {}).get("content", "")


def extract_json(text: str) -> dict | None:
    """容错抽取 JSON 对象：剥 think 块/围栏 → 整体解析 → 平衡扫描 → 补漏括号。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"```[a-zA-Z]*", "", text).strip().strip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
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
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                raw = text[start:i + 1]
                for attempt in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):
                    try:
                        return json.loads(attempt)
                    except json.JSONDecodeError:
                        continue
                return _repair_unclosed(text[start:])
    # 循环走完仍未闭合：按未闭合栈补全（小模型漏写最外层括号的兜底）
    return _repair_unclosed(text[start:])


def _repair_unclosed(fragment: str) -> dict | None:
    """小模型偶发漏写闭合括号：按未闭合栈补全后重试。"""
    if '"' in fragment and fragment.count('"') % 2 == 1:
        fragment += '"'
    closers = {"{": "}", "[": "]"}
    stack, in_str, esc = [], False, False
    for ch in fragment:
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
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack or closers.get(stack.pop()) != ch:
                return None
    if not stack:
        return None
    try:
        return json.loads(fragment + "".join(closers[c] for c in reversed(stack)))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# 步骤1：意图抽取
# ---------------------------------------------------------------------------
INTENT_SYSTEM = """你是视频助手的意图解析器。用户想要自动制作一个"介绍某主题"的讲解视频。
只输出一行JSON：
{"topic": "要介绍的主题（用于搜索）", "queries": ["搜索词1", "搜索词2", "搜索词3"], "pages": 期望页数(3-6)}
queries 用于百科式搜索，第一个就是主题本身，其余补充背景/成就/评价等角度。"""


def intent_from_text(text: str) -> dict:
    """无模型兜底：把「帮我做一个介绍XX的视频」剥壳出主题。质量低于模型抽取。"""
    topic = _INTENT_FALLBACK_RE.sub("", text).strip()
    if not topic:
        raise TopicVideoError("无法从输入中识别主题，请用「帮我做一个介绍×××的视频」这样的说法")
    return {"topic": topic[:30], "queries": [topic], "pages": None}


def parse_intent(model_output: str) -> dict | None:
    parsed = extract_json(model_output)
    if not parsed or not str(parsed.get("topic", "")).strip():
        return None
    queries = [str(q).strip() for q in (parsed.get("queries") or []) if str(q).strip()]
    topic = str(parsed["topic"]).strip()[:30]
    if topic and topic not in queries:
        queries.insert(0, topic)
    pages = parsed.get("pages")
    try:
        pages = max(3, min(6, int(pages))) if pages else None
    except (TypeError, ValueError):
        pages = None
    return {"topic": topic, "queries": (queries or [topic])[:3], "pages": pages}


def step_intent(request_text: str, *, model_ok: bool = True) -> tuple[dict, list[str]]:
    """意图抽取。模型不可用/输出非法时退化为正则，返回 (intent, warnings)。"""
    warnings = []
    if not model_ok:
        return intent_from_text(request_text), ["意图模型未就绪，已退化为关键词抽取"]
    try:
        raw = chat(INTENT_MODEL, INTENT_SYSTEM, request_text, num_gpu=0,
                   keep_alive="0", timeout=INTENT_TIMEOUT)
        intent = parse_intent(raw)
        if intent:
            return intent, warnings
        warnings.append("意图模型输出非法，已退化为关键词抽取")
    except Exception as exc:
        warnings.append(f"意图模型不可用（{exc}），已退化为关键词抽取")
    return intent_from_text(request_text), warnings


# ---------------------------------------------------------------------------
# 步骤2：素材检索（确定性管道，可整体替换为正式搜索 API）
# ---------------------------------------------------------------------------
def _http_get(url: str, timeout: float = DOWNLOAD_TIMEOUT) -> bytes | None:
    """urllib 优先；bing.com 直接走 curl（TLS 指纹不同，urllib 会拿到降级页）。"""
    if CURL and "bing.com" in url:
        return _curl_get(url, timeout)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept-Language": "zh-CN,zh;q=0.9"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return _curl_get(url, timeout) if CURL else None


def _curl_get(url: str, timeout: float) -> bytes | None:
    try:
        result = subprocess.run(
            [CURL, "-s", "-L", "--compressed", "-m", str(int(timeout)),
             "-A", USER_AGENT, "-H", "Accept-Language: zh-CN,zh;q=0.9", url],
            capture_output=True, timeout=timeout + 10)
        return result.stdout if result.returncode == 0 and result.stdout else None
    except Exception:
        return None


def _clean_fragment(fragment: str) -> str:
    return html_module.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def parse_text_results(page: str) -> list[dict]:
    """解析 DuckDuckGo HTML 端点的搜索结果 → [{title, snippet}]。"""
    out = []
    for m in re.finditer(r'class="result__a"[^>]*>(.*?)</a>.*?'
                         r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S):
        title, snippet = _clean_fragment(m.group(1)), _clean_fragment(m.group(2))
        if title and len(snippet) > 30:
            out.append({"title": title[:60], "snippet": snippet[:300]})
    return out


def parse_text_results_sogou(page: str) -> list[dict]:
    """搜狗 HTML 兜底解析。"""
    out = []
    for m in re.finditer(r'vr-title[^>]*><a[^>]*>(.*?)</a>(.*?)</(?:div|h3)>', page, re.S):
        title = _clean_fragment(m.group(1))
        body = re.search(r"<p[^>]*>(.*?)</p>", m.group(2), re.S)
        snippet = _clean_fragment(body.group(1)) if body else ""
        if title and len(snippet) > 30:
            out.append({"title": title[:60], "snippet": snippet[:300]})
    return out


def search_text(queries: list[str]) -> list[dict]:
    """文本检索：DDG 优先，搜狗兜底。产品化可替换为正式搜索 API，接口不变。"""
    results: list[dict] = []
    for q in queries[:3]:
        page_bytes = _http_get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q))
        page = (page_bytes or b"").decode("utf-8", errors="ignore")
        results.extend(parse_text_results(page))
        if not results:
            page_bytes = _http_get("https://www.sogou.com/web?query=" + urllib.parse.quote(q))
            page = (page_bytes or b"").decode("utf-8", errors="ignore")
            results.extend(parse_text_results_sogou(page))
        if len(results) >= 6:
            break
    # 去重（不同 query 可能命中同一条）
    seen, unique = set(), []
    for item in results:
        key = item["title"]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:8]


def parse_image_urls(page: str) -> list[dict]:
    """解析必应图片搜索页 → [{url, title}]（m="{...}" 属性，内层 JSON 转义）。"""
    out = []
    for m in re.finditer(r'm="([^"]+)"', page):
        item = m.group(1)
        url_m = re.search(r"murl&quot;:&quot;(https?://[^&]+?)&quot;", item)
        if not url_m:
            continue
        title_m = re.search(r"t&quot;:&quot;([^&]{2,80})&quot;", item)
        out.append({"url": html_module.unescape(url_m.group(1)),
                    "title": html_module.unescape(title_m.group(1)).strip() if title_m else ""})
    return out


def download_image(url: str) -> bytes | None:
    data = _http_get(url, timeout=12)
    if not data or len(data) < 15000 or len(data) > 8_000_000:
        return None
    if data[:2] == b"\xff\xd8":
        return data  # JPEG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data  # PNG
    return None


def search_images(queries: list[str], save_dir: str, want: int = IMAGE_COUNT) -> list[dict]:
    """必应图片搜索并下载，返回 [{file, name, source_title, source_url}]。"""
    os.makedirs(save_dir, exist_ok=True)
    got, seen = [], set()
    for q in queries[:3]:
        page_bytes = _http_get("https://cn.bing.com/images/search?q="
                               + urllib.parse.quote(q) + "&first=1")
        page = (page_bytes or b"").decode("utf-8", errors="ignore")
        for item in parse_image_urls(page):
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            data = download_image(item["url"])
            if not data:
                continue
            ext = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
            idx = len(got) + 1
            name = f"img_{idx:02d}{ext}"
            path = os.path.join(save_dir, name)
            with open(path, "wb") as fh:
                fh.write(data)
            got.append({"file": path, "name": name,
                        "source_title": item["title"][:60], "source_url": item["url"]})
            if len(got) >= want:
                return got
    return got


# ---------------------------------------------------------------------------
# 步骤3：逐页编排（核心模型步骤）
# ---------------------------------------------------------------------------
COMPOSE_SYSTEM = """你是讲解视频的编排师。你会拿到：主题、检索到的资料（标题+摘要）、一组已下载的图片（文件名+图片内容描述）。
任务：编排出与图片一一对应的逐页讲解文稿，输出一行JSON（禁止markdown）：
{"title": "视频标题", "pages": [{"file": "图片文件名", "text": "这一页的口语化讲解词", "importance": "low|normal|high|key"}]}
硬性要求：
1. pages 数量必须等于图片数量，file 只能用给定文件名，一个不落。
2. text 是解说词（不是图片描述），口语化、信息基于给定资料，禁止编造资料里没有的事实；每页60~140字。
3. 解说词不要出现"这张图片""如图"等指代画面的说法，观众听的时候画面自然配合。
4. 按叙事顺序排：开场→主体→收尾；最重要的页面标 key，次重点 high，细节 low。
5. 首页 text 开头用一句话点题，末页做总结。"""


def compose_pages(topic: str, texts: list[dict], images: list[dict]) -> dict | None:
    """调用 9B 编排。GPU 上跑，keep_alive=0 —— 返回后显存立即释放给后续任务。"""
    material = "\n".join(f"- [{t['title']}] {t['snippet']}" for t in texts) or "（无文本资料）"
    img_lines = "\n".join(f"- {i['name']}：{i.get('source_title') or '（无描述）'}" for i in images)
    user = f"主题：{topic}\n\n【检索资料】\n{material[:4000]}\n\n【可用图片】\n{img_lines}"
    raw = chat(COMPOSE_MODEL, COMPOSE_SYSTEM, user, num_gpu=1, keep_alive="0")
    return extract_json(raw)


# ---------------------------------------------------------------------------
# 步骤4：校验与产物
# ---------------------------------------------------------------------------
def validate_plan(plan: dict | None, images: list[dict], topic: str = "") -> tuple[list[str], list[str], dict]:
    """返回 (errors, warnings, stats)。errors 非空即失败。"""
    errors: list[str] = []
    warnings: list[str] = []
    if not plan:
        return ["模型编排输出无法解析为 JSON"], warnings, {}
    pages = plan.get("pages")
    files = [i["name"] for i in images]
    if not isinstance(pages, list) or len(pages) != len(images):
        got = len(pages) if isinstance(pages, list) else "非列表"
        return [f"页数与图片数不一致：期望 {len(images)}，实得 {got}"], warnings, {}
    stats = {"pages": len(pages), "chars": 0, "hallucinated_files": [],
             "bad_importance": [], "short_pages": 0}
    full_text = []
    for idx, page in enumerate(pages):
        if not isinstance(page, dict) or not isinstance(page.get("text"), str):
            errors.append(f"第 {idx + 1} 页结构无效")
            continue
        name = os.path.basename(str(page.get("file", "")))
        if name not in files:
            stats["hallucinated_files"].append(name or "（空）")
        if page.get("importance") not in IMPORTANCE:
            stats["bad_importance"].append(str(page.get("importance")))
        length = len(page["text"].strip())
        stats["chars"] += length
        if length < 25:
            stats["short_pages"] += 1
        full_text.append(page["text"])
    if stats["hallucinated_files"]:
        errors.append(f"编排引用了不存在的图片文件：{stats['hallucinated_files'][:3]}")
    if stats["bad_importance"]:
        errors.append(f"importance 取值非法：{stats['bad_importance'][:3]}")
    if stats["short_pages"]:
        errors.append(f"{stats['short_pages']} 页讲解词过短（少于25字）")
    if stats["chars"] < len(files) * 40:
        errors.append(f"讲解词总字数过少（{stats['chars']} 字）")
    # 软校验：主题词覆盖
    if full_text and not any(topic[:2] in text for text in full_text):
        warnings.append("讲解词中未覆盖主题关键词，请人工确认文稿相关性")
    return errors, warnings, stats


def build_pipeline_input(topic: str, plan: dict, images: list[dict]) -> dict:
    """把编排结果对齐为 /api/generate 的请求体。pages 按叙事顺序、图片按下载顺序，
    以 file 名对齐；importance 挂在 image_items 上，manuscript 只存 text。"""
    pages = [p for p in plan.get("pages", []) if isinstance(p, dict)]
    page_by_file = {os.path.basename(str(p.get("file", ""))): p for p in pages}

    def page_field(image: dict, key: str, default: str) -> str:
        page = page_by_file.get(image["name"])
        value = str(page.get(key, default)) if page else default
        return html_module.unescape(value.strip()) if key == "text" else (value or default)

    image_items = [
        {"file": img["file"], "name": img["name"],
         "importance": page_field(img, "importance", "normal") or "normal",
         "transition": "none"}
        for img in images
    ]
    manuscript = [{"text": page_field(img, "text", "")} for img in images]
    return {
        "topic": topic,
        "title": html_module.unescape(str(plan.get("title", "")).strip()) or topic,
        "image_items": image_items,
        "manuscript": manuscript,
        "voice": "default",
        "speech_speed": 1.0,
        "subtitle_mode": "on",
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_topic_pipeline(request_text: str, *, page_count: int | None = None,
                       progress=None, cancelled=None) -> dict:
    """端到端编排。progress(pct, message, stage) 汇报进度；cancelled() 抛异常即取消。

    返回与 /api/generate 兼容的请求体 + 展示元数据（images/warnings/env）。
    """
    def report(pct, message, stage=None):
        if progress:
            progress(pct, message, stage)

    def guard():
        if cancelled:
            cancelled()

    report(5, "检查本地模型环境...", stage="prepare")
    env = check_environment()
    if not env["compose_model_ok"]:
        raise TopicVideoError(env["errors"][0] if env["errors"] else "编排模型不可用")

    report(10, "解析视频主题...", stage="intent")
    guard()
    intent, warnings = step_intent(request_text, model_ok=env["intent_model_ok"])
    topic, queries = intent["topic"], intent["queries"]
    want = page_count or intent.get("pages") or IMAGE_COUNT
    report(15, f"主题「{topic}」，搜索词：{'、'.join(queries)}", stage="intent")

    report(20, "检索文字资料...", stage="search_text")
    guard()
    texts = search_text(queries)
    if len(texts) < MIN_TEXT_SNIPPETS:
        raise TopicVideoError(f"文字资料检索不足（{len(texts)} 条），无法保证文稿真实性，请稍后重试或换更常见的说法")
    report(35, f"检索到 {len(texts)} 条资料", stage="search_text")

    report(40, "搜索并下载配图...", stage="search_images")
    guard()
    images_dir = images_root_dir(topic)
    images = search_images(queries, images_dir, want=want)
    if len(images) < MIN_IMAGES:
        raise TopicVideoError(f"可用配图不足（{len(images)}/{MIN_IMAGES}），请稍后重试或更换主题关键词")
    report(60, f"已下载 {len(images)} 张配图", stage="search_images")

    report(65, "本地模型编排逐页文稿（显存用后即释）...", stage="compose")
    guard()
    plan = compose_pages(topic, texts, images)
    report(90, "编排完成，正在校验...", stage="validate")
    guard()
    errors, soft_warnings, stats = validate_plan(plan, images, topic)
    warnings.extend(soft_warnings)
    if errors:
        raise TopicVideoError("编排校验未通过：" + "；".join(errors))

    payload = build_pipeline_input(topic, plan, images)
    payload["confirm_large"] = True
    report(100, "编排完成", stage="completed")
    # 预览 URL 相对产物根（output/topic_video/），与 /api/topic-video/image/<rel> 一致
    asset_root = os.path.dirname(os.path.dirname(images_dir))
    return {
        "topic": topic,
        "title": payload["title"],
        "queries": queries,
        "images": [_image_view(i, asset_root) for i in images],
        "image_items": payload["image_items"],
        "manuscript": payload["manuscript"],
        "generate_payload": payload,
        "stats": stats,
        "warnings": warnings,
        "images_dir": images_dir,
    }


def _image_view(image: dict, asset_root: str) -> dict:
    """前端预览用：相对产物根目录（output/topic_video/）的路径 + 站内预览 URL。"""
    rel = os.path.relpath(image["file"], asset_root).replace(os.sep, "/")
    return {"name": image["name"], "file": image["file"],
            "source_title": image.get("source_title", ""),
            "preview_url": "/api/topic-video/image/" + urllib.parse.quote(rel, safe="/")}


def images_root_dir(topic: str) -> str:
    """素材落盘目录（绝对路径，保证 /api/generate 的 os.path.isfile 校验可用）。"""
    from paths import OUTPUT_FOLDER
    safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", topic).strip("_")[:24] or "topic"
    return os.path.abspath(os.path.join(OUTPUT_FOLDER, "topic_video", safe, "images"))
