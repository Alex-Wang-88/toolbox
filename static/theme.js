/* ============================================================
   TOOLBOX · 统一界面设置（明暗模式 + 主题色）
   ------------------------------------------------------------
   全站唯一实现：
     · 在 <head> 同步加载时立即确定主题，避免首屏闪白
     · DOM 就绪后在主导航右侧注入统一的设置入口
     · 主题键：toolbox-color-theme / toolbox-accent-color（沿用旧值）
   ============================================================ */
(function () {
    'use strict';

    var THEME_KEY = 'toolbox-color-theme';
    var ACCENT_KEY = 'toolbox-accent-color';
    var DEFAULT_ACCENT = '#2b4bee';

    var PRESETS = [
        { name: '靛蓝', hex: '#2b4bee' },
        { name: '青碧', hex: '#0d9488' },
        { name: '松绿', hex: '#15803d' },
        { name: '琥珀', hex: '#b45309' },
        { name: '赭红', hex: '#c02626' },
        { name: '紫藤', hex: '#7c3aed' }
    ];

    var root = document.documentElement;
    var isDark = root.dataset.theme === 'dark';

    /* ---------- 颜色工具 ---------- */
    function normalizeHex(value) {
        var match = String(value || '').trim().match(/^#?([0-9a-fA-F]{6})$/);
        return match ? '#' + match[1].toLowerCase() : '';
    }

    function hexToRgb(hex) {
        var h = normalizeHex(hex).slice(1);
        if (!h) return { r: 0, g: 0, b: 0 };
        return {
            r: parseInt(h.slice(0, 2), 16),
            g: parseInt(h.slice(2, 4), 16),
            b: parseInt(h.slice(4, 6), 16)
        };
    }

    function rgbToHex(rgb) {
        function part(v) {
            var s = Math.max(0, Math.min(255, Math.round(v))).toString(16);
            return s.length === 1 ? '0' + s : s;
        }
        return '#' + part(rgb.r) + part(rgb.g) + part(rgb.b);
    }

    function mix(hexA, hexB, weightA) {
        var a = hexToRgb(hexA);
        var b = hexToRgb(hexB);
        return rgbToHex({
            r: a.r * weightA + b.r * (1 - weightA),
            g: a.g * weightA + b.g * (1 - weightA),
            b: a.b * weightA + b.b * (1 - weightA)
        });
    }

    function rgbToHsl(rgb) {
        var r = rgb.r / 255, g = rgb.g / 255, b = rgb.b / 255;
        var max = Math.max(r, g, b), min = Math.min(r, g, b);
        var h = 0, s = 0, l = (max + min) / 2;
        if (max !== min) {
            var d = max - min;
            s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
            if (max === r) h = ((g - b) / d + (g < b ? 6 : 0));
            else if (max === g) h = (b - r) / d + 2;
            else h = (r - g) / d + 4;
            h /= 6;
        }
        return { h: h * 360, s: s, l: l };
    }

    function hslToHex(h, s, l) {
        h = ((h % 360) + 360) % 360 / 360;
        function hue2rgb(p, q, t) {
            if (t < 0) t += 1;
            if (t > 1) t -= 1;
            if (t < 1 / 6) return p + (q - p) * 6 * t;
            if (t < 1 / 2) return q;
            if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
            return p;
        }
        var r, g, b;
        if (s === 0) {
            r = g = b = l;
        } else {
            var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
            var p = 2 * l - q;
            r = hue2rgb(p, q, h + 1 / 3);
            g = hue2rgb(p, q, h);
            b = hue2rgb(p, q, h - 1 / 3);
        }
        return rgbToHex({ r: r * 255, g: g * 255, b: b * 255 });
    }

    /* ---------- 应用主题色 ---------- */
    var currentAccent = DEFAULT_ACCENT;

    function applyAccent(hex, persist) {
        var accent = normalizeHex(hex) || DEFAULT_ACCENT;
        currentAccent = accent;
        var dark = root.dataset.theme === 'dark';
        var rgb = hexToRgb(accent);
        var panel = dark ? '#12151b' : '#ffffff';

        // 深色模式需要更亮的文字色，浅色模式需要更暗的文字色
        var accentInk = dark ? mix('#ffffff', accent, 0.32) : mix('#000000', accent, 0.78);
        // 浅底：色与白混合 8%；深底：色与面板混合 16%
        var accentSoft = dark ? mix(accent, panel, 0.2) : mix(accent, panel, 0.09);

        root.style.setProperty('--accent', accent);
        root.style.setProperty('--accent-ink', accentInk);
        root.style.setProperty('--accent-soft', accentSoft);
        root.style.setProperty('--accent-rgb', rgb.r + ', ' + rgb.g + ', ' + rgb.b);
        root.style.setProperty('--focus-ring', 'rgba(' + rgb.r + ', ' + rgb.g + ', ' + rgb.b + ', ' + (dark ? 0.32 : 0.22) + ')');

        var dot = document.querySelector('.settings-toggle .settings-dot');
        if (dot) dot.style.background = accent;
        var wheel = document.querySelector('.color-wheel-center');
        if (wheel) wheel.style.background = accent;
        var value = document.querySelector('.color-picker-value');
        if (value) value.textContent = accent.toUpperCase();

        Array.prototype.forEach.call(document.querySelectorAll('.theme-swatch'), function (item) {
            item.setAttribute('aria-pressed', String(normalizeHex(item.dataset.themeColor) === accent));
        });

        if (persist) {
            try { localStorage.setItem(ACCENT_KEY, accent); } catch (e) { /* 忽略隐私模式限制 */ }
        }
    }

    function setTheme(mode) {
        root.dataset.theme = mode === 'dark' ? 'dark' : 'light';
        try { localStorage.setItem(THEME_KEY, root.dataset.theme); } catch (e) { /* 忽略 */ }
        Array.prototype.forEach.call(document.querySelectorAll('input[name="themeMode"]'), function (input) {
            input.checked = input.value === root.dataset.theme;
        });
        // accent 的派生色依赖底色，切换后重算
        applyAccent(currentAccent, false);
    }

    /* ---------- 首屏：尽早确定主题 ---------- */
    (function bootTheme() {
        var saved = null;
        try { saved = localStorage.getItem(THEME_KEY); } catch (e) { /* 忽略 */ }
        if (saved === 'dark' || saved === 'light') {
            root.dataset.theme = saved;
        } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            root.dataset.theme = 'dark';
        }
        // 允许用 ?theme=dark 临时预览，不写入本地存储
        var queryTheme = null;
        try { queryTheme = new URLSearchParams(window.location.search).get('theme'); } catch (e) { /* 忽略 */ }
        if (queryTheme === 'dark' || queryTheme === 'light') root.dataset.theme = queryTheme;
        root.dataset.theme = root.dataset.theme === 'dark' ? 'dark' : 'light';
        isDark = root.dataset.theme === 'dark';

        var accent = null;
        try { accent = localStorage.getItem(ACCENT_KEY); } catch (e) { /* 忽略 */ }
        // 内联脚本阶段 body 尚未解析，仅写 CSS 变量
        var normalized = normalizeHex(accent) || DEFAULT_ACCENT;
        currentAccent = normalized;
        var rgb = hexToRgb(normalized);
        var dark = isDark;
        root.style.setProperty('--accent', normalized);
        root.style.setProperty('--accent-ink', dark ? mix('#ffffff', normalized, 0.32) : mix('#000000', normalized, 0.78));
        root.style.setProperty('--accent-soft', dark ? mix(normalized, '#12151b', 0.2) : mix(normalized, '#ffffff', 0.09));
        root.style.setProperty('--accent-rgb', rgb.r + ', ' + rgb.g + ', ' + rgb.b);
        root.style.setProperty('--focus-ring', 'rgba(' + rgb.r + ', ' + rgb.g + ', ' + rgb.b + ', ' + (dark ? 0.32 : 0.22) + ')');
    }());

    /* ---------- 构建设置面板 ---------- */
    function buildSettings() {
        var nav = document.querySelector('.mainnav');
        if (!nav) return;

        // 清理旧版开关，避免两套主题入口并存
        Array.prototype.forEach.call(nav.querySelectorAll('.theme-toggle, .settings-toggle, .settings-wrap'), function (el) {
            el.remove();
        });

        var wrap = document.createElement('div');
        wrap.className = 'settings-wrap nav-tools';

        var toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'settings-toggle';
        toggle.id = 'uiSettingsToggle';
        toggle.setAttribute('aria-label', '界面设置：明暗模式与主题色');
        toggle.setAttribute('aria-expanded', 'false');
        toggle.setAttribute('aria-controls', 'uiSettingsPanel');
        toggle.title = '界面设置';
        toggle.innerHTML =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M4 7h6M14 7h6M4 17h2M10 17h10"/>' +
            '<circle cx="12" cy="7" r="2.1"/><circle cx="8" cy="17" r="2.1"/>' +
            '</svg><span class="sr-only">界面设置</span>';

        var panel = document.createElement('div');
        panel.className = 'settings-popover';
        panel.id = 'uiSettingsPanel';
        panel.tabIndex = -1;
        panel.hidden = true;
        panel.innerHTML =
            '<div class="settings-head">' +
                '<div><div class="settings-title">界面设置</div>' +
                '<div class="settings-subtitle">外观偏好会保存在本机</div></div>' +
                '<button type="button" class="settings-close" aria-label="关闭设置">' +
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
                    'stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>' +
                '</button>' +
            '</div>' +
            '<div class="settings-section">' +
                '<div class="settings-section-title">明暗模式</div>' +
                '<div class="theme-mode-options">' +
                    '<label class="theme-mode-option"><input type="radio" name="themeMode" value="light"><span>浅色</span></label>' +
                    '<label class="theme-mode-option"><input type="radio" name="themeMode" value="dark"><span>深色</span></label>' +
                '</div>' +
            '</div>' +
            '<div class="settings-section">' +
                '<div class="settings-section-title">主题色</div>' +
                '<div class="theme-presets">' +
                    PRESETS.map(function (p) {
                        return '<button type="button" class="theme-swatch" data-theme-color="' + p.hex +
                            '" aria-label="' + p.name + '" aria-pressed="false" title="' + p.name + '">' +
                            '<span class="theme-swatch-dot" style="--swatch:' + p.hex + '"></span></button>';
                    }).join('') +
                '</div>' +
            '</div>' +
            '<div class="settings-section">' +
                '<div class="settings-section-title">自定义</div>' +
                '<div class="color-picker-row">' +
                    '<button type="button" class="color-wheel-button" id="uiColorWheel" ' +
                    'aria-label="自定义主题色，点击或拖动选择">' +
                        '<span class="color-wheel-spectrum" aria-hidden="true"></span>' +
                        '<span class="color-wheel-center" aria-hidden="true"></span>' +
                    '</button>' +
                    '<div class="color-picker-copy">' +
                        '<strong>拖动取色</strong>' +
                        '<p>色轮选择色相，方向键可微调</p>' +
                        '<span class="color-picker-value">' + currentAccent.toUpperCase() + '</span>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="settings-note">主题色用于选中状态、进度与焦点提示；主操作按钮保持墨色，以保证对比度。</div>';

        wrap.appendChild(toggle);
        wrap.appendChild(panel);
        nav.appendChild(wrap);

        var closeBtn = panel.querySelector('.settings-close');
        var wheel = panel.querySelector('#uiColorWheel');
        var wheelCenter = panel.querySelector('.color-wheel-center');

        function openPanel() {
            panel.hidden = false;
            toggle.setAttribute('aria-expanded', 'true');
            panel.focus({ preventScroll: true });
        }

        function closePanel(returnFocus) {
            panel.hidden = true;
            toggle.setAttribute('aria-expanded', 'false');
            if (returnFocus) toggle.focus();
        }

        toggle.addEventListener('click', function () {
            if (panel.hidden) openPanel(); else closePanel(false);
        });

        closeBtn.addEventListener('click', function () { closePanel(true); });

        Array.prototype.forEach.call(panel.querySelectorAll('input[name="themeMode"]'), function (input) {
            input.addEventListener('change', function () { if (input.checked) setTheme(input.value); });
        });

        Array.prototype.forEach.call(panel.querySelectorAll('.theme-swatch'), function (swatch) {
            swatch.addEventListener('click', function () { applyAccent(swatch.dataset.themeColor, true); });
        });

        /* 色轮交互：角度决定色相，到圆心的距离决定饱和度 */
        function wheelPick(clientX, clientY, persist) {
            var rect = wheel.getBoundingClientRect();
            var cx = rect.left + rect.width / 2;
            var cy = rect.top + rect.height / 2;
            var deltaX = clientX - cx;
            var deltaY = clientY - cy;
            var angle = Math.atan2(deltaX, -deltaY) * 180 / Math.PI;
            if (angle < 0) angle += 360;
            var radius = rect.width / 2;
            var distance = Math.min(Math.hypot(deltaX, deltaY), radius);
            var saturation = Math.max(0.08, Math.min(1, distance / radius) * 0.9);
            var hex = hslToHex(angle, saturation, 0.52);
            applyAccent(hex, !!persist);
            var rad = angle * Math.PI / 180;
            wheelCenter.style.left = (50 + Math.sin(rad) * distance / rect.width * 100) + '%';
            wheelCenter.style.top = (50 - Math.cos(rad) * distance / rect.width * 100) + '%';
        }

        var dragging = false;

        wheel.addEventListener('pointerdown', function (event) {
            dragging = true;
            wheel.classList.add('is-dragging');
            wheel.setPointerCapture && wheel.setPointerCapture(event.pointerId);
            wheelPick(event.clientX, event.clientY, false);
            event.preventDefault();
        });

        wheel.addEventListener('pointermove', function (event) {
            if (!dragging) return;
            wheelPick(event.clientX, event.clientY, false);
        });

        ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(function (name) {
            wheel.addEventListener(name, function () {
                if (!dragging) return;
                dragging = false;
                wheel.classList.remove('is-dragging');
                applyAccent(currentAccent, true);
            });
        });

        wheel.addEventListener('keydown', function (event) {
            var step = event.shiftKey ? 15 : 3;
            var hsl = rgbToHsl(hexToRgb(currentAccent));
            if (event.key === 'ArrowLeft') { applyAccent(hslToHex(hsl.h - step, hsl.s, hsl.l), true); event.preventDefault(); }
            else if (event.key === 'ArrowRight') { applyAccent(hslToHex(hsl.h + step, hsl.s, hsl.l), true); event.preventDefault(); }
        });

        document.addEventListener('click', function (event) {
            if (panel.hidden) return;
            if (panel.contains(event.target) || toggle.contains(event.target)) return;
            closePanel(false);
        });

        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && !panel.hidden) closePanel(true);
        });

        // 同步面板状态并重写一次 accent（此时 DOM 已存在）
        Array.prototype.forEach.call(panel.querySelectorAll('input[name="themeMode"]'), function (input) {
            input.checked = input.value === root.dataset.theme;
        });
        applyAccent(currentAccent, false);

        // 便于截图与直接分享：?settings=1 打开设置面板
        try {
            if (new URLSearchParams(window.location.search).has('settings')) openPanel();
        } catch (e) { /* 忽略 */ }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', buildSettings);
    } else {
        buildSettings();
    }

    window.toolboxTheme = { setTheme: setTheme, setAccent: applyAccent };
}());
