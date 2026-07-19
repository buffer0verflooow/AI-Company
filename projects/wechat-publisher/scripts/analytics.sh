#!/usr/bin/env bash
# wechat-publisher: 微信公众号数据统计
# Usage: ./analytics.sh [--days N] [--all] [--json]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# 加载凭证
source "$SCRIPT_DIR/env.sh"

# 运行分析脚本
python3 "$SCRIPT_DIR/analytics.py" "$@"
