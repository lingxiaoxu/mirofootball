"""build_sft v4 —— comprehensive：以 EA FC26 内在能力为主画像 + API 比赛倾向为辅 + Mac 球队风格。
(v3 仅用稀疏 API 统计; v4 用 FIFA 稠密评分→决策更真实、球员特异、全员覆盖)。plan 04§5 升级。

每球员画像三层:
- FIFA(主, brain/fc26): overall + pac/sho/pas/dri/def/phy + finishing/vision/tackle/interceptions + playStyles + position
- API(辅, club_<pid>): shoot90/pass90/drib90/def90(实际产量/状态; 缺则忽略)
- Mac 风格(队): style/formation/possession/press/directness
决策(intra-game): ON-BALL action(shoot/pass/dribble/clear) + OFF-BALL target_zone+posture, 由 FIFA 能力+场区+风格决定。
输出 data/sft/<Team>.jsonl(2400/队, chat 格式)。scope 对 48 队统一。
"""
import json, os, glob, random
from collections import Counter
from brain.fc26 import get as fc_get

ROOT = os.path.join(os.path.dirname(__file__), "..")
DAPI = os.path.join(ROOT, "data", "api")
OUT = os.path.join(ROOT, "data", "sft")
os.makedirs(OUT, exist_ok=True)
COLS = "ABCDEF"

OFFBALL_SYS = ("You are an off-ball positioning brain for ONE football player. Pick target_zone "
               "(grid col A-F x row 1-9, e.g. C6) and posture. Reflect THIS PLAYER's FIFA attributes "
               "(pace/defending/physical/work) and the team style. Output ONLY JSON.")
ONBALL_SYS = ("You are the on-ball decision brain for ONE football player who HAS the ball. Pick action "
              "(shoot/pass/dribble/clear) reflecting THIS PLAYER's FIFA ability (finishing/shooting/"
              "dribbling/passing) + field position. Output ONLY JSON.")

def _n(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0

def zrc(z): return COLS.index(z[0]), int(z[1:]) - 1
def mkz(c, r): return f"{COLS[max(0,min(5,c))]}{max(1,min(9,r+1))}"

def api_tendency(team_id, name_norm_map):
    """API club_<pid> 辅助倾向(可选): 返回 {fc_name_norm: {shoot90,pass90,drib90,def90}}。缺则空。"""
    import unicodedata, re
    def nm(s):
        s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
        return re.sub(r"[^a-z ]", "", s).strip()
    out = {}
    for f in glob.glob(os.path.join(DAPI, "squad_*.json")):
        sq = json.load(open(f)).get("response", [])
        if not (sq and sq[0].get("team", {}).get("id") == team_id): continue
        for p in sq[0]["players"]:
            cf = os.path.join(DAPI, f"club_{p['id']}.json")
            if not os.path.exists(cf): continue
            apps = sh = pa = dr = ti = 0.0
            resp = json.load(open(cf)).get("response") or []
            if not resp: continue
            for s in (resp[0].get("statistics", []) or []):
                apps += _n((s.get("games") or {}).get("appearences"))
                sh += _n((s.get("shots") or {}).get("total")); pa += _n((s.get("passes") or {}).get("total"))
                dr += _n((s.get("dribbles") or {}).get("attempts"))
                tk = s.get("tackles") or {}; ti += _n(tk.get("total")) + _n(tk.get("interceptions"))
            g = max(apps, 1)
            out[nm(p["name"])] = {"shoot90": round(sh/g, 2), "pass90": round(pa/g, 1),
                                  "drib90": round(dr/g, 2), "def90": round(ti/g, 2)}
    return out

def onball_action(fp, ball_z):
    """持球决策：FIFA finishing/shooting/dribbling/passing + 场区。row 大=对方门(攻区)。"""
    bc, br = zrc(ball_z)
    pos = (fp.get("pos_fc") or "")
    if pos == "GK": return {"action": "pass"}
    attacking = br >= 6
    # 终结能力强 + 在攻区 → 射(FIFA finishing/shooting 高)
    if attacking and (fp["finishing"] >= 78 or fp["shooting"] >= 78 or pos in ("ST", "CF")):
        return {"action": "shoot"}
    # 盘带能力强 → 突破(FIFA dribbling 高)
    if fp["dribbling"] >= 82 and br >= 3:
        return {"action": "dribble"}
    # 防守型在后场 → 解围
    if fp["defending"] >= 75 and br <= 3 and random.random() < 0.35:
        return {"action": "clear"}
    return {"action": "pass"}

ATTACK = {"direct", "dominant_attack", "clinical", "high_volume"}

def offball(fp, ball_z, my_z, in_poss, style):
    codes = style.get("style_codes") or ["balanced"]; primary = codes[0]
    bc, br = zrc(ball_z); mc, mr = zrc(my_z)
    pos = (fp.get("pos_fc") or "")
    if pos == "GK": return {"target_zone": mkz(2, min(2, mr)), "posture": "hold"}
    attacker = pos in ("ST", "CF", "LW", "RW") or fp["shooting"] >= 75
    defender = pos in ("CB", "LB", "RB", "CDM") or fp["defending"] >= 72
    if in_poss:
        if attacker or primary in ATTACK or style.get("directness", 0) > 0.5:
            posture, tr, tc = "run_behind", min(8, br + 2), bc
        elif fp["passing"] >= 80 or primary == "possession":
            posture, tr, tc = "support", br, bc + random.choice([-1, 1])
        else:
            posture, tr, tc = "support", br, mc
    else:
        if defender and style.get("press_intensity", 0.5) < 0.4:
            posture, tr, tc = "track_back", max(0, br - 2), mc
        elif primary in {"high_press", "dominant_attack"} or fp["defending"] >= 75 or style.get("press_intensity", 0.5) > 0.6:
            posture, tr, tc = "press", br, bc
        else:
            posture, tr, tc = "hold", mr, mc
    if defender: tr = min(tr, 5)
    if attacker: tr = max(tr, 3)
    return {"target_zone": mkz(tc, tr), "posture": posture}

_APIPOS = {"Goalkeeper": "GK", "Defender": "CB", "Midfielder": "CM", "Attacker": "ST"}
def _api_to_profile(name, api_pos, a):
    """API 每90统计 → FIFA 同尺度(0-99)画像, 让决策函数统一处理 FIFA 缺的球员。"""
    clip = lambda v: int(max(20, min(95, v)))
    return {
        "name": name, "src": "api", "pos_fc": _APIPOS.get(api_pos, "CM"), "pos_type": api_pos,
        "overall": clip(60 + a.get("shoot90", 0) * 6 + a.get("pass90", 0) * 0.15),
        "shooting": clip(50 + a.get("shoot90", 0) * 15), "finishing": clip(50 + a.get("shoot90", 0) * 16),
        "passing": clip(55 + a.get("pass90", 0) * 0.55), "vision": clip(55 + a.get("pass90", 0) * 0.5),
        "dribbling": clip(52 + a.get("drib90", 0) * 12), "defending": clip(45 + a.get("def90", 0) * 8),
        "pace": 72, "interceptions": clip(45 + a.get("def90", 0) * 8),
        "tackle": clip(45 + a.get("def90", 0) * 8), "play_styles": [], "api": a,
    }

def team_players(team_name, team_id, fc):
    """该队真实阵容(squad) → FIFA profile(主) + API 倾向(辅)。无 squad 时回退 FIFA 按国籍取该队 top。"""
    profs = []
    api_t = api_tendency(team_id, None) if team_id else {}
    import unicodedata, re
    def nm(s):
        s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
        return re.sub(r"[^a-z ]", "", s).strip()
    squad = None
    for f in glob.glob(os.path.join(DAPI, "squad_*.json")):
        sq = json.load(open(f)).get("response", [])
        if sq and sq[0].get("team", {}).get("id") == team_id:
            squad = sq[0]["players"]; break
    if squad:
        for p in squad:
            r = fc.match(p["name"], team_name)
            if r:                                        # FIFA 主画像
                prof = fc.to_profile(r); prof["api"] = api_t.get(nm(p["name"]), {})
                prof["src"] = "fifa"
            else:                                        # 回退: API 统计 → 同尺度(0-99)画像
                a = api_t.get(nm(p["name"]))
                if not a: continue                       # 两者都无 → 跳
                prof = _api_to_profile(p.get("name"), p.get("position"), a)
            profs.append(prof)
    else:   # 回退: 该国籍 FIFA overall top 23
        from brain.fc26 import NAT_ALIAS
        nat = NAT_ALIAS.get(team_name, team_name)
        cand = sorted((r for _, _, r in fc.by_nat.get(nat, [])), key=lambda r: -_n(r.get("overallRating")))[:23]
        for r in cand:
            prof = fc.to_profile(r); prof["api"] = {}; profs.append(prof)
    return profs

def build_team(name, tid, fc, style):
    profs = team_players(name, tid, fc)
    if not profs: return None
    random.seed(hash(name) % 10000)
    rows = []
    for _ in range(2400):
        fp = random.choice(profs)
        ball_z = random.choice(COLS) + str(random.randint(1, 9))
        my_z = random.choice(COLS) + str(random.randint(1, 9))
        ts = {"team": name, "style": style.get("style_label"), "formation": style.get("formation"),
              "possession": style.get("possession_target"), "press": style.get("press_intensity")}
        # 决策画像视图(FIFA 主 + API 辅)
        pv = {"name": fp["name"], "pos": fp.get("pos_fc"), "overall": fp["overall"],
              "shooting": fp["shooting"], "passing": fp["passing"], "dribbling": fp["dribbling"],
              "defending": fp["defending"], "pace": fp["pace"], "finishing": fp["finishing"],
              "vision": fp["vision"], "play_styles": fp.get("play_styles", [])}
        if fp.get("api"): pv["recent"] = fp["api"]   # API 近期产量(辅)
        if random.random() < 0.5:
            user = {"world": {"ball_zone": ball_z, "i_have_ball": True}, "me": pv, "team_style": ts}
            dec, sys = onball_action(fp, ball_z), ONBALL_SYS
        else:
            in_poss = random.random() < style.get("possession_target", 0.5)
            user = {"world": {"ball_zone": ball_z, "holder_team": "home" if in_poss else "away"},
                    "me": {**pv, "zone": my_z}, "team_style": ts}
            dec, sys = offball(fp, ball_z, my_z, in_poss, style), OFFBALL_SYS
        rows.append({"messages": [{"role": "system", "content": sys},
                                  {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                                  {"role": "assistant", "content": json.dumps(dec, ensure_ascii=False)}]})
    with open(os.path.join(OUT, f"{name}.jsonl"), "w") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    acts = Counter(json.loads(r["messages"][2]["content"]).get("action") or
                   json.loads(r["messages"][2]["content"]).get("posture") for r in rows)
    return len(profs), dict(acts)

def main():
    fc = fc_get()
    ids = json.load(open(os.path.join(DAPI, "_target_team_ids_48.json")))["all48"]
    for name, tid in ids.items():
        sf = os.path.join(ROOT, "data", "styles", f"{name}.json")
        style = json.load(open(sf)) if os.path.exists(sf) else {"style_label": "balanced", "possession_target": 0.5}
        res = build_team(name, tid, fc, style)
        if res: print(f"  {name}: {res[0]} FIFA球员 → 2400例 | {res[1]}")
        else: print(f"  {name}: 无 FIFA 匹配, 跳过")

if __name__ == "__main__":
    main()
