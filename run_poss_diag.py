"""控球率统计诊断:纯引擎跑 N 拍,对比【当前法(每拍按 withTeam)】vs【准确法(仅 withPlayer 控球拍)】。"""
import asyncio, json, sys, httpx
ENGINE = "http://localhost:7000"
def load(p): return json.load(open(p))
HOME = sys.argv[1] if len(sys.argv) > 1 else "Spain"
AWAY = sys.argv[2] if len(sys.argv) > 2 else "Germany"
N = int(sys.argv[3]) if len(sys.argv) > 3 else 2000
init = {"team1": load(f"data/teams_engine/{HOME}.json"), "team2": load(f"data/teams_engine/{AWAY}.json"),
        "pitch": load("engine/init_config/pitch.json")}

async def main():
    async with httpx.AsyncClient(timeout=30) as h:
        md = (await h.post(f"{ENGINE}/initiate", json=init)).json()
        home, away = md["kickOffTeam"]["teamID"], md["secondTeam"]["teamID"]
        cur = {home: 0, away: 0, "tot": 0}      # 当前法:每拍 withTeam
        ctl = {home: 0, away: 0}                 # 准确法:仅 withPlayer 控球拍
        loose = 0
        for it in range(N):
            if it == N // 2:
                md = (await h.post(f"{ENGINE}/secondhalf", json={"matchDetails": md})).json()
            md = (await h.post(f"{ENGINE}/iterate", json={"matchDetails": md})).json()
            if "ball" not in md: print("err"); return
            b = md["ball"]; wt = b.get("withTeam")
            cur["tot"] += 1
            if wt in cur: cur[wt] += 1
            if b.get("withPlayer") and b.get("Player"):
                if wt in ctl: ctl[wt] += 1
            else:
                loose += 1
        ct = cur["tot"] or 1; tt = (ctl[home] + ctl[away]) or 1
        print(f"=== {HOME} vs {AWAY} · {N} 拍 ===")
        print(f"  散球/飞行(无人控)拍: {loose} ({loose*100//ct}%)  控球拍: {ctl[home]+ctl[away]}")
        print(f"  【当前法 每拍withTeam】 {HOME} {cur[home]/ct:.2f} / {AWAY} {cur[away]/ct:.2f}")
        print(f"  【准确法 仅控球拍】     {HOME} {ctl[home]/tt:.2f} / {AWAY} {ctl[away]/tt:.2f}")

asyncio.run(main())
