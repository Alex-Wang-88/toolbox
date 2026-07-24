#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试语音克隆训练"""
import requests
import json
import time
import sys

def main():
    # 1. 上传参考音频
    print('1. 上传参考音频...')
    with open(r'C:\Users\12992\Documents\录音\ref_audio.wav', 'rb') as f:
        resp = requests.post('http://localhost:5000/api/voices/upload', 
                           files={'file': ('ref_audio.wav', f, 'audio/wav')})
    upload_result = resp.json()
    print(f'上传结果: {json.dumps(upload_result, ensure_ascii=False)}')
    
    if not upload_result.get('ok'):
        print('上传失败')
        return 1
    
    upload_id = upload_result['upload_id']
    
    # 2. 启动训练
    print('\n2. 启动训练...')
    resp = requests.post('http://localhost:5000/api/voices/train', 
                        json={'upload_id': upload_id, 'name': '测试音色'})
    train_result = resp.json()
    print(f'训练启动: {json.dumps(train_result, ensure_ascii=False)}')
    
    task_id = train_result['task_id']
    
    # 3. 轮询训练进度
    print('\n3. 等待训练完成...')
    while True:
        resp = requests.get(f'http://localhost:5000/api/status/{task_id}')
        status = resp.json()
        progress = status.get('progress', 0)
        message = status.get('message', '')
        print(f'进度: {progress}% - {message}')
        
        if status.get('status') == 'completed':
            print('\n训练完成!')
            break
        elif status.get('status') == 'failed':
            print(f'\n训练失败: {message}')
            return 1
        
        time.sleep(2)
    
    # 4. 获取语音列表
    print('\n4. 获取语音列表...')
    resp = requests.get('http://localhost:5000/api/voices')
    voices = resp.json()
    print(f'可用语音: {json.dumps(voices, ensure_ascii=False, indent=2)}')
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
