#!/bin/bash
# Box B 训练启动器：自读 .env 的 HF_TOKEN + 重定向日志，避开 ssh→tmux 嵌套引号问题。
# 用法(在 tmux 内): bash ~/mirofootball/run_train.sh <Team> [epochs]
cd "$HOME/mirofootball" || { echo "cd 失败"; exit 1; }
export HF_TOKEN=$(grep '^HF_TOKEN=' .env | cut -d= -f2)
TEAM="${1:-Spain}"; EP="${2:-1}"
LOG="/tmp/train_${TEAM}.log"
echo "[run_train] team=$TEAM epochs=$EP base=models/gemma4-e2b-it -> $LOG" > "$LOG"
exec .venv-train/bin/python -u brain/train_team_lora.py \
  --base models/gemma4-e2b-it --data "data/sft/${TEAM}.jsonl" --out "lora/${TEAM}" --epochs "$EP" \
  >> "$LOG" 2>&1
