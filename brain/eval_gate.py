"""LoRA 评估门 + 注册表（plan 04 §8）。上线前校验:① JSON 合法率=100% ② 决策落合法 enum
③ 球队间分化(不同队同局面决策有别) ④ 不劣于裸底座。过门才写进 registry.json 供 MatchDirector 选用。

校验数据来自 sanity_test 的输出(各队 LoRA 对同组探针的决策)。本模块只做"门"逻辑 + 注册表读写;
实际跑 gemma 取决策在 Box B/sanity 完成(避免和运行中的比赛抢引擎)。
"""
import json, os

ROOT = os.path.join(os.path.dirname(__file__), "..")
REG = os.path.join(ROOT, "serving", "lora", "registry.json")
OFFBALL_POSTURES = {"hold", "press", "drop", "support", "run_behind", "widen", "tuck_in", "track_back", "overlap"}
ONBALL_ACTIONS = {"pass", "throughBall", "cross", "shoot", "dribble", "cleared", "boot"}


def check_decisions(decisions):
    """decisions: list[dict] 一个队 LoRA 对探针的输出。返回 (json_ok率, enum_ok率)。"""
    if not decisions:
        return 0.0, 0.0
    json_ok = sum(1 for d in decisions if isinstance(d, dict) and ("target_zone" in d or "action" in d))
    enum_ok = sum(1 for d in decisions if (d.get("posture") in OFFBALL_POSTURES or d.get("action") in ONBALL_ACTIONS))
    n = len(decisions)
    return round(json_ok / n, 3), round(enum_ok / n, 3)


def distinct(team_decisions):
    """team_decisions: {team: [决策...]}(同组探针)。返回区分度=有多少对队在某探针上决策不同 / 总对数。"""
    teams = list(team_decisions)
    if len(teams) < 2:
        return 0.0
    pairs = diff = 0
    for i in range(len(teams)):
        for j in range(i + 1, len(teams)):
            pairs += 1
            a, b = team_decisions[teams[i]], team_decisions[teams[j]]
            if any(json.dumps(x, sort_keys=True) != json.dumps(y, sort_keys=True) for x, y in zip(a, b)):
                diff += 1
    return round(diff / max(pairs, 1), 3)


def gate(team, decisions, base_decisions=None, distinct_score=None):
    """单队过门判定。门槛: json=100% + enum≥95% + (有区分度时)≥0.3。"""
    j, e = check_decisions(decisions)
    ok = (j >= 1.0 and e >= 0.95)
    if distinct_score is not None:
        ok = ok and distinct_score >= 0.3
    return {"team": team, "json_ok": j, "enum_ok": e, "distinct": distinct_score, "ok": bool(ok)}


def load_registry():
    return json.load(open(REG)) if os.path.exists(REG) else {}


def write_registry(entries: dict):
    os.makedirs(os.path.dirname(REG), exist_ok=True)
    json.dump(entries, open(REG, "w"), ensure_ascii=False, indent=2)
    return REG
