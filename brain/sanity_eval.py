"""LoRA 评估驱动（plan 04§8）：真查每支已注册球队 LoRA 对一组固定探针的决策,
喂 eval_gate 算 json_ok/enum_ok/球队区分度,过门才更新 registry。补"门逻辑有、驱动缺"的缺口。

用法: python -m brain.sanity_eval            # 评估 registry 里所有队
      python -m brain.sanity_eval Spain Germany   # 仅评估指定队
注: 会查 :11436 上的 gemma-<team>(逐队 load/unload, 内存安全)。不碰运行中的比赛——单独跑。
"""
import asyncio, json, sys
from brain.llm_client import LLM
from brain import config as C
from brain import eval_gate as EG
from brain.match_director import OFFBALL_SYS, OFFBALL_SCHEMA, ONBALL_SYS, ONBALL_SCHEMA
from brain import world as W

# 固定探针：同一组局面,各队都答,看决策是否合法 + 是否因队而异(区分度)
PROBES_OFFBALL = [
    {"world": {"ball_zone": "C6", "holder": 1, "holder_team": 1, "score": [0, 0], "minute": 20.0,
               "possession": {"home": 0.6, "away": 0.4}, "players": []},
     "me": {"id": 9, "role": "CF", "zone": "D7"}, "allowed_postures": W.role_options("CF"),
     "retention_bias": 0.7, "press_intensity": 0.4},
    {"world": {"ball_zone": "B3", "holder": 12, "holder_team": 2, "score": [0, 1], "minute": 70.0,
               "possession": {"home": 0.4, "away": 0.6}, "players": []},
     "me": {"id": 4, "role": "CB", "zone": "B2"}, "allowed_postures": W.role_options("CB"),
     "retention_bias": 0.4, "press_intensity": 0.7},
    {"world": {"ball_zone": "E5", "holder": 7, "holder_team": 1, "score": [1, 1], "minute": 85.0,
               "possession": {"home": 0.55, "away": 0.45}, "players": []},
     "me": {"id": 11, "role": "RM", "zone": "E4"}, "allowed_postures": W.role_options("RM"),
     "retention_bias": 0.5, "press_intensity": 0.5},
]
PROBES_ONBALL = [
    {"world": {"ball_zone": "D6", "holder": 8, "holder_team": 1, "score": [0, 0], "minute": 30.0},
     "me": {"id": 8, "team": "home"}, "retention_bias": 0.8, "press_intensity": 0.3},
    {"world": {"ball_zone": "F8", "holder": 9, "holder_team": 1, "score": [0, 1], "minute": 80.0},
     "me": {"id": 9, "team": "home"}, "retention_bias": 0.4, "press_intensity": 0.5},
]


async def probe_team(team):
    """加载 gemma-<team>, 对所有探针取决策。返回决策列表。结束 unload 省内存。"""
    llm = LLM(C.GEMMA_HOME_URL, f"gemma-{team.lower()}", think=C.GEMMA_THINK)
    decisions = []
    for u in PROBES_OFFBALL:
        d = await llm.decide(OFFBALL_SYS, u, OFFBALL_SCHEMA, 64)
        decisions.append(d if isinstance(d, dict) else {})
    for u in PROBES_ONBALL:
        d = await llm.decide(ONBALL_SYS, u, ONBALL_SCHEMA, 64)
        decisions.append(d if isinstance(d, dict) else {})
    return decisions


async def main(teams):
    reg = EG.load_registry()
    team_decisions = {}
    for t in teams:
        print(f"评估 {t} ...")
        try:
            team_decisions[t] = await probe_team(t)
        except Exception as e:
            print(f"  {t} 查询失败: {str(e)[:80]}")
    # 区分度：各队对同组探针的决策差异
    dist = EG.distinct(team_decisions) if len(team_decisions) >= 2 else None
    results = {}
    for t, decs in team_decisions.items():
        g = EG.gate(t, decs, distinct_score=dist)
        results[t] = g
        print(f"  {t}: json={g['json_ok']} enum={g['enum_ok']} distinct={g['distinct']} → {'✅OK' if g['ok'] else '❌FAIL'}")
    # 过门的写回 registry(保留已有结构, 更新 eval 字段)
    for t, g in results.items():
        if t not in reg:
            reg[t] = {}
        reg[t].update({"eval_json_ok": g["json_ok"], "eval_enum_ok": g["enum_ok"],
                       "eval_distinct": g["distinct"], "eval_ok": g["ok"]})
    EG.write_registry(reg)
    passed = sum(1 for g in results.values() if g["ok"])
    print(f"\n===== 评估汇总: {passed}/{len(results)} 过门 | 区分度 {dist} | registry 已更新 =====")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        reg = EG.load_registry()
        args = [k for k in reg.keys() if not k.startswith("_")]
    asyncio.run(main(args))
