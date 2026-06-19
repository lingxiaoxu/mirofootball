# MiroFootball — 编排控制流（Qwen+2Gemma+22人）+ Box 镜像（代码级·零引擎改动）

> 配套 00–05。本文用**实读引擎代码**钉死两件事，确保"每一步都可行、怎么做"：
> 1. **决策注入引擎的官方口子**（`player.action` + `player.intentPOS`，引擎现成消费，**零物理改动**）；
> 2. **Qwen 与两个 Gemma 的 22 人怎么 work together** —— **由 mirofootball 当指挥（conductor），三者经"共享世界 + 引擎"协同，不互相通信**；
> 3. **一台开发完成后，怎么镜像到第二台**（慢桥接友好的 provisioning）。

---

## 1. 注入接缝：引擎已有两个字段（代码实证，零物理改动）

### 1.1 `player.action` —— 离散动作注入（`checkProvidedAction`）
`lib/playerMovement.js::decideMovement` 对每个球员调 `checkProvidedAction(matchDetails, thisPlayer, action)`：
```js
let providedAction = (thisPlayer.action) ? thisPlayer.action : 'unassigned'
if (providedAction === 'none') return action            // 没注入 → 用引擎启发式
if (allActions.includes(providedAction)) {
   if (thisPlayer.playerID !== matchDetails.ball.Player) {   // 非持球者
      if (ballActions.includes(providedAction)) return 'run' // 不准射/传 → 降级run (引擎自保)
      return providedAction
   } else if (providedAction in [tackle,slide,intercept]) {  // 持球者
      return 随机球动作                                       // 持球者不准抢 → 改球动作
   } return providedAction                                   // ← 合法 → 用注入动作
}
```
→ **设 `thisPlayer.action` = LLM 决策**；引擎**直接用**并**自带合法性校验**（非持球者不能射传、持球者不能抢）。设 `'none'`/不设 = 回退引擎启发式（trivial 决策省 LLM）。

### 1.2 `player.intentPOS` —— 跑位目标注入（`getRunMovement`）
`getRunMovement` 决定无球球员朝哪跑：
```js
if (球在 ±60 内) { ...朝球移动(追/逼) }                       // 球近 → 追球
else {                                                       // 球远 →
  let formationDirection = setPositions.formationCheck(player.intentPOS, player.currentPOS)
  ...按 intentPOS 方向移动                                    // ← 朝 intentPOS 跑!
}
```
→ **设 `player.intentPOS` = Gemma 的目标 zone(换算坐标)**；球远时球员朝它跑，球近时引擎自动接管追球。`getMovement` 再按 action(run/sprint) 给位移；`getSprintMovement` 同样用 intentPOS。

### 1.3 结论：写两个字段即可，物理一行不改

| LLM 输出 | 写引擎字段 | 引擎消费处 |
|---|---|---|
| Qwen 持球动作（pass/shoot/throughBall/cross/dribble→run）| 持球者 `player.action` | `checkProvidedAction` |
| Gemma 跑位（target_zone→坐标）| 无球者 `player.intentPOS` | `getRunMovement`/`getSprintMovement` |
| Gemma 姿态（run/sprint/hold）| 无球者 `player.action` | `checkProvidedAction` |
| 不决策的球员 | `player.action='none'` | 回退引擎启发式 |

> **可行性钉死**：注入 = **写 `action` + `intentPOS` 两个引擎已有字段**。`decideMovement`/`getMovement` 原样跑，**零物理改动**。引擎还替我们做动作合法性 + 单球不变式。

---

## 2. Qwen + 2 Gemma + 22 人怎么 work together（mirofootball 当指挥）

### 2.1 核心答复：**靠 mirofootball 控制（conductor），三模型不互相通信**
三个 LLM **不直接对话**。它们经**两条共享通道**协同，由 mirofootball 编排器指挥：
1. **共享世界 `world`**：同一帧冻结的 `matchDetails` 切片 → 注入每个 LLM 的 prompt（都看到同一个球/位置/控球，01 §2.3）。
2. **共享 `matchDetails`**：每个 LLM 的决策由编排器写回**同一份** matchDetails 的 `action`/`intentPOS`；引擎在**一次** `playIteration` 里把 22 人的决策**一起**解算成一个连贯世界（单球、物理一致）。

→ "work together" = **看同一个世界、写同一份状态、引擎统一解算**。Qwen 不需要知道 Gemma 说了什么——它们的决策在引擎里**通过物理与单球不变式自然协调**（例：Qwen 让持球者直塞给 7 号，Gemma 已让 7 号 intentPOS 跑向空当 → 引擎解算出"传球到跑动路线"）。

### 2.2 一拍的完整控制流（mirofootball 指挥，代码级）

```python
# brain/orchestrator.py 的一个 tick (conductor)
async def tick(md, world_tools, gh, ga, qb, biases):
    # ① 引擎是唯一真相: ball/positions/possession 都在 md
    snap  = freeze(md)
    world = world_tools.slice(snap)              # 共享世界 (02 §0): ball.holder/positions/poss/score
    holder_id = md["ball"]["Player"] if md["ball"]["withPlayer"] else None

    # ② 路由: 球权争夺(持球者+第一防守者)→Qwen; 其余→各队Gemma(阵型/防守组织)
    # 第一防守者 = 防守方坐标离球最近者. **调引擎原函数, 不在 Python 重写几何**:
    # engine/server.js 暴露 POST /closest → 内部调 playerMovement.closestPlayerToBall(防守队)
    defender_id = await engine_closest_defender(md) if holder_id else None  # ← 引擎原 closestPlayerToBall
    contest = {holder_id, defender_id} - {None}
    offball_home = [p for p in players(md,"home") if p["playerID"] not in contest and on_pitch(p)]
    offball_away = [p for p in players(md,"away") if p["playerID"] not in contest and on_pitch(p)]

    # ③ 并发决策 (两队Gemma真并行 + Qwen争夺1-2人)
    tasks = [gh.decide_batch(offball_home, world, biases["home"]),     # :8002 主队阵型(攻/守)
             ga.decide_batch(offball_away, world, biases["away"])]     # :8003 客队阵型(攻/守)
    if holder_id is not None:
        tasks.append(qb.decide_contest(find(md,holder_id), find(md,defender_id), world, biases))  # :8001
    results = await asyncio.gather(*tasks)
    home_intents, away_intents, contest_actions = unpack(results, contest)

    # ④ 写回同一份 md (这就是"协同": 都写 action/intentPOS)
    for d in home_intents + away_intents:
        p = find(md, d["id"])
        p["intentPOS"] = zone_to_xy(md, d["target_zone"])   # → getRunMovement 用
        p["action"]    = posture_to_action(d["posture"])    # run/sprint/'none' → checkProvidedAction 用
    if contest_actions:
        a = contest_actions
        find(md, holder_id)["action"] = a["holder"]["action"]    # 持球: pass/shoot/...
        stash_pass_target(md, holder_id, a["holder"].get("target_id"))
        if defender_id and a.get("defender"):                    # 第一防守: tackle/jockey/...
            apply_defender_action(md, defender_id, a["defender"]) # tackle→action; jockey→intentPOS贴身
    # 未被决策的球员保持 action='none' → 引擎启发式兜底(含其它防守者自动逼抢)

    # ⑤ 引擎一次解算 22 人 (单球不变式 + 物理, 见 01 §2)
    md = await engine_iterate(md)
    return md
```

### 2.3 谁控制什么（职责矩阵）

| 角色 | 控制 | 不控制 |
|---|---|---|
| **mirofootball 编排器** | 指挥：路由(争夺1-2人→Qwen / 其余→各队Gemma)、并发调度、写回 md、调引擎、KG、控球反馈 | 不算物理、不判结果 |
| **Qwen (:8001)** | **球权争夺的 1-2 人**：持球进攻者(传/射/突) + 第一防守者(抢/封/卡位) | 不控阵型其余人、不定结果 |
| **Gemma 主/客 (:8002/8003)** | 各队**其余无球球员的阵型**（进攻接应跑动 + 防守防线/盯人/补位，含本队 GK 角色；按 team_intent 攻/守适配）| 不控对队、不控球权争夺 |
| **引擎** | 物理解算、单球不变式、动作合法性、扑救/抢断概率、自动逼抢其余防守者、比分 | 不做"决策风格"（交给 LLM）|

> **一句话**：**mirofootball 是指挥**；Qwen 跟着球走管球权争夺 1-2 人（持球者 + 第一防守者），两 Gemma 各管本队阵型(攻/守)，**全写进同一份 matchDetails，引擎统一解算成单球连贯世界**——22 人 + 一球的协同由编排器控制、经引擎落地，三模型无需互相通信。Qwen 球队无关、随球权 H↔A 切换。

---

## 3. 一台开发完成 → 镜像到第二台（慢桥接友好）

### 3.1 原则：box 是**自包含克隆**，镜像是**一次性 provisioning**，非持续同步
因为是 shared-nothing（一场=一台，00 §9.2），Box B 只是 Box A 的**完整副本**、各跑各的场。镜像 = 把 A 的"全套环境"一次性复刻到 B。

### 3.2 要复刻的 6 类资产
| 资产 | 大小 | 怎么过去（避开慢桥接）|
|---|---|---|
| OS/CUDA/驱动 | — | 两台都装**同版本 DGX OS**（独立安装，不走桥接）|
| **模型权重**（Qwen ~21GB + Gemma ~3GB）| ~24GB | **每台各自从 HF 拉**（独立下载，不走桥接）或 NVMe/USB 拷贝；**不要走慢桥接传 24GB** |
| **LoRA 适配器**（16 队，~1-3GB）| 小 | rsync 走桥接 OK，或随 repo |
| **mirofootball 仓库** | 小 | `git clone` / rsync |
| **静态 KG**（48队/球员/战术模板）| 小 | Neo4j `dump` → 拷 → `load`（或启动时各自从同一数据生成）|
| **配置**（.env / vllm 脚本 / compose）| 微 | 随 repo；只改 `ROLE`/`PEER_BOX` |

> 关键：**大件（模型权重）各台独立从 HF 下载**，慢桥接只传小件（LoRA/repo/KG dump/配置）。

### 3.3 `provision_box.sh`（在 Box B 上跑，骨架）
```bash
#!/usr/bin/env bash
set -euo pipefail
test "$(uname -m)" = aarch64                                  # 确认 DGX
# 1. 代码
git clone <mirofootball.git> ~/mirofootball && cd ~/mirofootball
# 2. 模型权重: 各台独立拉 (不走桥接)
huggingface-cli download Qwen/Qwen3.6-35B-A3B --local-dir models/qwen
huggingface-cli download google/gemma-4-E2B-it --local-dir models/gemma
# 3. LoRA + 静态KG: 从 Box A rsync (小, 桥接OK)
rsync -a BOX_A_IP:~/mirofootball/lora/ lora/
rsync -a BOX_A_IP:~/mirofootball/kg_static.dump .
# 4. 配置: 复制 A 的, 只改本机角色
cp .env.boxA .env && sed -i 's/^ROLE=.*/ROLE=worker/;s/^PEER_BOX=.*/PEER_BOX=BOX_A_IP/' .env
# 5. 起服 (与 A 同脚本)
bash serving/vllm_qwen.sh & bash serving/vllm_gemma_home.sh & bash serving/vllm_gemma_away.sh &
docker compose up -d neo4j engine orchestrator
neo4j-admin database load --from-path=. neo4j < kg_static.dump || true
echo "Box B provisioned — identical to A"
```

### 3.4 镜像后**一致性验证**（确保"做一模一样的事")
```bash
# 同一场比赛 + 固定随机种子, 两台各跑一遍, 比对结果
SEED=42 python -m brain.orchestrator data/brazil.json data/morocco.json data/pitch.json > /tmp/B.json
rsync BOX_A_IP:/tmp/A.json .                 # Box A 同样跑出的 A.json
diff <(jq -S . A.json) <(jq -S . B.json) && echo "PARITY OK (两台逐字节一致)"
```
> ⚠️ **确定性前提**：固定种子 + 同模型权重 + 同量化 + 关采样随机（`temperature=0` 或固定 seed）+ 同 LoRA。LLM 采样若有随机性，改比对**统计分布一致**（控球/射门/比分分布 KS 检验）而非逐字节。
>
> ⭐ **#3 决策（2026-06）：默认接受不确定性 → 只比统计分布**（单场叙事/研究不需逐字节复现）。代码核实：引擎全部随机走 `lib/common.js::getRandomNumber`→`Math.random()`，原生不可种子化。**若将来要逐场可复现**：在 `engine/server.js` require 引擎**之前**加 shim `Math.random = seededPRNG(seed)`，即让整引擎对给定 seed 确定——**shim 在外壳，引擎源码一行不改**（复用铁律不破）。属可选增强，非阻塞。

### 3.5 双机协作（镜像完成后）
- Box A 额外起 `coordinator/coordinator.py`（05/03 已述）：比赛队列 → 交替派发整场给 A/B → 回收结果（KB 级，走慢桥接 OK）。
- 两台**镜像一致** → 任一场派到哪台都**等价** → 吞吐 ×2，且结果可复现。

---

## 4. 把本文接回其它文档

| 本文钉死的 | 强化了 |
|---|---|
| `player.action`/`intentPOS` 注入（零物理改动）| 00 §4.2、01 §2、03 orchestrator —— 整合从"设计"变"代码确证可行" |
| mirofootball 当指挥的协同控制流 | 00 §4.3、01 §2.4 的 tick 落到职责矩阵 + 写回字段 |
| Box 镜像 + 一致性验证 | 00 §9.2、03 §10 双机 —— 补上"怎么复刻第二台" |

→ 更新 `05_PLAN_INDEX` 覆盖矩阵：新增"决策注入接缝(代码确证)""Qwen↔2Gemma↔22人协同""Box 镜像/一致性"三行，均 → 本文(06)。

---

## Sources
- 代码实证：[footballSimulationEngine `lib/playerMovement.js`](https://github.com/OWNER/footballSimulationEngine-test)（`checkProvidedAction` 读 `player.action`；`getRunMovement` 读 `player.intentPOS` via `formationCheck`）· `engine.js` `playIteration`
- [MiroFish `simulation_manager.py`/`llm_client.py`](https://github.com/OWNER/MiroFish-Offline-Test)
- 配套 00–05
