# -*- coding: utf-8 -*-
"""GPU 资源模式（生图 / 音视频互斥）单元测试。

只测状态机与门禁，不启动任何 GPU 进程；TTS Worker 启停用 monkeypatch 替身。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import resource_mode
from resource_mode import (
    MODE_IMAGEGEN,
    MODE_VIDEO,
    MODE_VIDEOGEN,
    ModeBusyError,
    ModeManager,
)


@pytest.fixture
def manager(monkeypatch):
    """独立的 ModeManager + 被替换掉的 worker 启停副作用。"""
    calls = {"tts_shutdown": 0, "imagegen_stop": 0, "videogen_stop": 0}
    monkeypatch.setattr(
        resource_mode, "_shutdown_tts_workers",
        lambda: calls.__setitem__("tts_shutdown", calls["tts_shutdown"] + 1) or ["cosyvoice3"],
    )
    monkeypatch.setattr(resource_mode, "_imagegen_running", lambda: False)
    monkeypatch.setattr(resource_mode, "_videogen_running", lambda: False)
    mgr = ModeManager()
    mgr.register_imagegen_stopper(
        lambda: calls.__setitem__("imagegen_stop", calls["imagegen_stop"] + 1)
    )
    mgr.register_videogen_stopper(
        lambda: calls.__setitem__("videogen_stop", calls["videogen_stop"] + 1)
    )
    return mgr, calls


def test_default_mode_is_video(manager):
    mgr, _ = manager
    assert mgr.mode == MODE_VIDEO
    assert mgr.busy == 0
    assert mgr.status()["mode"] == MODE_VIDEO


def test_switch_to_imagegen_stops_tts_workers(manager):
    mgr, calls = manager
    result = mgr.set_mode(MODE_IMAGEGEN, source="ui")
    assert result["ok"] and result["changed"] is True
    assert result["stopped_tts"] == ["cosyvoice3"]
    assert calls["tts_shutdown"] == 1
    assert mgr.mode == MODE_IMAGEGEN


def test_switch_is_idempotent(manager):
    mgr, calls = manager
    mgr.set_mode(MODE_IMAGEGEN)
    again = mgr.set_mode(MODE_IMAGEGEN)
    assert again["changed"] is False
    assert calls["tts_shutdown"] == 1, "重复切换不应重复停 Worker"


def test_switch_back_calls_imagegen_stopper(manager):
    mgr, calls = manager
    mgr.set_mode(MODE_IMAGEGEN)
    result = mgr.set_mode(MODE_VIDEO)
    assert result["changed"] is True
    assert calls["imagegen_stop"] == 1
    assert mgr.mode == MODE_VIDEO


def test_switch_to_videogen_stops_tts_and_imagegen(manager):
    mgr, calls = manager
    result = mgr.set_mode(MODE_VIDEOGEN, source="ui")
    assert result["ok"] and result["changed"] is True
    assert result["stopped_tts"] == ["cosyvoice3"]
    assert calls["tts_shutdown"] == 1
    assert calls["imagegen_stop"] == 1
    assert mgr.mode == MODE_VIDEOGEN


def test_switch_away_from_videogen_calls_videogen_stopper(manager):
    mgr, calls = manager
    mgr.set_mode(MODE_VIDEOGEN)
    assert calls["videogen_stop"] == 0, "切入 VIDEOGEN 不应停掉自己"
    result = mgr.set_mode(MODE_VIDEO)
    assert result["changed"] is True
    assert calls["videogen_stop"] == 1
    assert mgr.mode == MODE_VIDEO


def test_videogen_is_idempotent(manager):
    mgr, calls = manager
    mgr.set_mode(MODE_VIDEOGEN)
    again = mgr.set_mode(MODE_VIDEOGEN)
    assert again["changed"] is False
    assert calls["tts_shutdown"] == 1, "重复切换不应重复停 Worker"


def test_invalid_mode_raises_value_error(manager):
    mgr, _ = manager
    with pytest.raises(ValueError):
        mgr.set_mode("unknown")
    assert mgr.mode == MODE_VIDEO


def test_busy_switch_is_rejected(manager):
    mgr, calls = manager
    with mgr.task_scope(MODE_IMAGEGEN):
        assert mgr.busy == 1
        with pytest.raises(ModeBusyError):
            mgr.set_mode(MODE_VIDEO)
        assert mgr.mode == MODE_IMAGEGEN
        assert calls["imagegen_stop"] == 0
    assert mgr.busy == 0, "task_scope 退出后必须归还 busy 计数"


def test_task_scope_releases_busy_on_exception(manager):
    mgr, _ = manager
    with pytest.raises(RuntimeError):
        with mgr.task_scope(MODE_IMAGEGEN):
            raise RuntimeError("boom")
    assert mgr.busy == 0


def test_task_scope_switches_mode_lazily(manager):
    mgr, calls = manager
    with mgr.task_scope(MODE_IMAGEGEN, source="imagegen"):
        assert mgr.mode == MODE_IMAGEGEN
    assert calls["tts_shutdown"] == 1
    assert mgr.busy == 0


def test_ensure_is_alias_of_set_mode(manager):
    mgr, _ = manager
    mgr.ensure(MODE_IMAGEGEN)
    assert mgr.mode == MODE_IMAGEGEN
    result = mgr.ensure(MODE_IMAGEGEN)
    assert result["changed"] is False


def test_status_reports_busy_and_mode(manager):
    mgr, _ = manager
    mgr.set_mode(MODE_IMAGEGEN)
    with mgr.task_scope(MODE_IMAGEGEN):
        status = mgr.status()
        assert status["busy"] == 1, "任务进行中 busy 必须为 1"
    assert status["mode"] == MODE_IMAGEGEN
    assert mgr.status()["busy"] == 0, "任务结束后 busy 必须归零"
    assert status["last_source"] in ("ui", "api", "task", "imagegen")
    assert status["last_switch_at"]
