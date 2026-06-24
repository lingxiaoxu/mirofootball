"""mirofootball LLM 客户端 —— Ollama 原生 /api/chat（think 可控 + format schema）。

为什么不用 OpenAI /v1（实测 2026-06）：/v1 不认 think 开关，且 reasoning 进独立字段、
吃光 max_tokens 致 content 空。原生 /api/chat 才能控 think + 拿 format 的 JSON。
(plan 03 §4 / 记忆 mirofish-local-llm-paths)

reasoning 已定：brain think=True、gemma think=False。
铁律：绝不传 num_ctx（防共享 brain 重载）。
"""
import asyncio
import json
from typing import Any, Optional
import httpx


class LLM:
    def __init__(self, base_url: str, model: str, think: bool = False,
                 serving: str = "ollama", timeout: float = 180.0):
        # base_url 用 主机:端口（原生 /api，不带 /v1），如 http://localhost:11436
        self.base = base_url.rstrip("/")
        self.model = model
        self.think = think
        self.serving = serving
        self.timeout = timeout

    def _body(self, system: Optional[str], user: Any, schema: Optional[dict], max_tokens: int) -> dict:
        content = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": content}]
        body = {
            "model": self.model,
            "messages": msgs,
            "stream": False,
            "think": self.think,                       # 仅原生 /api 认；gemma=False 快、brain=True
            "options": {"num_predict": max_tokens},     # ⚠️ 绝不传 num_ctx（防共享 brain 重载）
        }
        if schema is not None:
            body["format"] = schema                     # 原生 structured output → 合法 JSON
        return body

    async def _post(self, body: dict, retries: int = 3) -> dict:
        """POST /api/chat，对瞬时错误退避重试（共享 brain 可能瞬时忙 → 5xx）。"""
        last = None
        for k in range(retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as c:
                    r = await c.post(f"{self.base}/api/chat", json=body)
                if r.status_code >= 500:
                    last = RuntimeError(f"{r.status_code} {r.text[:120]}")
                    await asyncio.sleep(1.5 * (k + 1)); continue
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last = e; await asyncio.sleep(1.5 * (k + 1))
        raise last if last else RuntimeError("post failed")

    async def chat(self, system: Optional[str], user: Any, max_tokens: int = 256) -> str:
        """自由文本（如 ReportAgent）。返回 message.content（thinking 在独立字段）。"""
        j = await self._post(self._body(system, user, None, max_tokens))
        return (j["message"].get("content") or "").strip()

    async def decide(self, system: Optional[str], user: Any, schema: dict, max_tokens: int = 64) -> dict:
        """约束 JSON 决策。schema → format，返回解析后的 dict。"""
        j = await self._post(self._body(system, user, schema, max_tokens))
        txt = (j["message"].get("content") or "").strip()
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            # format 已约束；万一异常，原文返回供上层兜底/修复
            return {"_raw": txt, "_error": "json_decode"}

    async def batch(self, system: Optional[str], users: list, schema: dict, max_tokens: int = 64) -> list:
        """一队多人并发（给服务端连续批处理）。注意:勿过量并发——见 ops 约束。"""
        return await asyncio.gather(*[self.decide(system, u, schema, max_tokens) for u in users])
