#!/bin/bash
# 跑完比赛释放 gemma 内存(卸载两个 daemon 上载入的模型, 释放膨胀的KV)。
# 不影响 48 队 LoRA 部署(注册+GGUF都在磁盘, 下次比赛自动重载)。
# 用法: release_gemma.sh        # 卸载腾内存
#       release_gemma.sh --reload # 卸载后重载到干净32K(下次比赛起步快)
OLL="$HOME/mirofootball/serving/ollama-new/bin/ollama"
echo "释放前: 可用 $(free -m|awk '/Mem:/{print $7}')MB | gemma $(ps -eo rss,comm|awk '/llama-server/{printf "%.1fG ",$1/1048576}')"
declare -A LOADED
for p in 11436 11437; do
  m=$(OLLAMA_HOST=127.0.0.1:$p "$OLL" ps 2>/dev/null | awk 'NR>1{print $1; exit}')
  [ -n "$m" ] && { LOADED[$p]=$m; OLLAMA_HOST=127.0.0.1:$p timeout 20 "$OLL" stop "$m" </dev/null >/dev/null 2>&1 && echo "  :$p 卸载 $m"; }
done
sleep 3
echo "释放后: 可用 $(free -m|awk '/Mem:/{print $7}')MB"
if [ "$1" = "--reload" ]; then
  for p in 11436 11437; do
    [ -n "${LOADED[$p]}" ] && curl -s --max-time 40 localhost:$p/api/generate -d "{\"model\":\"${LOADED[$p]}\",\"prompt\":\"hi\",\"stream\":false,\"options\":{\"num_predict\":1}}" >/dev/null 2>&1 && echo "  :$p 重载 ${LOADED[$p]}"
  done
  sleep 2; echo "重载后: 可用 $(free -m|awk '/Mem:/{print $7}')MB | gemma $(ps -eo rss,comm|awk '/llama-server/{printf "%.1fG ",$1/1048576}')"
fi
