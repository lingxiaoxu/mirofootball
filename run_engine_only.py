"""纯引擎对阵任意两队（无模型, 快）→ 验证强度差异化(A)。
用法: python run_engine_only.py Germany Japan 3000"""
import asyncio, json, sys, httpx
ENGINE = "http://localhost:7000"
def load(p): return json.load(open(p))
HOME = sys.argv[1] if len(sys.argv) > 1 else "Germany"
AWAY = sys.argv[2] if len(sys.argv) > 2 else "Japan"
N = int(sys.argv[3]) if len(sys.argv) > 3 else 3000
init = {"team1": load(f"data/teams_engine/{HOME}.json"),
        "team2": load(f"data/teams_engine/{AWAY}.json"),
        "pitch": load("engine/init_config/pitch.json")}

async def main():
    async with httpx.AsyncClient(timeout=30) as h:
        md = (await h.post(f"{ENGINE}/initiate", json=init)).json()
        home, away = md["kickOffTeam"]["teamID"], md["secondTeam"]["teamID"]
        poss = {home: 0, away: 0}
        for it in range(N):
            if it == N // 2:
                md = (await h.post(f"{ENGINE}/secondhalf", json={"matchDetails": md})).json()
                if "ball" not in md: print("secondhalf err"); return
            nm = (await h.post(f"{ENGINE}/iterate", json={"matchDetails": md})).json()
            if "ball" not in nm: print(f"iterate err @ {it}"); return
            md = nm
            b = md["ball"]
            if b.get("withPlayer") and b.get("Player"):   # 真实口径: 仅控球拍
                wt = b.get("withTeam")
                if wt in poss: poss[wt] += 1
        ks, ss = md["kickOffTeamStatistics"], md["secondTeamStatistics"]
        tot = poss[home] + poss[away] or 1
        print(f"=== {HOME}(rating {init['team1']['rating']}) vs {AWAY}(rating {init['team2']['rating']}) · {N} 拍 ===")
        print(f"  比分   {HOME} {ks['goals']} - {ss['goals']} {AWAY}")
        print(f"  控球   {HOME} {poss[home]/tot:.2f} / {AWAY} {poss[away]/tot:.2f}")
        print(f"  射门   {HOME} {ks['shots']} / {AWAY} {ss['shots']}")
        print(f"  扑救   {HOME} {ks.get('saves')} / {AWAY} {ss.get('saves')}")

asyncio.run(main())
