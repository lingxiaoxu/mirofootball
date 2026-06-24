"""build_sft v3 —— per-team SFT，**基于 API-Football 每场每球员真实动作**（用户指示：输出是 intra-game,
多参考每场球员在比赛中的动作）。plan 04 §5。

数据源:
- data/api/fx_players_<fid>.json (fixtures/players): 每场每球员 shots/passes/key/dribbles/tackles/
  interceptions/duels/saves/rating/position → 聚合成每名真实球员的"动作画像"。
- data/styles/<Team>.json (Mac 真实风格) + data/teams_engine (名单).
生成两类样本(都是 intra-game 决策):
- ON-BALL: 持球 → action(shoot/pass/dribble/clear), 由该球员真实 射门/盘带/传球倾向 + 场区决定。
- OFF-BALL: 无球 → target_zone + posture, 由该球员 防守/进攻倾向 + 球队风格决定。
输出 data/sft/<Team>.jsonl(chat 格式)。给 Box B 训 per-team LoRA。
"""
import json, os, glob, random
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(__file__), "..")
DAPI = os.path.join(ROOT, "data", "api")
OUT = os.path.join(ROOT, "data", "sft")
os.makedirs(OUT, exist_ok=True)
COLS = "ABCDEF"

OFFBALL_SYS = ("You are an off-ball positioning brain for ONE football player. Pick target_zone "
               "(grid col A-F x row 1-9, e.g. C6) and posture. Reflect THIS PLAYER's real in-match "
               "tendencies and the team style. Output ONLY JSON.")
ONBALL_SYS = ("You are the on-ball decision brain for ONE football player who HAS the ball. Pick action "
              "(shoot/pass/dribble/clear) reflecting THIS PLAYER's real in-match action tendencies and "
              "field position. Output ONLY JSON.")

def _n(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0

def goals_assists(team_id):
    """从 events 抽该队【进球者/助攻者】名字集合(强化射门/关键传倾向)。"""
    scorers, assisters = set(), set()
    for f in glob.glob(os.path.join(DAPI, "events_*.json")):
        for e in json.load(open(f)).get("response", []):
            if e.get("team", {}).get("id") != team_id: continue
            if e.get("type") == "Goal":
                if e.get("player", {}).get("name"): scorers.add(e["player"]["name"])
                if e.get("assist", {}).get("name"): assisters.add(e["assist"]["name"])
    return scorers, assisters

def player_profiles(team_id):
    """每名球员动作画像：俱乐部整季(club_<pid>, 样本大=主)聚合所有赛事条目 → 每场rate;
    无俱乐部数据则回退 WC 每场(fx_players)。进球/助攻者另加成。"""
    # 该队名单 pid→(name,pos)
    roster = {}
    for f in glob.glob(os.path.join(DAPI, f"squad_*.json")):
        sq = json.load(open(f)).get("response", [])
        if sq and sq[0].get("team", {}).get("id") == team_id:
            for p in sq[0]["players"]:
                roster[p["id"]] = (p.get("name"), p.get("position"))
    scorers, assisters = goals_assists(team_id)
    prof = {}
    for pid, (nm, posg) in roster.items():
        cf = os.path.join(DAPI, f"club_{pid}.json")
        apps = sh = go = ass = pa = key = dr = ti = sv = 0.0
        rates = []
        if os.path.exists(cf):
            for s in (json.load(open(cf)).get("response", [{}])[0].get("statistics", []) or []):
                g = s.get("games", {}) or {}
                apps += _n(g.get("appearences"))
                if g.get("rating"): rates.append(_n(g["rating"]))
                sh += _n((s.get("shots") or {}).get("total")); go += _n((s.get("goals") or {}).get("total"))
                ass += _n((s.get("goals") or {}).get("assists")); pa += _n((s.get("passes") or {}).get("total"))
                key += _n((s.get("passes") or {}).get("key")); dr += _n((s.get("dribbles") or {}).get("attempts"))
                tk = s.get("tackles") or {}; ti += _n(tk.get("total")) + _n(tk.get("interceptions"))
                sv += _n((s.get("goals") or {}).get("saves"))
        g1 = max(apps, 1)
        # API 位置: Goalkeeper/Defender/Midfielder/Attacker (squad) → 简码 G/D/M/F
        pos = {"Goalkeeper": "G", "Defender": "D", "Midfielder": "M", "Attacker": "F"}.get(posg, "M")
        shoot_pg = sh / g1 + (0.6 if nm in scorers else 0)        # 进球者射门倾向+
        key_pg = key / g1 + (0.5 if nm in assisters else 0)       # 助攻者关键传+
        prof[pid] = {"name": nm, "pos": pos, "apps": int(apps),
                     "rating": round(sum(rates) / len(rates), 2) if rates else 6.7,
                     "shoot90": round(shoot_pg, 2), "pass90": round(pa / g1, 1), "key90": round(key_pg, 2),
                     "drib90": round(dr / g1, 2), "def90": round(ti / g1, 2), "saves90": round(sv / g1, 2),
                     "scorer": nm in scorers, "assister": nm in assisters}
    return prof

def zrc(z): return COLS.index(z[0]), int(z[1:]) - 1
def mkz(c, r): return f"{COLS[max(0,min(5,c))]}{max(1,min(9,r+1))}"

def onball_action(prof, ball_z, style):
    """持球决策：由真实 射门/盘带/传球 倾向 + 场区 决定（intra-game 动作）。row 大=对方门。"""
    bc, br = zrc(ball_z)
    is_gk = prof["pos"] == "G"
    if is_gk:
        return {"action": "pass"}
    attacking_third = br >= 6
    if attacking_third and (prof["shoot90"] >= 1.0 or prof["pos"] == "F"):   # 真实爱射 + 在攻区 → 射
        return {"action": "shoot"}
    if prof["drib90"] >= 1.5 and br >= 3:                                    # 真实爱盘带 → 突破
        return {"action": "dribble"}
    if prof["def90"] >= 3 and br <= 3 and random.random() < 0.4:             # 防守型在后场 → 解围
        return {"action": "clear"}
    return {"action": "pass"}                                               # 默认传导

ATTACK = {"direct", "dominant_attack", "clinical", "high_volume"}

def offball(prof, ball_z, my_z, in_poss, style):
    """无球站位：球员真实 进攻/防守 倾向 + 球队风格。"""
    codes = style.get("style_codes") or ["balanced"]; primary = codes[0]
    bc, br = zrc(ball_z); mc, mr = zrc(my_z)
    is_gk = prof["pos"] == "G"
    if is_gk:
        return {"target_zone": mkz(2, min(2, mr)), "posture": "hold"}
    attacker = prof["pos"] == "F" or prof["shoot90"] >= 1.0
    defender = prof["pos"] == "D" or prof["def90"] >= 3
    if in_poss:
        if attacker or primary in ATTACK or style.get("directness", 0) > 0.5:
            posture, tr, tc = "run_behind", min(8, br + 2), bc
        elif prof["pass90"] >= 35 or primary == "possession":               # 真实组织者 → 支援给传球点
            posture, tr, tc = "support", br, bc + random.choice([-1, 1])
        else:
            posture, tr, tc = "support", br, mc
    else:
        if defender and style.get("press_intensity", 0.5) < 0.4:            # 防守型 + 低位 → 退防
            posture, tr, tc = "track_back", max(0, br - 2), mc
        elif primary in {"high_press", "dominant_attack"} or prof["def90"] >= 3 or style.get("press_intensity", 0.5) > 0.6:
            posture, tr, tc = "press", br, bc                               # 高压/拼抢型 → 上抢
        else:
            posture, tr, tc = "hold", mr, mc
    if prof["pos"] == "D": tr = min(tr, 5)
    if prof["pos"] == "F": tr = max(tr, 3)
    return {"target_zone": mkz(tc, tr), "posture": posture}

def main():
    ids = json.load(open(os.path.join(DAPI, "_target_team_ids.json")))
    for i, (name, tid) in enumerate(ids.items()):
        if not tid: continue
        sf = os.path.join(ROOT, "data", "styles", f"{name}.json")
        if not os.path.exists(sf): print(f"  {name}: 无 style, 跳过"); continue
        style = json.load(open(sf))
        prof = player_profiles(tid)
        if not prof: print(f"  {name}: 无真实球员动作数据(未踢/未拉), 跳过"); continue
        random.seed(i)
        rows = []
        players = list(prof.values())
        for _ in range(2400):
            p = random.choice(players)
            ball_z = random.choice(COLS) + str(random.randint(1, 9))
            my_z = random.choice(COLS) + str(random.randint(1, 9))
            ts = {"team": name, "style": style.get("style_label"), "formation": style.get("formation"),
                  "possession": style.get("possession_target"), "press": style.get("press_intensity")}
            pview = {"name": p["name"], "pos": p["pos"], "shoot90": p["shoot90"], "pass90": p["pass90"],
                     "drib90": p["drib90"], "def90": p["def90"], "rating": p["rating"]}
            if random.random() < 0.5:   # ON-BALL(持球动作)
                user = {"world": {"ball_zone": ball_z, "i_have_ball": True}, "me": pview, "team_style": ts}
                dec = onball_action(p, ball_z, style); sys = ONBALL_SYS
            else:                       # OFF-BALL(站位)
                in_poss = random.random() < style.get("possession_target", 0.5)
                user = {"world": {"ball_zone": ball_z, "holder_team": "home" if in_poss else "away"},
                        "me": {**pview, "zone": my_z}, "team_style": ts}
                dec = offball(p, ball_z, my_z, in_poss, style); sys = OFFBALL_SYS
            rows.append({"messages": [{"role": "system", "content": sys},
                                      {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                                      {"role": "assistant", "content": json.dumps(dec, ensure_ascii=False)}]})
        fp = os.path.join(OUT, f"{name}.jsonl")
        with open(fp, "w") as f:
            for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
        from collections import Counter
        acts = Counter(json.loads(r["messages"][2]["content"]).get("action") or
                       json.loads(r["messages"][2]["content"]).get("posture") for r in rows)
        print(f"  {name}: {len(players)} 真实球员 → {len(rows)} 例 | 动作/姿态 {dict(acts)}")

if __name__ == "__main__":
    main()
