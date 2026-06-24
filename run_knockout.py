"""淘汰赛 runner:FT(纯引擎求快) → 平局触发 加时 → 仍平 点球大战 → 决出 winner。
验证 plan 05 §3 编排层。用法: python run_knockout.py France Argentina [ft_iters]"""
import asyncio, json, sys, httpx
from brain import knockout as K

def load(p): return json.load(open(p))
HOME = sys.argv[1] if len(sys.argv) > 1 else "France"
AWAY = sys.argv[2] if len(sys.argv) > 2 else "Argentina"
FT = int(sys.argv[3]) if len(sys.argv) > 3 else 2000
t1, t2 = load(f"data/teams_engine/{HOME}.json"), load(f"data/teams_engine/{AWAY}.json")
pitch = load("engine/init_config/pitch.json")

async def main():
    async with httpx.AsyncClient(base_url="http://localhost:7000", timeout=60) as h:
        async def engine(action, payload):
            r = await h.post(f"/{action}", json=payload)
            return r.json()
        md = await engine("initiate", {"team1": t1, "team2": t2, "pitch": pitch})
        for it in range(FT):
            if it == FT // 2:
                md = await engine("secondhalf", {"matchDetails": md})
            nm = await engine("iterate", {"matchDetails": md})
            if "ball" in nm:
                md = nm
        print(f"FT 比分: {HOME} {K.score(md)[0]}-{K.score(md)[1]} {AWAY}  (平局={K.is_level(md)})")
        res = await K.run_to_decision(engine, md, knockout=True, et_iters=600)
        w = HOME if res["winner"] == "home" else AWAY
        line = f"=== 晋级: {w} | 方式: {res['method']} | 常规赛比分 {res['score']}"
        if res.get("shootout"):
            line += f" | 点球大战 {res['shootout'][0]}-{res['shootout'][1]}"
        print(line + " ===")

asyncio.run(main())
