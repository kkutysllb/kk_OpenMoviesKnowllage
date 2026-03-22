#!/bin/bash
# FinReport2Video Web UI 一键启动脚本

set -e
cd "$(dirname "$0")"

echo "=========================================="
echo "  FinReport2Video Web UI"
echo "=========================================="

# 检查 Python 依赖
echo "[1/2] 启动 FastAPI 后端 (port 8765)..."
if ! python3 -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "  安装缺失依赖..."
  pip3 install fastapi uvicorn python-multipart
fi

# 确保 input/ 目录存在
mkdir -p input

# 启动 FastAPI（后台）
python3 -m uvicorn api_server:app --port 8765 --reload &
FASTAPI_PID=$!
echo "  FastAPI PID: $FASTAPI_PID"

# 等待后端就绪
sleep 2

# 启动 Next.js 前端
echo "[2/2] 启动 Next.js 前端 (port 3000)..."
cd web

if [ ! -d "node_modules" ]; then
  echo "  安装 npm 依赖..."
  npm install
fi

# 前台运行 Next.js（Ctrl+C 时一并退出）
trap "kill $FASTAPI_PID 2>/dev/null; exit" INT TERM
npm run dev

