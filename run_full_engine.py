"""#3 完整比赛（纯引擎，无模型，cheap）→ 看涌现统计是否落真实均值区间（01 §0.2）。"""
import asyncio, json, httpx
ENGINE = "http://localhost:7000"
def load(p): return json.load(open(p))
B = "engine/init_config"
init = {"team1": load(f"{B}/team1.json"), "team2": load(f"{B}/team2.json"), "pitch": load(f"{B}/pitch.json")}

async def main(N=4000):
    async with httpx.AsyncClient(timeout=30) as h:
        md = (await h.post(f"{ENGINE}/initiate", json=init)).json()
        home, away = md["kickOffTeam"]["teamID"], md["secondTeam"]["teamID"]
        poss = {home: 0, away: 0}; seq = {home: 0, away: 0}; last = None
        for it in range(N):
            if it == N // 2:
                r = await h.post(f"{ENGINE}/secondhalf", json={"matchDetails": md})
                md = r.json()
                if "ball" not in md:
                    print(f"⚠️ secondhalf 在拍 {it} 出错: {str(md)[:300]}"); return
            r = await h.post(f"{ENGINE}/iterate", json={"matchDetails": md})
            nm = r.json()
            if "ball" not in nm:
                print(f"⚠️ iterate 在拍 {it} 出错(status {r.status_code}): {str(nm)[:400]}")
                print(f"   上一拍事件: {md.get('iterationLog', [])[-3:]}")
                return
            md = nm
            wt = md["ball"].get("withTeam")
            if wt in poss: poss[wt] += 1
            if wt and wt != last:
                if wt in seq: seq[wt] += 1
                last = wt
        ks, ss = md["kickOffTeamStatistics"], md["secondTeamStatistics"]
        tot = poss[home] + poss[away] or 1
        print(f"=== 纯引擎 {N} 拍（半场 {N//2}）===")
        print(f"比分 home {ks['goals']} - {ss['goals']} away")
        print(f"控球 home {poss[home]/tot:.2f} / away {poss[away]/tot:.2f}   (真实区间 0.35–0.65)")
        print(f"射门 home {ks['shots']} away {ss['shots']}   (真实 8–18/队)")
        print(f"角球 home {ks.get('corners')} away {ss.get('corners')}   (真实 ~5)")
        print(f"犯规 home {ks.get('fouls')} away {ss.get('fouls')}   (真实 ~11)")
        print(f"扑救 home {ks.get('saves')} away {ss.get('saves')}")
        print(f"控球段(进攻回合) home {seq[home]} away {seq[away]}   (真实危险进攻 8–14, 总控球段更多)")

asyncio.run(main())
