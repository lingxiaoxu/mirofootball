#!/bin/bash
# Box B 批量训练（tmux 内顺序训多队，无 exec 以便循环）。用法: bash train_all.sh France Argentina ...
cd "$HOME/mirofootball" || exit 1
export HF_TOKEN=$(grep '^HF_TOKEN=' .env | cut -d= -f2)
echo "[train_all] teams: $* @ $(date)" > /tmp/train_all.log
for T in "$@"; do
  echo "=== $T 开始 $(date +%H:%M:%S) ===" >> /tmp/train_all.log
  .venv-train/bin/python -u brain/train_team_lora.py \
    --base models/gemma4-e2b-it --data "data/sft/${T}.jsonl" --out "lora/${T}" --epochs 1 \
    > "/tmp/train_${T}.log" 2>&1
  echo "=== $T 完成 exit=$? $(date +%H:%M:%S)  adapter: $(du -sh lora/${T} 2>/dev/null|cut -f1) ===" >> /tmp/train_all.log
done
echo "[train_all] ALL DONE @ $(date)" >> /tmp/train_all.log
