#!/usr/bin/env bash
# 单元测试（无网络，覆盖 tests/ 下全部 test_*.py）
set -euo pipefail
cd "$(dirname "$0")/.."
python -m unittest discover -s tests -p 'test_*.py' -v "$@"
