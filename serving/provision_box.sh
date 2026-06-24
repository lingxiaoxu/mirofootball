#!/bin/bash
# Box 镜像/部署所需 6 类资产同步(plan 06§3.1-3.3)。把一台 box 配成可跑比赛的镜像。
# 用法: provision_box.sh <target_user@host> [ssh_key]
# 资产: ① engine/  ② brain/  ③ serving/lora/*.gguf + Modelfile  ④ data/teams_engine + styles
#        ⑤ .venv 需求(requirements)  ⑥ Modelfile/启动脚本
set -eu
TARGET="${1:?用法: provision_box.sh user@host [ssh_key]}"
KEY="${2:-$HOME/.ssh/id_ed25519}"
ROOT="$HOME/mirofootball"
RSH="ssh -i $KEY -o StrictHostKeyChecking=no"
R="rsync -az --delete -e \"$RSH\""

echo "== provision $TARGET =="
echo "① engine/";        eval $R "$ROOT/engine/"        "$TARGET:~/mirofootball/engine/"
echo "② brain/";         eval $R --exclude '__pycache__' "$ROOT/brain/" "$TARGET:~/mirofootball/brain/"
echo "③ serving/lora/";  eval $R "$ROOT/serving/lora/"  "$TARGET:~/mirofootball/serving/lora/"
echo "④ data/";          eval $R "$ROOT/data/teams_engine/" "$TARGET:~/mirofootball/data/teams_engine/"
                         eval $R "$ROOT/data/styles/"   "$TARGET:~/mirofootball/data/styles/"
echo "⑤ requirements";   eval $R "$ROOT/requirements.txt" "$TARGET:~/mirofootball/" 2>/dev/null || echo "  (无 requirements.txt, 跳过)"
echo "⑥ 启动/部署脚本";   eval $R "$ROOT/serving/"*.sh   "$TARGET:~/mirofootball/serving/"

echo "== 镜像一致性校验(06§3.4): 比对关键资产 checksum =="
LOCAL_SUM=$(cat "$ROOT/serving/lora/"*.gguf 2>/dev/null | sha256sum | cut -d' ' -f1)
REMOTE_SUM=$($RSH "$TARGET" "cat ~/mirofootball/serving/lora/*.gguf 2>/dev/null | sha256sum | cut -d' ' -f1")
if [ "$LOCAL_SUM" = "$REMOTE_SUM" ]; then
  echo "  ✓ LoRA 资产一致 ($LOCAL_SUM)"
else
  echo "  ✗ LoRA 不一致! local=$LOCAL_SUM remote=$REMOTE_SUM"
fi
echo "完成。目标机随后: bash serving/start_ollama_gemma.sh + node engine/server.js(run_in_background)"
