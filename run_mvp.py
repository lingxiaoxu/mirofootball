"""短集成：3 模型驱动引擎 20 拍 + 写轨迹。验证整链路（克制调用）。"""
import asyncio, json
from brain.orchestrator import simulate

def load(p): return json.load(open(p))
B = "engine/init_config"
init_body = {"team1": load(f"{B}/team1.json"), "team2": load(f"{B}/team2.json"), "pitch": load(f"{B}/pitch.json")}

md, traj, calls = asyncio.run(simulate("mvp_test", init_body, iters=20, gemma_every=8, brain_every=4, gemma_n=2))

print("模型调用次数:", calls)
print("比分:", md["kickOffTeamStatistics"]["goals"], "-", md["secondTeamStatistics"]["goals"])
print("轨迹行数:", traj.n, "->", traj.path)

lines = open(traj.path).read().splitlines()
f, l = json.loads(lines[0]), json.loads(lines[-1])
print("球 首->末:", f["ball"]["pos"], "->", l["ball"]["pos"])
pid = f["players"][0]["id"]
path = [json.loads(x)["players"][0]["pos"] for x in lines]
print(f"home 球员{pid} 路径(每拍连续):", path)
# 连续性检查：相邻拍位移是否都 <= sprint 上限(±2/轴 + 余量)
import math
maxstep = max((max(abs(path[i][0]-path[i-1][0]), abs(path[i][1]-path[i-1][1])) for i in range(1, len(path))), default=0)
print("该球员相邻拍最大单轴位移:", maxstep, "(应 <=4: run±1/sprint±2/带球±4 → 连续无瞬移)")
