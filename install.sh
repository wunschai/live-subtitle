#!/usr/bin/env sh
# 即時字幕 安裝程式（macOS / Linux）
#   sh install.sh [--profile cpu-high] [--skip-bench] [--no-shortcut]
set -eu
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8

echo "==> 檢查 uv"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
fi
uv --version

echo "==> 偵測硬體"
extra=$(uv run --no-project --python 3.12 python src/livesub/hardware.py --print-extra | tr -d '[:space:]')
echo "加速套件：$extra"

echo "==> 安裝套件"
if [ "$extra" = "none" ]; then
    uv sync --frozen --no-dev
else
    uv sync --frozen --no-dev --extra "$extra"
fi

echo "==> 下載模型與校正"
./.venv/bin/python -m livesub.setup_cli "$@"

echo "==> 完成：執行 ./.venv/bin/python -m livesub"
