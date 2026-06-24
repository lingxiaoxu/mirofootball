"""端到端：MatchDirector 组织一场短比赛（含 #1 控球反馈 + #2 ReACT 报告 + #4 KG）。"""
import asyncio, json
from brain.match_director import MatchDirector

def load(p): return json.load(open(p))
B = "engine/init_config"
t1, t2, pitch = load(f"{B}/team1.json"), load(f"{B}/team2.json"), load(f"{B}/pitch.json")
def info(t): return {"name": t.get("name"), "rating": t.get("rating"), "style_hint": "balanced"}

async def main():
    d = MatchDirector()
    st = d.create_match(t1.get("name", "Home"), t2.get("name", "Away"))
    print("① create →", st.match_id)
    st = await d.prepare_match(st, info(t1), info(t2))
    print("② prepare →", st.status.value, "| config:", json.dumps(d.cfg, ensure_ascii=False)[:160])
    st, md, traj, calls, stats = await d.run_match(
        st, t1, t2, pitch, iters=24, gemma_every=8, brain_every=4, gemma_n=2, kg_enabled=True)
    ph, pa = st.possession()
    print("③ run →", st.status.value, "| calls", calls,
          f"| 比分 {st.score_home}-{st.score_away} | 控球 {ph}/{pa} | 轨迹 {traj.n}")
    print("   stats:", stats)
    rep = await d.report(st, traj.path, stats)
    print("④ report(ReACT) →", rep[:320])

asyncio.run(main())
