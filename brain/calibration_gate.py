"""校准门(plan 01§0.3 / 05§3.7 / 00§11 P7)：从完赛 matchDetails + MatchState 测量真实统计,
对照计划区间判定 pass/fail。这是计划里最关键的"测量+gate"缺口。

指标(每队/每场, 90分钟当量):控球%、射门、射正%、扑救%、传球成功%、越位、控球段数(attacks)、
进球 vs xG 一致性。区间取自 05§3.7。
"""

# 计划区间(05§3.7);单位为单队每场(除控球%为整场比例)
RANGES = {
    "possession_pct": (35, 65),       # 单队控球%(整场两队和=100)
    "shots": (5, 20),                 # 单队射门数
    "shots_on_target_pct": (30, 60),  # 射正率%
    "save_pct": (60, 80),             # 扑救率%(面对射正)
    "pass_completion_pct": (70, 90),  # 传球成功率%
    "offsides": (0, 5),               # 单队越位
    "sequences": (60, 220),           # 控球段数(全场, 与拍数相关; 宽口径)
    "goals": (0, 7),                  # 单队进球
}


def _team_passes(md, side):
    key = "kickOffTeam" if side == "home" else "secondTeam"
    tot = acc = 0
    for p in md[key]["players"]:
        ps = (p.get("stats", {}) or {}).get("passes", {}) or {}
        tot += ps.get("total", 0) or 0
        acc += ps.get("accurate", ps.get("on", 0)) or 0
    # 引擎只累计 passes.total, 从不 increment on/accurate → acc 恒0 时成功率不可测(返回 None)
    return tot, (acc if acc > 0 else None)


def _xg(on, off):
    """粗略 xG：射正期望 ~0.33/次,射偏 ~0.04/次(简化模型,用于 goals↔xG 一致性检查)。"""
    return round(on * 0.33 + off * 0.04, 2)


def measure(md, st=None):
    """从完赛 md(+可选 MatchState)测量两队指标。返回 {home:{...}, away:{...}, _meta:{...}}。"""
    ks, ss = md["kickOffTeamStatistics"], md["secondTeamStatistics"]
    stat = {"home": ks, "away": ss}
    poss = {"home": 0.5, "away": 0.5}
    seqs = {"home": 0, "away": 0}
    if st is not None:
        ph, pa = st.possession()
        poss = {"home": ph, "away": pa}
        seqs = {"home": getattr(st, "seq_home", 0), "away": getattr(st, "seq_away", 0)}
    out = {}
    for side, opp in (("home", "away"), ("away", "home")):
        s = stat[side]
        sh = s.get("shots", {}) or {}
        total_sh = sh.get("total", 0) or 0
        on = sh.get("on", 0) or 0
        off = sh.get("off", 0) or 0
        tot_p, acc_p = _team_passes(md, side)
        # 对方射正 → 本方门将面对的射正数;扑救率 = saves / 对方射正
        opp_on = (stat[opp].get("shots", {}) or {}).get("on", 0) or 0
        saves = s.get("saves", 0) or 0
        out[side] = {
            "possession_pct": round(poss[side] * 100, 1),
            "shots": total_sh,
            "shots_on_target_pct": round(on / total_sh * 100, 1) if total_sh else 0.0,
            "save_pct": round(saves / opp_on * 100, 1) if opp_on else None,
            "pass_completion_pct": round(acc_p / tot_p * 100, 1) if (tot_p and acc_p) else None,  # 引擎不测成功→N/A
            "passes": tot_p,
            "offsides": s.get("offsides", 0) or 0,
            "sequences": seqs[side],
            "goals": s.get("goals", 0) or 0,
            "xg": _xg(on, off),
        }
    return out


def gate(metrics):
    """对 measure() 结果逐指标判定。返回 {ok, per_metric:[...], goals_xg_ok}。"""
    rows = []
    ok_all = True
    for side in ("home", "away"):
        m = metrics[side]
        for k, (lo, hi) in RANGES.items():
            v = m.get(k)
            if v is None:                      # 无数据(如 save% 对方0射正)→ 跳过, 不算 fail
                rows.append({"side": side, "metric": k, "value": v, "range": [lo, hi], "ok": None})
                continue
            ok = lo <= v <= hi
            ok_all = ok_all and ok
            rows.append({"side": side, "metric": k, "value": v, "range": [lo, hi], "ok": bool(ok)})
    # goals↔xG 一致性: |goals - xg| <= 2.5(宽口径, 足球进球方差大)
    gx_ok = True
    for side in ("home", "away"):
        m = metrics[side]
        if abs(m["goals"] - m["xg"]) > 2.5:
            gx_ok = False
    return {"ok": bool(ok_all and gx_ok), "goals_xg_ok": gx_ok, "per_metric": rows}


def format_report(metrics, gate_res):
    lines = ["=== 校准测量(单队/场) ==="]
    for side in ("home", "away"):
        m = metrics[side]
        lines.append(f"[{side}] 控球{m['possession_pct']}% 射门{m['shots']}(射正{m['shots_on_target_pct']}%) "
                     f"扑救{m['save_pct']}% 传球{m['passes']}(成功{m['pass_completion_pct']}%) "
                     f"越位{m['offsides']} 段数{m['sequences']} 进球{m['goals']} xG{m['xg']}")
    fails = [r for r in gate_res["per_metric"] if r["ok"] is False]
    lines.append(f"=== 门: {'✅PASS' if gate_res['ok'] else '❌FAIL'} | goals↔xG {'ok' if gate_res['goals_xg_ok'] else 'off'} "
                 f"| 越界项 {len(fails)} ===")
    for r in fails:
        lines.append(f"  ❌ {r['side']}.{r['metric']} = {r['value']} 不在 {r['range']}")
    return "\n".join(lines)
