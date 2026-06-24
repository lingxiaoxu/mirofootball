"""MatchConfigGenerator —— 赛前战术 config 的 LLM 智能生成器。

改造自 MiroFish `services/simulation_config_generator.py`（SimulationConfigGenerator）：
- 移植其**健壮性逻辑**：带重试的 LLM 调用（`_call_llm_with_retry`）、截断 JSON 修复
  （`_fix_truncated_json`，逐字移植：数未闭合的 {}/[]、补引号/括号）、配置 JSON 修复（`_try_fix_config_json`）。
- 原件生成社交模拟参数(agents/events/time/platform)；这里改为生成【足球比赛 config】
  （两队 possession_target / tempo / directness / press_intensity），用真实均值 BASELINES 锚定，
  并走我们的**原生 Ollama brain**(LLM.decide/chat)而非 OpenAI 客户端。
"""
import asyncio
import json
import re
from typing import Any, Callable, Dict, Optional


# ── 赛前 config 的 system / schema / 真实均值基准（原在 match_director，迁来此处统一归口）──
MATCH_CONFIG_SYS = (
    "You are a football match director. Given two squads (ratings, style) AND realistic-average "
    "baselines, produce a pre-match tactical config as STRICT JSON. Anchor targets to baselines, "
    "skew by the rating gap (stronger side: more possession/shots), keep within ranges, "
    "possession_target of both teams sums to ~1.0. Output ONLY JSON."
)
_SIDE_PROPS = {
    "possession_target": {"type": "number"}, "tempo": {"type": "number"},
    "directness": {"type": "number"}, "press_intensity": {"type": "number"},
    "line_height": {"type": "number"},                      # 防线高度 0-1(02§1)
    "shots_target": {"type": "number"},
    "shots_on_target_rate": {"type": "number"},             # 射正率 0-1
    "attacks_target": {"type": "number"},                   # 控球段数/进攻次数
    "pass_completion_target": {"type": "number"},           # 传球成功率 0-1
    "expected_goals": {"type": "number"},                   # xG
    "tactical_note": {"type": "string"},
}
_SIDE = {"type": "object", "properties": _SIDE_PROPS,
         "required": ["possession_target", "tempo", "directness", "press_intensity"]}
MATCH_CONFIG_SCHEMA = {
    "type": "object",
    "properties": {"home": _SIDE, "away": _SIDE, "narrative_seed": {"type": "string"}},
    "required": ["home", "away"],
}
# 真实均值基准（01 §0.2 / 05§3.7）—— 可被预测盘/WC 数据替换
BASELINES = {
    "possession": {"mean": 0.50, "min": 0.35, "max": 0.65},
    "shots": {"mean": 13, "min": 8, "max": 18},
    "shots_on_target_rate": {"mean": 0.45, "min": 0.30, "max": 0.60},
    "attacks": {"mean": 10, "min": 8, "max": 14},
    "pass_completion": {"mean": 0.82, "min": 0.70, "max": 0.90},
    "save_rate": {"mean": 0.70, "min": 0.60, "max": 0.80},
    "goals_total": {"mean": 2.7, "min": 0, "max": 7},
    "possession_sequences": {"mean": 140, "min": 60, "max": 220},
}


class MatchConfigGenerator:
    """足球赛前 config 智能生成器（MiroFish SimulationConfigGenerator 血统）。

    用法: cfg = await MatchConfigGenerator(brain).generate_config(home_info, away_info)
    """

    MAX_ATTEMPTS = 3

    def __init__(self, brain):
        self.brain = brain   # brain = LLM(原生 /api/chat)

    async def generate_config(
        self,
        home_info: dict,
        away_info: dict,
        baselines: dict = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """智能生成整场 config（改造自 generate_config 的分步+进度回调结构）。"""
        baselines = baselines or BASELINES
        total_steps = 2

        def report(step: int, msg: str):
            if progress_callback:
                progress_callback(step, total_steps, msg)

        report(1, "build context + call brain for match config")
        user = {"home": home_info, "away": away_info, "baselines": baselines}
        cfg = await self._call_llm_with_retry(MATCH_CONFIG_SYS, user, baselines)
        report(2, "match config ready")
        return cfg

    # ── 移植自 _call_llm_with_retry：重试 + 截断/格式修复 ──
    async def _call_llm_with_retry(self, system: str, user: Any, baselines: dict) -> Dict[str, Any]:
        last_error = None
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                # 首选: schema 约束解码(format=schema, 原生 Ollama 已很稳)
                d = await self.brain.decide(system, user, MATCH_CONFIG_SCHEMA, max_tokens=700)
                if isinstance(d, dict) and "_error" not in d and "home" in d and "away" in d:
                    return d
                # 兜底: 取原文 → 截断修复 → 配置修复(移植 _fix_truncated_json / _try_fix_config_json)
                raw = d.get("_raw") if isinstance(d, dict) else None
                if not raw:
                    raw = await self.brain.chat(system, user, max_tokens=700)
                raw = self._fix_truncated_json(raw)
                try:
                    parsed = json.loads(raw)
                    if "home" in parsed and "away" in parsed:
                        return parsed
                except json.JSONDecodeError as e:
                    last_error = e
                    fixed = self._try_fix_config_json(raw)
                    if fixed and "home" in fixed:
                        return fixed
            except Exception as e:
                last_error = e
                await asyncio.sleep(1.5 * (attempt + 1))
        # 全部失败 → 用 baselines 给个安全默认(别让比赛起不来)
        return self._fallback(baselines)

    # ── 逐字移植 _fix_truncated_json ──
    def _fix_truncated_json(self, content: str) -> str:
        content = (content or "").strip()
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        if content and content[-1] not in '",}]':
            content += '"'
        content += ']' * open_brackets
        content += '}' * open_braces
        return content

    # ── 逐字移植 _try_fix_config_json ──
    def _try_fix_config_json(self, content: str) -> Optional[Dict[str, Any]]:
        content = self._fix_truncated_json(content)
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()

            def fix_string(match):
                s = match.group(0)
                s = s.replace('\n', ' ').replace('\r', ' ')
                s = re.sub(r'\s+', ' ', s)
                return s

            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string, json_str)
            try:
                return json.loads(json_str)
            except Exception:
                json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                json_str = re.sub(r'\s+', ' ', json_str)
                try:
                    return json.loads(json_str)
                except Exception:
                    pass
        return None

    def _fallback(self, baselines: dict) -> Dict[str, Any]:
        m = (baselines.get("possession") or {}).get("mean", 0.5)
        side = {"possession_target": m, "tempo": 0.5, "directness": 0.5, "press_intensity": 0.5}
        return {"home": dict(side), "away": dict(side), "narrative_seed": "balanced (fallback)"}
