"""data_map —— API-FOOTBALL 数据 → 引擎 team JSON（真实 11 人含门将 + 技术值）。

plan 04 §3.2（原计划 FC26→skill；这里改用 API-FOOTBALL 数据派生 skill，因为用户用 api-football）。
- 名单/位置/身高来自 squad + players；skill 由 match rating + 位置 + 统计派生（API 没有 0-100 评分）。
- role→engine position 8 值映射（plan 04 §3.2 / 02 §2）。
- 输出满足 validate.js：恰好 11 人 + 必填字段。
"""
import json, os, glob

D = os.path.join(os.path.dirname(__file__), "..", "data", "api")
DMAC = os.path.join(os.path.dirname(__file__), "..", "data", "mac")
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "teams_engine")
os.makedirs(OUT, exist_ok=True)

def _mac_squad():
    p = os.path.join(DMAC, "squad.json")
    return {t["team_id"]: t for t in json.load(open(p)).get("teams", [])} if os.path.exists(p) else {}

MAC_SQUAD = _mac_squad()   # 球队强度 score_z / mw_rating / top_players（接进技术值, A: 强度差异化）

# API position(Goalkeeper/Defender/Midfielder/Attacker) + 细分 → 引擎 8 值
def eng_pos(api_pos, grid=None, idx=0):
    p = (api_pos or "").lower()
    if p.startswith("goal"): return "GK"
    if p.startswith("def"):  return ["LB", "CB", "CB", "RB"][idx % 4]
    if p.startswith("mid"):  return ["LM", "CM", "CM", "RM"][idx % 4]
    if p.startswith("att"):  return "ST"
    return "CM"

def _num(s, d=0):
    try: return int(float(str(s).split()[0]))
    except Exception: return d

def _clip(v, lo=20, hi=99): return max(lo, min(hi, int(v)))

def load_players(team):
    """合并 players_<team>_pN（含 stats/rating/height）。返回 {player_id: {...}}。"""
    out = {}
    for f in sorted(glob.glob(os.path.join(D, f"players_{team}_p*.json"))):
        for row in json.load(open(f)).get("response", []):
            pl = row.get("player", {}); st = (row.get("statistics") or [{}])[0]
            games = st.get("games", {}) or {}
            out[pl.get("id")] = {
                "name": pl.get("name"), "height": _num(pl.get("height"), 180),
                "pos": games.get("position"), "rating": float(games.get("rating") or 6.7),
                "shots": (st.get("shots") or {}), "goals": (st.get("goals") or {}),
                "passes": (st.get("passes") or {}), "tackles": (st.get("tackles") or {}),
                "appearences": games.get("appearences") or 0,
            }
    return out

def skills(api_pos, pdata, mult=1.0):
    """match rating(~6-8) + 位置 + 统计 → 引擎 0-99 skill；mult=球队强度乘子(A)。"""
    r = pdata.get("rating", 6.7)
    base = _clip((r - 5.5) / 3.0 * 100, 40, 95)   # 6.5→33%..8.5→100% 映射到 ~55-95
    p = (api_pos or "").lower()
    pacc = _num((pdata.get("passes") or {}).get("accuracy"), 0)
    is_gk = p.startswith("goal")
    is_def = p.startswith("def")
    is_att = p.startswith("att")
    raw = {
        "passing": pacc if pacc > 40 else base,
        "shooting": base + (12 if is_att else -10),
        "tackling": base + (12 if is_def else -8),
        "saving": base if is_gk else 20,
        "agility": base,
        "strength": base + (6 if is_def or is_gk else 0),
        "penalty_taking": base + (8 if is_att else 0),
        "perception": base,
        "control": base + (8 if is_att else 0),
    }
    out = {k: str(_clip(v * mult)) for k, v in raw.items()}
    out["jumping"] = str(_clip(pdata.get("height", 180) - 100))  # 身高派生, 不随强度缩放
    return out

# 标准 4-3-3 起始坐标（引擎竖直球场, 顶半场; pitch 680x1050）
FORMATION_433 = {
    "GK": [340, 60], "LB": [120, 230], "CB": [260, 200], "CB2": [420, 200], "RB": [560, 230],
    "LM": [180, 430], "CM": [340, 400], "RM": [500, 430], "LW": [160, 620], "ST": [340, 640], "RW": [520, 620],
}

def to_engine_team(team_name, team_id):
    squad_f = os.path.join(D, f"squad_{team_name}.json")
    sq = json.load(open(squad_f)).get("response", []) if os.path.exists(squad_f) else []
    roster = sq[0].get("players", []) if sq else []
    pdata = load_players(team_id if False else team_name) if False else load_players(team_name)
    # 按位置分组挑首发 11：1 GK + 4 DEF + 3 MID + 3 ATT，优先有 rating/出场的
    groups = {"Goalkeeper": [], "Defender": [], "Midfielder": [], "Attacker": []}
    for pl in roster:
        pos = pl.get("position"); groups.setdefault(pos, []).append(pl)
    def pick(group, n):
        cand = groups.get(group, [])
        cand = sorted(cand, key=lambda x: pdata.get(x.get("id"), {}).get("rating", 6.5), reverse=True)
        return cand[:n]
    starters = pick("Goalkeeper", 1) + pick("Defender", 4) + pick("Midfielder", 3) + pick("Attacker", 3)
    # 不足 11 用任意补
    if len(starters) < 11:
        rest = [p for p in roster if p not in starters]
        starters += rest[:11 - len(starters)]
    starters = starters[:11]

    # 球队强度（Mac score_z）→ 技术值乘子；核心球员再加成
    ms = MAC_SQUAD.get(team_name.lower())
    strz = ms.get("score_z", 0.0) if ms else 0.0
    team_mult = max(0.85, min(1.18, 1 + 0.06 * strz))   # z~0.13→1.01, z~1.8→1.11, 弱队<1
    top_names = {p["name"] for p in (ms.get("top_players", []) if ms else [])}
    team_rating = str(int((ms.get("mw_rating", 8.0) if ms else 8.0) * 10))

    from brain.fc26 import get as _fc_get
    fc = _fc_get()
    players = []
    di = {"Defender": 0, "Midfielder": 0}
    for i, pl in enumerate(starters):
        api_pos = pl.get("position")
        pdat = pdata.get(pl.get("id"), {})
        pmult = team_mult * (1.08 if pl.get("name") in top_names else 1.0)
        fr = fc.match(pl.get("name"), team_name)   # FIFA 内在能力(主); 无匹配回退 API 派生
        if api_pos == "Defender": ep = ["LB", "CB", "CB", "RB"][di["Defender"] % 4]; di["Defender"] += 1
        elif api_pos == "Midfielder": ep = ["LM", "CM", "RM"][di["Midfielder"] % 3]; di["Midfielder"] += 1
        elif (api_pos or "").startswith("Goal"): ep = "GK"
        else: ep = "ST"
        # 起始坐标
        key = ep if ep != "CB" else ("CB" if di["Defender"] <= 2 else "CB2")
        pos_xy = FORMATION_433.get(ep, [340, 400])
        players.append({
            "playerID": i + 1, "name": pl.get("name") or pdat.get("name") or f"P{i+1}",
            "position": ep, "role": api_pos,            # position 给引擎; role 给 LLM(02 §2)
            "rating": str(int(fc.to_profile(fr)["overall"]) if fr else int(pdat.get("rating", 6.7) * 10)),
            "skill": fc.to_skill(fr) if fr else skills(api_pos, pdat, pmult),
            "currentPOS": list(pos_xy), "originPOS": list(pos_xy), "intentPOS": list(pos_xy),
            "fitness": 100, "injured": False, "offside": False, "hasBall": False, "action": "none",
            "height": str(pdat.get("height", 180)),
            "stats": {"goals": 0, "shots": {"total": 0, "on": 0, "off": 0},
                      "passes": {"total": 0, "on": 0, "off": 0},
                      "tackles": {"total": 0, "on": 0, "off": 0},
                      "cards": {"yellow": 0, "red": 0}, "saves": 0},
        })
    return {"name": team_name, "teamID": team_id, "rating": team_rating, "players": players}

def main():
    f48 = os.path.join(D, "_target_team_ids_48.json")
    ids = json.load(open(f48))["all48"] if os.path.exists(f48) else json.load(open(os.path.join(D, "_target_team_ids.json")))
    for nm, tid in ids.items():
        if not tid: continue
        t = to_engine_team(nm, tid)
        json.dump(t, open(os.path.join(OUT, f"{nm}.json"), "w"), ensure_ascii=False, indent=1)
        gk = [p["name"] for p in t["players"] if p["position"] == "GK"]
        print(f"  {nm}: 11 人 (GK={gk}) → data/teams_engine/{nm}.json")

if __name__ == "__main__":
    main()
