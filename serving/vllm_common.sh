#!/bin/bash
# vLLM 共享配置(plan 00§7 / 03§2.1)。目标态: 三实例(nemotron + 2 gemma)各占端口。
# 注: Box A 当前实际用 Ollama(:11434/11436/11437), 已稳定。本脚本为 vLLM 等价部署的目标态骨架,
#     供显存更宽裕或需更高吞吐时切换。共享 nemotron 仍只读, 绝不在此重启共享实例。
export VLLM_HOST="${VLLM_HOST:-0.0.0.0}"
export DTYPE="${DTYPE:-bfloat16}"
export GPU_FRAC="${GPU_FRAC:-0.30}"          # 三实例分摊显存
serve_vllm() {  # serve_vllm <model_path> <port> [extra args...]
  local model="$1" port="$2"; shift 2
  echo "[vllm] serve $model :$port frac=$GPU_FRAC"
  python -m vllm.entrypoints.openai.api_server \
    --model "$model" --host "$VLLM_HOST" --port "$port" \
    --dtype "$DTYPE" --gpu-memory-utilization "$GPU_FRAC" \
    --max-model-len "${MAX_LEN:-4096}" "$@"
}
