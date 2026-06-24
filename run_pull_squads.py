"""拉 48 队 squad（/players/squads）——FIFA+真实名单方案的 40 次调用。已缓存的跳过。"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from brain.pull_api import get, KEY
ids = json.load(open("data/api/_target_team_ids_48.json"))["all48"]
if not KEY: print("无 API key"); sys.exit(1)
done = ok = fail = 0
for nm, tid in ids.items():
    fp = f"data/api/squad_{nm}.json"
    if os.path.exists(fp):
        done += 1; continue
    try:
        r = get(f"squad_{nm}", "/players/squads", {"team": tid})
        n = len((r.get("response", [{}]) or [{}])[0].get("players", [])) if r.get("response") else 0
        print(f"  {nm}(id={tid}): {n} 球员"); ok += 1
    except Exception as e:
        print(f"  {nm}: 失败 {str(e)[:60]}"); fail += 1
print(f"=== squad: 已缓存 {done}, 新拉 {ok}, 失败 {fail} ===")
