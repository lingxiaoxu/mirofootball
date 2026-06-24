#!/bin/bash
# A/B 对比: 引擎控摆位(MIRO_INTENT=0) vs LLM摆位(MIRO_INTENT=1)。真实 nemo+2gemma, gemma_n=6, 600拍, 各2场。
cd "$HOME/mirofootball" || exit 1
. .venv/bin/activate
echo "[compare] start $(date +%H:%M)" > /tmp/compare.log
for intent in 0 1; do
  [ "$intent" = 0 ] && lbl="B-引擎摆位" || lbl="C-LLM摆位"
  for i in 1 2; do
    echo "=== $lbl run$i (MIRO_INTENT=$intent) $(date +%H:%M) ===" >> /tmp/compare.log
    MIRO_INTENT=$intent python run_real_match.py Spain Germany lora 600 2>/dev/null \
      | tr '\r' '\n' | grep -aE "控球|传球|射门" >> /tmp/compare.log
  done
done
echo "[compare] DONE $(date +%H:%M)" >> /tmp/compare.log
