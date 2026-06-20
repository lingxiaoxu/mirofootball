# MiroFootball — Agentic 足球比赛模拟（融合方案 · 本机 DGX Spark 驱动）

> **一句话**：把 [`footballSimulationEngine-test`](https://github.com/OWNER/footballSimulationEngine-test)（Node.js 回合制物理引擎）和 [`MiroFish-Offline-Test`](https://github.com/OWNER/MiroFish-Offline-Test)（camel-ai 多 agent + Neo4j 知识图谱 + 本地 LLM + ReportAgent）**下载到一台 DGX Spark 上、改写融合成一个新项目 `mirofootball`**，用本机模型驱动：**每支球队一个 Gemma 4 E2B（共 2 个，各挂 LoRA，各自把 11 人打成一个 batch）+ 一个 Qwen3.6-35B-A3B 做持球决策（看两边信息）**，引擎负责物理，知识图谱承载每场/每队/每球员/每状态。
>
> **隔离铁律**：`mirofootball` 与 预测盘**完全隔离**——独立项目、独立服务、独立 DB，**绝不读写预测盘生产链路**。定位为战术叙事 / 可解释性 / 离线研究层，**不替代 Dixon-Coles 定价**。
>
> **复用铁律（贯穿全部 7 份文档）**：**足球引擎 `engine.js` + `lib/*.js` 原封不动 vendored（一行不改），完整运行方式（initiate→iterate→secondHalf + 各 lib 函数）原样保留**。引擎**已原生解算**的一律调用、不重写不简化：射门(`shotMade`)、扑救(`checkGoalScored`，GK 坐标/身高/弹跳/saving)、点球(`penaltyTaken`)、抢断(`calcTackleScore`)、越位/伤病/定位球(`setFreekicks`)、最近球员(`closestPlayerToBall`)。**LLM 只产决策（写 `player.action`/`intentPOS` 两个引擎已有字段），结果一律走引擎原函数**。加时/点球大战/换人/晋级只在编排层加**循环结构**，单次解算仍调引擎。
>
> 文档版本：v4（实跑校准后）· 决策依据：实读两 repo 代码 + DGX Spark 官方规格 + 模型官方规格 + **本机实跑验证（2026-06）**（见文末 Sources）。

---

## 0.0 ⚠️ 当前实跑 vs 目标态（先读）

本文 §0/§7 的"锁定架构"是**目标态**；**当前已实跑并验证的栈不同**（brain 可插拔）。架构 / 语义 / 注入接缝设计**全部通用**——只需把"Qwen"读作"brain（当前 nemotron）"、把 vLLM 端口读作 Ollama 端口（对照见 03 §8）。

| 维度 | 当前实跑（已验证 · 03 §2.0 / 05 §3.7） | 目标态（§0/§7） |
|---|---|---|
| brain（持球/解说） | **nemotron-3-super:120b**（Ollama, 共享 :11434, 只读不碰；远程经 :11435 token 代理） | Qwen3.6-35B-A3B（vLLM :8001） |
| 两队 gemma | **gemma4:e2b-it-qat**（Ollama, 独立 daemon :11436/:11437, 两份物理副本, 加载 ~2.5G/个） | Gemma4 E2B + 队 LoRA（vLLM :8002/:8003） |
| serving | **Ollama**：gemma 用本地 0.30.10 自有实例；nemotron 用系统 0.22.1 | vLLM×3 + NVFP4 |
| 机数 | 单机（3 模型共存, 实测 used 112G/121G, avail 9G） | 双机数据并行 ×2 |
| 开机自启 | systemd user service + linger（已配验证） | — |

> 早先按 spec 估的"Gemma ~2-3G/个"**实测有误**：默认/q4 版加载 ~7.8G、QAT 版 ~2.5G；故两队**必须用 QAT** 才能与 nemotron(94G) 共存（详见 05 §3.7）。

---

## 0. 目标架构（一页）—— brain 可插拔；当前实跑见 §0.0 / 03 §2.0

```
                         ── 一场比赛 = 一台 DGX Spark 完整跑完（绝不跨慢桥接） ──

   ┌──────────────────────────── mirofootball （新融合项目，一台 box）────────────────────────────┐
   │                                                                                              │
   │  Python 编排器 (brain/, 源自 MiroFish: camel-ai + Neo4j KG + ReportAgent, 丢掉 OASIS)         │
   │     每个 tick:                                                                                │
   │       snapshot = 读引擎 matchDetails (冻结)                                                   │
   │       ├─▶ Gemma-主队  (:8002, 主队LoRA)  batch 11 人 ─┐  两队并发, 都读同一 snapshot          │
   │       ├─▶ Gemma-客队  (:8003, 客队LoRA)  batch 11 人 ─┤  → 真·同时移动 (snapshot 同步)         │
   │       └─▶ Qwen3.6-35B (:8001)            持球者 1 人 ─┘  → 看两边信息做战术决策               │
   │       actions = 两队意图 + 持球动作                                                            │
   │       matchDetails = engine.playIteration(注入 actions)   ← 引擎解算物理, 全员一起落地         │
   │       KG.update(matchDetails)                              ← 记忆/轨迹写回 Neo4j               │
   │                                                                                              │
   │  Node 引擎 (engine/, 源自 footballSimulationEngine, 物理一行不改, 包成 localhost:7000 服务)   │
   │  本地 Neo4j (KG) · 本地 vLLM × 3 (NVFP4/Q4) · 全程 localhost, 零跨机                            │
   └──────────────────────────────────────────────────────────────────────────────────────────────┘

   两台 box: 各跑「不同的整场比赛」(数据并行) → 吞吐 ×2; 桥接只传任务派发/结果 (KB 级)
```

**锁定的模型分工**：

| 模型 | 数量/场 | 角色 | 调用方式 |
|---|---|---|---|
| **Gemma 4 E2B**（+ 主队 LoRA）| 1（主队专用）| 主队 11 人跑位/姿态/无球决策 | 一次 **batch 11** |
| **Gemma 4 E2B**（+ 客队 LoRA）| 1（客队专用）| 客队 11 人跑位/姿态/无球决策 | 一次 **batch 11** |
| **Qwen3.6-35B-A3B** | 1 | **持球球员战术决策**（传给谁/射/突/传中）+ 定位球 + 解说 | 事件触发，≤1 次/拍 |

> **两队各一个独立 Gemma 实例**（不共享底座，各批各的 11 人，真并行）。⚠️ 体积:此处"各 ~3GB"为目标态旧估,**实测**默认/q4 加载 ~7.8G、**QAT ~2.5G**;当前实跑用 **QAT + 两个独立 Ollama daemon**(:11436/:11437)保证真两个实例（同名模型在同 daemon 会被去重）,见 §0.0 / 03 §2.0。

---

## 1. 硬件现实与两条硬约束

### 1.1 单台 DGX Spark 规格

| 规格 | 数值 | 含义 |
|---|---|---|
| 芯片 | GB10 Grace Blackwell | 第5代 Tensor Core，**原生 NVFP4** |
| 统一内存 | **128 GB** LPDDR5x（CPU+GPU 共享）| 放得下 3 个模型 + KV + Neo4j + 引擎 |
| **内存带宽** | **~273 GB/s** | ⚠️ **瓶颈**：decode tok/s ≈ 带宽 ÷ 每 token 激活字节 |
| FP4 算力 | ~1 PFLOP / 31 TFLOPS FP32 | prefill/批处理充裕 → **鼓励批量并发** |
| CPU | 20 核 Grace | Node 引擎 + Python 编排 + Neo4j 全 CPU，不抢 GPU |
| 互联 | ConnectX 双 QSFP 200Gb 标称 | **你的实际桥接慢 → 架构必须最小化跨机流量** |
| 架构 | `sm_121` | 推理引擎须支持 sm_121 + NVFP4 |

### 1.2 两条必须服从的约束

1. **带宽受限（机内）** → 选**小激活模型**（MoE/MatFormer）+ **批处理**。两模型正合（3B / 2.3B 激活）。
2. **桥接慢（跨机）** → **一场比赛整场在一台机器本地跑完**；跨机只传任务/结果；**绝不**张量并行跨机、**绝不**两队拆两机。

---

## 2. 模型规格（已核实）

### 2.1 Qwen3.6-35B-A3B — 战术大脑

| 属性 | 值 |
|---|---|
| 发布/许可 | 2026-04-16 · Apache 2.0 |
| 架构 | Sparse MoE：35B 总 / **3B 激活**；256 专家(8 路由+1 共享)；Gated DeltaNet(线性注意力)+Gated Attention(GQA)+MoE 混合 → **KV cache 远小于普通 Transformer**（3/4 层是线性注意力，state 不随长度增长）|
| 多模态 | 含 Vision Encoder（未来可把球场态势渲染成图喂入）|
| 上下文 | 262,144 原生（足球用不到，设 ~32K 省 KV）|
| 显存 | **~21 GB @ 4-bit/NVFP4** |
| 角色 | 持球战术决策 + 定位球 + ReportAgent 解说 |

### 2.2 Gemma 4 E2B — 跑位反射

| 属性 | 值 |
|---|---|
| 厂商/架构 | Google · MatFormer + Per-Layer Embeddings |
| 参数 | **2.3B 有效 / 5.1B 总** |
| 上下文 | 8,192（跑位上下文极短，足够）|
| 显存 | ⚠️ 实测:默认/q4 版**加载 ~7.8 GB**；**QAT 版 ~2.5 GB**（当前实跑用 QAT, 03 §2.0）。早先"~2-3GB"系按 spec 估,以实测为准 |
| 角色 | 每队 11 人跑位/姿态意图，**批量**、高频、对延迟敏感 |

---

## 3. 新项目 `mirofootball` — 两个 repo 怎么融合

### 3.1 开发方式

两个 repo **clone 到同一台 DGX Spark**，融合成一个新仓库 `mirofootball`。引擎物理代码**原样保留**（vendored），MiroFish 剥离社交层、保留 agentic+KG+报告，新增 tick 循环把两者黏起来。

### 3.2 目标目录结构

```
mirofootball/
├── engine/                       # ← vendored: footballSimulationEngine-test (Node.js, 物理不改)
│   ├── lib/                       #   actions.js / playerMovement.js / ballMovement.js ... (保留)
│   ├── engine.js                  #   initiateGame / playIteration / startSecondHalf
│   ├── server.js                  # ★新增: 薄 Express, 暴露 /initiate /iterate /secondhalf @:7000
│   └── init_config/               #   pitch.json / team*.json (球员从你的 DB 生成)
│
├── brain/                        # ← 源自 MiroFish-Offline-Test (Python), 丢掉 OASIS
│   ├── orchestrator.py           # ★新增: 核心 tick 循环 (snapshot → 2 Gemma batch + Qwen → engine)
│   ├── agents/
│   │   ├── team_agent.py         # ★新增: 一队 Gemma 批 11 人决策 (跑位/姿态)
│   │   └── ball_agent.py         # ★新增: Qwen 持球战术决策 (看两边)
│   ├── kg/                        #   复用 MiroFish: neo4j_storage / graph_builder / graph_memory_updater
│   ├── report_agent.py           #   复用 MiroFish: 赛后解说/复盘
│   ├── llm_client.py             #   复用 + 加 async 并发 (批处理在 vLLM 服务层)
│   └── config.py
│
├── serving/                      # vLLM 启动 + LoRA 适配器
│   ├── vllm_qwen.sh              #   :8001  Qwen3.6-35B-A3B (NVFP4)
│   ├── vllm_gemma_home.sh        #   :8002  Gemma E2B + 主队 LoRA
│   ├── vllm_gemma_away.sh        #   :8003  Gemma E2B + 客队 LoRA
│   └── lora/                      #   home_tactics / away_tactics / (可选)role_* 适配器
│
├── coordinator/                  # 双机用: 任务派发 + 结果回收 (跑在 Box A)
│   └── coordinator.py
│
├── data/                         # 比赛配置 / 球队名单(来自你的 DB) / 输出(iterationLog/解说)
├── docker-compose.yml            # neo4j + 3×vllm + engine + orchestrator
└── README.md
```

### 3.3 从 MiroFish 保留什么 / 丢掉什么

| 保留 ✅ | 丢掉 ❌ |
|---|---|
| **`LLMClient` 直连**（OpenAI 兼容,核心 agent 全走它）| **`camel-ai` + `camel-oasis`**（实读确认:核心 `app/` **不依赖** camel,仅 OASIS 社交脚本用 → 一并丢；丢后解除 Python<3.12）|
| Neo4j 栈（`storage/neo4j_*`, `graph_builder`, `graph_memory_updater`）| `run_twitter_simulation.py` / `run_reddit_simulation.py` |
| `report_agent.py` → 比赛解说 | `oasis_profile_generator.py`（社交人格）|
| `llm_client.py`（OpenAI 格式 → vLLM）| OASIS env（recsys/feed/follower 图）|
| `agent_graph` 概念（agent 注册表+记忆）+ batch `env.step` 模式（见 §4）| 社交动作集（CREATE_POST/LIKE）|

---

## 4. 核心模拟循环（系统的心脏）

### 4.1 MiroFish 的编排模式（代码实证，直接复用）

OASIS 原生是**"凑齐一批动作、一次性同步推进"**（`run_twitter_simulation.py::handle_batch_interview`）：
```python
actions = {}
for x in batch:
    agent = self.agent_graph.get_agent(agent_id)
    actions[agent] = ManualAction(action_type=..., action_args={...})
await self.env.step(actions)        # ← 字典里所有 agent「一起」推进一步（非轮流）
```
→ **把 `env` 从社交平台换成足球引擎，模式照搬**：每 tick 凑齐 `{player: action}` 一起喂给引擎。

### 4.2 引擎的注入接缝（代码实证）

`engine.js::playIteration` 里**主队/客队各调一次 `decideMovement`**，而 `decideMovement` 本就**按整队遍历 11 人**：
⭐ **关键（复用铁律）：引擎本就有"接受外部决策"的口子 → 我们 0 改引擎，只写两个字段**（详见 06 §1，代码实证）：
```js
// decideMovement 内对每人: action = checkProvidedAction(matchDetails, thisPlayer, action)
//   → 若设了 thisPlayer.action 就「用注入的」(还自带合法性校验); 'none' 则用引擎启发式
// getRunMovement: 球远时 formationCheck(player.intentPOS, currentPOS) → 朝 intentPOS 跑
```
| LLM 输出 | 写引擎**已有字段** | 引擎原函数消费处 |
|---|---|---|
| Qwen 持球动作 | 持球者 `player.action` | `checkProvidedAction` |
| Gemma 跑位目标 | 无球者 `player.intentPOS` | `getRunMovement`/`getSprintMovement` |
| Gemma 姿态 | 无球者 `player.action`(run/sprint) | `checkProvidedAction` |
| 不决策 | `player.action='none'` | 回退引擎启发式 |

- 离散动作集（固定）：`shoot, throughBall, pass, cross, tackle, intercept, slide, run, sprint, cleared, boot, penalty`
- **不改 `decideMovement`/`getMovement`/物理**——编排器在调 `playIteration` 前**只设 `player.action`/`intentPOS`**，引擎原样跑、原样解算（含单球不变式、动作合法性）。**`findPossActions`/`selectAction` 保留作"未注入球员"的兜底**。

### 4.3 核心 tick 循环（orchestrator.py 伪代码）

```python
md = await engine.initiate(team1, team2, pitch)         # localhost:7000
kg.bootstrap_match(md)                                  # Neo4j: 建 match/team/player/state 节点
ITER, GEMMA_EVERY = 2000, 12                            # 每半场迭代数、意图刷新间隔(可调旋钮)

for it in range(ITER * 2):
    if it == ITER:
        md = await engine.second_half(md)
    snapshot = freeze(md)                               # 冻结这一帧, 两队都读它 → 同时性

    # ── 跑位意图: 两队 Gemma 并发, 各 batch 11 (周期性/事件触发, 非每拍) ──
    if it % GEMMA_EVERY == 0 or ball_state_changed(md):
        home_i, away_i = await asyncio.gather(          # ★两队真并行
            gemma_home.decide_batch(snapshot.home_11, snapshot, kg),   # :8002 主队LoRA, 11 并发请求
            gemma_away.decide_batch(snapshot.away_11, snapshot, kg),   # :8003 客队LoRA, 11 并发请求
        )
        inject_intents(md, home_i, away_i)              # 写 player.intentPOS / team.intent

    # ── 持球战术: Qwen, 看两边, 仅持球者、仅决策点 ──
    if on_ball_decision_needed(md):
        ball_action = await qwen.decide(on_ball_player(md), snapshot, kg)  # :8001
        inject_ball_action(md, ball_action)

    # ── 物理解算: 引擎, 每拍, 全员一起落地 ──
    md = await engine.iterate(md)                       # localhost:7000
    kg.update_state(md, it)                             # 写状态轨迹

report = await report_agent(md, kg)                     # 赛后解说 (Qwen)
return md, report
```

**同时性保证**：两队 Gemma 批都读**同一帧 `snapshot`**、Qwen 也读它；`engine.iterate` 内 `movePlayers` 全员一起落地。谁先算谁后算无所谓 → **真·同时移动**（计算上还并行：`asyncio.gather` + vLLM 批处理）。

### 4.4 Gemma 单球员 I/O（约束输出，让 2B 稳又快）

把半场切成**粗网格 zone**，Gemma 只做"选择题"，JSON-schema 约束解码：
```json
// 输入(每无球球员, 极简):
{"me":{"id":7,"pos_zone":"D4","role":"RW","pace":82,"stamina":71},
 "ball_zone":"C6","possession":"us","team_intent":"attack",
 "near_mates":["F5:CF","C5:CM"],"near_opps":["D5:LB"],
 "options":["hold","press","drop","support","run_behind","widen","tuck_in"]}
// 输出(~15 token, 限定枚举+合法zone):
{"id":7,"target_zone":"F5","posture":"run_behind"}
```
引擎按**固定步长（run=1 / sprint=2 单位/轴）+ fitness 递减**移动（**不按 pace 缩放**，代码核实见 01 §0.1）→ Gemma 再激进也只改 `intentPOS` 方向，步速恒定、累了变慢，**跑不出引擎物理范围**。门将/后卫的 `options` 不含 `run_behind`（角色限定）。

---

## 5. 三层区分度（互不冲突，共同保证"每球员各打各的"）

| 层 | 谁实现 | 区分什么 | 成本 |
|---|---|---|---|
| **球队战术** | 两个独立 Gemma + 各自 LoRA | 主队 vs 客队整体打法 | 6GB（两实例）|
| **位置角色** | Gemma 的 prompt（每位置允许的姿态枚举）| 门将 vs 边锋的跑位方式 | 0（上下文）|
| **球员个体** | Neo4j 注入（技术值/体能/记忆）+ 引擎按属性夹紧 | 同位置不同球员 | 0（KG 检索）|

> **不是给每人一个模型**（22 份权重既浪费又无区分——同权重=同行为）。区分度来自 **LoRA(队级) + prompt(位置) + KG(个体)**，三者叠加。
> **推进**：先只用 prompt+KG（零训练即有区分）；有数据后训练**球队 LoRA**（主队脑/客队脑），可选再加**角色 LoRA**。

---

## 6. 知识图谱 Schema（Neo4j，每台本地一份）

```
(:Match {id, date, competition, n_iterations, score, status})
(:Team  {id, name, formation, rating, intent})                 // intent: attack/defend/transition
(:Player{id, name, position, rating,
         passing,shooting,tackling,saving,agility,strength,
         perception,jumping,control, fitness,injured,height})
(:State {iter, ball_pos, possession, score, minute})           // 每关键拍一个状态节点

(:Match)-[:HOME|AWAY]->(:Team)
(:Team)-[:HAS_PLAYER]->(:Player)
(:Player)-[:PASSES_TO {weight}]->(:Player)                     // 战术传球倾向 (你的数据接地)
(:Player)-[:MARKS]->(:Player)                                  // 对位
(:Match)-[:AT_ITER]->(:State)
(:Player)-[:DID {action, iter, result}]->(:State)             // 决策轨迹 → 解说/可解释素材
```
- **静态部分**（Team/Player/PASSES_TO/MARKS）：启动时从你的 DB（FIFA 评分/强度模型/xG/阵容）生成一次，复制到两台各一份（一次性跨桥）。
- **动态部分**（Match/State/DID）：每场**本地建、本地写**，绝不跨机。
- 决策时 Gemma/Qwen 的上下文 = 本地 Neo4j 检索（该球员属性 + 队友空位 + 对位 + 当前态势）。
- **`PASSES_TO` 权重用你现成数据接地**——把你的量化优势注入 agentic 模拟。
- **轨迹分工(见 01 §0.4)**:**每拍全量连续轨迹**(球 + 22 人坐标/动作)写 `data/<match_id>/trajectory.jsonl`(回放/track/review 用);KG `State`/`DID` 只存**关键拍语义快照 + 决策**,用 `iter` 指针引用 jsonl 行。轨迹连续、可回溯是硬需求。

---

## 7. 模型服务（vLLM，本机三实例）—— ⚠️ 目标态；当前实跑是 Ollama，见 03 §2.0

```bash
# Qwen3.6-35B-A3B (战术大脑) — :8001
vllm serve Qwen/Qwen3.6-35B-A3B --quantization nvfp4 --tensor-parallel-size 1 \
  --max-model-len 32768 --gpu-memory-utilization 0.40 --enable-prefix-caching --port 8001

# Gemma E2B + 主队 LoRA (主队跑位) — :8002
vllm serve google/gemma-4-E2B-it --quantization awq --tensor-parallel-size 1 \
  --max-model-len 8192 --gpu-memory-utilization 0.06 --max-num-seqs 32 \
  --enable-lora --lora-modules home=serving/lora/home_tactics --port 8002

# Gemma E2B + 客队 LoRA (客队跑位) — :8003
vllm serve google/gemma-4-E2B-it --quantization awq --tensor-parallel-size 1 \
  --max-model-len 8192 --gpu-memory-utilization 0.06 --max-num-seqs 32 \
  --enable-lora --lora-modules away=serving/lora/away_tactics --port 8003
```
- **OpenAI 兼容** → `brain/llm_client.py` 和 camel-ai 零改动，只改 base_url。
- **`--enable-prefix-caching`**：规则/静态球员上下文是共享前缀 → 不重算（大提速）。
- LoRA 没训练前先不挂 `--lora-modules`，跑纯底座 + prompt 区分；有 LoRA 再挂。
- Gemma 若 vLLM 适配不顺，可退化 `llama.cpp`/Ollama（它太小，CPU 都能跑）。

---

## 8. 内存预算（一场，一台 box）

```
Qwen3.6-35B-A3B 权重 (NVFP4)   ~21 GB
Qwen KV (32K, 混合架构 KV 小)  ~10 GB
Gemma-主队 (Q4)                ~3 GB
Gemma-客队 (Q4)                ~3 GB
Gemma KV ×2 (8K, 小)           ~4 GB
Neo4j + Node 引擎 + 编排 + OS  ~18 GB
──────────────────────────────────────
合计                           ~59 GB / 128 GB   ← 富余, 一台还能并跑第 2 场
```
> ⚠️ 这是**目标态(Qwen+2×Gemma4)**预算,且 Gemma 体积按旧估。**当前实跑(nemotron 94G + 2×QAT ~2.5G + 基础设施)实测 used ~112G/121G、avail 9G**,详见 05 §3.7。

---

## 9. 速度：杠杆 + Box 分配

### 9.1 提速杠杆（按影响排序）

| 杠杆 | 效果 |
|---|---|
| **🔑 LLM 频率与物理拍解耦**：意图每 ~10-15 拍刷一次，中间引擎免费插值 | **砍 LLM 调用 5-15×（最大头）** |
| brain 只在真持球决策点触发（多数拍球在飞/无球决策）| brain 量大降 |
| **🔑 gemma 关 reasoning（已定）/ brain 开**：gemma 高频量层 → `think=False`，0.7s/次（开则 3–7.5s → 单场变几小时）；brain 低频战略层 → `think=True`，思考提质 | **gemma 关是单场提速关键之一**；brain 开不拖速（低频，1–2/拍触发）|
| 短约束输出（Gemma ~15t / Qwen ~30-50t）+ JSON-schema 解码 | decode 正比输出长度 → 越短越快 |
| `--enable-prefix-caching`（共享规则/静态前缀）| 不重算长 prompt |
| 两队 Gemma `asyncio.gather` 并发 + vLLM 连续批处理 | 真并行, 批量摊薄 |
| trivial 决策走引擎启发式（门将站位/回追不调 LLM）| 省 |
| 粗迭代（半场迭代数可调）| 研究跑可更粗更快 |
| NVFP4/Q4 + 小激活模型 | 已选 |

→ 单场 LLM 轮次降到 ~100-200，每轮(两 Gemma 批并行 ~0.3-0.5s + 偶尔 Qwen ~1-2s) → **单场约 2-7 分钟**（展示级），更粗更快。

### 9.2 Box 分配（锁定）

| 方案 | 选? | 理由 |
|---|---|---|
| **一场 = 一台 box（整场本地）** | ✅ **必须** | 两队每拍在同一快照互动；拆两机要跨**慢桥接**同步 → 拖死 |
| **两台 = 各跑不同的整场** | ✅ **推荐** | 数据并行，2 场并行 → **吞吐 ×2**，多场线性扩展 |
| 把一场两队拆两机求"单场更快" | ❌ | 慢桥接每拍同步会拖死；单场提速靠 §9.1 机内杠杆 |

> **单场更快** → §9.1 机内杠杆。**总量更快**（很多场）→ 两台各跑各场 ×2。

---

## 10. 吞吐量预算 & 可行性

| 目标 | 估算（两台并行）| 结论 |
|---|---|---|
| **1 场逼真比赛 + 解说** | 单场 ~2-7 分钟 | ✅ **很 doable**（叙事/战术/可解释）|
| **百场战术研究集** | ~一两小时 | ✅ 可行 |
| **1 万场离线建分布** | 调低 LLM 密度后 ~一晚到两天 | 🟡 离线研究可行，非实时 |
| **实时盘中/赛前赔率** | 每场需数千次取样 | ❌ 用 DC 模型，别用这个 |

---

## 11. 落地步骤（doable checklist）

**Phase 0 — 单台装机（半天）**
1. DGX OS + CUDA，确认 `sm_121` + NVFP4 工具链。

**Phase 1 — 推理服务（半天）★当前 Ollama 三模型**
2. brain = 已驻共享 `nemotron-3-super:120b`@:11434（**只读发请求、绝不碰**，临时替代 Qwen）；起 mirofootball **两个独立 Ollama daemon :11436/:11437**（本地 0.30.10），各下一份 **`gemma4:e2b-it-qat`**（两份物理副本；QAT 才装得下,03 §2.0）。（注:`:11435` 已被 nemotron 的 token 认证代理占用,避开。）
3. `curl /api/ps` 双端口验收**三模型同时在跑**（1 nemotron + 2 gemma），brain 只读 ping；实测 tok/s。（目标态可换 vLLM 三实例，§7。）

**Phase 2 — 建 mirofootball 骨架 + 引擎服务化（1 天）**
4. 新建 `mirofootball/`，把两 repo vendored 进 `engine/`、`brain/`（剥 OASIS）。
5. 写 `engine/server.js`（Express 包 initiate/iterate/secondhalf @:7000，**console.log 进程层静音 + 可选 seed shim**），用 `init_config` 跑通**纯引擎**一场（不接 LLM）。

**Phase 3 — MVP（1-2 天）★验证整个想法（已是三模型全接）**
6. 写最小 `orchestrator.py`：**brain 决持球点**（替 `selectAction`）+ **两个独立 gemma 各批一队无球跑位**（`asyncio.gather` 真并行），其余走引擎 → 跑通一场 → ReportAgent 解说。KG/控球反馈/第一防守者可先不接（05 §3.7）。

**Phase 4 — 两队 Gemma 批 + 快照同步（3-5 天）**
7. 写 `team_agent.py`：每队 Gemma 批 11（先纯底座+prompt 区分），`asyncio.gather` 两队并发。
8. 接快照同步 tick（§4.3）；把 Player/Team/State 写本地 Neo4j；决策时本地检索。
9. 用你的 DB 生成静态参考图（评分→skill，历史→PASSES_TO 权重）。

**Phase 5 — 双机数据并行（1-2 天）**
10. 第二台同样部署；Box A 起 `coordinator.py` 交替派发整场比赛、回收结果（桥接只传 KB 级）。
11. 跑多场量聚合吞吐；调 LLM 决策密度旋钮。

**Phase 6 — LoRA 专精（有数据后）**
12. 收集每队/每位置跑位样本 → 训练球队 LoRA（主队脑/客队脑），挂上 Gemma；可选角色 LoRA。

**Phase 7 — 验证与隔离**
13. 模拟赛事统计（射门/控球/进球分布）对照真实分布校验，调权重表/prompt。
14. 全套独立部署，**绝不触碰预测盘生产**。

---

## 12. 关键配置模板

### 12.1 `.env`（每台）
```bash
QWEN_BASE_URL=http://localhost:8001/v1   ; QWEN_MODEL=Qwen/Qwen3.6-35B-A3B
GEMMA_HOME_URL=http://localhost:8002/v1  ; GEMMA_HOME_LORA=home
GEMMA_AWAY_URL=http://localhost:8003/v1  ; GEMMA_AWAY_LORA=away
ENGINE_URL=http://localhost:7000
NEO4J_URI=bolt://localhost:7687          ; NEO4J_AUTH=neo4j/<SET_YOUR_PASSWORD>
ITER_PER_HALF=2000                       ; GEMMA_EVERY=12      # 速度/保真旋钮
PEER_BOX=BOX_B_IP                         ; ROLE=worker         # Box A 额外 ROLE=coordinator
```

### 12.2 进程（docker-compose 或 systemd，每台）
```
neo4j(:7687) · vllm-qwen(:8001) · vllm-gemma-home(:8002) · vllm-gemma-away(:8003)
engine-node(:7000) · orchestrator(Python; Box A 额外 coordinator)
```

---

## 13. 风险与缓解

| 风险 | 缓解 |
|---|---|
| vLLM 对 sm_121/NVFP4 适配不全 | 先 FP8/AWQ-4bit；跟进 vLLM/TRT-LLM Blackwell；Gemma 可退 llama.cpp |
| 慢桥接 | 架构已规避——模拟期零跨机；只在派发/回收/一次性 KG 复制时用桥 |
| LLM 决策让比赛统计失真 | 真实分布校验；`PASSES_TO`/技术权重用你的数据接地；JSON-schema 约束动作合法性 |
| 单场太慢 | §9.1 杠杆（意图解耦为首）；引擎多担常规决策；批量并发 |
| Python(brain) ↔ Node(engine) 桥 | HTTP @localhost:7000，标准；MiroFish 本有 subprocess/IPC 经验 |
| 污染预测盘 | 独立项目/服务/DB；只读不写预测盘；物理隔离 |

---

## 14. 后续可深入（写完即可挑一个动手）

1. **整合设计细化**：prompt 模板全集（Gemma 跑位 / Qwen 持球 / ReportAgent）、动作合法性校验、粗网格 zone 划分、`PASSES_TO` 权重来源。
2. **部署脚本**：`docker-compose.yml` + 三个 `vllm_*.sh` + `engine/server.js` + `orchestrator.py` 骨架。
3. **LoRA 训练流程**：球队/角色样本采集 → 微调 → 挂载。
4. **可视化**：复用引擎示例 GUI 或 MiroFish 前端，看一场带推理的模拟。

---

## Sources

- [NVIDIA DGX Spark 官方产品页](https://www.nvidia.com/en-us/products/workstations/dgx-spark/)
- [DGX Spark Hardware Overview（NVIDIA 文档）](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)
- [LMSYS：DGX Spark In-Depth Review](https://www.lmsys.org/blog/2025-10-13-nvidia-dgx-spark/)
- [Qwen3.6-35B-A3B 官方博客](https://qwen.ai/blog?id=qwen3.6-35b-a3b) · [HF](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) · [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.6-35B-A3B)
- [Gemma 4 E2B · HF](https://huggingface.co/google/gemma-4-E2B) · [Gemma 4 overview](https://ai.google.dev/gemma/docs/core)
- [footballSimulationEngine-test](https://github.com/OWNER/footballSimulationEngine-test) · [MiroFish-Offline-Test](https://github.com/OWNER/MiroFish-Offline-Test)
```
