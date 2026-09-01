#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "[RideBridge] .venv가 없습니다. README의 설치 절차를 먼저 실행하세요." >&2
  exit 1
fi

exec .venv/bin/python app.py
