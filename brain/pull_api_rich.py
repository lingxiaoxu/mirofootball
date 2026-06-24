"""增强数据拉取：俱乐部整季每球员动作画像 + 进球助攻事件。让 per-player 画像更锐利(plan §5)。
- /players?id=<pid>&season=2025 : 球员俱乐部整季汇总(样本远大于 1-3 场 WC) → club_<pid>.json
- /fixtures/events?fixture=<fid> : 进球/助攻/牌/换人时间线(8队已踢场) → events_<fid>.json
限速 0.5s + 断点续传。本机跑(public API)。
"""
import json, os, glob, time, httpx

D = os.path.join(os.path.dirname(__file__), "..", "data", "api")
BASE = "https://v3.football.api-sports.io"
SEASON_CLUB = 2025  # 2025/26 俱乐部赛季

def key():
    for line in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
        if line.startswith("API_FOOTBALL_KEY="):
            return line.strip().split("=", 1)[1]
HDR = {"x-apisports-key": key()}
_n = 0

def get(name, path, params):
    global _n
    fp = os.path.join(D, f"{name}.json")
    if os.path.exists(fp):
        return
    with httpx.Client(timeout=40) as c:
        r = c.get(f"{BASE}{path}", headers=HDR, params=params)
    _n += 1
    j = r.json()
    json.dump(j, open(fp, "w"), ensure_ascii=False)
    rem = r.headers.get("x-ratelimit-requests-remaining", "?")
    if _n % 20 == 0 or j.get("errors"):
        print(f"  [{_n}] {name} results={j.get('results')} day_remaining={rem} {j.get('errors') or ''}")
    time.sleep(0.5)

def main():
    # 1) 球员俱乐部整季
    pids = set()
    for f in glob.glob(os.path.join(D, "squad_*.json")):
        sq = json.load(open(f)).get("response", [])
        for p in (sq[0]["players"] if sq else []):
            pids.add(p["id"])
    print(f"=== 俱乐部整季: {len(pids)} 球员 ===")
    for pid in pids:
        get(f"club_{pid}", "/players", {"id": pid, "season": SEASON_CLUB})
    # 2) 已踢 fixtures 的事件
    played = [f.split("fx_lineups_")[1].split(".")[0] for f in glob.glob(os.path.join(D, "fx_lineups_*.json"))
              if json.load(open(f)).get("results", 0) > 0]
    print(f"=== 事件: {len(played)} 场 ===")
    for fid in played:
        get(f"events_{fid}", "/fixtures/events", {"fixture": fid})
    print(f"=== 完成, 本次新增 {_n} 次调用 ===")

if __name__ == "__main__":
    main()
