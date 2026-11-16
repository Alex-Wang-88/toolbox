# -*- coding: utf-8 -*-
"""一句话自动编排的 HTTP 接口（Blueprint）。

为避免 web_server.py 继续膨胀与循环导入，这里以依赖注入方式注册：
``register_topic_video(app, tasks=..., enqueue_task=..., ...)``。
任务执行沿用全站统一队列（tasks 字典 + update_task_progress），
前端用既有 ``GET /api/status/<task_id>`` 轮询即可。

端点：
    GET  /topic-video                      页面
    GET  /api/topic-video/env              环境自检（Ollama/模型/curl）
    POST /api/topic-video/plan             提交编排任务（异步，返回 task_id）
    GET  /api/topic-video/image/<path>     预览已下载的配图（限定产物根目录）
"""
from __future__ import annotations

import os
import uuid

from flask import Blueprint, jsonify, request, send_file

import topic_video
from paths import OUTPUT_FOLDER

blueprint = Blueprint("topic_video", __name__)

# 产物根目录（各主题素材的公共父目录），仅允许读取该目录内的文件
IMAGES_ROOT = os.path.realpath(os.path.join(OUTPUT_FOLDER, "topic_video"))

# 由 register_topic_video 注入的全站依赖
_state: dict = {}


def register_topic_video(app, *, tasks, enqueue_task, update_progress,
                         assert_not_cancelled, static_dir):
    """把全站任务队列依赖注入进来并挂载路由。"""
    _state.update(tasks=tasks, enqueue_task=enqueue_task, update_progress=update_progress,
                  assert_not_cancelled=assert_not_cancelled, static_dir=static_dir)
    app.register_blueprint(blueprint)


def _worker(task_id: str, request_text: str, page_count: int | None):
    tasks = _state["tasks"]
    update_progress = _state["update_progress"]

    def progress(pct, message, stage=None):
        update_progress(task_id, pct, message, stage=stage, log=True)

    def cancelled():
        _state["assert_not_cancelled"](task_id)

    try:
        tasks[task_id]["status"] = "processing"
        result = topic_video.run_topic_pipeline(request_text, page_count=page_count,
                                                progress=progress, cancelled=cancelled)
        tasks[task_id]["result"] = result
        tasks[task_id]["status"] = "completed"
        update_progress(task_id, 100, "编排完成，请确认文稿后生成视频", stage="completed", log=True)
    except Exception as exc:
        if tasks.get(task_id, {}).get("status") != "cancelled":
            tasks[task_id]["status"] = "failed"
            update_progress(task_id, tasks[task_id].get("progress", 0),
                            f"自动编排失败：{exc}", stage="failed", log=True)


@blueprint.route("/topic-video")
def topic_video_page():
    from flask import send_from_directory
    response = send_from_directory(_state["static_dir"], "topic_video.html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@blueprint.route("/api/topic-video/env", methods=["GET"])
def topic_video_env():
    return jsonify(topic_video.check_environment())


@blueprint.route("/api/topic-video/plan", methods=["POST"])
def topic_video_plan():
    data = request.get_json(silent=True) or {}
    request_text = str(data.get("request") or "").strip()
    if not request_text:
        return jsonify({"error": "请先用一句话描述要做的视频"}), 400

    page_count = data.get("pages")
    if page_count is not None:
        try:
            page_count = max(3, min(6, int(page_count)))
        except (TypeError, ValueError):
            return jsonify({"error": "页数必须是 3-6 的整数"}), 400

    env = topic_video.check_environment()
    if not env["compose_model_ok"]:
        return jsonify({"error": env["errors"][0] if env["errors"] else "编排模型不可用"}), 409

    task_id = str(uuid.uuid4())
    tasks = _state["tasks"]
    tasks[task_id] = {
        "status": "pending", "progress": 0, "message": "编排任务排队中...",
        "stage": "queued", "indeterminate": False, "result": None,
        "cancel_requested": False, "logs": [],
        "kind": "topic_plan", "title": "一句话自动编排", "page": "/topic-video",
    }
    _state["enqueue_task"](task_id, _worker, args=(task_id, request_text, page_count),
                           kind="topic_plan", title="一句话自动编排", page="/topic-video")
    return jsonify({"success": True, "task_id": task_id}), 202


@blueprint.route("/api/topic-video/image/<path:rel>", methods=["GET"])
def topic_video_image(rel: str):
    """只允许读取 output/topic_video 目录内的图片文件。"""
    target = os.path.realpath(os.path.join(IMAGES_ROOT, rel))
    if not target.startswith(IMAGES_ROOT + os.sep) or not os.path.isfile(target):
        return jsonify({"error": "文件不存在"}), 404
    return send_file(target)
