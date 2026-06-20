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

## 1.5 MiroFish → mirofootball 文件级改造对照（实读核实）

> 把 `MiroFish-Offline-Test-main/backend` 的件**逐个映射**到 mirofootball,标清"复用/改/丢/新写"。核心结论:**reuse 直连 LLMClient + ConfigGenerator + ReportAgent + Runner + KG;丢整个 camel/OASIS;新写引擎壳 + tick + 两类 agent**。

| MiroFish 源（backend/app） | → mirofootball | 改动 |
|---|---|---|
| `utils/llm_client.py`（OpenAI `/v1`） | `brain/llm_client.py` | 🔧 **改成 Ollama 原生 `/api/chat` + think + format**（§4，关键）；开 3 实例 |
| `services/simulation_config_generator.py`（LLM 生成 config） | `brain/match_director.py` | ♻️ 复用机制，换 **match config schema**（02 §1，带真实均值目标） |
| `services/simulation_manager.py`（SimulationManager/State） | `brain/match_director.py` | ♻️ 顶层导演：tick 循环 + 比分/控球/状态（01 §1.2） |
| `services/report_agent.py`（ReACT 工具循环，模型无关） | `brain/report_agent.py` | ♻️ 复用 ReACT；工具换 **football tools**（查比赛数据,非 Neo4j 也可） |
| `services/simulation_runner.py`（后台/日志） | `brain/orchestrator.py` | ♻️ 复用后台/日志；`env.step` 批量模式 → 改成**调引擎 tick**（00 §4.1） |
| `storage/neo4j_*` `graph_builder` `graph_memory_updater` | `brain/kg/` | ♻️ 复用 KG；schema 换 match/team/player/state（00 §6）；**MVP 可不接** |
| `storage/embedding_service.py`（nomic-embed） | （可选） | 仅 KG 文档检索用；**MVP 不接**（05 §3.7） |
| `utils/retry.py` | `brain/utils/` | ♻️ 原样复用 |
| `scripts/run_{twitter,reddit,parallel}_simulation.py` | ❌ 丢 | camel/OASIS 社交,整块删 |
| `services/oasis_profile_generator.py` · OASIS env/recsys | ❌ 丢 | 社交人格/推荐图 |
| `camel-ai` / `camel-oasis` 依赖 | ❌ 丢 | 核心不依赖（仅上面脚本用）；丢后解除 Python<3.12 |
| —（新写） | `engine/server.js` | 🆕 引擎 HTTP 壳 + 静音 + 可选 seed（§3） |
| —（新写） | `brain/agents/team_agent.py` · `ball_agent.py` | 🆕 两队 gemma 批 / brain 持球+第一防守者（§5） |
| —（新写） | `brain/{world,inject}.py`（zone↔坐标 / build_world / inject_*） | 🆕 编排工具（05 §3.6） |
| —（新写） | `data_map.py`（FC26→引擎 + role→position）| 🆕 名单映射（04 §3.2） |

> **"确定能这样调三模型"**：✅ 已实跑验证（3 模型并发 + 链式 brain→gemma）。唯一须落实的改造 = `llm_client.py` 走原生 `/api/chat`（上表第一行）；其余是复用 + 新写黏合层。**机制已证,代码待写(P2/P3)。**

---

## 2. serving/ — 模型服务（当前实跑 Ollama，目标态 vLLM）

> ⭐ **当前实跑 = Ollama，三模型同时在跑：1 个共享 nemotron-120b + 2 个独立 gemma4:e2b**（满足"必须两个 gemma"）。§2.0 现在就能起；§2.1 vLLM 脚本保留作目标态。

### 2.0 当前实跑：Ollama —— 三模型同时跑（✅ 2026-06-20 已实测验证）

**已落地拓扑（实跑确认,3 模型并存）：**
```
:11434  nemotron-3-super:120b   systemd ollama 0.22.1  (共享 brain, 只读, 绝不碰/不重载/不改 num_ctx)
:11435  ollama-auth-proxy.js    .nemoclaw 的 token 认证代理 → 转发 :11434  (nemotron 远程访问链路, 绝不碰)
:11436  gemma4:e2b-it-qat       独立 daemon + 独立库 ollama_home   (home, 加载 ~2.5G)  ← 真实例 1
:11437  gemma4:e2b-it-qat       独立 daemon + 独立库 ollama_away   (away, 加载 ~2.5G)  ← 真实例 2
```
**踩过的 4 个真坑(都已解决,记录备查)：**
1. **`:11435` 被占** = nemoclaw 认证代理(非空闲)→ gemma 改用 **:11436 / :11437**。
2. **"派生两个模型名"会被去重**：两个 Modelfile 若仅差 `SYSTEM` → digest 相同 → Ollama 当**同一 runner**(`ollama ps` 只剩一个)。**要真两个实例 → 两个独立 daemon + 两个独立 `OLLAMA_MODELS` 目录(两份物理副本)**,最稳。
3. **默认 `gemma4:e2b`/`e2b-it-q4_K_M` 太大**：磁盘 7.2G、**加载 ~7.8G**(含多模态),两个 + nemotron(94G) **超 121G 装不下**。**必须用 `gemma4:e2b-it-qat`**(磁盘 4.3G、**加载仅 ~2.5G**,即使 128K ctx)→ 两个才 ~5G,装得下(实测 used 112G/121G, avail 9G)。
4. **QAT 需新版 Ollama**：系统 0.22.1 拉 QAT 报 `412 requires newer version`。→ 本地(无 sudo)装 **ollama 0.30.10** 仅给 gemma daemon 用;nemotron 的 systemd 0.22.1 不动。

```bash
# (一次性) 本地装新版 ollama 给 gemma 用(systemd 那个 0.22.1 跑 nemotron, 不碰)
#   tarball: https://github.com/ollama/ollama/releases/download/v0.30.10/ollama-linux-arm64.tar.zst
#   解到 ~/mirofootball/serving/ollama-new/ ; 运行时 LD_LIBRARY_PATH 指 ollama-new/lib/ollama
NEW=~/mirofootball/serving/ollama-new/bin/ollama
export LD_LIBRARY_PATH=~/mirofootball/serving/ollama-new/lib/ollama

# 两个独立 daemon + 两个独立库(两份物理 QAT 副本)
for pair in "11436 ollama_home" "11437 ollama_away"; do set -- $pair
  OLLAMA_HOST=localhost:$1 OLLAMA_MODELS=$HOME/mirofootball/serving/$2 \
  OLLAMA_KEEP_ALIVE=-1 OLLAMA_MAX_LOADED_MODELS=1 nohup $NEW serve >/dev/null 2>&1 & disown
done
OLLAMA_HOST=localhost:11436 $NEW pull gemma4:e2b-it-qat   # 第一份
OLLAMA_HOST=localhost:11437 $NEW pull gemma4:e2b-it-qat   # 第二份(独立库 → 物理两份)
OLLAMA_HOST=localhost:11436 $NEW run gemma4:e2b-it-qat "hi" >/dev/null   # 预热
OLLAMA_HOST=localhost:11437 $NEW run gemma4:e2b-it-qat "hi" >/dev/null

# 验收: 3 模型同时在跑
curl -s localhost:11434/api/ps   # nemotron
OLLAMA_HOST=localhost:11436 $NEW ps   # gemma home
OLLAMA_HOST=localhost:11437 $NEW ps   # gemma away
```

**开机自启（systemd user service + linger,已配并验证）：**
```bash
# ~/.config/systemd/user/ollama-gemma-home.service  (away 同, 改 11437 + ollama_away)
#   ExecStart=%h/mirofootball/serving/ollama-new/bin/ollama serve
#   Environment=OLLAMA_HOST=localhost:11436 / OLLAMA_MODELS=%h/.../ollama_home
#   Environment=OLLAMA_KEEP_ALIVE=-1 / OLLAMA_MAX_LOADED_MODELS=1
#   Environment=LD_LIBRARY_PATH=%h/mirofootball/serving/ollama-new/lib/ollama ; Restart=always
systemctl --user enable --now ollama-gemma-home ollama-gemma-away
loginctl enable-linger $USER     # 无需登录也开机自启(已 Linger=yes)
```
> 两 gemma 实测各答各的、互不串(home "2+2"→4 / away "sky color"→Blue)。两个加载稳定都 ~2.5G(早先 ps 看到 1.9 vs 2.5 只是查询瞬间 KV/compute buffer 计数差,非真实差异)。两 daemon 共用同一 GPU/统一内存池,brain 全程未受影响。

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
> - ⚠️ **必须走 Ollama 原生 `/api/chat`，不能用 OpenAI `/v1`**（实测 2026-06）：`/v1` **不认 `think` 开关**，且 reasoning 输出进独立 `reasoning` 字段、**吃光 `max_tokens` → `content` 空**（gemma 给 300 token 全被思考吃光仍空）。原生 `/api/chat` 才能控 `think` + 拿 `format` 的 JSON。**MiroFish `LLMClient` 默认走 `/v1`，故"mirofish→mirofootball"必须把 client 改成原生 `/api/chat`**（这是关键改造点，见 §1.5）。
> - ✅ **reasoning 已定（2026-06）：brain `think=True`（开）· gemma `think=False`（关）**。依据(实测):开 reasoning 每次先吐"Thinking Process",gemma 0.7s→**3–7.5s/次**;gemma 是高频量层(~20 人×每 10–15 拍×整场),开则单场从几分钟变**几小时**,且跑位是"选 zone+姿态"选择题、思维链增益小 → **关**(用 format 约束直接出短 JSON)。brain 是低频战略层(持球点/赛前 config/赛后解说),思考提质 → **开**。

```python
# brain/llm_client.py  —— Ollama 原生 /api/chat (think 可控 + format schema); 批处理靠 async 并发
# ⚠️ 不用 OpenAI /v1 (它不认 think、reasoning 吃光 max_tokens → content 空)
import asyncio, httpx, json

class LLM:
    def __init__(self, base_url: str, model: str, serving: str = "ollama", think: bool = False):
        # base_url 用 主机:端口(原生 /api, 不带 /v1), 如 http://localhost:11436
        self.base = base_url.rstrip("/"); self.model = model
        self.serving, self.think = serving, think     # gemma 热路径 think=False(快); brain 可 True

    async def decide(self, system: str, user: dict, schema: dict, max_tokens: int = 64):
        msgs = [{"role":"system","content":system},
                {"role":"user","content":json.dumps(user, ensure_ascii=False)}]
        if self.serving == "vllm":                    # 目标态: vLLM OpenAI /v1 + guided_json
            from openai import AsyncOpenAI
            cli = AsyncOpenAI(base_url=f"{self.base}/v1", api_key="local")
            r = await cli.chat.completions.create(model=self.model, messages=msgs,
                    max_tokens=max_tokens, temperature=0.7, extra_body={"guided_json": schema})
            return json.loads(r.choices[0].message.content)
        # 当前实跑: Ollama 原生 /api/chat
        body = {"model": self.model, "stream": False, "messages": msgs,
                "format": schema,                     # 原生 structured output → 合法 JSON
                "think": self.think,                  # ⚠️ 只有原生 /api 认; gemma=False 快, brain 可 True
                "options": {"num_predict": max_tokens}}  # ⚠️ 绝不传 num_ctx(防共享 brain 重载)
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{self.base}/api/chat", json=body, timeout=180)
        return json.loads(r.json()["message"]["content"])  # think 在独立字段; content 是纯 JSON

    async def batch(self, system, users, schema, max_tokens: int = 64):
        return await asyncio.gather(*[self.decide(system, u, schema, max_tokens) for u in users])
```
> **三实例（Ollama 原生 /api，base_url 用 主机:端口 不带 /v1）**：
> - `brain=LLM("http://localhost:11434","nemotron-3-super:120b", think=True)`（共享 server，只读；reasoning 开）
> - `home=LLM("http://localhost:11436","gemma4:e2b-it-qat", think=False)`、`away=LLM("http://localhost:11437","gemma4:e2b-it-qat", think=False)`（自有两 daemon；reasoning 关求快）
> **走直连路线，不用 camel 全局 env。**

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
        gh = TeamGemma(CFG.GEMMA_HOME_URL, CFG.GEMMA_MODEL)  # :11436 home 独立实例(gemma4:e2b-it-qat)
        ga = TeamGemma(CFG.GEMMA_AWAY_URL, CFG.GEMMA_MODEL)  # :11437 away 独立实例(同模型, 不同 daemon → 真两个)
        qb = BallQwen(CFG.BRAIN_URL, CFG.BRAIN_MODEL)        # 持球决策 → 共享 nemotron

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
            traj.append_tick(md, it)        # ★每拍全量轨迹→ data/<match>/trajectory.jsonl(连续可回溯, 01 §0.4)
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
BRAIN_URL=http://localhost:11434/v1       ; BRAIN_MODEL=nemotron-3-super:120b   # 共享 daemon(:11434), 只读不碰; 远程经 :11435 token 代理
GEMMA_HOME_URL=http://localhost:11436/v1  ; GEMMA_AWAY_URL=http://localhost:11437/v1   # 两个独立 daemon(实跑验证, §2.0)
GEMMA_MODEL=gemma4:e2b-it-qat              # QAT 版(加载 ~2.5G, 两个+nemotron 装得下; 默认 7.2G 版装不下)
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
