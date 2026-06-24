"""双机协调器（plan 03§10 / 06§3.5）。

拓扑:
  Box A (本机)  = serving(:11434 共享 nemotron + :11436/:11437 自有 gemma) + 引擎 + 比赛编排。
  Box B (ConnectX 直连, 主机/用户/密钥见 .env 的 BOX_B_*) = LoRA 训练(不碰 Box A 显存)。

职责: 在 Box B 上训一支球队的 LoRA → 转 GGUF → rsync 回 Box A → ollama 部署 gemma-<team>。
铁律: 绝不在 Box A 跑训练(显存给 nemotron); Box B 长任务用 tmux(linger=no, ssh 断开不杀);
      绝不 pkill 含自身命令行的模式(会自杀 ssh 会话)。
"""
import os, subprocess, sys, shlex

BOX_B_HOST = os.environ.get("BOX_B_HOST", "")        # 从 .env 注入(勿硬编码真实 IP/主机)
BOX_B_USER = os.environ.get("BOX_B_USER", "")        # 从 .env 注入(勿硬编码用户名)
SSH_KEY = os.path.expanduser(os.environ.get("BOX_B_SSH_KEY", "~/.ssh/id_ed25519"))
REMOTE_ROOT = "~/mirofootball"
LOCAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLL = "~/mirofootball/serving/ollama-new/bin/ollama"


def _ssh(cmd, timeout=120):
    full = ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
            f"{BOX_B_USER}@{BOX_B_HOST}", cmd]
    return subprocess.run(full, capture_output=True, text=True, timeout=timeout)


def train_on_box_b(team, tmux_session=None):
    """在 Box B 用 tmux 跑训练(持久, ssh 断开不死)。返回 tmux 会话名。"""
    sess = tmux_session or f"train_{team.lower()}"
    # 用 tmux new-session -d 后台; 训练脚本是 brain/train_team_lora.py
    inner = f"cd {REMOTE_ROOT} && . .venv/bin/activate && python brain/train_team_lora.py {shlex.quote(team)}"
    cmd = f"tmux has-session -t {sess} 2>/dev/null && echo RUNNING || tmux new-session -d -s {sess} {shlex.quote(inner)}"
    r = _ssh(cmd)
    print(f"[Box B] train {team}: {r.stdout.strip() or 'launched tmux:' + sess} {r.stderr.strip()[:80]}")
    return sess


def training_done(team):
    """检查 Box B 上该队 adapter 是否产出(轻量, 不 pkill)。"""
    r = _ssh(f"ls -1 {REMOTE_ROOT}/lora/{team}/adapter_model.safetensors 2>/dev/null && echo OK || echo NO")
    return "OK" in r.stdout


def convert_to_gguf(team):
    """在 Box B 把 LoRA 转 GGUF(llama.cpp convert_lora_to_gguf.py)。"""
    cmd = (f"cd {REMOTE_ROOT} && . .venv/bin/activate && "
           f"python llama.cpp/convert_lora_to_gguf.py lora/{team} --outfile serving/lora/{team}.gguf 2>&1 | tail -2")
    r = _ssh(cmd, timeout=300)
    print(f"[Box B] gguf {team}: {r.stdout.strip()[-160:]}")
    return r.returncode == 0


def fetch_gguf(team):
    """rsync Box B 的 GGUF → Box A serving/lora/。"""
    src = f"{BOX_B_USER}@{BOX_B_HOST}:{REMOTE_ROOT}/serving/lora/{team}.gguf"
    dst = os.path.join(LOCAL_ROOT, "serving", "lora", f"{team}.gguf")
    r = subprocess.run(["rsync", "-az", "-e", f"ssh -i {SSH_KEY} -o StrictHostKeyChecking=no", src, dst],
                       capture_output=True, text=True, timeout=300)
    ok = r.returncode == 0 and os.path.exists(dst)
    print(f"[Box A] fetch {team}.gguf: {'OK ' + str(os.path.getsize(dst)) + 'B' if ok else 'FAIL ' + r.stderr[:80]}")
    return ok


def deploy_on_box_a(team):
    """Box A 本地 ollama create gemma-<team>(FROM 底座 + ADAPTER gguf)。不重启 daemon。"""
    mf = os.path.join(LOCAL_ROOT, "serving", "lora", f".Modelfile.{team}")
    with open(mf, "w") as f:
        f.write(f"FROM gemma4:e2b-it-qat\nADAPTER ./{team}.gguf\n")
    r = subprocess.run(f"cd {os.path.join(LOCAL_ROOT, 'serving', 'lora')} && "
                       f"OLLAMA_HOST=127.0.0.1:11436 {OLL} create gemma-{team.lower()} -f .Modelfile.{team}",
                       shell=True, capture_output=True, text=True, timeout=300)
    print(f"[Box A] deploy gemma-{team.lower()}: {'OK' if r.returncode == 0 else 'FAIL ' + r.stderr[:100]}")
    return r.returncode == 0


def pipeline(team):
    """整条: 训练→(等)→转 GGUF→拉回→部署。训练耗时长, 这里仅触发+检查; 完成后跑后半段。"""
    print(f"=== coordinator pipeline: {team} ===")
    if training_done(team):
        print(f"[Box B] {team} adapter 已存在, 跳过训练")
    else:
        train_on_box_b(team)
        print(f"[Box B] {team} 训练已在 tmux 后台启动; 完成后再次运行本脚本走转换/部署")
        return
    if convert_to_gguf(team) and fetch_gguf(team):
        deploy_on_box_a(team)


if __name__ == "__main__":
    team = sys.argv[1] if len(sys.argv) > 1 else "Spain"
    action = sys.argv[2] if len(sys.argv) > 2 else "pipeline"
    {"train": train_on_box_b, "convert": convert_to_gguf, "fetch": fetch_gguf,
     "deploy": deploy_on_box_a, "pipeline": pipeline}.get(action, pipeline)(team)
