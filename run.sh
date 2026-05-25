#!/usr/bin/env bash
# macOS/Linux 便捷入口。跨平台编排逻辑统一在 run.py，本脚本只负责定位仓库根并转交。
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$ROOT/run.py" "$@"
