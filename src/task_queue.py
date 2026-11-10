"""One FIFO execution lane shared by all toolbox feature pages."""
from collections import deque
import threading
import time


TERMINAL = {"completed", "done", "failed", "error", "cancelled"}


class TaskQueue:
    def __init__(self, tasks):
        self.tasks = tasks
        self._lock = threading.RLock()
        self._pending = deque()
        self._active = None
        self._worker = None

    def submit(self, task_id, target, args=(), kwargs=None, *, kind, title, page,
               prepare=None, cleanup=None):
        with self._lock:
            task = self.tasks[task_id]
            task.update(type=kind, title=title, page=page, status="pending",
                        stage="queued", message="等待前面的任务完成", queue_position=0,
                        cancel_requested=False, created_at=time.time())
            self._pending.append((task_id, target, args, kwargs or {}, prepare, cleanup))
            self._positions()
            if self._worker is None:
                self._worker = threading.Thread(target=self._drain, daemon=True,
                                                name="toolbox-task-queue")
                try:
                    self._worker.start()
                except Exception:
                    self._worker = None
                    self._pending.pop()
                    task.update(status="failed", stage="failed", message="无法启动任务队列")
                    self._positions()
                    if cleanup:
                        cleanup()
                    raise

    def _positions(self):
        for position, job in enumerate(self._pending, 1):
            task = self.tasks.get(job[0])
            if task:
                task["queue_position"] = position
                task["message"] = f"等待执行 · 队列第 {position} 位"

    def _drain(self):
        while True:
            with self._lock:
                if not self._pending:
                    self._active = self._worker = None
                    return
                task_id, target, args, kwargs, prepare, cleanup = self._pending.popleft()
                task = self.tasks.get(task_id)
                if task is None:
                    if cleanup:
                        cleanup()
                    continue
                self._active = task_id
                task.update(status="processing", stage="prepare", queue_position=0,
                            message="任务开始执行", started_at=time.time())
                self._positions()
            try:
                if not task.get("cancel_requested"):
                    if prepare:
                        prepare()
                    if not task.get("cancel_requested"):
                        target(*args, **kwargs)
                if task.get("cancel_requested"):
                    task.update(status="cancelled", stage="cancelled", message="任务已取消")
                elif task.get("status") not in TERMINAL:
                    task.update(status="completed", stage="completed", progress=100,
                                message="任务已完成")
            except Exception as exc:
                task.update(status="failed", stage="failed", message=f"任务失败：{exc}")
            finally:
                if cleanup:
                    try:
                        cleanup()
                    except Exception:
                        pass
                with self._lock:
                    task.update(finished_at=time.time(), indeterminate=False)
                    self._active = None

    def cancel(self, task_id):
        cleanup = None
        with self._lock:
            task = self.tasks.get(task_id)
            if not task or task.get("status") in TERMINAL:
                return False
            task["cancel_requested"] = True
            for job in self._pending:
                if job[0] == task_id:
                    self._pending.remove(job)
                    cleanup = job[5]
                    task.update(status="cancelled", stage="cancelled", message="已取消排队",
                                queue_position=0, finished_at=time.time(), indeterminate=False)
                    self._positions()
                    break
            else:
                task["message"] = "正在取消，当前处理步骤结束后释放资源"
        if cleanup:
            cleanup()
        return True

    def snapshot(self):
        with self._lock:
            active = self._active
            pending = [job[0] for job in self._pending]
            fields = ("type", "title", "page", "status", "stage", "progress", "message",
                      "queue_position", "created_at", "started_at", "finished_at",
                      "cancel_requested", "result", "outputs", "download_url", "zip_url")
            items = []
            # Queue metadata uses an allowlist; results follow the status API contract.
            for task_id, task in list(self.tasks.items()):
                if "page" in task:
                    items.append({"task_id": task_id, **{k: task[k] for k in fields if k in task}})
            items.sort(key=lambda item: item.get("created_at", 0), reverse=True)
            order = ([active] if active else []) + pending
            by_id = {item["task_id"]: item for item in items}
            ordered = [by_id[key] for key in order if key in by_id]
            ordered += [item for item in items if item["task_id"] not in order][:100]
            return {"active_task_id": active, "waiting_count": len(pending),
                    "tasks": ordered, "persistent": False}

    @property
    def busy(self):
        with self._lock:
            return self._active is not None or bool(self._pending)
