# -*- coding: utf-8 -*-
"""一句话自动编排（topic_video）单元测试。

原则：不联网、不依赖 Ollama——搜索解析器跑在真实结构截取的 fixture 上，
模型调用用 monkeypatch 替身并记录 keep_alive/num_gpu，验证显存释放策略。
需要真实模型与网络的端到端验证由 ``tests/e2e_topic_video.py`` 单独承担。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import topic_video
from topic_video import (
    TopicVideoError,
    build_pipeline_input,
    extract_json,
    intent_from_text,
    parse_image_urls,
    parse_intent,
    parse_text_results,
    run_topic_pipeline,
    validate_plan,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "topic_video")


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# JSON 抽取
# ---------------------------------------------------------------------------
def test_extract_json_plain():
    assert extract_json('{"tool": "tts"}') == {"tool": "tts"}


def test_extract_json_with_fences_and_think():
    raw = "<think>推理内容</think>```json\n{\"a\": 1}\n```"
    assert extract_json(raw) == {"a": 1}


def test_extract_json_missing_outer_brace_repaired():
    # 小模型偶发缺陷：嵌套对象漏写最外层闭合括号
    raw = '{"tool": "singing", "params": {"song": "a.wav"}, "reply": "好的。"'
    assert extract_json(raw) == {"tool": "singing", "params": {"song": "a.wav"}, "reply": "好的。"}


def test_extract_json_returns_none_on_garbage():
    assert extract_json("没有对象") is None
    assert extract_json('{"a": }') is None


# ---------------------------------------------------------------------------
# 意图解析
# ---------------------------------------------------------------------------
def test_intent_fallback_strips_shell_words():
    intent = intent_from_text("帮我做一个介绍张仲景的视频")
    assert intent["topic"] == "张仲景"
    assert intent["queries"] == ["张仲景"]


def test_intent_fallback_rejects_empty_topic():
    with pytest.raises(TopicVideoError):
        intent_from_text("帮我做一个视频")


def test_parse_intent_normalizes_queries_and_pages():
    parsed = parse_intent('{"topic":"张仲景","queries":["伤寒杂病论","医圣"],"pages":9}')
    assert parsed["topic"] == "张仲景"
    assert parsed["queries"][0] == "张仲景"          # 主题必须进搜索词
    assert parsed["pages"] == 6                      # 钳制到 3-6


def test_parse_intent_rejects_missing_topic():
    assert parse_intent('{"queries":["x"]}') is None


# ---------------------------------------------------------------------------
# 搜索解析器（真实结构 fixture）
# ---------------------------------------------------------------------------
def test_parse_text_results_on_ddg_fixture():
    results = parse_text_results(_fixture("ddg_results.html"))
    assert len(results) >= 1
    item = results[0]
    assert item["title"] and len(item["snippet"]) > 30
    assert "<" not in item["snippet"]                # 标签已剥离


def test_parse_image_urls_on_bing_fixture():
    items = parse_image_urls(_fixture("bing_images.html"))
    assert len(items) >= 2
    assert all(item["url"].startswith("http") for item in items)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def _images(n=3):
    return [{"file": f"/tmp/img_{i:02d}.jpg", "name": f"img_{i:02d}.jpg",
             "source_title": "图"} for i in range(1, n + 1)]


def _plan(files, texts=None, importance="normal"):
    texts = texts or [f"这是第{i+1}页的讲解词，围绕主题展开背景介绍与关键细节，"
                      f"补充资料中的重要事实，并保持口语化的表达节奏，方便配音朗读。"
                      for i in range(len(files))]
    return {"title": "测试视频", "pages": [
        {"file": f, "text": t, "importance": imp}
        for f, t, imp in zip(files, texts, [importance] * len(files))
    ]}


def test_validate_plan_pass():
    images = _images()
    errors, warnings, stats = validate_plan(_plan([i["name"] for i in images]), images)
    assert not errors
    assert stats["pages"] == 3
    assert stats["chars"] >= 120


def test_validate_plan_flags_hallucinated_file():
    errors, _, _ = validate_plan(_plan(["img_01.jpg", "img_02.jpg", "img_99.jpg"]), _images())
    assert any("不存在" in e for e in errors)


def test_validate_plan_flags_page_mismatch():
    errors, _, _ = validate_plan(_plan(["img_01.jpg", "img_02.jpg"]), _images())
    assert any("不一致" in e for e in errors)


def test_validate_plan_flags_bad_importance_and_short_text():
    images = _images(2)
    plan = _plan([i["name"] for i in images], texts=["短", "这是第二页，讲解词足够长，覆盖背景与细节。"],
                 importance="super")
    errors, _, _ = validate_plan(plan, images)
    assert any("importance" in e for e in errors)
    assert any("过短" in e for e in errors)


# ---------------------------------------------------------------------------
# 产物对齐（/api/generate 契约）
# ---------------------------------------------------------------------------
def test_build_pipeline_input_aligns_by_file_and_cleans_entities():
    images = _images(3)
    # 编排按叙事顺序引用图片（img_02 开场），与下载顺序不同
    plan = {"title": "标题&lt;b&gt;", "pages": [
        {"file": "img_02.jpg", "text": "开场词&quot;引号&quot;", "importance": "key"},
        {"file": "img_01.jpg", "text": "第二页内容", "importance": "normal"},
        {"file": "img_03.jpg", "text": "收尾总结", "importance": "low"},
    ]}
    payload = build_pipeline_input("张仲景", plan, images)
    # 契约字段
    assert set(payload) == {"topic", "title", "image_items", "manuscript",
                            "voice", "speech_speed", "subtitle_mode"}
    # 重要性跟随编排（按 file 对齐），而不是按数组顺序
    assert [it["importance"] for it in payload["image_items"]] == ["normal", "key", "low"]
    # 讲解词同样按 file 对齐
    assert payload["manuscript"][1]["text"] == "开场词\"引号\""      # HTML 实体已清理
    assert payload["title"] == "标题<b>"


def test_build_pipeline_input_defaults_importance_when_page_missing():
    images = _images(2)
    plan = {"title": "t", "pages": [{"file": "img_01.jpg", "text": "内容", "importance": "normal"}]}
    payload = build_pipeline_input("x", plan, images)
    assert payload["image_items"][1]["importance"] == "normal"
    assert payload["manuscript"][1]["text"] == ""


# ---------------------------------------------------------------------------
# 端到端（全 mock，不联网不加载模型）
# ---------------------------------------------------------------------------
class FakeChat:
    """替身 chat：记录调用参数，按序返回预设输出。"""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, model, system, user, *, num_gpu=1, keep_alive=None, timeout=None):
        self.calls.append({"model": model, "num_gpu": num_gpu, "keep_alive": keep_alive})
        return self.outputs.pop(0)


@pytest.fixture
def patched_searches(monkeypatch, tmp_path):
    texts = [{"title": "资料一", "snippet": "主题的背景介绍，包含足够长的摘要内容以供编排使用。"},
             {"title": "资料二", "snippet": "主题的关键成就与后世评价，摘要同样足够长。"},
             {"title": "资料三", "snippet": "主题的历史脉络与影响，内容充实可靠。"}]

    def fake_search_text(queries):
        return texts

    def fake_search_images(queries, save_dir, want=5):
        os.makedirs(save_dir, exist_ok=True)
        out = []
        for i in range(1, want + 1):
            path = os.path.join(save_dir, f"img_{i:02d}.jpg")
            with open(path, "wb") as fh:
                fh.write(b"\xff\xd8" + b"\x00" * 20000)      # 合法 JPEG 魔数
            out.append({"file": path, "name": f"img_{i:02d}.jpg", "source_title": "图", "source_url": "http://x"})
        return out

    monkeypatch.setattr(topic_video, "search_text", fake_search_text)
    monkeypatch.setattr(topic_video, "search_images", fake_search_images)
    monkeypatch.setattr(topic_video, "images_root_dir", lambda topic: str(tmp_path / "images"))
    return texts


def _patch_env(monkeypatch, env):
    monkeypatch.setattr(topic_video, "check_environment", lambda: env)


def _compose_output(files):
    pages = [{"file": f, "text": f"第{i+1}页：围绕主题的口语化讲解词，引用资料中的关键事实，长度六十个字以上，满足校验要求。",
              "importance": "normal"} for i, f in enumerate(files)]
    return json.dumps({"title": "自动视频", "pages": pages}, ensure_ascii=False)


def test_run_topic_pipeline_e2e_mocked(monkeypatch, patched_searches):
    env = {"ollama_ok": True, "intent_model_ok": True, "compose_model_ok": True, "errors": []}
    _patch_env(monkeypatch, env)
    fake = FakeChat([
        '{"topic":"张仲景","queries":["张仲景","医圣"],"pages":3}',
        _compose_output([f"img_{i:02d}.jpg" for i in range(1, 4)]),
    ])
    monkeypatch.setattr(topic_video, "chat", fake)

    progress_calls = []
    result = run_topic_pipeline("帮我做一个介绍张仲景的视频", page_count=3,
                                progress=lambda *a: progress_calls.append(a))
    # 显存策略：意图模型 CPU（num_gpu=0），编排模型 GPU + keep_alive=0 用后即卸
    assert fake.calls[0]["num_gpu"] == 0 and fake.calls[0]["keep_alive"] == "0"
    assert fake.calls[1]["num_gpu"] == 1 and fake.calls[1]["keep_alive"] == "0"
    # 产物契约
    assert result["topic"] == "张仲景"
    assert len(result["image_items"]) == 3 and len(result["manuscript"]) == 3
    assert result["generate_payload"]["image_items"] == result["image_items"]
    assert all(p["text"] for p in result["manuscript"])
    assert progress_calls, "必须汇报进度"
    assert progress_calls[-1][0] == 100


def test_run_topic_pipeline_falls_back_to_regex_intent(monkeypatch, patched_searches):
    env = {"ollama_ok": True, "intent_model_ok": False, "compose_model_ok": True,
           "errors": ["意图模型未拉取"]}
    _patch_env(monkeypatch, env)
    fake = FakeChat([_compose_output([f"img_{i:02d}.jpg" for i in range(1, 4)])])
    monkeypatch.setattr(topic_video, "chat", fake)

    result = run_topic_pipeline("帮我做一个介绍张仲景的视频", page_count=3)
    assert result["topic"] == "张仲景"
    assert any("意图模型" in w for w in result["warnings"])


def test_run_topic_pipeline_fails_when_compose_model_missing(monkeypatch):
    env = {"ollama_ok": True, "intent_model_ok": True, "compose_model_ok": False,
           "errors": ["未找到编排模型 qwen3.5:9b-q4_K_M（ollama pull）"]}
    _patch_env(monkeypatch, env)
    with pytest.raises(TopicVideoError):
        run_topic_pipeline("帮我做一个介绍张仲景的视频")


def test_run_topic_pipeline_fails_on_insufficient_text(monkeypatch, tmp_path):
    env = {"ollama_ok": True, "intent_model_ok": True, "compose_model_ok": True, "errors": []}
    _patch_env(monkeypatch, env)
    monkeypatch.setattr(topic_video, "search_text", lambda q: [{"title": "t", "snippet": "x" * 40}])
    monkeypatch.setattr(topic_video, "images_root_dir", lambda topic: str(tmp_path / "images"))
    with pytest.raises(TopicVideoError):
        run_topic_pipeline("帮我做一个介绍张仲景的视频")
