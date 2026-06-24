"""MatchDirector —— 组织整场比赛的顶层导演。

改造自 MiroFish `services/simulation_manager.py`（SimulationManager / SimulationState / 状态机 /
state.json 持久化）+ `simulation_config_generator.py`（LLM 生成整场 config）。
**比赛由 MatchDirector 组织**（北极星）；足球引擎 = 被它驱动的 env（取代 OASIS env）。
- create_match → prepare_match(brain 生成 match config) → run_match(tick 循环驱动引擎 + 写轨迹 + 管状态) → report
- 只发推理请求，绝不碰运行中的模型（ops 铁律）。
"""
import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

import httpx

from brain import config as C
from brain.llm_client import LLM
from brain import world as W
from brain.trajectory import TrajectoryWriter
from brain.possession import PossessionDirector
from brain.kg import KG
from brain.report_agent import FootballReportAgent
from brain.match_config_generator import MatchConfigGenerator, BASELINES


# ── 状态机（改造自 MiroFish SimulationStatus）──
class MatchStatus(str, Enum):
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ── 比赛状态（改造自 MiroFish SimulationState）──
@dataclass
class MatchState:
    match_id: str
    home_name: str
    away_name: str
    status: MatchStatus = MatchStatus.CREATED
    config_generated: bool = False
    config_reasoning: str = ""
    # 运行态（rounds=iterations 的足球版）
    current_iter: int = 0
    total_iters: int = 0
    score_home: int = 0
    score_away: int = 0
    poss_home_ticks: int = 0      # 仅【有球员控球(withPlayer)】的拍, 按队各自计
    poss_away_ticks: int = 0
    poss_total_ticks: int = 0     # = home+away 控球拍(散球/飞行不计, 真实控球率口径)
    seq_home: int = 0             # 控球段数(possession sequences, plan 01§3.3): withTeam 连续段计 1
    seq_away: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    error: Optional[str] = None

    def possession(self):
        # 真实口径: 只在控球拍里分配; away 用自身控球拍, 不是 1-home(散球不算给任一方)
        t = max(self.poss_home_ticks + self.poss_away_ticks, 1)
        return round(self.poss_home_ticks / t, 3), round(self.poss_away_ticks / t, 3)

    def to_dict(self):
        d = asdict(self); d["status"] = self.status.value
        ph, pa = self.possession(); d["possession_home"], d["possession_away"] = ph, pa
        return d

    def to_simple_dict(self):
        # 对外简版（改造自 SimulationState.to_simple_dict）
        ph, pa = self.possession()
        return {"match_id": self.match_id, "home": self.home_name, "away": self.away_name,
                "status": self.status.value, "score": [self.score_home, self.score_away],
                "possession": [ph, pa], "iter": self.current_iter, "config_generated": self.config_generated}


# 赛前 config 的 system/schema/BASELINES 已归口到 brain/match_config_generator.py（改造自 MiroFish
# SimulationConfigGenerator）；prepare_match 经 MatchConfigGenerator 生成（带重试+JSON修复健壮性）。

# ── tick prompt/schema（沿用已验证的）──
OFFBALL_SYS = ("You are an off-ball positioning brain for ONE football player. "
               "Pick target_zone (grid col A-F x row 1-9, e.g. C6) and posture — posture MUST be one of "
               "the provided allowed_postures (role-appropriate). Use the shared world (all 22 players' "
               "zones) to support the holder / mark opponents. Favor support/hold/drop when retention_bias "
               "is high; press/track_back when press_intensity is high. PLAY IN YOUR TEAM'S STYLE "
               "(team_style: formation / possession / directness). Output ONLY JSON.")
OFFBALL_SCHEMA = {"type": "object", "properties": {
    "target_zone": {"type": "string"},
    "posture": {"type": "string", "enum": ["hold", "press", "drop", "support", "run_behind",
                                           "widen", "tuck_in", "track_back", "overlap"]}},
    "required": ["target_zone", "posture"]}
ONBALL_SYS = ("You are the on-ball decision brain for the player in possession. "
              "Choose ONE action. Favor pass/throughBall (keep possession) when retention_bias is "
              "high; avoid aimless cleared/boot then. Output ONLY JSON.")
ONBALL_SCHEMA = {"type": "object", "properties": {
    "action": {"type": "string", "enum": ["pass", "throughBall", "cross", "shoot", "cleared", "boot"]},
    "intent": {"type": "string"}}, "required": ["action"]}
# 第一防守者决策（plan 01 §2.3 / 06 §2：球队无关、随球权切换、管争夺的 1-2 人）
DEFENSE_SYS = ("You are the FIRST DEFENDER (closest defender to the ball). Decide how to contest: "
               "tackle (close, win ball), intercept (cut passing lane), sprint (close down/press), "
               "slide (last-ditch). Aggressive when press_intensity high. Output ONLY JSON.")
DEFENSE_SCHEMA = {"type": "object", "properties": {
    "action": {"type": "string", "enum": ["tackle", "intercept", "sprint", "slide"]},
    "intent": {"type": "string"}}, "required": ["action"]}
# GK 专属决策（plan 01§4.3 / 02§3：门将永不 run/tackle/run_behind）
GK_SYS = ("You are a GOALKEEPER. Given ball zone + threat, choose gk_action: "
          "hold_line (stay on line), narrow_angle (cut shooting angle), rush_out (close down a "
          "through-ball/1v1), claim_cross (come for a cross), distribute_short / distribute_long "
          "(when holding the ball). Rush/claim only when the ball is a real threat near your box. "
          "Output ONLY JSON.")
GK_SCHEMA = {"type": "object", "properties": {
    "gk_action": {"type": "string", "enum": ["hold_line", "narrow_angle", "rush_out",
                                             "claim_cross", "distribute_short", "distribute_long"]},
    "intent": {"type": "string"}}, "required": ["gk_action"]}


class MatchDirector:
    """组织整场比赛的导演（MiroFish SimulationManager 血统）。"""

    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or C.DATA_DIR
        os.makedirs(self.data_dir, exist_ok=True)
        # 三模型（reasoning 已定：brain 开 / gemma 关）
        self.brain = LLM(C.BRAIN_URL, C.BRAIN_MODEL, think=C.BRAIN_THINK)
        # per-tick 持球/防守决策用【非思考】brain: 思考(10-30s/call)对快速动作决策是浪费且拖垮整场;
        # config-gen / report 才用思考版 self.brain(低频、需推理)。
        self.brain_fast = LLM(C.BRAIN_URL, C.BRAIN_MODEL, think=False)
        self.home = LLM(C.GEMMA_HOME_URL, C.GEMMA_MODEL, think=C.GEMMA_THINK)
        self.away = LLM(C.GEMMA_AWAY_URL, C.GEMMA_MODEL, think=C.GEMMA_THINK)
        # config-gen 也用非思考版: 思考模式同样返回 None/不可解析→回退 0.5/0.5(实测). 非思考可稳出 config。
        self.config_gen = MatchConfigGenerator(self.brain_fast)   # 改造自 SimulationConfigGenerator
        self._matches: Dict[str, MatchState] = {}            # 内存缓存(对应 _simulations)
        self.cfg: Dict[str, Any] = {}

    # ── 持久化（改造自 _save/_load_simulation_state：每 id 一个目录 + state.json + 内存缓存）──
    def _dir(self, mid):
        d = os.path.join(self.data_dir, mid); os.makedirs(d, exist_ok=True); return d

    def _save_state(self, st: MatchState):
        st.updated_at = datetime.now().isoformat()
        with open(os.path.join(self._dir(st.match_id), "state.json"), "w", encoding="utf-8") as f:
            json.dump(st.to_dict(), f, ensure_ascii=False, indent=2)
        self._matches[st.match_id] = st

    def _load_state(self, match_id: str) -> Optional[MatchState]:
        if match_id in self._matches:
            return self._matches[match_id]
        fp = os.path.join(self._dir(match_id), "state.json")
        if not os.path.exists(fp):
            return None
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        st = MatchState(
            match_id=match_id, home_name=data.get("home_name", ""), away_name=data.get("away_name", ""),
            status=MatchStatus(data.get("status", "created")),
            config_generated=data.get("config_generated", False),
            config_reasoning=data.get("config_reasoning", ""),
            current_iter=data.get("current_iter", 0), total_iters=data.get("total_iters", 0),
            score_home=data.get("score_home", 0), score_away=data.get("score_away", 0),
            poss_home_ticks=data.get("poss_home_ticks", 0), poss_away_ticks=data.get("poss_away_ticks", 0),
            poss_total_ticks=data.get("poss_total_ticks", 0),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()), error=data.get("error"))
        self._matches[match_id] = st
        return st

    def get_match(self, match_id: str) -> Optional[MatchState]:
        return self._load_state(match_id)

    def list_matches(self) -> list:
        return [s.to_simple_dict() if hasattr(s, "to_simple_dict") else s.to_dict()
                for s in self._matches.values()]

    # ── create（改造自 create_simulation）──
    def create_match(self, home_name: str, away_name: str) -> MatchState:
        mid = f"match_{uuid.uuid4().hex[:10]}"
        st = MatchState(match_id=mid, home_name=home_name, away_name=away_name,
                        status=MatchStatus.CREATED)
        self._save_state(st)
        return st

    # ── prepare：MatchConfigGenerator 生成 config（改造自 prepare_simulation 的 config 阶段）──
    async def prepare_match(self, st: MatchState, home_info: dict, away_info: dict) -> MatchState:
        st.status = MatchStatus.PREPARING; self._save_state(st)
        try:
            self.cfg = await self.config_gen.generate_config(home_info, away_info, BASELINES)
            with open(os.path.join(self._dir(st.match_id), "match_config.json"), "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
            st.config_generated = isinstance(self.cfg, dict) and "_error" not in self.cfg and "home" in self.cfg
            st.config_reasoning = (self.cfg or {}).get("narrative_seed", "") if isinstance(self.cfg, dict) else ""
            st.status = MatchStatus.READY; self._save_state(st)
        except Exception as e:
            st.status = MatchStatus.FAILED; st.error = str(e); self._save_state(st); raise
        return st

    async def _engine(self, http, path, payload):
        r = await http.post(f"{C.ENGINE_URL}/{path}", json=payload, timeout=30)
        r.raise_for_status(); return r.json()

    # ── run：MatchDirector 组织主循环，引擎当 env（改造自 SimulationManager + OASIS env.step 模式）──
    def _match_stats(self, md, st) -> dict:
        ph, pa = st.possession()
        ks, ss = md["kickOffTeamStatistics"], md["secondTeamStatistics"]
        return {"score": [st.score_home, st.score_away], "possession": [ph, pa],
                "shots": [ks.get("shots"), ss.get("shots")],
                "saves": [ks.get("saves", 0), ss.get("saves", 0)],
                "fouls": [ks.get("fouls", 0), ss.get("fouls", 0)],
                "corners": [ks.get("corners", 0), ss.get("corners", 0)],
                "offsides": [ks.get("offsides", 0), ss.get("offsides", 0)],
                "sequences": [st.seq_home, st.seq_away],   # #6 控球段数
                "iters": st.total_iters}

    async def run_match(self, st: MatchState, team1, team2, pitch,
                        iters=None, gemma_every=None, brain_every=4, gemma_n=3, kg_enabled=False,
                        home_style=None, away_style=None, resume_md=None, half_split=True, bench=None):
        # resume_md: 续打(加时)跳过 initiate; half_split: 中点换半场; bench: {side:[替补]} #8 换人用
        iters = iters or (C.ITER_PER_HALF * 2)
        gemma_every = gemma_every or C.GEMMA_EVERY
        st.status = MatchStatus.RUNNING; st.total_iters = iters; self._save_state(st)
        traj = TrajectoryWriter(st.match_id, self.data_dir, C.ITER_PER_HALF)
        # #1 控球反馈：目标取自 match config
        target_home = float((self.cfg.get("home") or {}).get("possession_target", 0.5)) if self.cfg else 0.5
        poss = PossessionDirector(target_home)
        kg = KG(enabled=kg_enabled)   # #4 KG（连不上则优雅 no-op）
        calls = {"gemma": 0, "brain": 0}
        try:
            async with httpx.AsyncClient() as http:
                if resume_md is not None:
                    md = resume_md   # 续打加时: 用已有 md, 不重新 initiate
                else:
                    md = await self._engine(http, "initiate", {"team1": team1, "team2": team2, "pitch": pitch})
                home_team_id = md["kickOffTeam"]["teamID"]
                away_team_id = md["secondTeam"]["teamID"]
                if kg.on and resume_md is None: kg.bootstrap(st.match_id, md)
                prev_hid = None          # #11 ball_state_changed 触发
                prev_seq_team = None     # #6 控球段数统计
                last_gemma_it = -999     # #11 ball_event 刷新节流(避免每拍抖动)
                for it in range(iters):
                    if half_split and it == iters // 2:
                        md = await self._engine(http, "secondhalf", {"matchDetails": md})
                    # #8 换人: ~67% 时间换下最低 fitness 的非门将首发(若有 bench)
                    if bench and it == int(iters * 0.67):
                        from brain import knockout as KO
                        for _s, _k in (("home", "kickOffTeam"), ("away", "secondTeam")):
                            pool = bench.get(_s, [])
                            onf = [p for p in md[_k]["players"] if W.on_pitch(p) and p.get("position") != "GK"]
                            if pool and onf:
                                worst = min(onf, key=lambda p: p.get("fitness", 100) or 100)
                                if KO.apply_sub(md, worst["playerID"], pool.pop(0)):
                                    calls["subs"] = calls.get("subs", 0) + 1
                    W.clear_actions(md)   # #task1 清上一拍残留 action(去 stale 'cannot pass→run' 噪声; intentTarget 保留)
                    hid, hteam = W.holder(md)
                    minute = round(it / max(iters, 1) * 90, 1)
                    ph_now, pa_now = st.possession()
                    wd = W.build_world(md, minute=minute, possession={"home": ph_now, "away": pa_now})  # #1 完整共享 world(全22人)
                    bz = {"home": poss.biases("home"), "away": poss.biases("away")}  # #1 偏置
                    # #11 控球者变化=事件触发, 但限最小刷新间隔(否则控球频繁变会每拍刷新 gemma→极慢)
                    ball_event = (hid != prev_hid) and (it - last_gemma_it >= max(4, gemma_every // 3))
                    # 无球跑位：两队【全部】无球球员并行批量(gemma_n=None→全 22 人都摆位, 球员主动支援持球者)
                    # 两队各自 daemon(:11436/:11437)并行(asyncio.gather), 队内 batch 再并发。
                    if it % gemma_every == 0 or ball_event:   # #11 周期 or 控球变化(已节流)即刷新
                        last_gemma_it = it
                        rosters = {}; gks = {}
                        for side in ("home", "away"):
                            on = [q for q in W.players(md, side) if W.on_pitch(q) and q.get("playerID") != hid]
                            outfield = [q for q in on if q.get("position") != "GK"]
                            gkl = [q for q in on if q.get("position") == "GK"]
                            if gemma_n:
                                outfield = outfield[:gemma_n]
                            us = []
                            for p in outfield:
                                role = p.get("position")
                                u = {"world": wd, "me": {"id": p["playerID"], "role": role,
                                     "zone": W.xy_to_zone(md["pitchSize"], p.get("currentPOS"))},
                                     "allowed_postures": W.role_options(role)}   # #3 按位置裁剪 options
                                u.update(bz[side])
                                u["team_style"] = home_style if side == "home" else away_style
                                us.append((p["playerID"], role, u))
                            rosters[side] = us
                            gus = []                                            # #2 GK 专属路径
                            for p in gkl:
                                u = {"world": wd, "me": {"id": p["playerID"], "role": "GK",
                                     "zone": W.xy_to_zone(md["pitchSize"], p.get("currentPOS"))}}
                                u.update(bz[side])
                                gus.append((p["playerID"], u))
                            gks[side] = gus
                        # 两队 outfield off-ball + 两队 GK,四批并行(GK 各1人, 廉价)
                        hres, ares, hgk, agk = await asyncio.gather(
                            self.home.batch(OFFBALL_SYS, [u for _, _, u in rosters["home"]], OFFBALL_SCHEMA, 64),
                            self.away.batch(OFFBALL_SYS, [u for _, _, u in rosters["away"]], OFFBALL_SCHEMA, 64),
                            self.home.batch(GK_SYS, [u for _, u in gks["home"]], GK_SCHEMA, 48),
                            self.away.batch(GK_SYS, [u for _, u in gks["away"]], GK_SCHEMA, 48))
                        for side, res in (("home", hres), ("away", ares)):
                            calls["gemma"] += len(res)
                            for (pid, role, _), d in zip(rosters[side], res):
                                if d and "_error" not in d:
                                    if d.get("posture") not in W.role_options(role):   # #3 夹回合法 posture
                                        d["posture"] = W.role_options(role)[0]
                                    W.inject_offball(md, pid, d)
                        for side, res in (("home", hgk), ("away", agk)):
                            calls["gemma"] += len(res)
                            for (pid, _), d in zip(gks[side], res):
                                if d and "_error" not in d:
                                    W.inject_gk(md, pid, d)
                    # 持球决策（brain，节流，顺序；注入保球偏置）
                    if hid and it % brain_every == 0:
                        side = W.team_side(md, hteam)
                        u = {"world": wd, "me": {"id": hid, "team": side}}; u.update(bz[side])
                        u["team_style"] = home_style if side == "home" else away_style
                        d = await self.brain_fast.decide(ONBALL_SYS, u, ONBALL_SCHEMA, 200)
                        calls["brain"] += 1
                        if "_error" not in d:
                            W.inject_onball(md, hid, d)
                            if d.get("action") in ("pass", "throughBall", "cross"):
                                W.nudge_receiver_intent(md, hid, hteam)   # #23 接球人 nudge(引擎自选接球, 给好目标)
                            if kg.on: kg.record_decision(hid, d.get("action"), it)   # #task3 KG 决策边
                    # #task2 第一防守者路由：防守侧最近者上抢(每拍启发式 press), brain 节流给防守决策(/closest 概念)
                    if hid:
                        dside = "away" if W.team_side(md, hteam) == "home" else "home"
                        ddec = None
                        if it % (brain_every * 2) == 0:   # 防守 brain 决策降频(省 nemo); 启发式上抢仍每拍
                            du = {"world": wd, "me": {"role": "first_defender", "team": dside}}
                            du.update(bz[dside])
                            ddec = await self.brain_fast.decide(DEFENSE_SYS, du, DEFENSE_SCHEMA, 80)
                            calls["brain"] += 1
                            if "_error" in (ddec or {}): ddec = None
                        def_id = W.inject_first_defender(md, dside, ddec)
                        if kg.on and def_id: kg.record_mark(def_id, hid, it)   # #12 盯防边
                    # #a 控球控制层：实测"全员拉向球"强控会崩阵型、控球更糟 → 默认关闭。
                    # 控球是引擎涌现量(01 §3.1)，只测量不硬掰；保留函数待更温和方案。
                    if getattr(self, "force_possession", False):
                        W.apply_possession_control(md, hid, hteam, bz)
                    md = await self._engine(http, "iterate", {"matchDetails": md})
                    traj.append_tick(md, it)
                    # 管状态：比分 + 控球
                    st.current_iter = it
                    st.score_home = md["kickOffTeamStatistics"]["goals"]
                    st.score_away = md["secondTeamStatistics"]["goals"]
                    # 真实控球率口径: 按 hasBall(控球绝对真相)判, 不用 withTeam(常空→漏计485拍/场→失真)。
                    # ground truth: 哪队有球员 hasBall=true 即该队控球; 都没有=散球/飞行不计。
                    _kh = any(p.get("hasBall") for p in md["kickOffTeam"]["players"])
                    _ah = any(p.get("hasBall") for p in md["secondTeam"]["players"])
                    cur_team = home_team_id if _kh else (away_team_id if _ah else None)
                    if cur_team == home_team_id: st.poss_home_ticks += 1
                    elif cur_team == away_team_id: st.poss_away_ticks += 1
                    st.poss_total_ticks = st.poss_home_ticks + st.poss_away_ticks
                    # #6 控球段数(possession sequences): 控球队切换=新的一段
                    if cur_team and cur_team != prev_seq_team:
                        if cur_team == home_team_id: st.seq_home += 1
                        elif cur_team == away_team_id: st.seq_away += 1
                    if cur_team: prev_seq_team = cur_team
                    prev_hid = hid   # #11 供下拍 ball_event 比较
                    # #9 红牌罚下: 引擎累计 cards.red>=1 且仍在场 → 设 NP(少打一人, 自动生效)
                    for _side in ("kickOffTeam", "secondTeam"):
                        for _p in md[_side]["players"]:
                            cp = _p.get("currentPOS")
                            if cp and cp[0] != "NP" and ((_p.get("stats", {}).get("cards", {}) or {}).get("red", 0) or 0) >= 1:
                                _p["currentPOS"] = ["NP", "NP"]
                                calls["reds"] = calls.get("reds", 0) + 1
                    if it % 50 == 0 and st.poss_total_ticks:
                        poss.observe(st.poss_home_ticks / st.poss_total_ticks)  # #1 反馈微调
                        self._save_state(st)
                    if kg.on and it % 25 == 0:                                   # #4 关键拍语义
                        kg.update_state(st.match_id, md, it, round(it / max(C.ITER_PER_HALF / 45, 1), 1))
                if kg.on:
                    kg.finalize(st.match_id, st.score_home, st.score_away); kg.close()
            traj.close()
            st.status = MatchStatus.COMPLETED; self._save_state(st)
            return st, md, traj, calls, self._match_stats(md, st)
        except Exception as e:
            st.status = MatchStatus.FAILED; st.error = str(e); self._save_state(st)
            traj.close()
            if kg.on: kg.close()
            raise

    # ── #7 完整 LLM 淘汰赛: 常规(LLM)→平局加时(LLM续打)→仍平点球大战(引擎/penalty) ──
    async def run_knockout_match(self, st, team1, team2, pitch, ft_iters=None, et_iters=None, **kw):
        """05§3.5。返回 (result{stage,score,winner,...}, md, stats)。整合 knockout 层进 LLM 管线。"""
        from brain import knockout as KO
        ft_iters = ft_iters or (C.ITER_PER_HALF * 2)
        et_iters = et_iters or max(200, ft_iters // 4)
        st, md, traj, calls, stats = await self.run_match(st, team1, team2, pitch, iters=ft_iters, **kw)
        if st.score_home != st.score_away:
            w = st.home_name if st.score_home > st.score_away else st.away_name
            return {"stage": "regulation", "score": [st.score_home, st.score_away], "winner": w}, md, stats
        # 平局 → 加时(LLM 续打, resume_md 不重 initiate)
        st, md, traj, calls2, stats = await self.run_match(
            st, team1, team2, pitch, iters=et_iters, resume_md=md, **kw)
        if st.score_home != st.score_away:
            w = st.home_name if st.score_home > st.score_away else st.away_name
            return {"stage": "extra_time", "score": [st.score_home, st.score_away], "winner": w}, md, stats
        # 仍平 → 点球大战(引擎 /penalty)
        async with httpx.AsyncClient() as http:
            async def eng(path, payload): return await self._engine(http, path, payload)
            so_side, sa, sb, log = await KO.shootout(eng, md)   # 返回 ('home'/'away', sa, sb, log)
        w = st.home_name if so_side == "home" else st.away_name
        return {"stage": "shootout", "score": [st.score_home, st.score_away],
                "shootout": {"home": sa, "away": sb}, "winner": w}, md, stats

    # ── #2 赛后报告：MiroFish ReACT 改造的 FootballReportAgent（工具读 trajectory+stats）──
    async def report(self, st: MatchState, traj_path: str, stats: dict) -> str:
        agent = FootballReportAgent(self.brain, traj_path, stats)
        return await agent.generate()
