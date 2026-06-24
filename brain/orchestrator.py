"""最小 orchestrator —— 3 模型驱动引擎一个 tick 循环 + 逐拍写轨迹。

MVP/gentle 版：调用克制（gemma 每 EVERY 拍、限量；brain 仅持球决策点、有节流），顺序为主。
铁律：只对模型发推理请求，绝不重启/改配置；引擎在 :7000。轨迹每拍写（01 §0.4）。
"""
import asyncio
import httpx
from brain import config as C
from brain.llm_client import LLM
from brain import world as W
from brain.trajectory import TrajectoryWriter

OFFBALL_SYS = ("You are an off-ball positioning brain for ONE football player. "
               "Pick target_zone (grid col A-F x row 1-9, e.g. C6) and posture. Output ONLY JSON.")
OFFBALL_SCHEMA = {"type": "object", "properties": {
    "target_zone": {"type": "string"},
    "posture": {"type": "string", "enum": ["hold", "press", "drop", "support",
                                           "run_behind", "widen", "tuck_in", "track_back", "overlap"]}},
    "required": ["target_zone", "posture"]}

ONBALL_SYS = ("You are the on-ball decision brain for the player in possession. "
              "Choose ONE action. Output ONLY JSON.")
ONBALL_SCHEMA = {"type": "object", "properties": {
    "action": {"type": "string", "enum": ["pass", "throughBall", "cross", "shoot", "cleared", "boot"]},
    "intent": {"type": "string"}},
    "required": ["action"]}


async def engine(http, path, payload):
    r = await http.post(f"{C.ENGINE_URL}/{path}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


async def simulate(match_id, init_body, iters=20, gemma_every=8, brain_every=4, gemma_n=2):
    home = LLM(C.GEMMA_HOME_URL, C.GEMMA_MODEL, think=C.GEMMA_THINK)
    away = LLM(C.GEMMA_AWAY_URL, C.GEMMA_MODEL, think=C.GEMMA_THINK)
    brain = LLM(C.BRAIN_URL, C.BRAIN_MODEL, think=C.BRAIN_THINK)
    traj = TrajectoryWriter(match_id, C.DATA_DIR, C.ITER_PER_HALF)
    calls = {"gemma": 0, "brain": 0}

    async with httpx.AsyncClient() as http:
        md = await engine(http, "initiate", init_body)
        for it in range(iters):
            wd = W.build_world(md)
            hid, hteam = W.holder(md)

            # 无球跑位：每 gemma_every 拍，两队各限量 gemma_n 人（顺序，克制）
            if it % gemma_every == 0:
                for side, llm in (("home", home), ("away", away)):
                    picks = [p for p in W.players(md, side)
                             if W.on_pitch(p) and p.get("playerID") != hid][:gemma_n]
                    for p in picks:
                        d = await llm.decide(OFFBALL_SYS,
                                             {"world": wd, "me": {"id": p["playerID"], "role": p.get("position"),
                                              "zone": W.xy_to_zone(md["pitchSize"], p.get("currentPOS"))}},
                                             OFFBALL_SCHEMA, 64)
                        calls["gemma"] += 1
                        if "_error" not in d:
                            W.inject_offball(md, p["playerID"], d)

            # 持球决策：仅持球者、按 brain_every 节流（顺序）
            if hid and it % brain_every == 0:
                d = await brain.decide(ONBALL_SYS,
                                       {"world": wd, "me": {"id": hid, "team": W.team_side(md, hteam)}},
                                       ONBALL_SCHEMA, 200)
                calls["brain"] += 1
                if "_error" not in d:
                    W.inject_onball(md, hid, d)

            md = await engine(http, "iterate", {"matchDetails": md})
            traj.append_tick(md, it)

    traj.close()
    return md, traj, calls
