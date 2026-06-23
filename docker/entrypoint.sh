#!/usr/bin/env sh
set -eu

exec python openfugu/product_serve.py \
  --config "${FUGU_CONFIG:-configs/fugu.yaml}" \
  --port "${PORT:-8090}" \
  --model-dir "${FUGU_MODEL:-Qwen/Qwen3-0.6B}" \
  --vector "${FUGU_VECTOR:-artifacts/router_head_zero_svf.npy}" \
  --flash-head "${FUGU_FLASH_HEAD:-data/flash_head.npy}" \
  --pro-head "${FUGU_PRO_HEAD:-data/pro_head.npy}" \
  --live \
  ${FUGU_DEBUG:+--debug}
