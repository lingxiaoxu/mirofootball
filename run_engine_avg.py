"""多场纯引擎取平均 → 强度差异化是否稳健(A)。用法: python run_engine_avg.py Germany Japan 4 1500"""
import asyncio, json, sys, httpx, statistics as st
ENGINE = "http://localhost:7000"
def load(p): return json.load(open(p))
HOME, AWAY = sys.argv[1], sys.argv[2]
REPS = int(sys.argv[3]) if len(sys.argv) > 3 else 4
N = int(sys.argv[4]) if len(sys.argv) > 4 else 1500
init = {"team1": load(f"data/teams_engine/{HOME}.json"),
        "team2": load(f"data/teams_engine/{AWAY}.json"),
        "pitch": load("engine/init_config/pitch.json")}

async def one(h):
    md = (await h.post(f"{ENGINE}/initiate", json=init)).json()
    home, away = md["kickOffTeam"]["teamID"], md["secondTeam"]["teamID"]
    poss = {home: 0, away: 0}
    for it in range(N):
        if it == N // 2:
            md = (await h.post(f"{ENGINE}/secondhalf", json={"matchDetails": md})).json()
            if "ball" not in md: return None
        nm = (await h.post(f"{ENGINE}/iterate", json={"matchDetails": md})).json()
        if "ball" not in nm: return None
        md = nm
        wt = md["ball"].get("withTeam")
        if wt in poss: poss[wt] += 1
    ks, ss = md["kickOffTeamStatistics"], md["secondTeamStatistics"]
    tot = poss[home] + poss[away] or 1
    return (poss[home] / tot, ks["goals"], ss["goals"], ks["shots"]["total"], ss["shots"]["total"])

async def main():
    async with httpx.AsyncClient(timeout=30) as h:
        rows = [r for r in [await one(h) for _ in range(REPS)] if r]
    ph = [r[0] for r in rows]
    print(f"=== {HOME}(r{init['team1']['rating']}) vs {AWAY}(r{init['team2']['rating']}) · {len(rows)}场×{N}拍 平均 ===")
    print(f"  控球 {HOME} {st.mean(ph):.2f} (±{st.pstdev(ph):.2f}) / {AWAY} {1-st.mean(ph):.2f}")
    print(f"  进球 {HOME} {st.mean(r[1] for r in rows):.1f} / {AWAY} {st.mean(r[2] for r in rows):.1f}")
    print(f"  射门 {HOME} {st.mean(r[3] for r in rows):.1f} / {AWAY} {st.mean(r[4] for r in rows):.1f}")

asyncio.run(main())
