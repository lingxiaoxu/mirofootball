"""API-FOOTBALL v3 数据拉取器 —— WC2026(league=1,season=2026) + 8 支目标队。

- 限速友好：每次请求间小延迟 + 读 x-ratelimit 头；
- 断点续传：每个响应存 data/api/<name>.json，已存在则跳过（再跑只补缺的）；
- 覆盖用户要的端点：timezone/countries/leagues/teams/venues/standings/fixtures/
  injuries/predictions/coachs/players/transfers/trophies/sidelined/odds。
铁律：只读 GET；key 从 .env 读，绝不硬编码/入库。
"""
import json, os, sys, time
import httpx

BASE = "https://v3.football.api-sports.io"
LEAGUE, SEASON = 1, 2026
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "api")
TARGETS = ["France", "Argentina", "Netherlands", "Japan", "Norway", "Germany", "Spain", "Brazil"]

def _key():
    p = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(p):
        for line in open(p):
            if line.startswith("API_FOOTBALL_KEY="):
                return line.strip().split("=", 1)[1]
    return os.environ.get("API_FOOTBALL_KEY", "")

KEY = _key()
HDR = {"x-apisports-key": KEY}
os.makedirs(OUT, exist_ok=True)
_calls = 0

def get(name, path, params=None, force=False):
    """GET + 存盘（断点续传）。name=文件名（去 .json）。返回 response 数组。"""
    global _calls
    fp = os.path.join(OUT, f"{name}.json")
    if os.path.exists(fp) and not force:
        return json.load(open(fp)).get("response", [])
    with httpx.Client(timeout=40) as c:
        r = c.get(f"{BASE}{path}", headers=HDR, params=params or {})
    _calls += 1
    rem = r.headers.get("x-ratelimit-requests-remaining", "?")
    j = r.json()
    errs = j.get("errors")
    if errs and (errs if isinstance(errs, list) else list(errs.values())):
        print(f"  ⚠️ {name}: errors={errs}")
    json.dump(j, open(fp, "w"), ensure_ascii=False)
    print(f"  [{_calls}] {name}  results={j.get('results')}  day_remaining={rem}")
    time.sleep(0.5)  # 限速：别突发（PDF 警告）
    return j.get("response", [])

def paged(name, path, params, max_pages=10):
    """分页拉取（players 等 20/页）。"""
    first = get(f"{name}_p1", path, {**params, "page": 1})
    fp = os.path.join(OUT, f"{name}_p1.json")
    total = json.load(open(fp)).get("paging", {}).get("total", 1)
    allr = list(first)
    for pg in range(2, min(total, max_pages) + 1):
        allr += get(f"{name}_p{pg}", path, {**params, "page": pg})
    return allr

def main():
    if not KEY:
        print("无 API key"); sys.exit(1)
    print(f"=== 拉 WC2026 reference ===")
    get("timezone", "/timezone")
    get("countries", "/countries")
    get("league_wc", "/leagues", {"id": LEAGUE, "season": SEASON})
    get("seasons", "/leagues/seasons")
    teams = get("teams_wc", "/teams", {"league": LEAGUE, "season": SEASON})
    get("standings_wc", "/standings", {"league": LEAGUE, "season": SEASON})
    get("rounds_wc", "/fixtures/rounds", {"league": LEAGUE, "season": SEASON})
    fixtures = get("fixtures_wc", "/fixtures", {"league": LEAGUE, "season": SEASON})

    # 解析 8 队 ID
    name2id = {}
    for t in teams:
        tm = t.get("team", {})
        name2id[tm.get("name")] = tm.get("id")
    ids = {}
    for want in TARGETS:
        tid = name2id.get(want)
        if tid is None:  # 容错模糊匹配
            for nm, i in name2id.items():
                if want.lower() in (nm or "").lower():
                    tid = i; break
        ids[want] = tid
    print("=== 8 队 team_id ===", ids)
    json.dump(ids, open(os.path.join(OUT, "_target_team_ids.json"), "w"))

    print("=== 每队: 统计/教练/阵容/球员/伤病/转会/球场 ===")
    coach_ids = []
    for nm, tid in ids.items():
        if not tid:
            print(f"  {nm}: 未找到 team_id, 跳过"); continue
        get(f"team_stats_{nm}", "/teams/statistics", {"league": LEAGUE, "season": SEASON, "team": tid})
        get(f"squad_{nm}", "/players/squads", {"team": tid})
        paged(f"players_{nm}", "/players", {"team": tid, "season": SEASON})
        get(f"injuries_{nm}", "/injuries", {"league": LEAGUE, "season": SEASON, "team": tid})
        get(f"transfers_{nm}", "/transfers", {"team": tid})
        coaches = get(f"coach_{nm}", "/coachs", {"team": tid})
        for ch in coaches:
            if ch.get("id"): coach_ids.append((nm, ch["id"]))
        # 球场
        venue = next((t["venue"]["id"] for t in teams if t["team"]["id"] == tid and t.get("venue", {}).get("id")), None)
        if venue: get(f"venue_{nm}", "/venues", {"id": venue})

    print("=== 教练: trophies/sidelined ===")
    for nm, cid in coach_ids:
        get(f"coach_trophies_{nm}_{cid}", "/trophies", {"coach": cid})
        get(f"coach_sidelined_{nm}_{cid}", "/sidelined", {"coach": cid})

    print("=== 8 队相关 fixture 的: lineups/statistics/players/predictions/odds ===")
    tid_set = {v for v in ids.values() if v}
    our_fx = []
    for fx in fixtures:
        tt = fx.get("teams", {})
        if tt.get("home", {}).get("id") in tid_set or tt.get("away", {}).get("id") in tid_set:
            our_fx.append((fx["fixture"]["id"], fx["fixture"]["status"]["short"]))
    print(f"  涉及 8 队的 fixtures: {len(our_fx)}")
    for fid, status in our_fx:
        get(f"fx_lineups_{fid}", "/fixtures/lineups", {"fixture": fid})
        get(f"fx_stats_{fid}", "/fixtures/statistics", {"fixture": fid})
        get(f"fx_players_{fid}", "/fixtures/players", {"fixture": fid})
        get(f"predictions_{fid}", "/predictions", {"fixture": fid})
        get(f"odds_{fid}", "/odds", {"fixture": fid})

    print(f"=== 完成. 本次实际请求 {_calls} 次 ===")

if __name__ == "__main__":
    main()
