/* 克隆音色创建表单（共享模块）：「素材转视频」与「TTS」页的二级「音色管理」面板共用。
 *
 * 此前同一份表单标记 + 创建流程在 index.html 与 tts.html 各复制一份（约 300 行 × 2），
 * 文案与创建后的页面行为略有差异。本模块收敛标记与流程，差异通过 options 注入：
 *
 *   const form = VoiceCloneForm.mount(mountEl, {
 *       summary: '新增克隆音色',              // <details><summary> 文案
 *       dropHint: '建议自然说话，避免多人声和背景音乐',
 *       namePlaceholder: '例如：产品经理小林',
 *       consentText: '我确认已获得该声音的使用授权，并同意仅在本机处理这段录音。',
 *       submitLabel: '创建克隆音色',
 *       apiBase: '/api',                     // TaskResume.request 的 URL 前缀（transcribe/create）
 *       requestUrl: (path) => apiBase + path,  // 可选：动态构造请求 URL（如 index 的探测回退）
 *       fetcher: (path, options) => ...,     // VoiceClipEditor 的取数通道，path 形如 '/voices/analyze'
 *                                            // 缺省为 fetch(apiBase + path)
 *       formatError: (error) => '...',       // 可选：错误状态文案加工
 *       onCreated: async ({ name, voiceId, info, autoTranscript }) => {},  // 创建成功后的页面逻辑
 *       onError: (error) => {},              // 可选：创建失败后的页面逻辑（如记日志）
 *   });
 *   form.setBusy(true);  // 任务执行期间整体禁用（index 的 setRunningState 调用）
 *
 * 依赖：app_common.js（TaskResume）、voice_clip_editor.js（VoiceClipEditor）须先于本模块加载。
 * 挂载产物是 <details data-advanced="voice">，workspace.js 会照常把它移入二级「高级设置」视图。
 * 状态元素保留 id="voiceCreateStatus"：workspace.js 的 toolbox-task-progress 事件按该 id 定位。
 */
(function () {
    'use strict';

    async function safeReadJson(response) {
        try { return await response.json(); } catch (e) { return {}; }
    }

    // 骨架用模板字符串一次成型（与两页原有标记逐字一致），动态文案经 options 注入。
    // 注入值都是页面代码里的常量，非用户输入。
    function buildMarkup(options) {
        const details = document.createElement('details');
        details.className = 'voice-create';
        details.id = 'voiceCreatePanel';
        details.setAttribute('data-advanced', 'voice');
        details.setAttribute('data-advanced-title', '音色管理');
        details.innerHTML = `
            <summary>${options.summary}</summary>
            <div class="voice-create-body">
                <div class="voice-guide" aria-label="录音要求">
                    <div class="voice-guide-row"><b>1</b><span>准备一段<strong>单人、无音乐、无回声</strong>的清晰录音。</span></div>
                    <div class="voice-guide-row"><b>2</b><span>支持 WAV / MP3，3–120 秒，文件不超过 25MB；最终保留 3–15 秒。</span></div>
                    <div class="voice-guide-row"><b>3</b><span>系统会推荐人声片段；可微调、轻度降噪并试听后创建。</span></div>
                </div>
                <form id="voiceCreateForm">
                    <input id="voiceFileInput" type="file" accept=".wav,.mp3,audio/wav,audio/mpeg" hidden>
                    <div class="voice-drop" id="voiceDrop" role="button" tabindex="0" aria-controls="voiceFileInput">
                        <strong>选择或拖入参考音频</strong>
                        <span>${options.dropHint}</span>
                    </div>
                    <div class="voice-file-meta" id="voiceFileMeta">尚未选择文件</div>
                    <audio class="voice-audio-preview" id="voiceAudioPreview" controls preload="metadata"></audio>
                    <div class="voice-clip-editor" id="voiceClipEditor" hidden>
                        <canvas class="voice-waveform" id="voiceWaveform" role="slider" tabindex="0" aria-label="参考音频选区；拖动中间可移动，拖动左右边缘可调整时长；方向键移动，上下键调整时长" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0"></canvas>
                        <div class="voice-clip-summary" id="voiceClipSelection">等待分析</div>
                        <label class="voice-denoise"><input id="voiceDenoiseInput" type="checkbox"><span>轻度降噪（适合底噪；明显背景音乐仍建议更换录音）</span></label>
                        <div class="voice-clip-actions">
                            <button id="voicePlaySelectionBtn" type="button">试听原选区</button>
                            <button id="voiceProcessedPreviewBtn" type="button">试听处理后</button>
                        </div>
                        <audio class="voice-processed-preview" id="voiceProcessedPreview" controls hidden></audio>
                    </div>
                    <div class="voice-train-fields">
                        <label><span>音色名称</span><input class="text-input" id="voiceNameInput" maxlength="30" placeholder="${options.namePlaceholder}" autocomplete="off"></label>
                        <label><span>参考音频语言</span><select class="text-input" id="voiceLanguageInput" aria-describedby="voiceLanguageHint">
                            <option value="auto">自动识别</option>
                            <option value="zh">中文</option>
                            <option value="en">英语</option>
                            <option value="ja">日语</option>
                            <option value="ko">韩语</option>
                            <option value="de">德语</option>
                            <option value="es">西班牙语</option>
                            <option value="fr">法语</option>
                            <option value="it">意大利语</option>
                            <option value="ru">俄语</option>
                        </select><small class="hint" id="voiceLanguageHint">留空逐字稿时，系统会按此语言引导识别；选择“自动识别”可自动判断。</small></label>
                        <label><span>录音逐字稿（选填）</span><textarea class="text-input" id="voiceRefTextInput" maxlength="500" placeholder="留空将自动识别；手动填写通常更准确"></textarea></label>
                        <label class="voice-train-consent"><input id="voiceConsentInput" type="checkbox"><span>${options.consentText}</span></label>
                        <button class="primary voice-create-submit" id="voiceCreateBtn" type="submit">${options.submitLabel}</button>
                    </div>
                    <div class="voice-create-status" id="voiceCreateStatus" role="status" aria-live="polite"></div>
                </form>
            </div>`;
        return {
            details,
            form: details.querySelector('#voiceCreateForm'),
            fileInput: details.querySelector('#voiceFileInput'),
            drop: details.querySelector('#voiceDrop'),
            fileMeta: details.querySelector('#voiceFileMeta'),
            audioPreview: details.querySelector('#voiceAudioPreview'),
            clip: details.querySelector('#voiceClipEditor'),
            canvas: details.querySelector('#voiceWaveform'),
            clipSummary: details.querySelector('#voiceClipSelection'),
            denoiseInput: details.querySelector('#voiceDenoiseInput'),
            playSelectionBtn: details.querySelector('#voicePlaySelectionBtn'),
            processedPreviewBtn: details.querySelector('#voiceProcessedPreviewBtn'),
            processedAudio: details.querySelector('#voiceProcessedPreview'),
            nameInput: details.querySelector('#voiceNameInput'),
            languageInput: details.querySelector('#voiceLanguageInput'),
            refTextInput: details.querySelector('#voiceRefTextInput'),
            consentInput: details.querySelector('#voiceConsentInput'),
            submitBtn: details.querySelector('#voiceCreateBtn'),
            status: details.querySelector('#voiceCreateStatus'),
        };
    }

    function mount(container, options) {
        const refs = buildMarkup(options);
        container.append(refs.details);

        const submitLabel = options.submitLabel;
        const apiBase = options.apiBase || '/api';
        // requestUrl 可注入（index 的 API_BASE 会被取数探测动态更新，需要闭包取最新值）
        const requestUrl = options.requestUrl || ((path) => apiBase + path);
        const setStatus = (message, type) => {
            refs.status.textContent = message || '';
            refs.status.className = 'voice-create-status' + (type ? ` ${type}` : '');
        };

        const voiceClipEditor = new VoiceClipEditor({
            fetcher: options.fetcher || ((path, fetchOptions) => fetch(apiBase + path, fetchOptions)),
            sourceAudio: refs.audioPreview,
            panel: refs.clip,
            canvas: refs.canvas,
            selectionLabel: refs.clipSummary,
            denoiseInput: refs.denoiseInput,
            playSelectionBtn: refs.playSelectionBtn,
            previewBtn: refs.processedPreviewBtn,
            processedAudio: refs.processedAudio,
            setStatus: setStatus,
        });

        let selectedFile = null;
        let selectedFileUrl = '';

        function clearFilePreview() {
            selectedFile = null;
            if (selectedFileUrl) URL.revokeObjectURL(selectedFileUrl);
            selectedFileUrl = '';
            refs.audioPreview.onloadedmetadata = null;
            refs.audioPreview.onerror = null;
            refs.audioPreview.pause();
            refs.audioPreview.removeAttribute('src');
            refs.audioPreview.classList.remove('active');
            refs.fileInput.value = '';
            refs.fileMeta.className = 'voice-file-meta';
            refs.fileMeta.textContent = '尚未选择文件';
            voiceClipEditor.reset();
        }

        function acceptFile(file) {
            if (!file) return;
            const ext = String(file.name || '').split('.').pop().toLowerCase();
            if (!['wav', 'mp3'].includes(ext)) {
                clearFilePreview();
                setStatus('格式不支持，请选择 WAV 或 MP3 文件。', 'error');
                return;
            }
            if (file.size <= 0 || file.size > 25 * 1024 * 1024) {
                clearFilePreview();
                setStatus(file.size <= 0 ? '音频文件为空。' : '文件超过 25MB，请压缩后再上传。', 'error');
                return;
            }
            clearFilePreview();
            selectedFile = file;
            selectedFileUrl = URL.createObjectURL(file);
            refs.audioPreview.src = selectedFileUrl;
            refs.audioPreview.classList.add('active');
            refs.fileMeta.textContent = `正在读取 ${file.name}…`;
            setStatus('');
            refs.audioPreview.onloadedmetadata = () => {
                const duration = Number(refs.audioPreview.duration || 0);
                if (!Number.isFinite(duration) || duration < 3 || duration > 120) {
                    const reason = duration < 3 ? '录音不足 3 秒，请选择更长的音频。' : '录音超过 120 秒，请先缩短后再上传。';
                    clearFilePreview();
                    setStatus(reason, 'error');
                    return;
                }
                refs.fileMeta.className = 'voice-file-meta ready';
                refs.fileMeta.textContent = `${file.name} · ${duration.toFixed(1)} 秒 · ${(file.size / 1024 / 1024).toFixed(1)} MB`;
                if (!refs.nameInput.value.trim()) {
                    refs.nameInput.value = file.name.replace(/\.[^.]+$/, '').slice(0, 30);
                }
                voiceClipEditor.load(file, duration);
            };
            refs.audioPreview.onerror = () => {
                clearFilePreview();
                setStatus('无法读取这段音频，请确认文件未损坏。', 'error');
            };
        }

        refs.drop.addEventListener('click', () => refs.fileInput.click());
        refs.drop.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                refs.fileInput.click();
            }
        });
        refs.fileInput.addEventListener('change', () => acceptFile(refs.fileInput.files[0]));
        ['dragenter', 'dragover'].forEach((name) => refs.drop.addEventListener(name, (event) => {
            event.preventDefault();
            refs.drop.classList.add('dragover');
        }));
        ['dragleave', 'drop'].forEach((name) => refs.drop.addEventListener(name, (event) => {
            event.preventDefault();
            refs.drop.classList.remove('dragover');
        }));
        refs.drop.addEventListener('drop', (event) => acceptFile(event.dataTransfer.files[0]));

        refs.form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const name = refs.nameInput.value.trim();
            let language = refs.languageInput.value || 'auto';
            let refText = refs.refTextInput.value.trim();
            if (!selectedFile) { setStatus('请先选择参考音频。', 'error'); return; }
            if (!name) { setStatus('请填写音色名称。', 'error'); refs.nameInput.focus(); return; }
            if (!refs.consentInput.checked) { setStatus('请先确认已获得声音使用授权。', 'error'); refs.consentInput.focus(); return; }

            refs.submitBtn.disabled = true;
            let autoTranscript = '';
            try {
                if (refText.length < 2) {
                    refs.submitBtn.textContent = '正在识别逐字稿…';
                    setStatus('未填写逐字稿，正在本机识别参考音频，请稍候…');
                    const transcriptForm = new FormData();
                    transcriptForm.append('file', selectedFile, selectedFile.name);
                    transcriptForm.append('language', language);
                    voiceClipEditor.appendTo(transcriptForm);
                    const transcriptResponse = await TaskResume.request(requestUrl('/voices/transcribe'), { method: 'POST', body: transcriptForm });
                    const transcriptResult = await safeReadJson(transcriptResponse);
                    if (!transcriptResponse.ok || !transcriptResult.ok) {
                        throw new Error(transcriptResult.reason || '没有识别到清晰文字，请手动填写逐字稿');
                    }
                    refText = transcriptResult.text.trim();
                    autoTranscript = refText;
                    refs.refTextInput.value = refText;
                    const detectedLanguage = String(transcriptResult.language || '').trim().toLowerCase();
                    const detectedOption = Array.from(refs.languageInput.options).find((option) => option.value === detectedLanguage);
                    if (language === 'auto' && detectedOption) {
                        language = detectedLanguage;
                        refs.languageInput.value = detectedLanguage;
                    }
                    setStatus(`已自动识别并填入逐字稿：${refText}`);
                }

                const form = new FormData();
                form.append('file', selectedFile, selectedFile.name);
                form.append('name', name);
                form.append('ref_text', refText);
                form.append('language', language);
                form.append('consent', 'true');
                voiceClipEditor.appendTo(form);
                refs.submitBtn.textContent = '正在分析并创建…';
                setStatus(autoTranscript ? '逐字稿已应用，正在创建克隆音色…' : '正在检测有效人声并生成标准参考片段，请稍候…');
                const response = await TaskResume.request(requestUrl('/voices/create'), { method: 'POST', body: form });
                const result = await safeReadJson(response);
                if (!response.ok || !result.ok) throw new Error(result.reason || result.error || '创建失败');
                const info = result.processing || {};
                refs.form.reset();
                clearFilePreview();
                setStatus(
                    autoTranscript
                        ? `「${name}」创建成功；自动识别逐字稿：${autoTranscript}`
                        : info.auto_trimmed
                        ? `「${name}」创建成功，已从 ${info.source_duration_sec} 秒录音中选取 ${info.duration_sec} 秒有效人声。`
                        : `「${name}」创建成功，参考音频 ${info.duration_sec} 秒。`,
                    'success'
                );
                await options.onCreated?.({ name, voiceId: result.voice.id, info, autoTranscript });
            } catch (error) {
                const message = options.formatError ? options.formatError(error) : (error && error.message) || '创建失败';
                setStatus(message, 'error');
                options.onError?.(error);
            } finally {
                refs.submitBtn.disabled = false;
                refs.submitBtn.textContent = submitLabel;
            }
        });

        return {
            setBusy(disabled) {
                [refs.fileInput, refs.drop, refs.nameInput, refs.languageInput, refs.refTextInput,
                 refs.consentInput, refs.playSelectionBtn, refs.processedPreviewBtn, refs.submitBtn]
                    .forEach((element) => { element.disabled = disabled; });
            },
        };
    }

    window.VoiceCloneForm = { mount: mount };
}());
