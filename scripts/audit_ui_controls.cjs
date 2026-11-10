/* Read-only browser audit. Fixtures run only in an isolated browser context.
 * PLAYWRIGHT_MODULE=path/to/playwright-core node scripts/audit_ui_controls.cjs
 * Optional: TOOLBOX_URL, CHROMIUM_EXECUTABLE, AUDIT_OUTPUT, --baseline.
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const baseline = process.argv.includes('--baseline');
const base = process.env.TOOLBOX_URL || 'http://127.0.0.1:5000';
const out = process.env.AUDIT_OUTPUT || 'output/verification/ui-controls';
fs.mkdirSync(out, { recursive: true });
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#e8ecf4"/><text x="40" y="190" font-size="36">UI layout sample</text></svg>';
const longName = '用于检查长文件名换行与按钮宽度的音频转换结果_2026_11_09.wav';
const result = { audio_url: '/api/tts/file/ui-audit.wav', download_url: '/api/tts/file/ui-audit.wav', filename: 'UI audit.wav', song_name: 'UI audit', auto_pitch: true, pitch_shift_applied: -3, duration_sec: 5, source_mode: 'original', mode_label: '原曲模式', elapsed: 1, success_count: 2, zip_filename: '转换结果.zip', zip_url: '/api/audio-converter/archive/ui-audit', items: [longName, '示例歌曲.wav'].map(filename => ({ status: 'completed', filename, audio_url: '/api/tts/file/ui-audit.wav', download_url: '/api/tts/file/ui-audit.wav', source_format: 'mp3' })) };
const done = { status: 'completed', stage: 'completed', progress: 100, result, logs: [] };
const queueTasks = [
    { task_id: 'audit-running', type: 'singing_vc', title: '正在处理的任务', page: '/singing', status: 'processing', progress: 40, message: '正在转换歌声' },
    { task_id: 'audit-pending', type: 'tts', title: '等待任务', page: '/tts', status: 'pending', queue_position: 1, message: '等待执行' },
    { task_id: 'audit-done', type: 'audio_conversion', title: '已完成的音频转换', page: '/audio-converter', ...done },
];
const wav = Buffer.alloc(44 + 8000 * 5 * 2);
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(8000, 24); wav.writeUInt32LE(16000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40);
for (let i = 0; i < 40000; i++) wav.writeInt16LE(Math.round(Math.sin(2 * Math.PI * 180 * i / 8000) * 5000), 44 + i * 2);
const project = {
    version: 2, selectedVoiceId: 'default', screenCount: 2, msFilename: 'UI检查',
    preparedImages: [1, 2].map(i => ({ file: 'ui-audit-' + i + '.png', name: '示例画面 ' + i, ext: 'png', importance: 'normal', transition: 'fade' })),
    manuscriptData: [1, 2].map(i => ({ page: i, file: 'ui-audit-' + i + '.png', name: '示例画面 ' + i, text: '用于检查文稿与生成页面布局。' })),
    completedOutputs: [{ label: '讲解视频', download_url: '/api/download/audit', open_url: '/api/open-video/audit' }],
    currentCompletedTaskId: 'audit-done',
};
const cases = [
    ['home', '/'], ['materials', '/materials', 'project'], ['manuscript', '/manuscript', 'project'], ['generate', '/generate', 'project'],
    ['tts', '/tts', 'session'], ['tts-voice', '/tts#advanced/voice', 'voice'], ['tts-audio', '/tts#advanced/audio'], ['tts-history', '/tts#advanced/history', 'session'],
    ['singing', '/singing', 'session'], ['singing-pitch', '/singing#advanced/pitch'], ['singing-history', '/singing#advanced/history', 'session'],
    ['converter', '/audio-converter', 'session'], ['imagegen', '/imagegen', 'session'], ['imagegen-sampling', '/imagegen#advanced/sampling'],
    ['video-voice', '/generate#advanced/voice', 'project-voice'], ['video-output', '/generate#advanced/output', 'project'], ['video-environment', '/generate#advanced/environment', 'project'], ['queue', '/queue'],
];
const requestedCases = (process.env.AUDIT_CASES || '').split(',').filter(Boolean);
const runCases = requestedCases.length ? cases.filter(([name]) => requestedCases.includes(name)) : cases;
if (!runCases.length) throw new Error('AUDIT_CASES did not match any audit cases');
async function prepare(context, route, state) {
    await context.route('**/api/**', async r => {
        const u = new URL(r.request().url());
        if (u.pathname === '/api/queue') return r.fulfill({ json: { active_task_id: 'audit-running', waiting_count: 1, tasks: queueTasks, persistent: false } });
        if (u.pathname.startsWith('/api/status/ui-audit')) return r.fulfill({ json: done });
        if (u.pathname.includes('/preview/') || u.pathname === '/api/imagegen/history') return u.pathname.endsWith('/history') ? r.fulfill({ json: { data: [] } }) : r.fulfill({ contentType: 'image/svg+xml', body: svg });
        if (u.pathname === '/api/voices/analyze') return r.fulfill({ json: { ok: true, source_duration_sec: 5, recommended_start_sec: 0, recommended_duration_sec: 5 } });
        if (u.pathname.includes('/tts/file/') || u.pathname === '/api/voices/preview') return r.fulfill({ contentType: 'audio/wav', body: wav });
        if (r.request().method() !== 'GET') return r.fulfill({ json: { success: true } });
        return r.continue();
    });
    await context.addInitScript(({ project, state, route }) => {
        if (state?.startsWith('project')) localStorage.setItem('TOOLBOX_current_project_v2', JSON.stringify(project));
        if (state === 'session') {
            const key = route.startsWith('/audio-converter') ? 'audio-converter' : route.startsWith('/imagegen') ? 'imagegen' : route.startsWith('/singing') ? 'singing' : 'tts';
            localStorage.setItem('toolbox.task.' + key, JSON.stringify({ taskId: 'ui-audit-' + key, startedAt: Date.now(), text: 'UI检查', voiceId: 'default' }));
        }
    }, { project, state, route });
}
async function collect(page, caseName, theme, width, overlay) {
    return page.evaluate(({ caseName, theme, width, overlay }) => {
        const shown = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden' && !el.closest('[hidden]');
        const selector = 'button, a.nav-link, a.settings-link, a.back-link, a.download-link, a.dl-btn, .result-actions a, audio[controls], summary, [role="button"]';
        const controls = [...document.querySelectorAll(selector)].filter(shown).filter(el => !overlay || el.closest(overlay === true ? '.settings-popover' : overlay));
        const rows = controls.map(el => {
            const r = el.getBoundingClientRect(), css = getComputedStyle(el);
            const card = el.matches('.thumb-preview,.step-nav,.color-wheel-button,[role="button"],summary');
            const text = (el.getAttribute('aria-label') || el.textContent || el.title || '').trim().replace(/\s+/g, ' ');
            const textRange = document.createRange(); textRange.selectNodeContents(el);
            const rects = [...textRange.getClientRects()].filter(r => r.width && r.height);
            const multiline = new Set(rects.map(r => Math.round(r.top))).size > 1;
            const issues = [];
            if (!card && r.height < 43.5) issues.push('height-below-44');
            if (!card && r.width < 43.5) issues.push('width-below-44');
            if (!card && !multiline && Math.abs(r.height - 44) > .6) issues.push('single-line-height-not-44');
            if (!card && (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)) issues.push('label-overflow');
            if (card && (r.width < 24 || r.height < 24)) issues.push('small-interaction-target');
            if (r.right > document.documentElement.clientWidth + 1 || r.left < -1) issues.push('outside-viewport');
            if (!text && !el.querySelector('img[alt]') && el.tagName !== 'AUDIO') issues.push('missing-label');
            if (el.matches('.primary,.secondary,.preset,.settings-link,.back-link,.download-link,.dl-btn,.remove-file,.result-actions a,.voice-actions button,.voice-clip-actions button,.download button') && css.fontSize !== '14px') issues.push('action-font-not-14');
            return { case: caseName, theme, viewport: width, overlay, tag: el.tagName, id: el.id, className: el.className, text, width: +r.width.toFixed(2), height: +r.height.toFixed(2), font: css.fontSize, padding: css.padding, multiline, card, issues };
        });
        const groups = [...document.querySelectorAll('.actions,.row-actions,.result-actions,.queue-card-actions,.voice-clip-actions,.background-music-actions,.gen-actions,.output-row,.manuscript-choice')].filter(shown).map(el => {
            const items = [...el.children].filter(ch => shown(ch) && ch.matches('button,a,input:not([type=hidden])')).map(ch => ({ text: (ch.textContent || ch.getAttribute('aria-label') || '').trim(), y: ch.getBoundingClientRect().top, height: ch.getBoundingClientRect().height }));
            return { className: el.className, items, mismatch: items.some((item, i) => items.slice(i + 1).some(next => Math.abs(item.y - next.y) < 8 && Math.abs(item.height - next.height) > 1)) };
        }).filter(g => g.items.length > 1);
        const workspaceAlignment = [...document.querySelectorAll('.workspace')].filter(shown).flatMap(el => {
            const panels = [...el.children].filter(ch => shown(ch) && ch.matches('.panel')).map(ch => ch.getBoundingClientRect());
            return panels.length >= 2 && Math.abs(panels[0].left - panels[1].left) > 20 && Math.abs(panels[0].top - panels[1].top) > 1 ? [caseName] : [];
        });
        return { case: caseName, theme, viewport: width, overlay, pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1, workspaceAlignment, controls: rows, groups };
    }, { caseName, theme, width, overlay });
}
(async () => {
    const browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}) });
    const reports = [], errors = [];
    try {
        for (const width of baseline ? [1440, 375] : [1440, 768, 375]) for (const theme of baseline ? ['light'] : ['light', 'dark']) {
            for (const [name, route, state] of runCases) {
                const context = await browser.newContext({ viewport: { width, height: 1000 }, reducedMotion: 'reduce' });
                await prepare(context, route, state);
                const page = await context.newPage();
                page.on('pageerror', e => errors.push({ case: name, width, theme, error: e.message }));
                const [pathname, hash] = route.split('#');
                await page.goto(base + pathname + '?theme=' + theme + (hash ? '#' + hash : ''));
                await page.locator('.queue-strip-copy').waitFor();
                await page.waitForTimeout(150);
                if (['tts', 'generate'].includes(name)) await page.locator('.voice-group').evaluateAll(nodes => nodes.forEach(el => el.open = true));
                if (name === 'converter') await page.locator('#fileInput').setInputFiles({ name: '用于检查列表移除按钮.ncm', mimeType: 'audio/x-ncm', buffer: Buffer.from('UI audit') });
                if (state?.includes('voice')) {
                    await page.locator('#voiceFileInput').setInputFiles({ name: 'UI-audit.wav', mimeType: 'audio/wav', buffer: wav });
                    await page.locator('#voiceClipEditor').waitFor({ state: 'visible' });
                }
                if (name === 'video-environment') await page.locator('#gpuDetails').evaluate(el => el.open = true);
                reports.push(await collect(page, name, theme, width, false));
                // Compare disabled/enabled geometry without submitting a task.
                const geometry = await page.locator('button.primary').first().evaluateAll(nodes => {
                    const el = nodes[0]; if (!el || !el.getClientRects().length) return null;
                    const before = el.getBoundingClientRect(), disabled = el.disabled;
                    el.disabled = !disabled;
                    const after = el.getBoundingClientRect(); el.disabled = disabled;
                    return { width: after.width - before.width, height: after.height - before.height };
                });
                if (geometry && (Math.abs(geometry.width) > .5 || Math.abs(geometry.height) > .5)) errors.push({ case: name, width, theme, error: 'disabled state changes button geometry' });
                if ((width === 375 || width === 1440) && ['imagegen', 'materials', 'tts', 'generate', 'tts-voice', 'converter', 'queue'].includes(name)) await page.screenshot({ path: path.join(out, `${name}-${width}-${theme}.png`), fullPage: true });
                if (name === 'materials') {
                    await page.locator('.thumb-preview').first().click();
                    reports.push(await collect(page, 'image-preview', theme, width, '.image-dialog'));
                    await page.locator('#imageDialogClose').click();
                    await page.evaluate(() => showNotice('检查提示信息的关闭按钮'));
                    reports.push(await collect(page, 'material-notice', theme, width, false));
                }
                await page.locator('#uiSettingsToggle').click();
                reports.push(await collect(page, name, theme, width, true));
                if (name === 'imagegen' && width === 375) await page.screenshot({ path: path.join(out, `settings-${width}-${theme}.png`), fullPage: true });
                await context.close();
            }
            console.log(`Checked ${width}px ${theme}: ${runCases.length} pages/states + settings`);
        }
    } finally { await browser.close(); }
    const issues = reports.flatMap(r => r.controls.filter(c => c.issues.length));
    const summary = { states: reports.length, controls: reports.reduce((n,r) => n + r.controls.length, 0), issues: issues.length, overflow: reports.filter(r => r.pageOverflow).length, workspaceAlignment: reports.flatMap(r => r.workspaceAlignment), mismatchedGroups: reports.flatMap(r => r.groups.filter(g => g.mismatch).map(g => ({ case: r.case, width: r.viewport, theme: r.theme, ...g }))), errors };
    fs.writeFileSync(path.join(out, 'audit.json'), JSON.stringify({ summary, reports }, null, 2));
    fs.writeFileSync(path.join(out, 'issues.json'), JSON.stringify(issues, null, 2));
    console.log(JSON.stringify(summary, null, 2));
    if (!baseline && (issues.length || summary.overflow || summary.workspaceAlignment.length || summary.mismatchedGroups.length || errors.length)) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
