"""核对每个页面的 class 是否都有对应样式定义（ui.css 或页面内联 style）。

用法：python scripts/ui_class_audit.py
输出：每个页面中「HTML 用到但没有样式定义」的 class 列表。
"""
import io
import os
import re

STATIC = "static"
PAGES = ["index.html", "tts.html", "singing.html", "audio_converter.html", "imagegen.html"]
TOKEN_OK = re.compile(r"^[a-z][a-z0-9_-]*$")
# 仅作为 JS 语义标记、不需要样式的 class
IGNORED = {"clone-group", "edge-group"}


def extract_selectors_class_names(css: str) -> set:
    """粗粒度提取 CSS 里出现的 .class 名（含伪类/后代选择器）。"""
    css = re.sub(r"/\*[\s\S]*?\*/", "", css)
    # 去掉声明块，只留选择器部分
    selectors = re.findall(r"([^{}]+)\{", css)
    names = set()
    for sel in selectors:
        for m in re.finditer(r"\.(-?[_a-zA-Z][\w-]*)", sel):
            names.add(m.group(1))
    return names


def main() -> int:
    ui_css = io.open(os.path.join(STATIC, "ui.css"), encoding="utf-8").read()
    ui_names = extract_selectors_class_names(ui_css)
    print(f"ui.css 定义 class: {len(ui_names)}\n")

    total_missing = 0
    for page in PAGES:
        html = io.open(os.path.join(STATIC, page), encoding="utf-8").read()
        style_blocks = "\n".join(re.findall(r"<style>([\s\S]*?)</style>", html))
        page_names = extract_selectors_class_names(style_blocks)
        defined = ui_names | page_names

        body = html.split("</style>", 1)[-1]
        used = set()
        for m in re.finditer(r'class="([^"]*)"', body):
            for token in m.group(1).split():
                if TOKEN_OK.match(token):
                    used.add(token)

        missing = sorted(c for c in used if c not in defined and c not in IGNORED)
        total_missing += len(missing)
        print(f"### {page}: 使用 {len(used)} 个 class，页面自定义 {len(page_names)} 个")
        if missing:
            print("  未定义：" + " ".join(missing))
        else:
            print("  未定义：无")
        print()

    print(f"合计未定义 class: {total_missing}")
    return 0 if total_missing == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
