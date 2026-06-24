"""全场统计 dump:跑 N 拍纯引擎,打印两队完整 statistics + 准确控球率,对标真实比赛。"""
import asyncio, json, sys, httpx
ENGINE = "http://localhost:7000"
def load(p): return json.load(open(p))
HOME = sys.argv[1] if len(sys.argv) > 1 else "Germany"
AWAY = sys.argv[2] if len(sys.argv) > 2 else "Japan"
N = int(sys.argv[3]) if len(sys.argv) > 3 else 4000
init = {"team1": load(f"data/teams_engine/{HOME}.json"), "team2": load(f"data/teams_engine/{AWAY}.json"),
        "pitch": load("engine/init_config/pitch.json")}

async def main():
    async with httpx.AsyncClient(timeout=30) as h:
        md = (await h.post(f"{ENGINE}/initiate", json=init)).json()
        home, away = md["kickOffTeam"]["teamID"], md["secondTeam"]["teamID"]
        ctl = {home: 0, away: 0}
        # 统计每队传球(从球员 stats 累加 or 引擎事件)—— 先看引擎给什么
        for it in range(N):
            if it == N // 2:
                md = (await h.post(f"{ENGINE}/secondhalf", json={"matchDetails": md})).json()
            md = (await h.post(f"{ENGINE}/iterate", json={"matchDetails": md})).json()
            if "ball" not in md: print("err"); return
            b = md["ball"]
            if b.get("withPlayer") and b.get("Player"):
                if b.get("withTeam") in ctl: ctl[b["withTeam"]] += 1
        ks, ss = md["kickOffTeamStatistics"], md["secondTeamStatistics"]
        tt = (ctl[home] + ctl[away]) or 1
        def team_agg(side):
            ps = md[side]["players"]
            pas = sum((p.get("stats", {}).get("passes", {}) or {}).get("total", 0) for p in ps)
            yc = sum((p.get("stats", {}).get("cards", {}) or {}).get("yellow", 0) for p in ps)
            rc = sum((p.get("stats", {}).get("cards", {}) or {}).get("red", 0) for p in ps)
            return pas, yc, rc
        hp, hy, hr = team_agg("kickOffTeam"); ap, ay, ar = team_agg("secondTeam")
        print(f"=== {HOME} vs {AWAY} · {N} 拍 ===")
        print(f"  控球率(准确): {HOME} {ctl[home]/tt:.0%} / {AWAY} {ctl[away]/tt:.0%}")
        print(f"  传球(队级,累加球员): {HOME} {hp} / {AWAY} {ap}")
        print(f"  黄牌 {HOME} {hy}/{AWAY} {ay} | 红牌 {HOME} {hr}/{AWAY} {ar}")
        print(f"=== {HOME} 完整 statistics ==="); print("  ", json.dumps(ks, ensure_ascii=False))
        print(f"=== {AWAY} 完整 statistics ==="); print("  ", json.dumps(ss, ensure_ascii=False))
        # 球员级是否有传球统计
        p0 = md["kickOffTeam"]["players"][5]
        print(f"=== 球员级 stats 样例({p0.get('name')}) ==="); print("  ", json.dumps(p0.get("stats", {}), ensure_ascii=False))

asyncio.run(main())
