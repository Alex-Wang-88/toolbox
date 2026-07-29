(function () {
    'use strict';

    class VoiceClipEditor {
        static MIN_DURATION = 3;
        static MAX_DURATION = 15;

        constructor(options) {
            this.fetcher = options.fetcher;
            this.sourceAudio = options.sourceAudio;
            this.panel = options.panel;
            this.canvas = options.canvas;
            this.selectionLabel = options.selectionLabel;
            this.denoiseInput = options.denoiseInput;
            this.playSelectionBtn = options.playSelectionBtn;
            this.previewBtn = options.previewBtn;
            this.processedAudio = options.processedAudio;
            this.setStatus = options.setStatus || function () {};
            this.file = null;
            this.sourceDuration = 0;
            this.processedUrl = '';
            this.playTimer = null;
            this.analysisToken = 0;
            this.waveform = [];
            this.selectionStart = 0;
            this.selectionDuration = VoiceClipEditor.MIN_DURATION;
            this.draggingSelection = false;
            this.dragOffsetSec = 0;
            this.dragMode = '';
            this.resizeAnchorSec = 0;

            this.denoiseInput.addEventListener('change', () => this.clearProcessedPreview());
            this.playSelectionBtn.addEventListener('click', () => this.playOriginalSelection());
            this.previewBtn.addEventListener('click', () => this.previewProcessed());
            this.canvas.addEventListener('pointerdown', (event) => this.startSelectionDrag(event));
            this.canvas.addEventListener('pointermove', (event) => this.dragSelection(event));
            this.canvas.addEventListener('pointerup', (event) => this.endSelectionDrag(event));
            this.canvas.addEventListener('pointercancel', (event) => this.endSelectionDrag(event));
            this.canvas.addEventListener('pointerleave', () => this.updateHoverCursor());
            this.canvas.addEventListener('keydown', (event) => this.moveSelectionWithKeyboard(event));
            window.addEventListener('resize', () => this.drawWaveform());
        }

        async load(file, duration) {
            this.reset();
            this.file = file;
            this.sourceDuration = duration;
            this.panel.hidden = false;
            this.selectionStart = 0;
            this.selectionDuration = Math.min(
                VoiceClipEditor.MAX_DURATION,
                Math.max(VoiceClipEditor.MIN_DURATION, duration),
            );
            this.constrainSelection();
            const token = ++this.analysisToken;
            this.setStatus('正在分析人声并生成推荐选区…');
            this.decodeWaveform(file);

            const form = new FormData();
            form.append('file', file, file.name);
            try {
                const response = await this.fetcher('/voices/analyze', { method: 'POST', body: form });
                const data = await response.json().catch(() => ({}));
                if (token !== this.analysisToken || file !== this.file) return;
                if (!response.ok || !data.ok) throw new Error(data.reason || '无法分析参考音频');
                this.selectionStart = Number(data.recommended_start_sec) || 0;
                this.selectionDuration = Number(data.recommended_duration_sec) || VoiceClipEditor.MIN_DURATION;
                this.constrainSelection();
                this.setStatus('已标出推荐片段；拖动蓝色选区可移动，拖动左右边缘可调整时长。');
            } catch (error) {
                if (token !== this.analysisToken || file !== this.file) return;
                this.setStatus(error.message || '自动分析失败，请手动选择片段。', 'error');
            }
        }

        reset() {
            this.analysisToken += 1;
            this.file = null;
            this.sourceDuration = 0;
            this.waveform = [];
            this.selectionStart = 0;
            this.selectionDuration = VoiceClipEditor.MIN_DURATION;
            this.draggingSelection = false;
            this.dragMode = '';
            this.panel.hidden = true;
            this.stopOriginalSelection();
            this.clearProcessedPreview();
            this.drawWaveform();
        }

        appendTo(form) {
            if (!this.file || this.panel.hidden) return;
            form.append('clip_start_sec', this.selectionStart.toFixed(2));
            form.append('clip_duration_sec', this.selectionDuration.toFixed(2));
            form.append('denoise', this.denoiseInput.checked ? 'true' : 'false');
        }

        constrainSelection() {
            if (!this.sourceDuration) return;
            let duration = Math.min(
                VoiceClipEditor.MAX_DURATION,
                Math.max(VoiceClipEditor.MIN_DURATION, this.selectionDuration || VoiceClipEditor.MIN_DURATION),
            );
            duration = Math.min(duration, this.sourceDuration);
            let start = Math.max(0, this.selectionStart || 0);
            start = Math.min(start, Math.max(0, this.sourceDuration - duration));
            this.selectionStart = start;
            this.selectionDuration = duration;
            this.selectionLabel.textContent =
                `${start.toFixed(1)}–${(start + duration).toFixed(1)} 秒，共 ${duration.toFixed(1)} 秒`;
            this.canvas.setAttribute('aria-valuemax', Math.max(0, this.sourceDuration - duration).toFixed(1));
            this.canvas.setAttribute('aria-valuenow', start.toFixed(1));
            this.canvas.setAttribute(
                'aria-valuetext',
                `从 ${start.toFixed(1)} 秒到 ${(start + duration).toFixed(1)} 秒，共 ${duration.toFixed(1)} 秒`,
            );
            this.drawWaveform();
        }

        async decodeWaveform(file) {
            try {
                const AudioCtx = window.AudioContext || window.webkitAudioContext;
                if (!AudioCtx) return;
                const context = new AudioCtx();
                const buffer = await context.decodeAudioData(await file.arrayBuffer());
                const channel = buffer.getChannelData(0);
                const buckets = 240;
                const size = Math.max(1, Math.floor(channel.length / buckets));
                this.waveform = Array.from({ length: buckets }, (_, index) => {
                    let peak = 0;
                    const end = Math.min(channel.length, (index + 1) * size);
                    for (let i = index * size; i < end; i += 1) peak = Math.max(peak, Math.abs(channel[i]));
                    return peak;
                });
                await context.close();
                this.drawWaveform();
            } catch (_) {
                this.waveform = [];
                this.drawWaveform();
            }
        }

        drawWaveform() {
            const rect = this.canvas.getBoundingClientRect();
            const width = Math.max(1, Math.round(rect.width * (window.devicePixelRatio || 1)));
            const height = Math.max(1, Math.round(rect.height * (window.devicePixelRatio || 1)));
            if (this.canvas.width !== width) this.canvas.width = width;
            if (this.canvas.height !== height) this.canvas.height = height;
            const ctx = this.canvas.getContext('2d');
            ctx.clearRect(0, 0, width, height);
            ctx.fillStyle = '#eef2f7';
            ctx.fillRect(0, 0, width, height);
            if (this.waveform.length) {
                ctx.strokeStyle = '#7f8da3';
                ctx.lineWidth = Math.max(1, window.devicePixelRatio || 1);
                const center = height / 2;
                this.waveform.forEach((peak, index) => {
                    const x = index / Math.max(1, this.waveform.length - 1) * width;
                    const amplitude = Math.max(1, peak * height * 0.46);
                    ctx.beginPath();
                    ctx.moveTo(x, center - amplitude);
                    ctx.lineTo(x, center + amplitude);
                    ctx.stroke();
                });
            }
            if (this.sourceDuration) {
                const start = this.selectionStart;
                const duration = this.selectionDuration;
                const left = start / this.sourceDuration * width;
                const selectedWidth = duration / this.sourceDuration * width;
                ctx.fillStyle = 'rgba(47, 111, 237, 0.22)';
                ctx.fillRect(left, 0, selectedWidth, height);
                ctx.strokeStyle = '#2f6fed';
                ctx.lineWidth = Math.max(2, 2 * (window.devicePixelRatio || 1));
                ctx.strokeRect(left, 0, selectedWidth, height);
                const handleWidth = Math.max(6, 6 * (window.devicePixelRatio || 1));
                const handleHeight = Math.max(24, height * 0.42);
                const handleY = (height - handleHeight) / 2;
                ctx.fillStyle = '#2f6fed';
                ctx.fillRect(left - handleWidth / 2, handleY, handleWidth, handleHeight);
                ctx.fillRect(left + selectedWidth - handleWidth / 2, handleY, handleWidth, handleHeight);
            }
        }

        pointerTime(event) {
            const rect = this.canvas.getBoundingClientRect();
            const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
            return ratio * this.sourceDuration;
        }

        startSelectionDrag(event) {
            if (!this.sourceDuration) return;
            const rect = this.canvas.getBoundingClientRect();
            const duration = this.selectionDuration;
            const pointerSec = this.pointerTime(event);
            const edgeHitSec = Math.max(0.2, 14 / rect.width * this.sourceDuration);
            const selectionEnd = this.selectionStart + duration;
            if (Math.abs(pointerSec - this.selectionStart) <= edgeHitSec) {
                this.dragMode = 'resize-left';
                this.resizeAnchorSec = selectionEnd;
            } else if (Math.abs(pointerSec - selectionEnd) <= edgeHitSec) {
                this.dragMode = 'resize-right';
                this.resizeAnchorSec = this.selectionStart;
            } else {
                this.dragMode = 'move';
            }
            const insideSelection =
                pointerSec >= this.selectionStart && pointerSec <= selectionEnd;
            this.dragOffsetSec = insideSelection ? pointerSec - this.selectionStart : duration / 2;
            this.draggingSelection = true;
            this.canvas.classList.add(this.dragMode === 'move' ? 'dragging' : 'resizing');
            this.canvas.setPointerCapture(event.pointerId);
            this.updateSelectionFromPointer(event);
            this.clearProcessedPreview();
        }

        dragSelection(event) {
            if (!this.draggingSelection) {
                this.updateHoverCursor(event);
                return;
            }
            this.updateSelectionFromPointer(event);
        }

        endSelectionDrag(event) {
            if (!this.draggingSelection) return;
            this.draggingSelection = false;
            this.canvas.classList.remove('dragging', 'resizing');
            this.dragMode = '';
            if (this.canvas.hasPointerCapture(event.pointerId)) {
                this.canvas.releasePointerCapture(event.pointerId);
            }
            this.updateHoverCursor(event);
        }

        updateSelectionFromPointer(event) {
            const pointerSec = this.pointerTime(event);
            if (this.dragMode === 'resize-left') {
                const minStart = Math.max(0, this.resizeAnchorSec - VoiceClipEditor.MAX_DURATION);
                const maxStart = this.resizeAnchorSec - VoiceClipEditor.MIN_DURATION;
                this.selectionStart = Math.max(minStart, Math.min(maxStart, pointerSec));
                this.selectionDuration = this.resizeAnchorSec - this.selectionStart;
            } else if (this.dragMode === 'resize-right') {
                const minEnd = this.resizeAnchorSec + VoiceClipEditor.MIN_DURATION;
                const maxEnd = Math.min(
                    this.sourceDuration,
                    this.resizeAnchorSec + VoiceClipEditor.MAX_DURATION,
                );
                const selectionEnd = Math.max(minEnd, Math.min(maxEnd, pointerSec));
                this.selectionStart = this.resizeAnchorSec;
                this.selectionDuration = selectionEnd - this.resizeAnchorSec;
            } else {
                const maxStart = Math.max(0, this.sourceDuration - this.selectionDuration);
                this.selectionStart = Math.max(
                    0,
                    Math.min(maxStart, pointerSec - this.dragOffsetSec),
                );
            }
            this.constrainSelection();
        }

        updateHoverCursor(event) {
            if (this.draggingSelection) return;
            if (!event || !this.sourceDuration) {
                this.canvas.style.cursor = 'grab';
                return;
            }
            const rect = this.canvas.getBoundingClientRect();
            const pointerSec = this.pointerTime(event);
            const edgeHitSec = Math.max(0.2, 14 / rect.width * this.sourceDuration);
            const nearEdge =
                Math.abs(pointerSec - this.selectionStart) <= edgeHitSec
                || Math.abs(pointerSec - (this.selectionStart + this.selectionDuration)) <= edgeHitSec;
            this.canvas.style.cursor = nearEdge ? 'ew-resize' : 'grab';
        }

        moveSelectionWithKeyboard(event) {
            if (
                !this.sourceDuration
                || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)
            ) return;
            event.preventDefault();
            const step = event.shiftKey ? 1 : 0.1;
            if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                this.selectionStart += event.key === 'ArrowLeft' ? -step : step;
            } else {
                this.selectionDuration += event.key === 'ArrowDown' ? -step : step;
            }
            this.constrainSelection();
            this.clearProcessedPreview();
        }

        playOriginalSelection() {
            if (!this.file) return;
            this.stopOriginalSelection();
            const start = this.selectionStart;
            const duration = this.selectionDuration;
            this.sourceAudio.currentTime = start;
            this.sourceAudio.play().catch(() => {});
            this.playTimer = window.setTimeout(() => {
                this.sourceAudio.pause();
            }, duration * 1000);
        }

        stopOriginalSelection() {
            if (this.playTimer) window.clearTimeout(this.playTimer);
            this.playTimer = null;
            if (this.sourceAudio) this.sourceAudio.pause();
        }

        clearProcessedPreview() {
            if (this.processedUrl) URL.revokeObjectURL(this.processedUrl);
            this.processedUrl = '';
            this.processedAudio.pause();
            this.processedAudio.removeAttribute('src');
            this.processedAudio.hidden = true;
        }

        async previewProcessed() {
            if (!this.file) return;
            const form = new FormData();
            form.append('file', this.file, this.file.name);
            this.appendTo(form);
            this.previewBtn.disabled = true;
            this.setStatus(this.denoiseInput.checked ? '正在生成轻度降噪试听…' : '正在生成裁剪试听…');
            try {
                const response = await this.fetcher('/voices/preview', { method: 'POST', body: form });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.reason || '试听生成失败');
                }
                this.clearProcessedPreview();
                this.processedUrl = URL.createObjectURL(await response.blob());
                this.processedAudio.src = this.processedUrl;
                this.processedAudio.hidden = false;
                await this.processedAudio.play().catch(() => {});
                this.setStatus('正在试听最终参考片段；确认效果后即可创建。');
            } catch (error) {
                this.setStatus(error.message || '试听生成失败', 'error');
            } finally {
                this.previewBtn.disabled = false;
            }
        }
    }

    window.VoiceClipEditor = VoiceClipEditor;
}());
