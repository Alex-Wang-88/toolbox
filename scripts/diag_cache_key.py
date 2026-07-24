#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：用真实 MultiTtsCacheManager 计算两段文本的缓存键，对比已知 0.4s 缓存目录。"""
import os, sys

SRC = "C:/Users/12992/Desktop/work/code/toolbax/src"
sys.path.insert(0, SRC)
os.chdir("C:/Users/12992/Desktop/work/code/toolbax")

from multi_tts_voice import MultiTtsCacheManager

MODEL_DIR = "C:/Users/12992/Desktop/work/code/toolbax/tts_poc/models/CosyVoice3-0.5B"
CACHE_DIR = "C:/Users/12992/Desktop/work/code/toolbax/app_data/tts_cache"
REF_AUDIO = "C:/Users/12992/Desktop/work/code/toolbax/app_data/voices/crystal_cosyvoice3/speaker.wav"
REF_TEXT = "我們最後天氣怎麼那麼熱愛現在我不知道今年要熱到多久什麼才是個頭真麻煩"

m = MultiTtsCacheManager(CACHE_DIR, MODEL_DIR, "cosyvoice3", ref_text=REF_TEXT)

zh = "你好，我是 Crystal。今天天气真不错，我们一起来测试一下语音合成的效果。希望这次克隆是完整的，能读完整段文字。"
en = "Hello, this is a test of the Crystal voice."

k_zh = m.compute_key(zh, REF_AUDIO, 1.0)
k_en = m.compute_key(en, REF_AUDIO, 1.0)
stale = "abae81872ee049ef4fd8a54561beb9bbfd0b6e44249c738434fd031d7c9acb9e"

print("中文文本缓存键:", k_zh)
print("英文文本缓存键:", k_en)
print("已知 0.4s 缓存键:", stale)
print("中文==英文? ", k_zh == k_en)
print("中文命中stale? ", k_zh == stale)
print("英文命中stale? ", k_en == stale)
