/* ============================================================
   全站公共脚本
   ------------------------------------------------------------
   1. 客户端心跳：让打包态的本地服务知道还有页面活着；关闭窗口后
      服务才会自行退出。所有页面都引用本文件，因此页面之间跳转
      不会误判为“客户端已关闭”。
   2. TaskResume：把异步任务的 task_id 持久化到 localStorage，
      切页 / 刷新回来后继续轮询并展示当前进度。
   ============================================================ */
(function (global) {
    'use strict';

    /* ---------- 1. 客户端心跳 ---------- */
    function ping() {
        fetch('/api/client-alive', { method: 'POST', keepalive: true }).catch(function () {});
    }
    function sayGoodbye() {
        var url = '/api/client-close';
        if (navigator.sendBeacon) {
            navigator.sendBeacon(url, new Blob([], { type: 'application/octet-stream' }));
            return;
        }
        fetch(url, { method: 'POST', keepalive: true }).catch(function () {});
    }
    ping();
    global.setInterval(ping, 1500);
    global.addEventListener('pagehide', sayGoodbye);

    /* ---------- 2. 任务会话持久化 ---------- */
    var PREFIX = 'toolbox.task.';

    function save(key, data) {
        try { global.localStorage.setItem(PREFIX + key, JSON.stringify(data)); } catch (e) { /* 隐私模式忽略 */ }
    }
    function load(key) {
        try {
            var raw = global.localStorage.getItem(PREFIX + key);
            return raw ? JSON.parse(raw) : null;
        } catch (e) { return null; }
    }
    function clear(key) {
        try { global.localStorage.removeItem(PREFIX + key); } catch (e) { /* 忽略 */ }
    }

    /**
     * 轮询任务状态；页面加载时用同一份 session 再调用一次即可恢复进度。
     * options:
     *   taskId    任务 id（必填）
     *   interval  轮询间隔毫秒，默认 900
     *   onTask(task)  每次拿到状态
     *   onDone(task)  完成
     *   onFail(task)  失败 / 取消
     *   onLost(reason) 任务不存在（服务重启）或连续三次取不到状态
     * 返回 stop()
     */
    function watch(options) {
        var stopped = false;
        var failures = 0;
        var timer = null;
        var interval = options.interval || 900;

        function schedule(delay) {
            if (stopped) return;
            timer = global.setTimeout(tick, delay == null ? interval : delay);
        }

        function finish(callback, payload) {
            stopped = true;
            if (timer) { global.clearTimeout(timer); timer = null; }
            if (callback) callback(payload);
        }

        async function tick() {
            if (stopped) return;
            try {
                var response = await fetch('/api/status/' + encodeURIComponent(options.taskId), { cache: 'no-store' });
                var task = await response.json().catch(function () { return {}; });
                if (stopped) return;
                if (response.status === 404) {
                    finish(options.onLost, task.error || '任务不存在');
                    return;
                }
                if (!response.ok) throw new Error(task.error || '读取任务状态失败');
                failures = 0;
                if (options.onTask) options.onTask(task);
                if (task.status === 'completed' || task.status === 'done') {
                    finish(options.onDone, task);
                    return;
                }
                if (task.status === 'failed' || task.status === 'error' || task.status === 'cancelled') {
                    finish(options.onFail, task);
                    return;
                }
                schedule();
            } catch (error) {
                if (stopped) return;
                failures += 1;
                if (failures >= 3) {
                    finish(options.onLost, (error && error.message) || '无法连接本地服务');
                    return;
                }
                schedule(interval * 2);
            }
        }

        tick();
        return function stop() {
            stopped = true;
            if (timer) { global.clearTimeout(timer); timer = null; }
        };
    }

    async function queuedRequest(url, options) {
        options = options || {};
        if (options.body instanceof FormData) options.body.set('async', 'true');
        var response = await fetch(url, options);
        if (response.status !== 202) return response;
        var body = await response.json();
        return new Promise(function (resolve, reject) {
            watch({ taskId: body.task_id,
                onTask: function (task) {
                    global.dispatchEvent(new CustomEvent('toolbox-task-progress', { detail: task }));
                },
                onDone: function (task) { resolve(new Response(JSON.stringify(task.result || {}), { status: 200, headers: { 'Content-Type': 'application/json' } })); },
                onFail: function (task) { resolve(new Response(JSON.stringify(task.result || { ok: false, reason: task.message }), { status: 422, headers: { 'Content-Type': 'application/json' } })); },
                onLost: reject
            });
        });
    }
    global.TaskResume = { save: save, load: load, clear: clear, watch: watch, request: queuedRequest };
}(window));
