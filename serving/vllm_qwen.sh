#!/bin/bash
# 持球/防守决策模型 vLLM 实例(plan 03§2.1)。目标态; 当前实际走 Ollama :11434 nemotron。
# 计划用 Qwen 系作 on-ball/first-defender brain; 这里参数化, 实际模型路径按部署填。
source "$(dirname "$0")/vllm_common.sh"
MODEL="${BRAIN_MODEL_PATH:-Qwen/Qwen2.5-14B-Instruct}"
serve_vllm "$MODEL" "${BRAIN_PORT:-11434}" --served-model-name brain --enable-prefix-caching
