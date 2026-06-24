#!/bin/bash
cd "$HOME/mirofootball/serving/lora" || exit 1
OLL="$HOME/mirofootball/serving/ollama-new/bin/ollama"
echo "=== 部署 48 队 → 两个 daemon $(date) ==="
mapfile -t GG < <(ls *.gguf)
i=0; ok=0; fail=0
for gguf in "${GG[@]}"; do
  team="${gguf%.gguf}"; i=$((i+1))
  s=$(echo "$team" | tr 'A-Z' 'a-z' | tr -d " '.")
  # 重命名为 slug(避免空格), 单词队仅大小写差异也统一
  [ "$gguf" != "$s.gguf" ] && mv -f "$gguf" "$s.gguf"
  printf 'FROM gemma4:e2b-it-qat\nADAPTER ./%s.gguf\n' "$s" > ".mf_$s"
  r=""
  for port in 11436 11437; do
    if OLLAMA_HOST=127.0.0.1:$port "$OLL" create "gemma-$s" -f ".mf_$s" >/tmp/dep_$s.log 2>&1; then r="$r :$port✓"; else r="$r :$port✗"; fi
  done
  echo "[$i/48] gemma-$s$r"
  [ "${r//✓/}" = "$r" ] && fail=$((fail+1)) || ok=$((ok+1))
done
echo "=== 部署完成: $ok ok, $fail 有失败 $(date) ==="
