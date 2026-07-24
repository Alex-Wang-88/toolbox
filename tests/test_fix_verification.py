# -*- coding: utf-8 -*-
"""旧版 BugFix 回归测试 — 已过时（重构后 MIN/MAX_IMAGE_DURATION 已删除）。

重构后变化（PRD P0-12）：
- MIN_IMAGE_DURATION / MAX_IMAGE_DURATION 常量已删除
- 长旁白不再被 120 秒规则截断
- 画面时长 = 配音时长 + 停顿（无截断/填充逻辑）

本测试文件原用于验证旧版截断逻辑（MIN=8, MAX=120），该逻辑已被有意移除。
测试保留为 skip 状态，记录历史变更。
"""

import unittest


@unittest.skip(
    "已过时：MIN_IMAGE_DURATION/MAX_IMAGE_DURATION 已在重构中删除（PRD P0-12）。"
    "长旁白不再被截断，画面时长 = 配音时长 + 停顿。"
    "旧版截断逻辑测试不再适用。"
)
class TestFixVerificationDeprecated(unittest.TestCase):
    def test_fix_verification(self):
        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
