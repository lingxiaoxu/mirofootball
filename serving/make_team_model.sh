#!/usr/bin/env bash
# 为某支球队从 gemma4:e2b-it-qat 派生一个"挂队级 LoRA"的 Ollama 模型: gemma-<team>
# 切换球队 = 给两队各 create 一次（秒级），编排器请求 model="gemma-<team>"（plan 04 §6）。
#
# 用法: make_team_model.sh <team> <lora_gguf_path|''> <port>
#   有训练好的队级 LoRA(gguf) → 挂 ADAPTER（gemma 获得该队战术知识）
#   无 LoRA(传 '') → 纯 base（= 当前状态，只靠 prompt 区分）
#
# ⚠️ 现状：还没有真 LoRA（需先用球队数据训练，plan 04 §5/§7）。此脚本是就位的切换机制。
set -euo pipefail
TEAM="${1:?need team name}"; LORA="${2:-}"; PORT="${3:-11436}"
NEW="$HOME/mirofootball/serving/ollama-new/bin/ollama"
export LD_LIBRARY_PATH="$HOME/mirofootball/serving/ollama-new/lib/ollama:${LD_LIBRARY_PATH:-}"

MF="$(mktemp)"
echo "FROM gemma4:e2b-it-qat" > "$MF"
if [ -n "$LORA" ]; then echo "ADAPTER $LORA" >> "$MF"; fi

OLLAMA_HOST="localhost:$PORT" "$NEW" create "gemma-$TEAM" -f "$MF"
rm -f "$MF"
echo "created gemma-$TEAM (lora=${LORA:-none}) on :$PORT"
# 切换：home daemon(:11436) 造主队、away daemon(:11437) 造客队；MatchDirector 用 model='gemma-<team>'
