"""FootballReportAgent —— 赛后解说（ReACT 工具循环，改造自 MiroFish report_agent.py）。

保留 MiroFish 的"文本式 ReACT"范式：<tool_call>{...}</tool_call> 解析、强制最少工具调用、Final Answer。
工具换成读 **trajectory.jsonl + 比赛统计**（不依赖 Neo4j）。模型无关，用 brain(/api/chat)。
另移植 MiroFish `ReportLogger` 范式：每步写 agent_log.jsonl(action/details/elapsed)。
"""
import json
import os
import re
import time

SECTION_SYS = """You are a football match analyst writing a vivid, concise post-match report.
Observe the match ONLY through tools (do not invent). Each reply do EXACTLY ONE of:
  (A) call a tool:  <tool_call>{"name":"TOOL","parameters":{...}}</tool_call>
  (B) output final report starting with "Final Answer:"
Call at least 2 tools before the Final Answer. Tools available:
- match_stats(): final score, possession, shots, saves, attacks
- key_events(): goals / shots / tackles / saves / set-pieces timeline
- player_path(player_id): one player's movement summary
Ground every claim in tool results. Keep the report to 4-6 sentences."""

EVENT_KW = ("Goal", "goal", "Shot", "shot", "save", "Save", "Tackle", "tackle",
            "passed", "through", "cross", "penalty", "corner", "Throw", "freekick")


class FootballReportAgent:
    VALID = {"match_stats", "key_events", "player_path"}

    def __init__(self, llm, traj_path: str, stats: dict, log_dir: str = None):
        self.llm = llm
        self.traj_path = traj_path
        self.stats = stats
        self._lines = None
        self._t0 = time.time()
        self.log_path = os.path.join(log_dir or os.path.dirname(traj_path) or ".", "agent_log.jsonl")

    # ── 改造自 MiroFish ReportLogger.log：每步一行 jsonl ──
    def _log(self, action: str, details: dict = None):
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"elapsed": round(time.time() - self._t0, 2),
                                    "action": action, "details": details or {}}, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _traj(self):
        if self._lines is None:
            self._lines = []
            if os.path.exists(self.traj_path):
                with open(self.traj_path, encoding="utf-8") as f:
                    self._lines = [json.loads(x) for x in f if x.strip()]
        return self._lines

    # ── 工具 ──
    def _tool(self, name, params):
        if name == "match_stats":
            return json.dumps(self.stats, ensure_ascii=False)
        if name == "key_events":
            evs = []
            for rec in self._traj():
                for e in rec.get("events", []):
                    if any(k in e for k in EVENT_KW):
                        evs.append({"min": rec.get("min"), "e": e})
            return json.dumps(evs[:40], ensure_ascii=False) if evs else "no notable events logged"
        if name == "player_path":
            pid = params.get("player_id")
            pts = []
            for rec in self._traj():
                for p in rec.get("players", []):
                    if str(p.get("id")) == str(pid):
                        pts.append(p.get("pos"))
            if not pts:
                return f"no path for player {pid}"
            return json.dumps({"player": pid, "from": pts[0], "to": pts[-1], "samples": pts[::max(len(pts)//6, 1)]}, ensure_ascii=False)
        return f"unknown tool {name}"

    def _parse(self, text):
        m = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL)
        if m:
            try:
                d = json.loads(m.group(1))
                if d.get("name") in self.VALID:
                    return d
            except json.JSONDecodeError:
                pass
        return None

    async def generate(self, max_rounds=5, min_tools=2):
        msgs_user = "Write the match report. Start by calling a tool."
        history = []   # 累积对话（拼进 user 内容，brain /api/chat 无状态）
        tools_used = 0
        self._log("start", {"min_tools": min_tools, "max_rounds": max_rounds})
        for _ in range(max_rounds):
            convo = SECTION_SYS + "\n\n" + "\n".join(history) + "\n\n" + msgs_user
            # brain reasoning 开 → 给足 token（思考 + 输出）
            try:
                resp = await self.llm.chat(None, convo, max_tokens=900)
            except Exception:
                break  # brain 持续不可用 → 退到下方 stats 简报兜底
            if not resp:
                break
            call = self._parse(resp)
            has_final = "Final Answer:" in resp
            self._log("llm_response", {"has_tool_call": bool(call), "has_final": has_final})
            if call and not has_final:
                self._log("tool_call", {"name": call["name"], "params": call.get("parameters", {})})
                result = self._tool(call["name"], call.get("parameters", {}))
                tools_used += 1
                self._log("tool_result", {"name": call["name"], "len": len(result)})
                history.append(f"Assistant called {call['name']}.")
                history.append(f"Observation ({call['name']}): {result[:1200]}")
                msgs_user = (f"Tool result above. Called {tools_used} tools. "
                             + ("You may now output Final Answer." if tools_used >= min_tools
                                else "Call another tool (need >=2)."))
                continue
            if has_final:
                if tools_used < min_tools:
                    msgs_user = "You must call at least 2 tools first. Call a tool now."
                    history.append("(rejected premature Final Answer)")
                    continue
                self._log("section_complete", {"tools_used": tools_used})
                return resp.split("Final Answer:")[-1].strip()
            # 既无工具也无 final → 催
            msgs_user = "Call a tool (<tool_call>...) or output 'Final Answer:'."
        # 兜底：直接让它基于 stats 写
        self._log("fallback", {"tools_used": tools_used})
        return await self.llm.chat(
            "You are a football analyst. Summarize the match in 3-4 sentences from these stats.",
            json.dumps(self.stats, ensure_ascii=False), max_tokens=800)
