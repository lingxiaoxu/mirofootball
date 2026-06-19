# MiroFootball — 部署脚本骨架（NVIDIA DGX Spark / ARM64 + Blackwell）

> 配套 00/01/02。本文给**可直接起步的代码骨架** + **DGX Spark 环境的可用/不可用清单**。
> ⚠️ **开发环境恒定前提：NVIDIA DGX Spark** —— Grace **ARM64(aarch64)** CPU + Blackwell **`sm_121`** GPU + **128GB 统一内存** + **~273GB/s 带宽** + **NVFP4 原生**。所有镜像/wheel/二进制**必须 arm64**；跨两台只有**慢桥接**。

---

## 0. DGX Spark 环境：可用 ✅ / 不可用或需注意 ⚠️❌

### ✅ 可用
| 项 | 说明 |
|---|---|
| CUDA on ARM64 | DGX OS（Ubuntu 基底）自带，`sm_121` 架构 |
| **NVFP4 原生量化** | Blackwell 最优精度，Qwen/Gemma 优先用 |
| 128GB 统一内存 | CPU+GPU 共享，**一份权重 CPU/GPU 都能访问**（省拷贝）|
| Ollama (arm64) | 调试/小模型可用（Gemma 退路）|
| Docker + nvidia-container-toolkit (arm64) | 容器可用，但镜像**必须 arm64** |
| Neo4j (arm64 镜像) | `neo4j:5-community` 有 arm64 manifest |
| Node.js (arm64) | 引擎直接跑 |
| Python 3.12 | 丢掉 camel-oasis 后无 <3.12 限制 |
| Grace 20 核 CPU | 跑 Node 引擎 + 编排 + Neo4j + grammar 编译 |

### ⚠️ 需注意 / ❌ 不可用
| 项 | 状况 | 对策 |
|---|---|---|
| **x86_64 镜像/wheel** | ❌ 跑不了（架构不符）| 一律用 **arm64/aarch64** 构建；ML 镜像优先 **NVIDIA NGC**（为 GB10/arm64 出的）|
| **vLLM 预编译 wheel** | ⚠️ 多为 x86；**sm_121+arm64** 可能要**源码编译**或用 NGC 容器 | 先试官方 arm64 wheel/NGC；不行则源码 build（开 `TORCH_CUDA_ARCH_LIST=12.1`）|
| flash-attention / 自定义 CUDA kernel | ⚠️ 需 arm64+sm_121 构建 | 缺则关掉/退 xformers/默认 attention |
| **NVFP4 在 vLLM 的成熟度** | ⚠️ 新架构，路径可能不全 | 退 **FP8 / AWQ-4bit / GPTQ-4bit**（先跑通，再上 NVFP4）|
| TensorRT-LLM for GB10 | ⚠️ 支持可能滞后 | **优先 vLLM / SGLang**，别一上来 TRT-LLM |
| 跨两台高速互联 | ❌ 无 NVLink，只有**慢桥接** | **绝不**跨机张量并行；一场=一台（见 00 §9.2）|
| 大 dense 模型 (70B+ FP16) | ⚠️ 273GB/s 带宽慢 | 只用 **MoE/MatFormer 小激活** + 批处理 |
| `--gpu-memory-utilization` 语义 | ⚠️ 统一内存，CPU+GPU 同池 128GB | 三 vLLM 利用率之和 + Neo4j/Node/OS **< 安全线**（见 00 §8）|
| 高功耗持续负载 | ⚠️ 桌面级散热 | 长跑监控温度/降频 |

> **一句话**：能用的是 ARM64 CUDA + NVFP4 + vLLM(优先) + 统一内存 + MoE 模型；不能指望 x86 镜像、跨机高速互联、未成熟的量化路径——**先 FP8/AWQ 跑通，再上 NVFP4**。

---

## 1. 目录与起服顺序

```
mirofootball/
├── serving/   vllm_qwen.sh  vllm_gemma_home.sh  vllm_gemma_away.sh   # 跑在 HOST(见 §2 说明)
├── engine/    engine.js(vendored)  server.js  init_config/          # Node, :7000
├── brain/     llm_client.py  agents/{team_agent,ball_agent}.py  orchestrator.py  match_director.py  kg/  config.py
├── coordinator/ coordinator.py                                      # 双机用
├── docker-compose.yml                                               # neo4j + engine + orchestrator (arm64)
└── .env
```
**起服顺序**：vLLM×3(host) → neo4j → engine → orchestrator。

> **为什么 vLLM 放 HOST 不进 compose**：sm_121+arm64 的 GPU 容器化最易踩坑（驱动/架构/NVFP4）。**先在宿主机把 vLLM 跑通**，容器只放无 GPU 依赖的 neo4j/engine/orchestrator。等稳定再考虑用 NGC 容器化 vLLM。

---

## 2. serving/ — 模型服务（当前实跑 Ollama，目标态 vLLM）

> ⭐ **当前实跑 = Ollama，三模型同时在跑：1 个共享 nemotron-120b + 2 个独立 gemma4:e2b**（满足"必须两个 gemma"）。§2.0 现在就能起；§2.1 vLLM 脚本保留作目标态。

### 2.0 当前实跑：Ollama —— 三模型同时跑（brain 共享 + 两个独立 gemma）

**Ollama 加载语义（关键，决定"真两个 gemma"）**：Ollama 按**模型名**识别已加载实例。对同一名字 `gemma4:e2b` 发两路请求 → **只加载一份**权重 + 用 `OLLAMA_NUM_PARALLEL` 开并行 slot（= 一个 gemma 服务两队，**不要**）。**要两个独立实例 → 用两个不同模型名**：用 Modelfile 从 `gemma4:e2b` 派生 `gemma-home`/`gemma-away`（底座相同，将来各挂 LoRA）→ Ollama 当两个不同模型、**各加载一份** → 内存里真有两个 gemma。

```
共享 daemon :11434  ── nemotron-3-super:120b  (已驻, 只读发请求, 绝不碰/不重载/不改 num_ctx)
mirofootball 自有 daemon :11435  ┬─ gemma-home  (FROM gemma4:e2b, 独立加载)
                                 └─ gemma-away  (FROM gemma4:e2b, 独立加载)
```
内存：94(brain)+~2(home)+~2(away)+~1(infra) ≈ 100GB / 127.5GB ✅。加载/驱逐只在 :11435 内，共享 brain 永不受影响。

```bash
# serving/start_ollama_gemma.sh  —— mirofootball 自有 Ollama 实例(独立端口+独立模型目录, 零干扰共享 daemon)
OLLAMA_HOST=localhost:11435 \
OLLAMA_MODELS=$HOME/mirofootball/serving/ollama_models \
OLLAMA_MAX_LOADED_MODELS=2 \          # ← 关键: 允许 2 个 gemma 同时常驻
OLLAMA_NUM_PARALLEL=4 \               # 每个 gemma 的并发 slot(批 10-11 人)
OLLAMA_KEEP_ALIVE=-1 \                # 永不自动卸载(只对我们这台; 共享 daemon 不碰)
ollama serve &

OLLAMA_HOST=localhost:11435 ollama pull gemma4:e2b-it-q4_K_M
# serving/Modelfile.home:  FROM gemma4:e2b-it-q4_K_M   (将来加 ADAPTER ./lora/home, 见 04 §6.1)
# serving/Modelfile.away:  FROM gemma4:e2b-it-q4_K_M   (将来加 ADAPTER ./lora/away)
OLLAMA_HOST=localhost:11435 ollama create gemma-home -f serving/Modelfile.home
OLLAMA_HOST=localhost:11435 ollama create gemma-away -f serving/Modelfile.away
OLLAMA_HOST=localhost:11435 ollama run gemma-home "ok" >/dev/null   # 预热进显存
OLLAMA_HOST=localhost:11435 ollama run gemma-away "ok" >/dev/null

# 验收: 三个模型真同时在跑
curl -s localhost:11434/api/ps   # 期望 nemotron-3-super:120b
curl -s localhost:11435/api/ps   # 期望 gemma-home + gemma-away 两个都在
```
> brain 不用我们起（已在共享 :11434 跑）；mirofootball 只对它**发推理请求**。两 daemon 共用同一 GPU/统一内存池没问题。

### 2.1 目标态：vLLM 三实例（HOST，arm64/Blackwell）

`serving/_common.sh`
```bash
#!/usr/bin/env bash
# DGX Spark: 确认 arch 与 GPU
set -euo pipefail
test "$(uname -m)" = "aarch64" || { echo "NOT arm64 — wrong host"; exit 1; }
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader   # 期望 GB10 / 128GB 统一内存
# 量化优先级: 先用能跑通的, 再升 NVFP4。用环境变量切换。
QUANT="${QUANT:-fp8}"     # 起步用 fp8/awq; 验证后改 nvfp4
```

`serving/vllm_qwen.sh`  （战术大脑 :8001）
```bash
#!/usr/bin/env bash
source "$(dirname "$0")/_common.sh"
# ⚠️ Qwen3.6 是 MoE+混合注意力(Gated DeltaNet)，确认 vLLM 版本支持该 arch；不支持则等更新或用官方推荐镜像
exec vllm serve Qwen/Qwen3.6-35B-A3B \
  --quantization "$QUANT" \
  --tensor-parallel-size 1 \                # 单机单GPU, 绝不跨机
  --max-model-len 32768 \                   # 足球用不到256K, 省KV
  --gpu-memory-utilization 0.40 \           # 统一内存: 与两Gemma+Neo4j共享128GB
  --enable-prefix-caching \                 # 共享规则前缀
  --port 8001
```

`serving/vllm_gemma_home.sh`  （主队跑位 :8002）
```bash
#!/usr/bin/env bash
source "$(dirname "$0")/_common.sh"
# Gemma 极小; 若 vLLM 对 MatFormer/PLE 适配不顺 → 退 llama.cpp/Ollama(arm64)
exec vllm serve google/gemma-4-E2B-it \
  --quantization "${QUANT}" \
  --tensor-parallel-size 1 --max-model-len 8192 \
  --gpu-memory-utilization 0.06 --max-num-seqs 32 \
  --enable-prefix-caching \
  ${HOME_LORA:+--enable-lora --lora-modules home=$HOME_LORA} \   # 有LoRA才挂; 没有先跑底座+prompt
  --port 8002
```

`serving/vllm_gemma_away.sh`  （客队跑位 :8003）— 同上，端口 8003、`away=$AWAY_LORA`。

> **退路（Gemma）**：若 vLLM 不收 Gemma4 → `ollama run gemma-4-E2B`（arm64），编排器 base_url 指 Ollama 的 `:11434/v1`。接口都是 OpenAI 兼容，编排器不变。

---

## 3. engine/server.js — 引擎薄 HTTP 包装（Node, :7000）

> **铁律：`engine.js` 和 `lib/*.js` 原封不动 vendored（一行不改）**。server.js 只是**外壳**——`import` 引擎原函数 + 暴露 HTTP，所有解算（射门/扑救/点球/抢断/越位/伤病/定位球/最近球员）一律调引擎原函数。

```javascript
// engine/server.js  —— 仅外壳; 只 import 引擎原函数, 引擎源码一行不改
// DGX: 纯 CPU, 跑在 Grace 上; Node arm64
import express from 'express'
import { initiateGame, playIteration, startSecondHalf } from './engine.js'        // vendored 原样
import playerMovement from './lib/playerMovement.js'                              // 复用原函数(如 closestPlayerToBall)

const app = express()
app.use(express.json({ limit: '8mb' }))

app.post('/initiate',   async (req,res)=>{ try{ const{team1,team2,pitch}=req.body; res.json(await initiateGame(team1,team2,pitch)) }catch(e){res.status(400).json({error:String(e)})} })
// matchDetails 里已被编排器注入各球员 action/intentPOS(LLM); playIteration 原样解算物理+单球不变式
app.post('/iterate',    async (req,res)=>{ try{ res.json(await playIteration(req.body.matchDetails)) }catch(e){res.status(400).json({error:String(e)})} })
app.post('/secondhalf', async (req,res)=>{ try{ res.json(await startSecondHalf(req.body.matchDetails)) }catch(e){res.status(400).json({error:String(e)})} })

// 第一防守者: 调引擎原 closestPlayerToBall, 不在编排器重写几何 (06 §2.2)
app.post('/closest', (req,res)=>{ try{
  const { matchDetails, team } = req.body
  const closest = { name:'', position:1e9 }
  playerMovement.closestPlayerToBall(closest, team, matchDetails)   // ← 引擎原函数
  res.json(closest)
}catch(e){res.status(400).json({error:String(e)})} })

app.listen(7000, 'localhost', () => console.log('engine on :7000'))
```
> ⚠️ **静音每拍 console.log（代码核实有两处，2026-06）**：`engine.js:89 console.log(JSON.stringify(matchDetails))`（每拍）+ `lib/ballMovement.js:692 console.log(finalTarget)`（命中该路径时）。都是**原代码、一行不改**。编排器读的是 `playIteration` 的**返回值**(matchDetails)，不依赖 stdout。静音三选一，**全在包装层、不碰引擎源码**：① 起服 `node server.js >/dev/null`（最简）；② `server.js` 顶部 `console.log = () => {}`（monkeypatch，保留 `console.error` 看告警）；③ 只重定向 stdout、留 stderr。
>
> ⭐ **（可选）确定性随机种子**（#3，默认接受不确定性；真要逐场复现时启用）：引擎全部随机走 `lib/common.js::getRandomNumber`→`Math.random()`，**不可种子化**。在 server.js **require 引擎之前**加一行 shim `Math.random = seededPRNG(seed)` 即让整引擎对给定 seed 确定——**仍不碰引擎源码**（shim 在外壳）。配合 LLM `temperature=0`/固定 seed 才完全确定；否则按 06 §3.4 只比统计分布。

---

## 4. brain/llm_client.py — async OpenAI（serving-aware：vLLM 或 Ollama）

> ⭐ **当前实跑 = Ollama，但 brain 与 gemma 分两处（隔离铁律：绝不碰共享 brain）**：
> - **brain = `nemotron-3-super:120b`** 是**共享模型，被 web 其它服务在用** → mirofootball **只把它当只读外部推理端点**：仅发 chat 请求，**绝不 reload/evict/重启其 server，绝不覆盖 `num_ctx`**（改 ctx 会触发整模型重载 = 打断共享方）。发请求时**不传 num_ctx**（用它已加载的 256K），不传强制重配项。
> - **两队 `gemma4:e2b` 跑在 mirofootball 自己的独立 Ollama 实例/端口**（如 `OLLAMA_HOST=localhost:11435`，设它自己的 `OLLAMA_MAX_LOADED_MODELS≥2`）→ 加载 gemma **不会驱逐共享 brain**，两边内存仍在同一 128GB 池但互不重载。
> - client 同时支持 **vLLM `guided_json`** 与 **Ollama `format`** 两种 schema 约束解码（#2 解法）；MiroFish `LLMClient` 已自动剥 `<think>`，**保留 brain 的 thinking 不影响 JSON**。**#6：不强制关 thinking**（brain 共享、不为我们重配），`think` 默认不传 = 用模型默认（开）。

```python
# brain/llm_client.py  —— OpenAI 兼容; 批处理在服务层(并发请求自动合批)
# 纯网络IO; 用async并发把一队请求让服务端合批 → 关键提速
import asyncio, json
from openai import AsyncOpenAI

class LLM:
    def __init__(self, base_url: str, model: str, lora: str | None = None,
                 serving: str = "ollama", think: bool | None = None):
        self.cli = AsyncOpenAI(base_url=base_url, api_key="local")  # 本地服务忽略key
        self.model, self.lora = model, lora
        # serving∈{"ollama","vllm"}; think=None=不传(用模型默认,共享brain保持thinking开); 仅显式False才关
        self.serving, self.think = serving, think

    def _schema_extra(self, schema: dict) -> dict:
        # #2 严格 schema 约束解码 —— 按服务选透传方式; #6 不强制关 thinking
        if self.serving == "vllm":
            extra = {"guided_json": schema}                                  # vLLM(outlines/xgrammar)
        else:
            extra = {"format": schema}                                       # Ollama 原生 structured outputs(≥0.5.0)
            if self.think is False: extra["think"] = False                   # 仅显式要求才关(默认不传→thinking开)
            # ⚠️ 绝不传 num_ctx → 避免触发共享 brain 重载(隔离铁律)
        if self.lora: extra["model"] = self.lora        # vLLM multi-LoRA 选适配器; Ollama 用派生模型名(下行 model=)
        return extra

    async def decide(self, system: str, user: dict, schema: dict, max_tokens: int = 64):
        r = await self.cli.chat.completions.create(
            model=self.lora or self.model,              # Ollama: "nemotron-3-super:120b"/"gemma4:e2b"/"gemma-home"
            messages=[{"role":"system","content":system},
                      {"role":"user","content":json.dumps(user, ensure_ascii=False)}],
            max_tokens=max_tokens, temperature=0.7,
            extra_body=self._schema_extra(schema))
        txt = r.choices[0].message.content
        return json.loads(txt)                          # schema 已保证合法; 仍可包 try + 手工修复兜底(见 02 §6)

    async def batch(self, system, users, schema, max_tokens=24):
        # 一队并发 → 服务端连续批处理合成一个batch (真并行, 见 00 §4)
        return await asyncio.gather(*[self.decide(system, u, schema, max_tokens) for u in users])
```
> **三实例怎么开（Ollama 版，brain/gemma 分两个 server）**：
> - `brain=LLM("http://localhost:11434/v1","nemotron-3-super:120b")`（**共享 server，只读发请求，think 不传=保持 thinking 开，不传 num_ctx**）；
> - `home=LLM("http://localhost:11435/v1","gemma4:e2b")`、`away=LLM("http://localhost:11435/v1","gemma4:e2b")`（**mirofootball 自己的独立 Ollama 实例 :11435**，加载 gemma 不驱逐共享 brain；有 LoRA 后换 `gemma-home`/`gemma-away`）。
> **走 MiroFish 直连路线，不用 camel 全局 env。**

---

## 5. brain/agents/ — 两队 Gemma 批 + Qwen 持球

`brain/agents/team_agent.py`
```python
# 一队的 Gemma: 把无球球员(含GK走GK路径) 一批决策
from ..llm_client import LLM
from .prompts import OFFBALL_SYS, GK_SYS, OFFBALL_SCHEMA, GK_SCHEMA, role_options

class TeamGemma:
    def __init__(self, base_url, model, lora):  # 主队/客队各一个实例(独立端口)
        self.llm = LLM(base_url, model, lora)

    async def decide_batch(self, offball_players: list[dict], world: dict, biases: dict):
        outs = await self.llm.batch(
            OFFBALL_SYS,
            [self._mk_user(p, world, biases) for p in offball_players if p["role"] != "GK"],
            OFFBALL_SCHEMA, max_tokens=24)
        gk = [p for p in offball_players if p["role"] == "GK"]
        if gk:  # GK 走 GK 角色 prompt (见 02 §3)
            outs += await self.llm.batch(GK_SYS, [self._mk_gk(g, world) for g in gk], GK_SCHEMA, 24)
        return outs

    def _mk_user(self, p, world, biases):
        return {"world": world, "me": p, "team_intent": world["phase"],
                "retention_bias": biases["retention"], "press_intensity": biases["press"],
                "options": role_options(p["role"])}
    def _mk_gk(self, g, world):
        return {"world": world, "me": g, "threat": world.get("threat", {}),
                "options": ["hold_line","narrow_angle","rush_out","claim_cross","set_wall",
                            "distribute_short","distribute_long"]}
```

`brain/agents/ball_agent.py`
```python
# 持球者一人 → Qwen, 看 world 全局 (见 02 §4)
from ..llm_client import LLM
from .prompts import ONBALL_SYS, ONBALL_SCHEMA, allowed_actions

class BallQwen:
    def __init__(self, base_url, model): self.llm = LLM(base_url, model)
    async def decide(self, holder: dict, world: dict, biases: dict):
        user = {"world": world, "me": holder,
                "teammates": world["_teammates_of"](holder),     # 编排器预备
                "opponents_near": world["_opps_near"](holder),
                "retention_bias": biases["retention"],
                "allowed": allowed_actions(holder, world)}        # 按区域裁剪
        return await self.llm.decide(ONBALL_SYS, user, ONBALL_SCHEMA, max_tokens=64)
```

---

## 6. brain/orchestrator.py — 核心 tick 循环

```python
# brain/orchestrator.py  —— 一场比赛(整场在一台box, 见 00 §9.2)
import asyncio, httpx
from .agents.team_agent import TeamGemma
from .agents.ball_agent import BallQwen
from .match_director import MatchDirector
from .possession import PossessionDirector
from .kg import KG
from .config import CFG

ENGINE = CFG.ENGINE_URL  # http://localhost:7000

async def engine_call(client, path, payload):
    r = await client.post(f"{ENGINE}/{path}", json=payload, timeout=30)
    r.raise_for_status(); return r.json()

async def simulate_match(team1, team2, pitch):
    async with httpx.AsyncClient() as http:
        director = MatchDirector(CFG.BRAIN_URL, CFG.BRAIN_MODEL)    # 共享 nemotron-120b @:11434(只读, §2.0/§4)
        cfg  = await director.match_config(team1, team2)            # brain 生成配置(02 §1)
        poss = PossessionDirector(cfg["home"]["possession_target"]) # 控球反馈(01 §3.2); MVP 可后接
        kg   = KG(CFG.NEO4J_URI)                                    # MVP 可先不接(05 §3.7 #5)

        # 两队 = 两个独立 Ollama 模型名 @:11435(真两个 gemma 实例, §2.0); 持球走共享 brain
        gh = TeamGemma(CFG.GEMMA_URL, CFG.GEMMA_HOME)   # model='gemma-home'
        ga = TeamGemma(CFG.GEMMA_URL, CFG.GEMMA_AWAY)   # model='gemma-away'
        qb = BallQwen(CFG.BRAIN_URL, CFG.BRAIN_MODEL)   # 持球决策 → 共享 nemotron

        md = await engine_call(http, "initiate", {"team1":team1,"team2":team2,"pitch":pitch})
        kg.bootstrap(md)
        ITER, EVERY = CFG.ITER_PER_HALF, CFG.GEMMA_EVERY

        for it in range(ITER*2):
            if it == ITER:
                md = await engine_call(http, "secondhalf", {"matchDetails": md})
            world  = build_world(md)                               # 冻结快照→共享切片(02 §0)
            biases = poss.biases(world["possession"])              # 控球偏置

            # 跑位意图: 每EVERY拍或事件触发; 两队Gemma并发(真并行)
            if it % EVERY == 0 or ball_state_changed(md):
                hi, ai = await asyncio.gather(
                    gh.decide_batch(offball(md, "home"), world, biases["home"]),
                    ga.decide_batch(offball(md, "away"), world, biases["away"]))
                inject_intents(md, hi, ai)

            # 持球者一人 → Qwen
            if (holder := on_ball(md)) is not None:
                inject_ball_action(md, await qb.decide(holder, world, biases[holder["team"]]))

            md = await engine_call(http, "iterate", {"matchDetails": md})  # 物理+单球不变式
            kg.update(md, it); poss.observe(md)

        report = await director.report(md, kg.stats())             # ReportAgent(02 §5)
        return md, report

if __name__ == "__main__":
    import json, sys
    t1, t2, pitch = (json.load(open(f)) for f in sys.argv[1:4])
    md, report = asyncio.run(simulate_match(t1, t2, pitch))
    print(report)
```
> `build_world / offball / on_ball / inject_* / ball_state_changed` = 编排器里把 `matchDetails` ↔ prompt 的转换工具（zone 换算、按 `hasBall` 路由），见 02 §7 映射表。

---

## 7. docker-compose.yml — 无 GPU 依赖的服务（arm64）

```yaml
# 仅放 neo4j + engine + orchestrator; vLLM 在 HOST(见 §1 说明)
# 所有镜像必须 arm64 (DGX = aarch64)
services:
  neo4j:
    image: neo4j:5-community            # 有 arm64 manifest
    environment: [ "NEO4J_AUTH=neo4j/<SET_YOUR_PASSWORD>" ]
    ports: [ "7474:7474", "7687:7687" ]
    volumes: [ "neo4j_data:/data" ]

  engine:
    build: { context: ./engine }         # node:22-slim (arm64)
    network_mode: host                    # 直连 :7000, 与编排器同机
    command: [ "node", "server.js" ]

  orchestrator:
    build: { context: ./brain }          # python:3.12-slim (arm64)
    network_mode: host                    # 访问 host 的 vLLM :8001-8003 + neo4j
    env_file: .env
    depends_on: [ neo4j ]
    # vLLM 不在此; 由 host 的 serving/*.sh 起

volumes: { neo4j_data: {} }
```
> `network_mode: host` 让容器直接访问宿主机的 vLLM 端口（最省事）。`engine`/`brain` 的 Dockerfile 用 **arm64 基础镜像**（`node:22-slim`、`python:3.12-slim` 都有 arm64）。

---

## 8. .env（每台）

```bash
# 当前实跑(Ollama): brain 共享只读 + 两个独立 gemma 在自有实例(§2.0)
BRAIN_URL=http://localhost:11434/v1      ; BRAIN_MODEL=nemotron-3-super:120b   # 共享 daemon, 只读不碰
GEMMA_URL=http://localhost:11435/v1      ; GEMMA_HOME=gemma-home ; GEMMA_AWAY=gemma-away  # 自有 daemon, 两个独立模型名
ENGINE_URL=http://localhost:7000
NEO4J_URI=bolt://localhost:7687          ; NEO4J_AUTH=neo4j/<SET_YOUR_PASSWORD>   # MVP 可先不接
ITER_PER_HALF=2000                        ; GEMMA_EVERY=12     # 速度/保真旋钮(00 §9.1)
ROLE=worker                               ; PEER_BOX=BOX_B_IP  # Box A 额外 ROLE=coordinator
# 目标态(vLLM)备选: QWEN_URL=:8001 ; GEMMA_HOME_URL=:8002 ; GEMMA_AWAY_URL=:8003 ; QUANT=fp8
```

---

## 9. 起步验证（DGX 上逐条过）

```bash
# 0. 确认环境
uname -m                       # 期望 aarch64
free -g                        # 统一内存看占用(非 nvidia-smi); brain 已驻 ~94G

# 1. 模型服务 —— 当前 Ollama 三模型(P2)
curl -s localhost:11434/api/ps                       # 共享 brain: nemotron-3-super:120b 在
bash serving/start_ollama_gemma.sh                   # 起自有 :11435 + 造/预热 gemma-home/away(§2.0)
curl -s localhost:11435/api/ps                       # 期望 gemma-home + gemma-away 两个都在 ← "真两个 gemma"
curl -s localhost:11434/v1/chat/completions -d '{"model":"nemotron-3-super:120b","messages":[{"role":"user","content":"ping"}],"max_tokens":8}'   # brain 只读 ping; 不传 num_ctx
#   (目标态 vLLM 路径: QUANT=fp8 bash serving/vllm_qwen.sh & ...)

# 2. engine 服务化 + 纯引擎自检(P2, 不接 LLM)
cd engine && npm ci && node server.js >/dev/null &   # console.log 进程层静音(§3)
curl -s localhost:7000/initiate -d @init_config/sample.json -H 'content-type: application/json' | head -c 300
#   再循环 /iterate 跑完一场, 确认纯引擎物理无误

# 3. 一场 MVP(P3): brain 持球点 + 两个 gemma 各决一队跑位 → 一场 + 解说
python -m brain.orchestrator init_config/team1.json init_config/team2.json init_config/pitch.json
#   验收: 日志见 brain 持球决策 + gemma-home/away 注入分别命中两队 + 比分/控球合理 + 出解说
```

---

## 10. DGX 双机（数据并行，慢桥接友好）

```
Box A (BOX_A_IP): 全栈 + coordinator.py    Box B (BOX_B_IP): 全栈
coordinator: 比赛队列 → 交替派发整场给空闲 box → 回收 result JSON (KB级, 走慢桥接OK)
跨机流量 = 仅 派发/结果 + 启动时静态KG复制一次; 模拟期零跨机 (见 00 §3)
```
`coordinator/coordinator.py`（骨架）
```python
# 跑在 Box A; 不做张量并行, 只派发整场
import asyncio, httpx
BOXES = ["http://localhost:9000", "http://BOX_B_IP:9000"]   # 每台一个 orchestrator HTTP 入口
async def run_many(matches):
    sem = {b: asyncio.Semaphore(1) for b in BOXES}           # 每台一次一场(或按内存并发2场)
    async def one(m):
        b = await pick_free_box(sem)
        async with sem[b], httpx.AsyncClient() as c:
            return (await c.post(f"{b}/simulate", json=m, timeout=None)).json()  # 小JSON往返
    return await asyncio.gather(*[one(m) for m in matches])
```

---

## Sources
- [NVIDIA DGX Spark Hardware](https://docs.nvidia.com/dgx/dgx-spark/hardware.html) · [DGX Spark 产品页](https://www.nvidia.com/en-us/products/workstations/dgx-spark/)
- [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) · [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.6-35B-A3B) · [Gemma 4 E2B](https://huggingface.co/google/gemma-4-E2B)
- [footballSimulationEngine-test](https://github.com/OWNER/footballSimulationEngine-test) · [MiroFish-Offline-Test](https://github.com/OWNER/MiroFish-Offline-Test)
- 配套：00_MIROFOOTBALL_PLAN · 01_SIMULATION_SEMANTICS · 02_PROMPT_TEMPLATES
```
