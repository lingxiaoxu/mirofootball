#!/bin/bash
# Home 队无球摆位 gemma 实例(plan 03§2.1)。目标态; 当前走 Ollama :11436 gemma-<home> LoRA。
source "$(dirname "$0")/vllm_common.sh"
MODEL="${GEMMA_BASE_PATH:-google/gemma-2-2b-it}"
serve_vllm "$MODEL" "${GEMMA_HOME_PORT:-11436}" --served-model-name gemma-home \
  --enable-lora --lora-modules "home=${HOME_LORA_PATH:-./lora/Spain}"
