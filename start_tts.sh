#!/bin/bash
# TTS Proxy 启动器 - 设置 ffmpeg 路径
FFMPEG_DIR="/Users/tukouxiaoyuan/WorkBuddy/Claw/ai-insights-hub/openvoice_env/lib/python3.9/site-packages/imageio_ffmpeg/binaries"
export PATH="$FFMPEG_DIR:$PATH"
cd /Users/tukouxiaoyuan/WorkBuddy/Claw/ai-insights-hub
exec /Users/tukouxiaoyuan/WorkBuddy/Claw/ai-insights-hub/openvoice_env/bin/python tts_proxy.py --port 8124