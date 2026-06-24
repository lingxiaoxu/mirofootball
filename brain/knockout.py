"""淘汰赛层（plan 05 §3）：加时 + 点球大战 + 换人 + 红牌 + 晋级判定。
编排层补，复用引擎函数(secondhalf / penaltyTaken / 'NP')，不改物理。
engine = async callable(action:str, payload:dict)->md（即 MatchDirector._engine 的偏函数）。
"""
from typing import Callable, Awaitable


def is_level(md) -> bool:
    return md["kickOffTeamStatistics"]["goals"] == md["secondTeamStatistics"]["goals"]


def score(md):
    return md["kickOffTeamStatistics"]["goals"], md["secondTeamStatistics"]["goals"]


def _players(md, side):
    return md["kickOffTeam" if side == "home" else "secondTeam"]["players"]


import json as _json, os as _os
_POS_MAP = {"Goalkeeper": "GK", "Defender": "CB", "Midfielder": "CM", "Attacker": "CF"}
_SKILL_BY_POS = {  # 替补技能默认(无真实评分时按位置), 与引擎 skill 字段对齐
    "GK": {"passing": "60", "shooting": "30", "tackling": "45", "saving": "78", "agility": "70",
           "strength": "70", "penalty_taking": "40", "perception": "70", "control": "60", "jumping": "78"},
    "CB": {"passing": "68", "shooting": "40", "tackling": "80", "saving": "20", "agility": "62",
           "strength": "80", "penalty_taking": "45", "perception": "72", "control": "62", "jumping": "78"},
    "CM": {"passing": "82", "shooting": "62", "tackling": "70", "saving": "20", "agility": "72",
           "strength": "70", "penalty_taking": "60", "perception": "78", "control": "78", "jumping": "65"},
    "CF": {"passing": "70", "shooting": "82", "tackling": "45", "saving": "20", "agility": "80",
           "strength": "72", "penalty_taking": "72", "perception": "78", "control": "80", "jumping": "72"},
}


def load_bench(team_name, starting_ids, n=7, base_id=900000):
    """从 data/api/squad_<team>.json 取非首发 → 引擎格式替补(currentPOS=NP, 技能按位置默认, 真实姓名)。
    plan §3.3 的 bench 来源(teams_engine 只有首发11人)。"""
    path = _os.path.join("data", "api", f"squad_{team_name}.json")
    if not _os.path.exists(path):
        return []
    try:
        resp = _json.load(open(path)).get("response", [])
        squad = resp[0].get("players", []) if resp else []
    except Exception:
        return []
    bench, used = [], set(str(i) for i in starting_ids)
    for i, sp in enumerate(squad):
        if str(sp.get("id")) in used:
            continue
        pos = _POS_MAP.get(sp.get("position"), "CM")
        bench.append({
            "playerID": base_id + i, "name": sp.get("name", "Sub"), "position": pos, "role": pos,
            "rating": 72, "skill": dict(_SKILL_BY_POS[pos]),
            "currentPOS": ["NP", "NP"], "originPOS": ["NP", "NP"], "intentPOS": ["NP", "NP"],
            "fitness": 100, "injured": False, "offside": False, "hasBall": False, "action": "none",
            "height": 180, "stats": {"cards": {"yellow": 0, "red": 0}, "goals": 0,
                                     "tackles": {"total": 0}, "passes": {"total": 0}, "shots": {"total": 0}}})
        if len(bench) >= n:
            break
    return bench


def apply_sub(md, out_id, in_player):
    """换人:出场者 currentPOS='NP'(引擎跳过), 替补顶位置。plan §3.3。"""
    for side in ("kickOffTeam", "secondTeam"):
        for i, p in enumerate(md[side]["players"]):
            if p.get("playerID") == out_id:
                pos = p.get("originPOS", p.get("currentPOS"))
                np = dict(in_player)
                np.update({"currentPOS": list(pos), "originPOS": list(pos), "intentPOS": list(pos),
                           "offside": False, "hasBall": False, "action": "none"})
                md[side]["players"][i] = np
                return True
    return False


def red_card(md, player_id):
    """红牌罚下 → currentPOS='NP'(引擎 != 'NP' 判断会跳过, 自动少打一人)。plan §3.4。"""
    for side in ("kickOffTeam", "secondTeam"):
        for p in md[side]["players"]:
            if p.get("playerID") == player_id:
                p["currentPOS"] = ["NP", "NP"]
                p["injured"] = p.get("injured", False)
                return True
    return False


def top_takers(md, side, n=11):
    """按 penalty_taking 排序选罚球手(在场的)。"""
    ps = [p for p in _players(md, side) if p.get("currentPOS", [None])[0] != "NP"]
    return sorted(ps, key=lambda p: int(p.get("skill", {}).get("penalty_taking", 50) or 50), reverse=True)[:n]


async def extra_time(engine: Callable[[str, dict], Awaitable[dict]], md, et_iters: int = 1000):
    """加时 2×15′:复用引擎 secondhalf(half++/换边/重置) + 同一 tick 循环。plan §3.1。
    返回 md(只跑纯引擎推进, ET 期不接 LLM 以控时; 需要可接)。"""
    for _ in range(2):                       # ET 上、下半场
        md = await engine("secondhalf", {"matchDetails": md})
        for _ in range(et_iters):
            nm = await engine("iterate", {"matchDetails": md})
            if "ball" in nm:
                md = nm
    return md


async def shootout(engine: Callable[[str, dict], Awaitable[dict]], md, rounds: int = 5):
    """点球大战:每脚调引擎 /penalty(penaltyTaken+checkGoalScored 原生解算)。plan §3.2。
    返回 ('home'/'away', sa, sb, log)。"""
    A = top_takers(md, "home"); B = top_takers(md, "away")
    sa = sb = 0
    log = []

    async def take(side, taker):
        r = await engine("penalty", {"matchDetails": md, "takerTeam": side, "takerID": taker["playerID"]})
        return bool(r.get("scored"))

    for rnd in range(rounds):
        if await take("home", A[rnd % len(A)]):
            sa += 1
        log.append(("home", rnd, sa))
        if await take("away", B[rnd % len(B)]):
            sb += 1
        log.append(("away", rnd, sb))
        # 提前锁定(剩余球数无法追平)
        rem = rounds - rnd - 1
        if sa > sb + rem or sb > sa + rem:
            break
    i = 0
    while sa == sb:                          # 突然死亡
        if await take("home", A[i % len(A)]):
            sa += 1
        if await take("away", B[i % len(B)]):
            sb += 1
        i += 1
        if i > 20:
            break
    return ("home" if sa >= sb else "away"), sa, sb, log


async def run_to_decision(engine, md, knockout: bool = True, et_iters: int = 1000):
    """FT 已跑完 → 平局且淘汰赛 → 加时 → 仍平 → 点球大战 → 决出 winner。plan §3.5。
    返回 {winner, method, score, shootout}。"""
    gh, ga = score(md)
    if not knockout or not is_level(md):
        return {"winner": "home" if gh > ga else "away", "method": "regulation",
                "score": [gh, ga], "md": md}
    md = await extra_time(engine, md, et_iters)
    gh, ga = score(md)
    if not is_level(md):
        return {"winner": "home" if gh > ga else "away", "method": "extra_time",
                "score": [gh, ga], "md": md}
    winner, sa, sb, log = await shootout(engine, md)
    return {"winner": winner, "method": "shootout", "score": [gh, ga],
            "shootout": [sa, sb], "md": md}
