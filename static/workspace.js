/* Shared navigation, secondary settings views, and live queue. No resource changes on navigation. */
(function () {
    'use strict';
    const $ = (id) => document.getElementById(id);
    const labels = { material_upload: '导入素材', voice_analyze: '分析参考音频', video: '讲解视频', manuscript: '文稿', tts: '语音合成', tts_batch: '批量配音', singing_vc: 'AI 唱歌', ncm_conversion: '音频转换', audio_conversion: '音频转换', voice_create: '创建音色', voice_transcribe: '识别逐字稿', imagegen: 'AI 生图' };
    const states = { pending: '等待执行', processing: '正在执行', completed: '已完成', done: '已完成', failed: '失败', error: '失败', cancelled: '已取消' };
    const terminal = ['completed', 'done', 'failed', 'error', 'cancelled'];
    function node(tag, cls, text) {
        const el = document.createElement(tag); if (cls) el.className = cls;
        if (text != null) el.textContent = text; return el;
    }
    function link(text, href, cls) {
        const el = node('a', cls, text); el.href = href; return el;
    }
    function init() {
        const main = document.querySelector('main.app');
        const nav = document.querySelector('.mainnav');
        if (!main || !nav) return;
        const links = nav.querySelector('.nav-links');
        links.append(link('任务队列', '/queue', 'nav-link' + (location.pathname === '/queue' ? ' active' : '')));
        if (location.pathname === '/queue') links.lastChild.setAttribute('aria-current', 'page');
        const strip = node('a', 'queue-strip');
        strip.href = '/queue'; strip.setAttribute('aria-label', '查看全站任务队列');
        strip.append(node('span', 'queue-indicator'), node('strong', '', '任务队列'), node('span', 'queue-strip-copy', '正在连接…'), node('span', 'queue-strip-action', '查看队列 →'));
        nav.after(strip);
        const nextTask = node('button', 'secondary queue-next-task', '继续创建任务');
        nextTask.type = 'button'; nextTask.hidden = true;
        nextTask.addEventListener('click', () => {
            window.dispatchEvent(new Event('toolbox-new-task'));
            nextTask.hidden = true;
        });
        strip.after(nextTask);
        const advancedNodes = Array.from(main.querySelectorAll('[data-advanced]'));
        if (advancedNodes.length) buildSecondary(main, nav, strip, advancedNodes);
        let timer;
        let lastQueueKey = '';
        async function refresh() {
            try {
                const res = await fetch('/api/queue', { cache: 'no-store' });
                if (!res.ok) throw new Error('读取队列失败');
                const data = await res.json();
                const active = data.tasks.find((t) => t.task_id === data.active_task_id);
                const sessionKey = { '/tts': 'tts', '/singing': 'singing', '/audio-converter': 'audio-converter', '/imagegen': 'imagegen' }[location.pathname];
                nextTask.hidden = location.hash.startsWith('#advanced/') || !(sessionKey ? TaskResume.load(sessionKey) : location.pathname === '/generate' && data.tasks.some(t => t.type === 'video' && !terminal.includes(t.status)));
                strip.classList.toggle('is-running', !!active);
                strip.querySelector('.queue-strip-copy').textContent = active ? `${labels[active.type] || '任务'} · ${active.message || '执行中'}${data.waiting_count ? ` · ${data.waiting_count} 项等待` : ''}` : data.waiting_count ? `${data.waiting_count} 项等待执行` : '当前空闲，提交后立即开始';
                if ($('queueRunning')) {
                    // Preserve focused cancel/result controls when the queue is unchanged.
                    const key = JSON.stringify(data);
                    if (key !== lastQueueKey) { renderQueue(data); lastQueueKey = key; }
                }
                $('queueConnection')?.replaceChildren(document.createTextNode('状态自动更新 · 可切换页面继续操作'));
            } catch (error) {
                strip.querySelector('.queue-strip-copy').textContent = '暂时无法连接，正在重试';
                if ($('queueConnection')) $('queueConnection').textContent = '无法连接本地服务，正在重试';
            } finally { timer = setTimeout(refresh, document.hidden ? 6000 : 1500); }
        }
        refresh();
        window.addEventListener('pagehide', () => clearTimeout(timer), { once: true });
        window.addEventListener('toolbox-task-progress', (event) => {
            const status = $('voiceCreateStatus');
            if (status) status.textContent = event.detail.message || '正在处理…';
        });
        window.addEventListener('toolbox-queue-refresh', () => { clearTimeout(timer); refresh(); });
    }
    function buildSecondary(main, nav, strip, sections) {
        const toolbar = node('nav', 'page-tools'); toolbar.setAttribute('aria-label', '功能设置');
        toolbar.append(node('span', 'page-tools-label', '更多选项'));
        const view = node('section', 'secondary-view'); view.id = 'advancedPage'; view.hidden = true;
        const back = link('← 返回核心操作', '#', 'back-link');
        const heading = node('h1', '', '高级设置'); heading.tabIndex = -1;
        view.append(back, heading, node('p', 'secondary-description', '调整会保留，返回后即可提交任务。'));
        const tabs = node('nav', 'secondary-tabs'); tabs.setAttribute('aria-label', '高级功能'); view.append(tabs);
        const entries = [];
        sections.forEach((section) => {
            const key = section.dataset.advanced;
            const title = section.dataset.advancedTitle;
            const href = '#advanced/' + key;
            toolbar.append(link(title + ' ↗', href, 'settings-link'));
            tabs.append(link(title, href, 'settings-link'));
            const panel = node('div', 'secondary-content'); panel.dataset.view = key; panel.hidden = true;
            if (section.tagName === 'DETAILS') section.open = true;
            panel.append(section); view.append(panel); entries.push({ key, title, panel });
        });
        // Keep optional settings beside the page context, after its title.
        (main.querySelector('header.top, header.hero') || strip).after(toolbar); main.append(view);
        const primary = Array.from(main.children).filter((el) => ![nav, strip, view].includes(el));
        function route(focus) {
            const key = location.hash.startsWith('#advanced/') ? location.hash.slice(10) : '';
            const entry = entries.find((item) => item.key === key);
            primary.forEach((el) => el.classList.toggle('primary-view-hidden', !!entry));
            view.hidden = !entry;
            entries.forEach((item) => { item.panel.hidden = item !== entry; });
            tabs.querySelectorAll('a').forEach((a) => a.setAttribute('aria-current', a.hash === location.hash ? 'page' : 'false'));
            if (entry) { heading.textContent = entry.title; if (focus) heading.focus(); }
            else if (focus) (main.querySelector('header h1') || toolbar).focus?.();
        }
        window.addEventListener('hashchange', () => route(true)); route(false);
    }
    function resultLinks(task, card) {
        const result = task.result || {};
        const actions = node('div', 'queue-card-actions');
        const safeLink = (label, url) => {
            if (typeof url === 'string' && url.startsWith('/api/') && !url.includes('\\')) actions.append(link(label, url, 'settings-link'));
        };
        safeLink('下载音频', result.download_url || result.audio_url);
        safeLink('下载视频', result.video_url);
        safeLink('下载结果', task.download_url || task.zip_url || result.zip_url || result.archive_url);
        if (task.type === 'imagegen') safeLink('查看图片', '/api/imagegen/file/' + encodeURIComponent(task.task_id));
        if (task.type === 'video') safeLink('下载视频', '/api/download/' + encodeURIComponent(task.task_id));
        (result.files || result.items || []).forEach((file) => safeLink(file.filename || '下载音频', file.download_url || file.audio_url));
        if (task.type === 'manuscript') {
            const button = node('button', 'secondary', '应用文稿'); button.type = 'button';
            button.addEventListener('click', () => {
                TaskResume.save('manuscript', { taskId: task.task_id }); location.href = '/manuscript';
            }); actions.append(button);
        }
        if (actions.childElementCount) card.append(actions);
    }
    function taskCard(task) {
        const card = node('article', 'queue-card');
        const top = node('div', 'queue-card-top');
        const copy = node('div', 'queue-card-copy');
        copy.append(node('span', 'queue-kind', labels[task.type] || '任务'), node('h3', '', task.title || '未命名任务'));
        const badge = node('span', 'queue-state state-' + task.status, task.cancel_requested && !terminal.includes(task.status) ? '正在取消' : task.status === 'pending' ? `第 ${task.queue_position} 位` : states[task.status] || task.status);
        top.append(copy, badge); card.append(top, node('p', 'queue-message', task.message || ''));
        if (task.status === 'processing') {
            const progress = node('progress'); progress.max = 100; progress.value = Number(task.progress) || 0; progress.setAttribute('aria-label', '任务进度'); card.append(progress);
        }
        const actions = node('div', 'queue-card-actions'); actions.append(link('打开功能页', task.page, 'settings-link'));
        if (!terminal.includes(task.status)) {
            const button = node('button', 'secondary danger', task.cancel_requested ? '正在取消…' : task.status === 'pending' ? '取消排队' : '取消任务');
            button.type = 'button'; button.disabled = !!task.cancel_requested;
            button.addEventListener('click', async () => {
                button.disabled = true;
                try {
                    const res = await fetch('/api/cancel/' + encodeURIComponent(task.task_id), { method: 'POST' });
                    if (!res.ok) throw new Error((await res.json()).error || '取消失败');
                    window.dispatchEvent(new Event('toolbox-queue-refresh'));
                } catch (error) { button.disabled = false; $('queueConnection').textContent = error.message; }
            }); actions.append(button);
        }
        card.append(actions);
        if (['completed', 'done'].includes(task.status)) resultLinks(task, card);
        return card;
    }
    function renderQueue(data) {
        const tasks = data.tasks || [];
        const active = tasks.filter((t) => t.task_id === data.active_task_id);
        const pending = tasks.filter((t) => t.status === 'pending');
        const history = tasks.filter((t) => terminal.includes(t.status));
        $('runningCount').textContent = active.length;
        $('waitingCount').textContent = pending.length;
        $('finishedCount').textContent = history.length;
        [['queueRunning', active, '当前没有正在执行的任务'], ['queueWaiting', pending, '暂无等待任务'], ['queueHistory', history, '本次运行的任务记录将显示在这里']].forEach(([id, list, empty]) => {
            const focused = document.activeElement;
            const focusId = focused?.closest('[data-task-id]')?.dataset.taskId;
            const focusLabel = focused?.textContent;
            $(id).replaceChildren(...(list.length ? list.map((task) => { const el = taskCard(task); el.dataset.taskId = task.task_id; return el; }) : [node('div', 'queue-empty', empty)]));
            if (focusId && !document.contains(focused)) {
                const card = Array.from($(id).children).find((el) => el.dataset.taskId === focusId);
                Array.from(card?.querySelectorAll('button,a') || []).find((el) => el.textContent === focusLabel)?.focus();
            }
        });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
}());
