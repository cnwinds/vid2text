#!/usr/bin/env bash
# Pipeline 步骤逻辑单元测试（无网络）
set -euo pipefail
cd "$(dirname "$0")/.."
python -m unittest tests.test_pipeline_steps -v "$@"
