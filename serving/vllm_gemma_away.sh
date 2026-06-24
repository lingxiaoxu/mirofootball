#!/bin/bash
# Away 队无球摆位 gemma 实例(plan 03§2.1)。目标态; 当前走 Ollama :11437 gemma-<away> LoRA。
source "$(dirname "$0")/vllm_common.sh"
MODEL="${GEMMA_BASE_PATH:-google/gemma-2-2b-it}"
serve_vllm "$MODEL" "${GEMMA_AWAY_PORT:-11437}" --served-model-name gemma-away \
  --enable-lora --lora-modules "away=${AWAY_LORA_PATH:-./lora/Germany}"
