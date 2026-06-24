"""编排工具：zone↔坐标、holder 检测、world 切片、注入引擎字段（player.action / intentPOS）。

zone 网格：col A–F(6) × row 1–9(9) = 54 格（plan 02 / 01 §0.4）。
注入只写引擎已有字段，零改引擎（plan 06 §1）。
"""
import os
COLS = "ABCDEF"
_USE_INTENT = os.environ.get("MIRO_INTENT", "1") == "1"   # 1=LLM摆位混合 / 0=引擎自控摆位(对比用)

def zone_to_xy(pitch, zone):
    w, h = pitch[0], pitch[1]
    try:
        c = COLS.index(str(zone)[0].upper()); r = int(str(zone)[1:]) - 1
    except Exception:
        c, r = 2, 4
    c = max(0, min(5, c)); r = max(0, min(8, r))
    return [round((c + 0.5) * w / 6), round((r + 0.5) * h / 9)]

def xy_to_zone(pitch, pos):
    if not pos:
        return "C5"
    w, h = pitch[0], pitch[1]
    c = min(5, max(0, int(pos[0] // (w / 6))))
    r = min(8, max(0, int(pos[1] // (h / 9))))
    return f"{COLS[c]}{r + 1}"

def team_side(md, team_id):
    return "home" if team_id == md["kickOffTeam"]["teamID"] else "away"

def players(md, side):
    return md["kickOffTeam"]["players"] if side == "home" else md["secondTeam"]["players"]

def find_player(md, pid):
    for s in ("kickOffTeam", "secondTeam"):
        for p in md[s]["players"]:
            if p.get("playerID") == pid:
                return p
    return None

def holder(md):
    b = md.get("ball", {})
    if b.get("withPlayer") and b.get("Player"):
        return b.get("Player"), b.get("withTeam")
    return None, None

def on_pitch(p):
    cp = p.get("currentPOS")
    return bool(cp) and cp[0] != "NP"

def build_world(md, minute=0.0, phase="open_play", possession=None):
    """共享 world 切片（plan 01§2.3 / 02§0）：所有模型都看到同一份——
    全 22 人(zone/role/hasBall) + 球 + 比分 + possession{home,away} + minute + phase。
    让 gemma/brain 决策时看得见队友/对手位置(不再盲决)。"""
    pitch = md["pitchSize"]; b = md.get("ball", {})
    ps = []
    for side in ("home", "away"):
        for p in players(md, side):
            if not on_pitch(p):
                continue
            ps.append({"id": p["playerID"], "team": side, "role": p.get("position"),
                       "zone": xy_to_zone(pitch, p.get("currentPOS")),
                       "hasBall": p.get("playerID") == b.get("Player")})
    return {
        "ball_zone": xy_to_zone(pitch, b.get("position")),
        "holder": b.get("Player") or None,
        "holder_team": b.get("withTeam") or None,
        "score": [md["kickOffTeamStatistics"]["goals"], md["secondTeamStatistics"]["goals"]],
        "minute": round(minute, 1),
        "phase": phase,                       # open_play / corner / freekick / kickoff
        "possession": possession or {},       # {home: 0.x, away: 0.x}
        "players": ps,
    }

# ── 按位置裁剪 posture 选项（plan 00§4.4 / 02§2：GK/CB 不该有 run_behind 等）──
_ALL_POSTURES = ["hold", "press", "drop", "support", "run_behind", "widen", "tuck_in", "track_back", "overlap"]
ROLE_POSTURES = {
    "GK": ["hold"],   # GK 走专属路径(inject_gk), 这里仅兜底
    "CB": ["hold", "drop", "track_back", "tuck_in", "press"],
    "LB": ["overlap", "widen", "track_back", "support", "drop"],
    "RB": ["overlap", "widen", "track_back", "support", "drop"],
    "CM": ["support", "hold", "press", "drop", "run_behind", "track_back"],
    "LM": ["widen", "support", "run_behind", "track_back", "overlap"],
    "RM": ["widen", "support", "run_behind", "track_back", "overlap"],
    "CF": ["run_behind", "support", "hold", "press", "widen"],
}
def role_options(role):
    return ROLE_POSTURES.get(role, _ALL_POSTURES)

def inject_gk(md, pid, decision):
    """GK 专属注入（plan 01§4.3 / 02§3）。gk_action→引擎字段:
    rush_out/claim_cross→冲出扑救(intentTarget→球+sprint); distribute→持球时传球; hold_line/narrow_angle→引擎默认守门。"""
    p = find_player(md, pid)
    if not p:
        return
    act = (decision or {}).get("gk_action", "hold_line")
    ball = md["ball"]["position"]
    if act in ("rush_out", "claim_cross"):
        p["intentTarget"] = list(ball[:2]); p["action"] = "sprint"
    elif act.startswith("distribute"):
        p["action"] = "pass" if p.get("playerID") == md["ball"].get("Player") else "none"
    else:  # hold_line / narrow_angle → 让引擎守门几何主导
        p["action"] = "none"

def nudge_receiver_intent(md, holder_id, holder_team):
    """接球人 nudge（plan 02§4a / 06§1.3）：引擎自选接球人,这里把一名最前插的同队非持球者
    intentTarget 推向前方空当, 给引擎一个更好的传球目标。"""
    h = find_player(md, holder_id)
    if not h:
        return None
    side = team_side(md, holder_team)
    goal_x = md["pitchSize"][0] if side == "home" else 0   # 粗略进攻方向
    cand, best = None, -1e9
    for p in players(md, side):
        if not on_pitch(p) or p.get("playerID") == holder_id:
            continue
        cp = p.get("currentPOS")
        if not cp or cp[0] == "NP":
            continue
        adv = cp[0] if side == "home" else (md["pitchSize"][0] - cp[0])
        if adv > best:
            best, cand = adv, p
    if cand:
        cp = cand["currentPOS"]
        cand["intentTarget"] = [round((cp[0] + goal_x) / 2), cp[1]]
        return cand.get("playerID")
    return None

# ── 注入（写引擎已有字段）──
_POSTURE2ACTION = {"run_behind": "run", "support": "run", "press": "sprint", "track_back": "sprint",
                   "hold": "none", "drop": "run", "widen": "run", "tuck_in": "run", "overlap": "sprint"}

def clear_actions(md):
    """每拍开头清空在场球员的 action(→none)。避免上一拍注入的动作(尤其持球者传球后的 'pass')
    残留到下一拍触发引擎 'doesnt have the ball so cannot pass→run' 噪声。注:不清 intentTarget(摆位需持续)。"""
    for side in ("home", "away"):
        for p in players(md, side):
            if on_pitch(p):
                p["action"] = "none"

def first_defender(md, defending_side):
    """防守方离球最近的在场球员(= 第一防守者, 复用 closestPlayerToBall 的几何)。"""
    ball = md["ball"]["position"]
    best, bd = None, 1e9
    for p in players(md, defending_side):
        if not on_pitch(p):
            continue
        cp = p.get("currentPOS")
        if not cp or cp[0] == "NP":
            continue
        d = abs(cp[0] - ball[0]) + abs(cp[1] - ball[1])
        if d < bd:
            bd, best = d, p
    return best, bd

def inject_first_defender(md, defending_side, decision=None):
    """第一防守者上抢:intentTarget→球, 近球则 tackle/铲球, 否则 sprint。decision 可由 brain 给(action 覆盖)。"""
    fd, dist = first_defender(md, defending_side)
    if not fd:
        return None
    fd["intentTarget"] = list(md["ball"]["position"][:2])
    act = (decision or {}).get("action")
    # mirofootball 调参(破 bistable): 近球(dist<20)→上抢 tackle(引擎仅在±6内真正触发, 自然限频; 抢断NaN已修→
    # 吃 tackling+strength vs control+strength 技能, 强队防守者夺球)→ 提 skill-based turnover → 半场内强队夺主导。
    # 远球→sprint 逼近。brain 显式 tackle/intercept 时覆盖。(原全程sprint从不真上抢→持球者never被challenge→雪球)
    if act in ("tackle", "slide", "intercept"):
        fd["action"] = act
    elif dist is not None and dist < 20:
        fd["action"] = "tackle"
    else:
        fd["action"] = "sprint"
    return fd.get("playerID")

def inject_offball(md, pid, decision):
    p = find_player(md, pid)
    if not p:
        return
    tz = decision.get("target_zone")
    if tz and _USE_INTENT:
        # MIRO_INTENT=1: 设 intentTarget → 引擎混合 60%引擎+40%LLM 摆位。
        # MIRO_INTENT=0: 不设 → 引擎自控摆位(稳定均衡控球), LLM 只经 action/posture 影响。
        tgt = zone_to_xy(md["pitchSize"], tz)
        # mirofootball 摆位层修复: 锚定阵型(originPOS), 只朝 gemma 意图做有限位移 → 保持阵型分散、不扎堆抢球
        # → 散球公平争夺(谁近谁得, 不是谁扎堆多) → 控球随球队质量分化, 消除 LLM 雪球。
        origin = p.get("originPOS")
        if origin and origin[0] != "NP":
            w = 0.45  # gemma 意图权重; 其余 0.55 锚 formation 槽位(维持阵型)
            tgt = [round(origin[0] * (1 - w) + tgt[0] * w), round(origin[1] * (1 - w) + tgt[1] * w)]
        p["intentTarget"] = tgt
    p["action"] = _POSTURE2ACTION.get(decision.get("posture", ""), "none")  # → checkProvidedAction

def apply_possession_control(md, holder_id, holder_team, biases):
    """控球控制层（plan 01 §3.2）：把 retention/press 偏置直接接到引擎层(位置+动作)，
    不只 prompt 提示。进攻方高保球→全员拉近支援(给传球点);防守方高逼抢→近球者上抢;
    持球者高保球→强制 keep(pass)。比 prompt 提示强得多。"""
    if not holder_id:
        return
    in_side = team_side(md, holder_team)
    ball = md["ball"]["position"]
    for side in ("home", "away"):
        b = biases.get(side, {})
        ret = b.get("retention_bias", 0.5); prs = b.get("press_intensity", 0.5)
        for p in players(md, side):
            if not on_pitch(p) or p.get("playerID") == holder_id:
                continue
            cp = p.get("currentPOS")
            if not cp or cp[0] == "NP":
                continue
            if side == in_side and ret > 0.55:
                # 进攻方拉近支援：intentPOS 取当前与球的中点（提供短传点）
                p["intentPOS"] = [round((cp[0] + ball[0]) / 2), round((cp[1] + ball[1]) / 2)]
                p["action"] = "run"
            elif side != in_side and prs > 0.55:
                # 防守方近球者上抢
                if abs(cp[0] - ball[0]) + abs(cp[1] - ball[1]) < 350:
                    p["intentPOS"] = [ball[0], ball[1]]
                    p["action"] = "sprint"
    # 持球者高保球 → keep（除非已是射门/传球类）
    h = find_player(md, holder_id)
    if h and biases.get(in_side, {}).get("retention_bias", 0.5) > 0.6 and \
       h.get("action") in (None, "none", "cleared", "boot"):
        h["action"] = "pass"

def inject_onball(md, pid, decision):
    p = find_player(md, pid)
    if not p:
        return
    act = decision.get("action")
    # 引擎自带合法性：非持球者射传会降级；这里持球者，直接写
    if act:
        p["action"] = act
    # 注：传球"目标"引擎自选（plan 06 §1.3），target_id 仅作 nudge 提示，MVP 暂不 nudge
