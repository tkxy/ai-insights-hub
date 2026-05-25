#!/usr/bin/env python3
"""启动器：在正确的环境中启动 TTS 代理"""
import os
import sys
import subprocess

# 找到 ffmpeg 路径
import imageio_ffmpeg
ffmpeg_dir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())

# 设置环境：把当前目录加到 PATH，这样 ffmpeg_wrapper 会被找到
env = os.environ.copy()
script_dir = os.path.dirname(os.path.abspath(__file__))
# 添加项目目录到 PATH 前面，ffmpeg_wrapper 就在那里
env['PATH'] = script_dir + ':' + ffmpeg_dir + ':' + env.get('PATH', '')
env['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'

# 启动 TTS 代理
tts_script = os.path.join(script_dir, 'tts_proxy.py')

proc = subprocess.Popen(
    [sys.executable, tts_script, '--port', '8124'],
    env=env,
    cwd=script_dir,
    stdout=open('tts_proxy.log', 'w'),
    stderr=subprocess.STDOUT
)
print(f"Started TTS proxy with PID {proc.pid}")