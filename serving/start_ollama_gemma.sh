#!/bin/bash
# 启动/校验两个自有 gemma 实例(plan 03§2.0/§9)。Box A 实际用 systemd 用户服务:
#   ollama-gemma-home.service(:11436) / ollama-gemma-away.service(:11437)。
# 铁律: 绝不碰 :11434(共享 nemotron)/:11435(认证代理)。本脚本只管自有的 :11436/:11437。
set -u
HOME_PORT=11436; AWAY_PORT=11437

start_svc() {
  local svc=$1 port=$2
  if curl -s --max-time 4 "localhost:$port/api/tags" >/dev/null 2>&1; then
    echo "  :$port 已在运行 ✓"; return 0
  fi
  echo "  启动 $svc ..."
  systemctl --user start "$svc" 2>/dev/null || {
    echo "  systemd 启动失败, 回退直接拉起"; return 1; }
  for _ in $(seq 1 20); do
    curl -s --max-time 4 "localhost:$port/api/tags" >/dev/null 2>&1 && { echo "  :$port OK ✓"; return 0; }
    sleep 1
  done
  echo "  :$port 启动超时 ✗"; return 1
}

echo "== 启动两个 gemma 实例 =="
start_svc ollama-gemma-home.service "$HOME_PORT"
start_svc ollama-gemma-away.service "$AWAY_PORT"

echo "== 校验模型可用 =="
for port in "$HOME_PORT" "$AWAY_PORT"; do
  models=$(curl -s --max-time 5 "localhost:$port/api/tags" 2>/dev/null | grep -oE '"name":"[^"]+"' | head -3 | tr '\n' ' ')
  echo "  :$port → ${models:-无}"
done
echo "(共享 :11434 nemotron 不在本脚本管辖, 只读使用)"
