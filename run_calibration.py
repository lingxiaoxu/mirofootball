"""校准门 runner（plan 05§3.7 / 00§11 P7）：跑 N 场真实比赛(nemo+2gemma, gemma_n=6),
每场 measure+gate,汇总过门率。用法: python run_calibration.py <Home> <Away> [iters] [N]"""
import asyncio, json, sys
from brain.match_director import MatchDirector
from brain.llm_client import LLM
import brain.config as C
from brain import calibration_gate as CG

def load(p): return json.load(open(p))
def info(name, style, team):
    return {"name": name, "formation": style.get("formation"), "style": style.get("style_label"),
            "possession": style.get("possession_target"), "directness": style.get("directness"),
            "press_intensity": style.get("press_intensity"), "tempo": style.get("tempo"),
            "rating": team.get("rating"), "score_z": style.get("score_z"),
            "top_players": style.get("top_players")}

HOME = sys.argv[1] if len(sys.argv) > 1 else "Spain"
AWAY = sys.argv[2] if len(sys.argv) > 2 else "Germany"
ITERS = int(sys.argv[3]) if len(sys.argv) > 3 else 600
N = int(sys.argv[4]) if len(sys.argv) > 4 else 1
GN = int(sys.argv[5]) if len(sys.argv) > 5 else 6

async def one(idx):
    d = MatchDirector()
    d.home = LLM(C.GEMMA_HOME_URL, f"gemma-{HOME.lower()}", think=C.GEMMA_THINK)
    d.away = LLM(C.GEMMA_AWAY_URL, f"gemma-{AWAY.lower()}", think=C.GEMMA_THINK)
    t1, t2 = load(f"data/teams_engine/{HOME}.json"), load(f"data/teams_engine/{AWAY}.json")
    pitch = load("engine/init_config/pitch.json")
    sh, sa = load(f"data/styles/{HOME}.json"), load(f"data/styles/{AWAY}.json")
    st = d.create_match(HOME, AWAY)
    try:
        await d.prepare_match(st, info(HOME, sh, t1), info(AWAY, sa, t2))
    except Exception as e:
        print(f"  config-gen 异常: {str(e)[:50]}")
    if not st.config_generated:
        d.cfg = {"home": {"possession_target": 0.5}, "away": {"possession_target": 0.5}}
        st.config_generated = True
    st, md, traj, calls, stats = await d.run_match(
        st, t1, t2, pitch, iters=ITERS, gemma_every=12, brain_every=18, gemma_n=GN,
        kg_enabled=False, home_style=sh, away_style=sa)
    m = CG.measure(md, st)
    g = CG.gate(m)
    print(f"##### 场 {idx} ({HOME} {st.score_home}-{st.score_away} {AWAY}, {traj.n}拍, calls {calls}) #####")
    print(CG.format_report(m, g))
    return g["ok"]

async def main():
    oks = 0
    for i in range(N):
        oks += 1 if await one(i + 1) else 0
    print(f"\n===== 校准汇总: {oks}/{N} 场过门 ({HOME} vs {AWAY}, {ITERS}拍) =====")

asyncio.run(main())
