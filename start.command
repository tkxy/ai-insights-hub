#!/bin/bash
# AI Insights Hub 本地预览一键启动
# 双击即可：起本地服务 + 自动打开浏览器
# 必须用 localhost:8765，因为 index.html 里 DATA_DIR 在 localhost 下写死指向该端口。

set -e
PORT=8765
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# 若端口已被占用（服务已在跑），直接打开；否则先起服务
if lsof -i ":$PORT" >/dev/null 2>&1; then
  echo "本地服务已在 $PORT 运行，直接打开。"
else
  echo "启动本地服务：http://localhost:$PORT ..."
  python3 -m http.server "$PORT" >/tmp/ai-insights-hub-$PORT.log 2>&1 &
  sleep 1.2
fi

URL="http://localhost:$PORT/index.html"
echo "打开 $URL"
open "$URL"

echo ""
echo "======================================================"
echo " AI Insights Hub 已在浏览器打开：$URL"
echo " 关闭此终端窗口即可停止本地服务。"
echo "======================================================"
echo ""
echo "（按 Ctrl+C 或直接关闭窗口结束）"
# 保持前台，方便查看日志/停止
wait
