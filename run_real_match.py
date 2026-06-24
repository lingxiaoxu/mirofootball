"""真实球队对阵 —— 走【完整 MiroFish 管线】:
create_match → prepare_match(MatchConfigGenerator=改造自 SimulationConfigGenerator, brain 生成 config)
→ run_match(tick循环驱动引擎 + 全队 gemma 摆位 + nemo 持球 + intentTarget 混合) → report(ReportAgent ReACT)。
用法: python run_real_match.py <Home> <Away> [base|lora] [iters]"""
import asyncio, json, sys, os
from brain.match_director import MatchDirector
from brain.llm_client import LLM
import brain.config as C

def load(p): return json.load(open(p))
HOME = sys.argv[1] if len(sys.argv) > 1 else "Spain"
AWAY = sys.argv[2] if len(sys.argv) > 2 else "Germany"
t1, t2 = load(f"data/teams_engine/{HOME}.json"), load(f"data/teams_engine/{AWAY}.json")
pitch = load("engine/init_config/pitch.json")
sh, sa = load(f"data/styles/{HOME}.json"), load(f"data/styles/{AWAY}.json")

def info(name, style, team):
    return {"name": name, "formation": style.get("formation"), "style": style.get("style_label"),
            "possession": style.get("possession_target"), "directness": style.get("directness"),
            "press_intensity": style.get("press_intensity"), "tempo": style.get("tempo"),
            "rating": team.get("rating"), "score_z": style.get("score_z"),
            "top_players": style.get("top_players")}

async def main():
    d = MatchDirector()
    use_lora = len(sys.argv) <= 3 or sys.argv[3] != "base"
    if use_lora:
        d.home = LLM(C.GEMMA_HOME_URL, f"gemma-{HOME.lower()}", think=C.GEMMA_THINK)
        d.away = LLM(C.GEMMA_AWAY_URL, f"gemma-{AWAY.lower()}", think=C.GEMMA_THINK)
        print(f"用 LoRA 模型: gemma-{HOME.lower()}(:11436) vs gemma-{AWAY.lower()}(:11437)")
    st = d.create_match(HOME, AWAY)

    # 完整管线①: MiroFish ConfigGenerator 生成赛前 config(brain, 锚定真实风格); 失败回退真实风格
    try:
        await d.prepare_match(st, info(HOME, sh, t1), info(AWAY, sa, t2))
        print(f"MiroFish ConfigGenerator → home {json.dumps(d.cfg.get('home', {}), ensure_ascii=False)[:110]}")
    except Exception as e:
        print(f"config-gen 异常, 回退真实风格: {str(e)[:60]}")
    if not st.config_generated:
        ph, pa = sh["possession_target"], sa["possession_target"]; home_t = max(.35, min(.65, ph / (ph + pa)))
        d.cfg = {"home": {"possession_target": round(home_t, 3), "tempo": sh["tempo"], "directness": sh["directness"], "press_intensity": sh["press_intensity"]},
                 "away": {"possession_target": round(1 - home_t, 3), "tempo": sa["tempo"], "directness": sa["directness"], "press_intensity": sa["press_intensity"]}}
        st.config_generated = True
        print("(用真实风格 config)")

    gk_h = [p['name'] for p in t1['players'] if p['position'] == 'GK']
    gk_a = [p['name'] for p in t2['players'] if p['position'] == 'GK']
    print(f"对阵 {HOME}({sh['formation']}) vs {AWAY}({sa['formation']}) | 门将 {gk_h}/{gk_a}")
    iters = int(sys.argv[4]) if len(sys.argv) > 4 else 1500

    # 完整管线②: run_match (MatchDirector 主循环)
    st, md, traj, calls, stats = await d.run_match(
        st, t1, t2, pitch, iters=iters, gemma_every=12, brain_every=18, gemma_n=6,
        kg_enabled=False, home_style=sh, away_style=sa)
    p1, p2 = st.possession()
    ks, ss = md["kickOffTeamStatistics"], md["secondTeamStatistics"]
    def tpass(side): return sum((p.get("stats", {}).get("passes", {}) or {}).get("total", 0) for p in md[side]["players"])
    print(f"calls {calls} | 比分 {HOME} {st.score_home}-{st.score_away} {AWAY} | 轨迹 {traj.n}")
    print(f"  控球 {HOME} {p1} / {AWAY} {p2} | 传球 {tpass('kickOffTeam')}/{tpass('secondTeam')} | 射门 {ks['shots']['total']}/{ss['shots']['total']} | 角球 {ks.get('corners')}/{ss.get('corners')}")

    # 完整管线③: MiroFish ReportAgent 赛后解说(ReACT 读 trajectory+stats)
    try:
        traj_path = os.path.join(d._dir(st.match_id), "trajectory.jsonl")
        rep = await d.report(st, traj_path, stats)
        print(f"=== ReportAgent 赛后解说 ===\n{str(rep)[:600]}")
    except Exception as e:
        print(f"report 异常: {str(e)[:80]}")

asyncio.run(main())
