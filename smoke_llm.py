"""冒烟测试：顺序调三端点各一次（不批量、留足时间）。验证 brain/llm_client 走原生 /api/chat。"""
import asyncio, time
from brain import config as C
from brain.llm_client import LLM

POS_SCHEMA = {"type": "object",
              "properties": {"target_zone": {"type": "string"}, "posture": {"type": "string"}},
              "required": ["target_zone", "posture"]}

async def main():
    home = LLM(C.GEMMA_HOME_URL, C.GEMMA_MODEL, think=C.GEMMA_THINK)
    away = LLM(C.GEMMA_AWAY_URL, C.GEMMA_MODEL, think=C.GEMMA_THINK)
    brain = LLM(C.BRAIN_URL, C.BRAIN_MODEL, think=C.BRAIN_THINK)

    sys_g = "You are an off-ball positioning brain for ONE player. Output ONLY JSON."

    t = time.time()
    r = await home.decide(sys_g, {"me": {"role": "RW", "zone": "D4"}, "ball_zone": "C6"}, POS_SCHEMA, 64)
    print(f"[gemma-home think={C.GEMMA_THINK}] {r}  ({time.time()-t:.1f}s)")

    t = time.time()
    r = await away.decide(sys_g, {"me": {"role": "LB", "zone": "D5"}, "ball_zone": "C6"}, POS_SCHEMA, 64)
    print(f"[gemma-away think={C.GEMMA_THINK}] {r}  ({time.time()-t:.1f}s)")

    t = time.time()
    txt = await brain.chat("You are a football tactical brain. One short sentence only.",
                           "Home leads 1-0 at 63', has ball in midfield. Give the directive.", 220)
    print(f"[brain think={C.BRAIN_THINK}] {txt[:120]}  ({time.time()-t:.1f}s)")

if __name__ == "__main__":
    asyncio.run(main())
