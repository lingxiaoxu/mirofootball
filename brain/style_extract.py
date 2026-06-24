"""style_extract —— 每队 team_style 向量（plan 04 §4）。

主源：Mac 预测盘 data/mac/team_styles.json(真实 metrics + 10 风格码) + squad.json(强度/核心球员)。
辅源：API-FOOTBALL data/api/(阵型 from lineups)。Mac 缺则回退 API-FOOTBALL 聚合。
team_style 喂 MatchDirector config + 决策 prompt（球队区分）。
"""
import json, os

ROOT = os.path.join(os.path.dirname(__file__), "..")
DAPI = os.path.join(ROOT, "data", "api")
DMAC = os.path.join(ROOT, "data", "mac")
OUT = os.path.join(ROOT, "data", "styles")
os.makedirs(OUT, exist_ok=True)

# 10 风格码 → (press_intensity, tempo)
STYLE_PT = {
    "high_press": (0.80, 0.70), "low_block": (0.25, 0.35), "possession": (0.55, 0.50),
    "direct": (0.50, 0.75), "dominant_attack": (0.70, 0.70), "clinical": (0.50, 0.55),
    "high_volume": (0.60, 0.72), "set_piece": (0.50, 0.45), "balanced": (0.50, 0.50),
    "contained": (0.35, 0.40),
}

def jload(p):
    return json.load(open(p)) if os.path.exists(p) else None

def api_formation(team_name):
    ts = jload(os.path.join(DAPI, f"team_stats_{team_name}.json"))
    resp = (ts or {}).get("response") if ts else None
    lus = resp.get("lineups", []) if isinstance(resp, dict) else []
    return max(lus, key=lambda x: x.get("played", 0))["formation"] if lus else "4-3-3"

def main():
    # 48 队清单(all48: name→API ID); 回退 8 队文件
    f48 = os.path.join(DAPI, "_target_team_ids_48.json")
    ids = json.load(open(f48))["all48"] if os.path.exists(f48) else json.load(open(os.path.join(DAPI, "_target_team_ids.json")))
    mac_ts = jload(os.path.join(DMAC, "team_styles.json")) or {"teams": []}
    mac_sq = jload(os.path.join(DMAC, "squad.json")) or {"teams": []}
    # 按 Mac "name" 字段索引(= WC 队名, 精确匹配 48 队)
    ts_idx = {t["name"]: t for t in mac_ts.get("teams", [])}
    sq_idx = {t["name"]: t for t in mac_sq.get("teams", [])}

    for nm, tid in ids.items():
        if not tid:
            continue
        mt = ts_idx.get(nm); ms = sq_idx.get(nm)
        formation = api_formation(nm)
        if mt:  # 主源：Mac 真实风格
            m = mt.get("metrics", {})
            codes = [s["code"] for s in mt.get("styles", [])]
            primary = codes[0] if codes else "balanced"
            press, tempo = STYLE_PT.get(primary, (0.5, 0.5))
            style = {
                "team": nm, "team_id": tid, "source": "mac_team_styles",
                "style_label": mt.get("style"), "style_codes": codes, "cluster": mt.get("cluster"),
                "formation": formation,
                "possession_target": round(m.get("possession", 0.5), 3),
                "directness": round(m.get("directness", 0.5), 3),
                "press_intensity": press, "tempo": tempo,
                "pass_completion": m.get("pass_pct"),
                "shots_avg": m.get("shots"), "xg_avg": m.get("xg"), "chance_q": m.get("chance_q"),
            }
            if ms:  # 强度 + 核心球员
                style.update({"score_z": ms.get("score_z"), "mw_rating": ms.get("mw_rating"),
                              "ga_per90": ms.get("ga_per90"),
                              "top_players": [p["name"] for p in ms.get("top_players", [])]})
            print(f"  {nm}: {style['style_label']} | poss={style['possession_target']} "
                  f"direct={style['directness']} press={press} shots={style['shots_avg']} "
                  f"xg={style['xg_avg']} z={style.get('score_z')}")
        else:  # 回退：无 Mac 数据
            style = {"team": nm, "team_id": tid, "source": "default", "formation": formation,
                     "possession_target": 0.5, "directness": 0.5, "press_intensity": 0.5, "tempo": 0.5}
            print(f"  {nm}: (无 Mac 数据, 默认)")
        json.dump(style, open(os.path.join(OUT, f"{nm}.json"), "w"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
