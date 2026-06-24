"""#1 较长 + 非对称控球目标的 LLM 驱动比赛，看控球反馈是否拉向目标 + 涌现统计。"""
import asyncio, json
from brain.match_director import MatchDirector

def load(p): return json.load(open(p))
B = "engine/init_config"
t1, t2, pitch = load(f"{B}/team1.json"), load(f"{B}/team2.json"), load(f"{B}/pitch.json")

async def main():
    d = MatchDirector()
    st = d.create_match("Home", "Away")
    # 强制非对称控球目标（跳过 brain prepare 省调用）
    d.cfg = {"home": {"possession_target": 0.62, "tempo": 0.5, "directness": 0.4, "press_intensity": 0.6},
             "away": {"possession_target": 0.38, "tempo": 0.6, "directness": 0.6, "press_intensity": 0.4}}
    st.config_generated = True
    st, md, traj, calls, stats = await d.run_match(
        st, t1, t2, pitch, iters=150, gemma_every=12, brain_every=6, gemma_n=3, kg_enabled=False)
    ph, pa = st.possession()
    print(f"calls {calls} | 轨迹 {traj.n}")
    print(f"控球 home {ph} / away {pa}   (目标 0.62 / 0.38)")
    print(f"比分 {st.score_home}-{st.score_away} | 射门 {stats['shots']} | 角球 {stats['corners']} | 犯规 {stats['fouls']}")

asyncio.run(main())
