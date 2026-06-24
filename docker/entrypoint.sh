#!/usr/bin/env sh
set -eu

python openfugu/console.py \
  --port "${CONSOLE_PORT:-8091}" \
  --serve-url "${FUGU_SERVE_URL:-http://127.0.0.1:${PORT:-8090}}" \
  --config "${FUGU_CONFIG:-configs/fugu.yaml}" &
console_pid=$!

python openfugu/product_serve.py \
  --config "${FUGU_CONFIG:-configs/fugu.yaml}" \
  --port "${PORT:-8090}" \
  --model-dir "${FUGU_MODEL:-Qwen/Qwen3-0.6B}" \
  --vector "${FUGU_VECTOR:-artifacts/router_head_zero_svf.npy}" \
  --flash-head "${FUGU_FLASH_HEAD:-data/flash_head.npy}" \
  --pro-head "${FUGU_PRO_HEAD:-data/pro_head.npy}" \
  --live \
  ${FUGU_DEBUG:+--debug} &
serve_pid=$!

trap 'kill "$console_pid" "$serve_pid" 2>/dev/null || true' INT TERM
wait "$serve_pid"
status=$?
kill "$console_pid" 2>/dev/null || true
wait "$console_pid" 2>/dev/null || true
exit "$status"
