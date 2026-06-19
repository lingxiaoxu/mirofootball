# MiroFootball — 计划索引 · 覆盖矩阵 · 缺口与补全 · 总路线图

> 本文是 `agentic-simulation/` 计划的**总入口 + 全面性审计**：①文档索引；②把全程讨论的每个需求 → 落到哪份文档；③显式列出**尚未设计的缺口**（尤其淘汰赛特性）并给补全设计；④合并的分阶段路线图。**确保计划全面、可追溯、不遗漏。**

---

## 1. 文档索引（6 份）

| # | 文件 | 覆盖 |
|---|---|---|
| 00 | `00_MIROFOOTBALL_PLAN.md` | 融合架构 · 模型分工(2 Gemma+1 Qwen) · DGX 硬件/内存/速度 · box 分配 · tick 循环 · KG schema · 部署 checklist |
| 01 | `01_SIMULATION_SEMANTICS_AND_ORCHESTRATION.md` | MiroFish 顶层编排(本地模型) · **单球/共享世界意识** · **控球率 65/35** · **攻防回合** · **门将 LoRA+扑救率** |
| 02 | `02_PROMPT_TEMPLATES.md` | MatchDirector/Gemma 跑位/GK/Qwen 持球/ReportAgent 全 prompt · 约束解码 · prompt↔引擎字段映射 |
| 03 | `03_DEPLOYMENT_SKELETON.md` | **DGX 可用/不可用清单** · server.js/orchestrator.py/llm_client/agents/vllm/docker-compose 骨架 · 双机 coordinator |
| 04 | `04_LORA_TRAINING_AND_ROSTER.md` | **世界杯国家队** LoRA 训练 · FC26→引擎 skill 映射 · 风格锚定自博弈蒸馏 · 16 队任选 2 热切换 · **切换不紊乱** · 淘汰赛后工作流 |
| 05 | 本文 | 索引 · 覆盖矩阵 · **缺口补全(加时/点球大战/换人/红牌)** · **§3.7 实现细节核实与 6 点解决 + 本地模型现状(Ollama/nemotron-120b/gemma4:e2b/内存)** · 总路线图 |
| 06 | `06_ORCHESTRATION_CONTROL_AND_BOX_MIRROR.md` | **决策注入接缝(`player.action`/`intentPOS`，零引擎改动·代码确证)** · **Qwen↔2Gemma↔22人协同(mirofootball 当指挥)** · **Box 镜像 + 一致性验证** |

---

## 2. 覆盖矩阵（讨论过的每点 → 落点）

| 需求/问题（全程讨论）| 落到哪 |
|---|---|
| 融合两 repo → mirofootball（一台 DGX 开发）| 00 §3 |
| 两队各一个 Gemma（共 2）+ 一个 Qwen 决策 | 00 §0, 01 §2.4 |
| 顶层用 MiroFish 组织、且能用本地模型 | 01 §1, 04 §0（代码核实 `config.py`）|
| 引擎决策接缝（decideMovement 按队）| 00 §4.2, 01 |
| MiroFish 编排改造（agent_graph + batch step，丢 OASIS）| 00 §4.1, 01 §1.2 |
| 快照同步 tick = 同时移动（非轮流）| 00 §4.3, 01 §2.4 |
| **单一足球、只一人持球、别人不能有** | 01 §2.1（`ball` 单值 + `removeBallFromAllPlayers`）|
| **Qwen 与两 Gemma 同步知道球/位置/控球** | 01 §2.3（共享 `world` 切片）|
| **控球率 50/50 与 65/35 怎么模拟** | 01 §3.2（PossessionDirector 反馈控制）|
| **攻防回合数（home 11/away 9）怎么决定** | 01 §3.3（涌现+测量+引导）|
| **门将 LoRA 在各自 Gemma 下怎么设计** | 01 §4.3 |
| **扑救成功率怎么设计** | 01 §4.4（引擎概率解算，可校准 ~65-75%）|
| 三层区分度（队 LoRA/角色/球员属性）| 00 §5, 04 §2 |
| KG schema（match/team/player/state）| 00 §6, 01 §6 |
| Prompt 全集（5 类）+ 约束解码 | 02 全 |
| 速度够不够 + 怎么更快 | 00 §9.1（LLM 与物理拍解耦为首）|
| 一台跑一场 / 两台各跑各场 | 00 §9.2 |
| DGX 上几 B 模型 / 几个模型 / 怎么配 | 00 §2/§7/§8, 03 §0 |
| DGX 可用/不可用（ARM64/sm_121/NVFP4）| 03 §0 |
| 部署脚本骨架（server/orchestrator/vllm/compose）| 03 §2–§7 |
| **LoRA 训练流程** | 04 §5/§7 |
| **16 队任选 2、球员会变、切换能力** | 04 §6（vLLM 热切换）, §3（名单=数据）|
| **切换球队/球员会不会紊乱** | 04 §6.2（无状态+KV隔离+边界切换）|
| **世界杯国家队 + 赛后稀疏数据** | 04 §1/§3/§4/§9 |
| FC26 评分 → 引擎 skill 映射 | 04 §3.2 |
| 隔离不碰预测盘 | 00 §0, 03, 04 §3.1 |
| **决策怎么注入引擎（零物理改动·代码确证）** | 06 §1（`player.action`+`intentPOS`）|
| **Qwen 与两 Gemma 的 22 人怎么协同** | 06 §2（mirofootball 当指挥 + 职责矩阵）|
| **Qwen 角色（球队无关·随球权切换·管争夺1-2人）** | 01 §2.3（持球者+第一防守者）, 02 §4a/§4b（攻/防 prompt）|
| **一台开发完成 → 镜像第二台** | 06 §3（provisioning + 一致性验证）|

→ **全部讨论点均有落点。** 以下是**尚未设计的缺口**（之前未覆盖，本文补）。

---

## 3. 缺口与补全（淘汰赛特性等）

> 16 强 = **淘汰赛**，每场**必须分胜负**。实读引擎确认：**有**两半场/定位球/伤病/红牌计数/`'NP'`离场/**单个点球解算**(`penalty_taking` vs `saving`)；**无**加时/点球大战结构/换人/晋级判定。以下在 **MatchDirector(编排层)** 补，引擎物理不改。

### 3.1 ❌→✅ 加时赛（Extra Time）—— **复用引擎 `startSecondHalf` + 同一 tick 循环**
平局且为淘汰赛 → 再打 2×15′。**换边/重置位置直接调引擎原函数 `startSecondHalf`**（它已 `half++` + `switchSide` + 重置位置），中间用**同一个 `playIteration` tick 循环**，不另写：
```python
while level(md) and md["knockout"]:
    md = await engine.second_half(md)     # ← 引擎原 startSecondHalf: half++ + 换边 + 重置 (ET 上半)
    md = await play_iters(md, ET_ITER)    # 同一 tick 循环 (00 §4.3), 不改
    md = await engine.second_half(md)     # ← 再调 = ET 下半换边
    md = await play_iters(md, ET_ITER)
    break                                 # 满 ET 仍平 → §3.2 点球大战
```

### 3.2 ❌→✅ 点球大战（Penalty Shootout）—— **每球调引擎 `penaltyTaken`，不重写**
引擎已**原生解算单个点球**：`penaltyTaken(matchDetails, team, player)`（`skill.penalty_taking` + GK 经 `checkGoalScored`）。**只缺"大战的循环结构"**（引擎是比赛引擎、无赛会结构）→ 编排层只做循环，**每一脚都走引擎原函数**：
```python
async def shootout(md):                       # 仅编排层循环; 单球解算全交引擎
    order_A = top5_takers(teamA); order_B = top5_takers(teamB)   # 按 penalty_taking 排序
    sa = sb = 0
    def take(taker, defending_gk_team):
        setup_penalty_state(md, taker, defending_gk_team)        # 摆好引擎点球态(球在点上,taker持球,action='penalty')
        engine.playIteration(md)                                 # ← 引擎 penaltyTaken + checkGoalScored 原生解算
        return read_goal_from_log(md)                            # 读引擎结果(进/未进)
    for rnd in range(5):
        sa += take(order_A[rnd], "away"); sb += take(order_B[rnd], "home")
        if decided(sa, sb, rnd): break
    while sa == sb:                                              # 突然死亡
        sa += take(next_taker(A), "away"); sb += take(next_taker(B), "home")
    return "home" if sa > sb else "away"
```
- **不自写概率**：进球率由引擎 `penalty_taking` 给出；要校准到 ~75% 调引擎参数。罚球/扑点方向可让 Qwen/GK 角色给意图（写进 currentPOS/action），仍由引擎解算。

### 3.3 ❌→✅ 换人（Substitutions）
国家队每场换人。引擎吃 `matchDetails`、且 `'NP'` 表示不在场 → 编排层在决策点换：
```python
# 触发: HT / ~60' / 红牌 / 伤病 / 落后
sub = await director.decide_sub(md, bench)          # Qwen 决定 out/in
md = apply_sub(md, out_id, in_player)               # out → currentPOS='NP'; in 插入该位置
kg.swap_agent(out_id, in_player)                    # agent_graph 更新
```
> 引擎已 `if (player.currentPOS[0] != 'NP')` 跳过离场者 → 换人/罚下天然兼容。

### 3.4 ❌→✅ 红牌罚下（→ 10 人）
引擎有红牌计数 + `'NP'`。第二黄/直红 → 编排层把该球员 `currentPOS='NP'`、球队 10 人；in-play 模型(λ)与跑位自动适应（少一人）。

### 3.5 ❌→✅ 晋级判定（Winner）
引擎只记比分，不判晋级。MatchDirector 串起来：`FT 平 → ET → 仍平 → 点球大战 → winner`，写入 KG + 输出。

### 3.6 🟡 其它待细化（非阻塞）
| 项 | 状态 | 去处 |
|---|---|---|
| 编排工具函数 `build_world/offball/on_ball/inject_*/ball_state_changed` | 骨架已述，待实现 | Phase 4 |
| `data_map.py` / `style_extract.py`（FC26→引擎、WC统计→风格）| 骨架已给，待补全 | 收到数据后 |
| **整场模拟 vs 真实**校准（比分/统计分布合理性）| 原则已述（calibration gate 思路），待落指标 | Phase 7 |
| 可视化（看一场带推理的模拟）| 可选，复用引擎示例 GUI / MiroFish 前端 | 后续 |
| 定位球（角球/任意球）决策接 LLM | 引擎有 setFreekicks，决策可后接 Qwen | Phase 5 |

---

## 3.7 实现细节核实与 6 点解决（2026-06 实读本地 repo + 硬件实测）

> 用两个 Explore agent 读穿 `footballSimulationEngine-test-master` 与 `MiroFish-Offline-Test-main/backend`，并实测本机硬件。修正/坐实如下，**6 点全部有解、无一需改引擎源码**（复用铁律守住）。

**本地模型现状（替代计划原 vLLM+Qwen 设想）：**
- 机器 = **DGX Spark GB10**，aarch64，**128GB 统一内存**（`free` 看占用，非 `nvidia-smi`）。
- **serving = Ollama**；**brain 与 gemma 分两个 server**（见下隔离约束）。
- **brain = `nemotron-3-super:120b`**（nemotron_h_moe, 123.6B, Q4_K_M, ~94GB, 已驻共享 server `:11434`）**临时替代 Qwen3.6-35B-A3B**；两队 = **`gemma4:e2b`**（2026-04 发布，PLE 有效 2.3B，q4 下 <1.5GB/份，thinking 可配但**本项目保持开、不强制关**）。
- ⚠️ **隔离铁律延伸（共享 brain 绝不碰）**：nemotron-120b **被 web 上其它服务在用** → mirofootball **只把它当只读外部推理端点**：仅发 chat 请求，**绝不 reload/evict/重启其 server、绝不覆盖 `num_ctx`**（改 ctx 会触发整模型重载 = 打断共享方）。**两队 gemma 跑在 mirofootball 自己的独立 Ollama 实例 `:11435`**（设其自身 `OLLAMA_MAX_LOADED_MODELS≥2`）→ 加载/驱逐只发生在我们这台实例内，**共享 brain 永远不受影响**。两实例共享 128GB 内存池但互不重载。
- **内存账（实测）**：94(brain)+2×Gemma(~5G)+infra(~1G) ≈ **100GB / 127.5GB**（不开 KG）；开 KG+embed ≈ 103GB。**两个 Gemma 装得下，不用打断 brain**。真约束是 120b decode 速度，非内存。

**6 点解决落点：**

| # | 问题 | 解决 | 落点 |
|---|---|---|---|
| 1 | 引擎只认 8 种 position `{GK,LB,CB,RB,LM,CM,RM,ST}`（`validate.js` 不枚举校验，但 `setFreekicks` 分组/GK 特判 keys off 这些串） | **双字段**：`position`(8 值)给引擎、`role`(细分)给 LLM；`data_map.py` 一张 `ROLE_TO_POS` 映射 | 02 §2/§7、**04 §3.2** |
| 2 | 无 guided_json（MiroFish 只 `json_object`+手工修复） | fork `brain/llm_client.py` 加 **serving-aware schema 透传**：Ollama `format=<schema>`（≥0.5.0，本机 0.22.1 有）/ vLLM `guided_json`；旧修复保留兜底 | **02 §6、03 §4** |
| 3 | `Math.random()` 不可种子化 | **接受**（只比统计分布，KS 检验）；可选 `server.js` 顶部 `Math.random=seededPRNG` shim（外壳不碰引擎） | **06 §3.4** |
| 4 | 每拍 `console.log`（核实**两处**：`engine.js:89` + `lib/ballMovement.js:692`） | 进程层 `>/dev/null` 或 server.js `console.log=()=>{}`（外壳）；编排器读返回值不依赖 stdout | **03 §3** |
| 5 | embedding（`nomic-embed-text`）是第三个 Ollama 模型 | **多半不用**：仅 KG 文档摄入/语义检索/report 相关性用；比赛 KG 是结构化节点→Cypher 直查，不需 embedding。MVP 不开 KG；**需要时再加**（~0.5G，按需载，计入 `MAX_LOADED_MODELS`） | 03、本节 |
| 6 | 120b 速度（brain 瓶颈） | **接受**；编排层杠杆（LLM/物理拍解耦 + 短 JSON + trivial 走引擎 + prefix 缓存）已够，**不换模型、不碰共享 brain**；**热路径关 thinking 改为可选、不强制**（brain 共享不重配，保持 thinking 开）；大批量才考虑换小激活 brain | **00 §9.1** |

**MiroFish 复用边界（核实）**：可复用 `LLMClient`（每实例独立 base_url+model→同端点按名开 3 实例，走直连**不用 camel 全局 env**）、`SimulationConfigGenerator`（生成 match config）、`ReportAgent`（文本式 ReACT，模型无关）、`SimulationRunner`。`camel`/`oasis` 仅 `scripts/` 依赖，`app/` 零依赖 → 整块剥离。OASIS 的 `env.step({agent:action})` 批量同步模式照搬，但 env/agent_graph 不复用，自写 tick 循环调引擎。

---

## 4. 合并路线图（含淘汰赛补全）

| 阶段 | 内容 | 产出 |
|---|---|---|
| **P0** 装机 | DGX OS/CUDA，确认 aarch64 + sm_121 + NVFP4；桥接测速 | 环境就绪 |
| **P1** 推理 | **当前：brain=共享 nemotron-120b（已驻 `:11434`，只读不碰）+ mirofootball 独立 Ollama 实例 `:11435` 拉 gemma4:e2b ×2（设其 `MAX_LOADED_MODELS≥2`）**；目标态可换 vLLM×3。实测 tok/s | 多模型端点 + 预算坐实 |
| **P2** 骨架 | 建 mirofootball；engine/server.js（静音+可选 seed shim）；纯引擎跑通一场 | 引擎服务化 |
| **P3** MVP | 只接 brain 持球点（热路径关 thinking）→ 跑通一场 + ReportAgent | **验证想法** |
| **P4** 两脑+KG | 两队 Gemma 批 + 快照同步 + KG 注入 + 工具函数 | 完整 tick |
| **P5** 淘汰赛特性 | **加时 + 点球大战 + 换人 + 红牌 + 晋级判定**（§3）+ 定位球 | **可打完整淘汰赛** |
| **P6** 数据+LoRA | 收 WC 数据 → FC26 映射 + 风格抽取 + 自博弈训队 LoRA + 注册 | 16 队可对阵 |
| **P7** 双机+校准 | 第二台 + coordinator（各跑各场 ×2）；模拟 vs 真实校验 | 规模 + 可信 |
| **P8** 自提升 | 带 LoRA 自博弈迭代；可视化 | 持续优化 |

---

## 5. 隔离与定位（不变的铁律）

- **独立项目 `mirofootball/`、独立服务、独立 Neo4j、独立数据副本**；**只读**消费你导出的 WC/FC26 数据，**绝不写或依赖预测盘生产**。
- 定位：**战术叙事 / 可解释 / 离线研究**；**不替代 Dixon-Coles 定价**（实时赔率仍归 DC，算力+校准两道墙）。

---

## 6. 全面性结论

- ✅ 全程讨论的需求**全部有落点**（§2 矩阵）。
- ✅ 发现并补上**淘汰赛关键缺口**：加时、点球大战、换人、红牌罚下、晋级判定（§3，编排层补、引擎不改）。
- ✅ **实读本地 repo + 硬件实测**（§3.7）：6 点实现细节全部有解、无一改引擎源码；本地模型现状（Ollama + nemotron-120b brain + gemma4:e2b ×2）已坐实，内存够、两个 Gemma 可与 brain 共存。
- 🟡 非阻塞细化项已登记（§3.6），不会"忘记"——都在路线图（§4）里有阶段归属。
- **计划现已全面、可追溯、对淘汰赛完整、并与本地实跑环境对齐。**

---

## Sources
- 代码核实：[footballSimulationEngine](https://github.com/OWNER/footballSimulationEngine-test)（`engine.js`/`lib/{setFreekicks,common,actions,setVariables}.js`：两半场/定位球/伤病/红牌/`'NP'`/点球解算）· [MiroFish](https://github.com/OWNER/MiroFish-Offline-Test)（`config.py` 本地模型）
- **本地实读核实（2026-06）**：`footballSimulationEngine-test-master`（位置 8 值 / checkProvidedAction / 双 console.log / Math.random 不可种子）· `MiroFish-Offline-Test-main/backend`（LLMClient 双路径 / embedding=nomic-embed-text / camel-oasis 仅 scripts/）· 硬件实测 DGX Spark GB10 128GB + Ollama 0.22.1 + nemotron-3-super:120b + gemma4:e2b
- 配套 00–04
