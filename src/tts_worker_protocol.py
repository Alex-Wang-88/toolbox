# -*- coding: utf-8 -*-
"""TTS Worker IPC 协议常量与消息辅助函数。

主流程（Py3.13）与 Worker（独立 venv）通过 stdin/stdout JSONL 通信。
本模块定义消息格式、命令名、状态枚举，以及序列化/反序列化辅助函数。
两端共享同一套协议约定，确保消息格式一致。
"""

import json
import uuid
from enum import Enum


# ---- 命令名常量 ----
CMD_HEALTH = "health"
CMD_STATUS = "status"
CMD_LOAD = "load"
CMD_SYNTHESIZE = "synthesize"
CMD_UNLOAD = "unload"
CMD_SHUTDOWN = "shutdown"

# 所有合法命令
ALL_COMMANDS = frozenset({
    CMD_HEALTH, CMD_STATUS, CMD_LOAD, CMD_SYNTHESIZE, CMD_UNLOAD, CMD_SHUTDOWN,
})


class WorkerState(str, Enum):
    """Worker 状态机枚举。

    转换路径：
        NOT_STARTED -> MODEL_NOT_LOADED  (Worker 启动)
        MODEL_NOT_LOADED -> LOADING      (收到 load 命令)
        LOADING -> READY                 (模型加载成功)
        LOADING -> ERROR                 (模型加载失败)
        READY -> SYNTHESIZING            (收到 synthesize 命令)
        SYNTHESIZING -> READY            (合成完成)
        SYNTHESIZING -> ERROR            (合成异常)
        ERROR -> MODEL_NOT_LOADED        (恢复)
        任意 -> SHUTDOWN                 (收到 shutdown 命令)
    """
    NOT_STARTED = "NOT_STARTED"
    MODEL_NOT_LOADED = "MODEL_NOT_LOADED"
    LOADING = "LOADING"
    READY = "READY"
    SYNTHESIZING = "SYNTHESIZING"
    ERROR = "ERROR"
    SHUTDOWN = "SHUTDOWN"


# ---- 消息类型 ----
MSG_TYPE_REQUEST = "request"
MSG_TYPE_RESPONSE = "response"
MSG_TYPE_EVENT = "event"


def serialize_message(msg: dict) -> str:
    """将消息字典序列化为一行 JSON（不含换行符）。

    Worker stdout 每行一条 JSON，调用方需自行追加换行。
    """
    return json.dumps(msg, ensure_ascii=False)


def deserialize_message(line: str) -> dict:
    """从一行文本反序列化 JSON 消息。

    解析失败时返回空字典，调用方应忽略。
    """
    line = (line or "").strip()
    if not line:
        return {}
    try:
        obj = json.loads(line)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def make_request(cmd: str, params: dict = None, req_id: str = None) -> dict:
    """构造 IPC 请求消息。

    Args:
        cmd: 命令名（CMD_* 常量之一）
        params: 命令参数字典
        req_id: 请求 ID（用于匹配响应）；不传则自动生成

    Returns:
        {"type":"request","id":"req_xxx","cmd":"synthesize","params":{...}}
    """
    return {
        "type": MSG_TYPE_REQUEST,
        "id": req_id or ("req_" + uuid.uuid4().hex[:12]),
        "cmd": cmd,
        "params": params or {},
    }


def make_response(req_id: str, cmd: str, ok: bool, data=None, error: str = None) -> dict:
    """构造 IPC 响应消息。

    Args:
        req_id: 对应请求的 ID
        cmd: 对应请求的命令名
        ok: 是否成功
        data: 成功时的数据负载
        error: 失败时的错误信息

    Returns:
        {"type":"response","id":"req_xxx","cmd":"synthesize","ok":true,"data":{...}}
    """
    return {
        "type": MSG_TYPE_RESPONSE,
        "id": req_id,
        "cmd": cmd,
        "ok": bool(ok),
        "data": data,
        "error": error,
    }


def make_event(event: str, data: dict = None) -> dict:
    """构造异步事件消息（Worker -> 主流程，无需匹配请求）。

    Args:
        event: 事件名（如 "ready"、"state_change"）
        data: 事件数据

    Returns:
        {"type":"event","event":"state_change","data":{"state":"READY"}}
    """
    return {
        "type": MSG_TYPE_EVENT,
        "event": event,
        "data": data or {},
    }
