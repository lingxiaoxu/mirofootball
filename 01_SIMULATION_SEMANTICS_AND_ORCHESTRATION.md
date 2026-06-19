# MiroFootball — 模拟语义与顶层编排设计（与 00_MIROFOOTBALL_PLAN 配套）

> 本文回答四个核心模拟语义问题，全部**基于实读两 repo 代码**确认可行：
> 1. **顶层由 MiroFish 组织、且驱动本地模型** —— 代码确认 + 改造方案
> 2. **球的归属：单一足球、只能一个人持球、Qwen 与两个 Gemma 同步共享世界状态**
> 3. **控球率（50/50 或 65/35）怎么模拟** + **攻防回合数（home 11 / away 9）怎么决定**
> 4. **门将的 LoRA 怎么设计 + 扑救成功率怎么设计**

---

## 1. 顶层编排 = MiroFish 的 `SimulationManager`，驱动本地模型（代码确认）

### 1.1 代码证据

| MiroFish 现有机制 | 文件 | 作用 |
|---|---|---|
| `SimulationManager` | `services/simulation_manager.py` | **顶层导演**：`create_simulation` / `prepare_simulation`，管 `SimulationState`(status, `current_round`, 各阶段状态) |
| `SimulationConfigGenerator(base_url, model_name)` | `services/simulation_config_generator.py` | `base_url = base_url or Config.LLM_BASE_URL`、`model_name = … or Config.LLM_MODEL_NAME` → **本地 OpenAI 兼容端点**；**用 LLM 智能生成整场配置**（agent 数、回合数、参数）|
| `config.llm_model / llm_base_url` | 同上 | 配置对象携带本地模型端点，全程本地 |
| `llm_client.py` | `utils/llm_client.py` | `OpenAI(base_url=…)` → 指向 vLLM/Ollama，**本地驱动已坐实** |

→ **MiroFish 的顶层编排本就是"用本地模型当大脑、自己生成并管理整场模拟"的架构**。社交场是它的一个 env；我们把 env 换成足球引擎即可。

### 1.2 改造：`MatchDirector`（由 SimulationManager 派生）

```
MatchDirector (= 改造后的 SimulationManager, 顶层)
  ├─ 用 Qwen(本地 :8001) 智能生成「比赛配置」:
  │     阵型 / 战术风格 / 控球目标(65/35) / 节奏(tempo) / 直接性(directness)
  │     ← 复用 SimulationConfigGenerator 的「LLM 生成 config」机制, 只换 schema
  ├─ 建 agent_graph: 22 个球员 agent (人格+属性+记忆, 存 Neo4j)
  ├─ engine.initiate(team1, team2, pitch) → 拿到初始 matchDetails (含 ball)
  ├─ 主循环: 逐 tick 调度 (见 §2.4) ── 这是「rounds」的足球版
  ├─ 管 SimulationState: current_round=current_iteration, 比分, 控球, 状态
  └─ 结束 → ReportAgent(本地模型) 生成解说/复盘
```

**确认**：顶层组织这场球赛的，就是 MiroFish 的 `SimulationManager`（改名 `MatchDirector`），它通过 `Config.LLM_BASE_URL` 指向本机 vLLM 的三个端点（Qwen :8001 / Gemma 主 :8002 / Gemma 客 :8003），**全程本地模型驱动**，零云依赖。

---

## 2. 球的归属：单一足球 + 共享世界状态（最重要）

### 2.1 引擎是「球」的单一真相（代码证据）

`matchDetails.ball`（`setVariables.js` populateMatchDetails）：
```js
ball: {
  position: [x, y, z],        // 单一坐标 (z = 高度)
  withPlayer: true|false,      // 球是否在某人脚下
  Player: <playerID>,          // 哪一个球员持球 (单一 ID)
  withTeam: <teamID>,          // 哪一队持球 (单一)
  ballOverIterations: [],      // 球在飞行中的轨迹
  lastTouch: {playerID, teamID, iterations, bodyPart, deflection}
}
```
每球员 `player.hasBall = true|false`。

**单球不变式（引擎强制）**：`common.js::removeBallFromAllPlayers()`
```js
matchDetails.ball.withPlayer = false
matchDetails.ball.withTeam = ''
for (player of 所有球员) player.hasBall = false      // ← 先全部清零
```
任何赋球（`setGoalieHasBall` / 触球 / 抢断成功）都**先 removeBallFromAll 再赋给一个人** → **任意时刻最多一个 `hasBall=true`**。引擎 changelog 亦写明"only one player can execute a ball action per iteration"、"single ball interaction"。

> **结论**：你担心的"只能一个足球、在一个人那、别人不能有"——**引擎本就硬性保证**，我们不需要在 LLM 层重新发明，只要**让引擎当唯一真相、LLM 只读不写球权**。

### 2.2 设计原则：引擎拥有世界状态，LLM 只「读」不「占」

- **球、所有球员位置、控球、比分 = 引擎的 `matchDetails`（唯一真相）**。
- 三个 LLM（2 Gemma + Qwen）**每拍拿到同一帧 `matchDetails` 快照**，只是**读**它来决策，**绝不**自己声明"我有球"。谁有球由引擎告知（`ball.Player`）。

### 2.3 共享世界意识：每个 LLM 的 prompt 都注入同一份世界切片

```json
// 每个 LLM 调用 (Gemma×22 / Qwen×1) 的 prompt 都带这块「世界状态」(来自同一帧快照):
"world": {
  "ball": {"pos_zone":"C6", "holder_id":11, "holder_team":"home", "in_flight":false},
  "score": "1-0", "minute": 63, "possession": {"home":0.62, "away":0.38},
  "players": [ {"id":7,"team":"home","pos_zone":"D4","role":"RW"}, ... 全 22 人 ... ]
}
```
- **三个模型看到的 `world` 完全一致**（同一帧快照）→ Qwen 和两个 Gemma **同步知道球在哪、谁持球、每人位置、控球率**。
- **Qwen 的角色 = 「球权争夺脑」（跟着球走，球队无关，非"主队脑"）**。持球者在 H 还是 A 一直切换；每拍球边有个 ~2 人的争夺决定这次控球成败：
  - **持球进攻者**（`world.ball.holder_id`）→ **Qwen**：传/射/突/做球
  - **第一防守者** → **Qwen**：抢/封/卡位/拦截/延缓
- **"谁是第一防守者"怎么判断 —— 几何决定，不是固定的人、也不是 Qwen 猜**：
  - **由编排器/引擎按物理坐标算**：防守方中**到球距离最近**的球员（引擎 `closestPlayerToBall`：`min(|Δx|+|Δy|)` 到 `ball.position`，**每拍重算**）。例：H 队梅西持球 → 第一防守者 = **A 队此刻坐标离球最近的那个**（随站位动态变，不固定）。
  - 引擎本就这么算并用：`decideMovement` 对"对方持球时本队最近者"强制 `sprint` 逼抢、给其 tackle/intercept 选项。编排器复用同一几何选出该人 → 喂给 Qwen 决策。
- **"第一防守者做什么" → Qwen 决定，但必须知道双方 skills**（编排器从引擎球员数据/KG 注入）：
  - 防守者：`tackling, agility, strength, perception`（能不能抢下、会不会被过）
  - 持球者：`control, agility, pace`（盘带威胁多大）
  - → Qwen 据此权衡**下脚抢 vs 贴身延缓**（鲁莽=被过+可能犯规；延缓=等支援）。**职责分离**：几何选"谁防"，Qwen 定"怎么防"，引擎按技术解算"抢没抢到/犯没犯规"。
- **路由规则**：
  - 球权争夺的 **1-2 人**（持球者 + 第一防守者）→ **Qwen**
  - 其余 **20-21 人** → 各自队 **Gemma**（**阵型/跑位**：进攻方接应跑动 + 防守方防线/盯人/补位，按 `team_intent` 攻/守适配 + 球队 LoRA）
  - 引擎 `playIteration` 内 `validBallMoves.filter(m => m.player.hasBall === true)` 兜底 → **物理上仍只解算持球者那一个球动作**（单球不变式）。

→ **A 持球时**：Qwen = A 持球者 + H 第一防守者；Gemma-A = A 其余 9 人(进攻跑动)；Gemma-H = H 其余 10 人(**防守阵型**)。**H 夺回则全部翻转，Qwen 跟着球走**。
→ **可降级**：嫌复杂可先只 Qwen 持球者，让引擎自动逼抢 + 技术解算防守（引擎已做）；防守者上 Qwen 是**增强**。每拍 Qwen 1-2 次（"single ball interaction" 决定争夺=每拍核心），Gemma 20-21 次批量。

### 2.4 一拍的完整调度（落实单球 + 同步）

```python
md = engine.state()                      # 唯一真相
snap = freeze(md)                         # 冻结这一帧, 三模型共用
world = world_slice(snap)                # ball/holder/positions/possession/score → 注入所有 prompt

holder   = holder_of(snap)                # 持球者 (可能在任一队, 或 None)
defender = engaging_defender(snap)        # 防守方离球最近者 (引擎 closestPlayer; 持球时存在)
contest  = {holder["id"], defender["id"]} if holder else set()

# 阵型/跑位: 两队 Gemma 并发, 各 batch (排除争夺中的 1-2 人)
home_intents, away_intents = await gather(
  gemma_home.batch([p for p in snap.home if p["id"] not in contest], world),  # :8002 攻/守阵型
  gemma_away.batch([p for p in snap.away if p["id"] not in contest], world),  # :8003
)
# 球权争夺 1-2 人 → Qwen (看 world 全局)
contest_actions = await qwen.decide_contest(holder, defender, world)          # :8001

inject(md, home_intents, away_intents, contest_actions)
md = engine.playIteration(md)            # 引擎解算物理 + 强制单球不变式
kg.update(md)
```
> Qwen 跟着球走、为**球权争夺的 1-2 人**（持球者 + 第一防守者）决策；其余 20-21 人（含两队阵型/防守组织）走各队 Gemma。引擎单球不变式仍保证物理上只一人动球。

---

## 3. 控球率（50/50 / 65/35）与攻防回合数

### 3.1 先认清：控球率/回合数是**输出**，不是直接输入

引擎里"谁控球"是**涌现**的：每拍 `ball.withTeam` 指向某队，**逐拍累计 = 控球率**；一段连续同队控球 = **一次进攻回合**（possession sequence），易于**测量**：
```python
# 从 iterationLog 直接测:
possession_home = #{iter : ball.withTeam==home} / total_iters
attacks_home    = 连续 ball.withTeam==home 的「段」数 (段间被对方夺断/射门/出界打断)
```

### 3.2 控球率怎么"模拟到 65/35"——目标 + 反馈控制（PossessionDirector）

不能硬塞，但能**有界地引导**。设计一个控制器，把目标转成**每队的"保球倾向" `retention_bias ∈ [0,1]`**，注入两队的 Gemma/Qwen 决策，再用**测量值反馈微调**：

```
目标: home 0.65 / away 0.35
  → retention_bias_home 高 (短传/回做/控制), press_intensity_away 低(落位反击)
  → retention_bias_away 低 (直接/快出球),    press_intensity_home 高(高位逼抢夺回)

retention_bias 影响什么 (注入决策, 不破坏物理):
  • Gemma 跑位: 高 bias → 更多 support/控制型跑位 (给持球者更多短传选项)
  • Qwen 持球: 高 bias → pass/throughBall 权重↑、直接 clear/boot 权重↓ (改 action 概率分布)
  • press_intensity: 影响无球方逼抢/落位 → 决定夺回球的快慢

反馈环 (每 ~2 分钟模拟时间):
  measured = possession_home_so_far
  error = target(0.65) - measured
  retention_bias_home += Kp * error    (PI 控制, 夹在 [0,1])
  → 逐步收敛到 65/35 ± 容差
```

- **本质**：控球率不是"设"出来的，是把**目标当战术偏置喂进决策**，再用**测量反馈**收敛。50/50 = 两队 bias 对称（默认）；65/35 = 给 home 高保球 + 给 away 低保球高反击。
- **接地**：bias 调的是引擎已有的 `action` 权重分布（pass vs clear）和 Gemma 跑位倾向，**不改物理**，所以仍然真实。

### 3.3 攻防回合数（home 11 / away 9）怎么决定

回合数 = **控球率 × 节奏/直接性**的涌现结果：
```
attacks ≈ f(possession_share, tempo, directness)
  • 控球高但慢(tiki-taka): 回合少、每段长
  • 直接快(长传冲吊):     回合多、每段短
```
- **测量**：从 log 数 possession sequence 段数（§3.1）。
- **引导**：给每队一个 `tempo`/`directness` 战术参数（由 MatchDirector 用 Qwen 生成的比赛配置给定），注入决策 → 影响每段控球的长短与转换频率 → 涌现出大致的回合数。
- **想要精确 11/9**：两条路——
  1. **软引导（推荐, 真模拟）**：设 tempo/directness + 控球目标，跑出来 ~11/9，接受 ±2 的自然波动。
  2. **硬脚本（可选, 半确定）**：MatchDirector 加一个"段计数器"，到达目标段数后**抬高对方夺球倾向**强制转换——能逼近精确值，但牺牲一点自然性。默认用软引导。

> 诚实说明：足球是连续涌现的，"恰好 11 次进攻"是结果不是开关。我们能**测量它、用战术参数引导它、用反馈逼近它**，但不建议硬写死（那就不是模拟了）。

---

## 4. 门将：LoRA 设计 + 扑救成功率

### 4.1 引擎已有的门将模型（代码证据）

`actions.js`：GK 有**专属动作权重**（11 维 `[shoot,throughBall,pass,cross,tackle,intercept,slide,run,sprint,cleared,boot]`）：
```js
if (position==='GK' && oppositionNearPlayer(...)) return [0,0,10,0,0,0,0,10,0,40,40]  // 逼近时: 40%清球+40%开球
else if (position==='GK')                         return [0,0,50,0,0,0,0,10,0,20,20]  // 常态: 50%分球+开球/清球
```
→ 门将**永不射门/抢断**，专做**分球/清球/开球**；并有 `skill.saving`、`player.stats.saves`、`setGoalieHasBall`。

### 4.2 门将的"两件事"分开设计

| 门将的事 | 谁负责 | 怎么设计 |
|---|---|---|
| **行为决策**（站位/出击/分球选择）| **Gemma（在各自队下）+ GK 角色** | 见 §4.3 |
| **扑救成功/失败**（射门来了挡不挡得住）| **引擎概率解算**（按 `skill.saving`）| 见 §4.4 —— **绝不让 LLM 判定**，必须接地可校准 |

### 4.3 门将 LoRA 在各自 Gemma 下怎么设计

门将是各队 Gemma 控制的 11 人之一。其**区分**来自三层（与外场球员同框架，但 GK 特化）：
```
各队 Gemma 底座
   + 球队 LoRA (主/客战术)              ← 队级
   + GK 角色 (二选一, 推荐先用 prompt):
       A) GK 角色 prompt: options 限定为 {hold_line, rush_out, narrow_angle,
          claim_cross, distribute_short, distribute_long, set_wall}, 永不含跑动/抢断
       B) GK 角色 LoRA (有数据后): 一个「门将行为」适配器, 与球队 LoRA 组合挂载
   + 该门将个体属性 (saving/perception/jumping/height/agility) 从 KG 注入
```
- **设计建议**：**GK 角色用一个共享的"门将行为 LoRA/prompt"**（门将的站位/出击逻辑跨队相通），**叠在各自球队 LoRA 之上** —— 即"在各自 Gemma 下，但 GK 走 GK 角色路径"。不需要"每队一个独立 GK 模型"。
- 门将的**个体差异**（诺伊尔 vs 普通门将）= KG 注入的 `saving/perception/jumping/height` + 引擎扑救解算（§4.4），不是靠多模型。
- GK 的 **prompt 输出**只决定**站位/出击/分球**（影响封角度、是否弃门），**不决定**"扑没扑到"。

### 4.4 扑救成功率：**用引擎原生解算，不另写公式**（复用原文件原则）

⚠️ **引擎已原生解算扑救**——核实代码：`ballMovement.js::checkGoalScored` 判定球是否入门时，**已检查 GK 坐标接近度 + `height + jumping`（+ `saving`）**（`KOrHeight = KOGoalie.height + KOGoalie.skill.jumping`、`nearKOGoalieX/Y`…）；射门由 `shotMade`（`skill.shooting` vs roll + 是否到门）解算；点球由 `penaltyTaken`（`skill.penalty_taking`）解算。**这些都不要重写、不要新增公式。**

正确设计 = **复用引擎原生解算 + 让 LLM 只影响输入**：
- **扑救结果**：完全交引擎 `checkGoalScored`（射门到门 + GK 接近度/身高/弹跳/扑救技术 → 进球或扑出）。
- **GK 的 LLM 决策只改 `GK.currentPOS`**（站位/出击）→ 自动改变 `checkGoalScored` 的"GK 接近度"判定 → **经引擎原生解算自然影响扑救**。即"站位好 → 离球近 → 引擎判扑出概率高"，无需我们算 angle。
- **个体差异自然体现**：引擎已用 `height/jumping/saving`，诺伊尔(saving 高)比普通门将扑得多——引擎原生给出。
- **点球**：直接走引擎 `penaltyTaken`（`penalty_taking` vs GK），**不重写**。
- **要校准全局扑救率到 ~65-75%**：**调引擎 `checkGoalScored` 现有参数**（如 `ballProx` 接近度阈值、身高/弹跳权重），不替换逻辑；用真实分布校验（像你预测盘校准思路）。

> 原则：**LLM 永不判"扑没扑到"，也不另写扑救公式**；扑救/射门/点球**一律走引擎原生函数**，LLM 只通过"GK 站位(currentPOS)"这个**引擎输入**间接影响结果。这既接地可校准，又**零引擎逻辑重写**。

---

## 5. 把这些挂回主循环（与 00 文档的 tick 对齐）

```python
# MatchDirector (顶层, 源自 MiroFish SimulationManager, 本地模型驱动)
cfg = await qwen.generate_match_config(team1, team2)   # 阵型/控球目标65-35/tempo/directness
poss = PossessionDirector(target_home=cfg.poss_home)   # §3.2 反馈控制器
md = await engine.initiate(team1, team2, pitch)        # 引擎=球与世界的唯一真相
kg.bootstrap(md, agents=build_player_agents(team1, team2))   # 22 agent → Neo4j

for it in range(ITER*2):
    snap  = freeze(md);  world = world_slice(snap)     # §2.3 三模型共享同一世界
    biases = poss.biases(measured_possession(md))      # §3.2 控球偏置
    # 无球者跑位 (两队 Gemma 并发, GK 走 GK 角色路径)
    hi, ai = await gather(
        gemma_home.batch(offball(snap.home), world, biases.home),   # 含主队 GK(GK角色)
        gemma_away.batch(offball(snap.away), world, biases.away))   # 含客队 GK(GK角色)
    # 持球者一人 → Qwen
    ba = await qwen.decide(holder(snap), world, biases) if has_holder(snap) else None
    inject(md, hi, ai, ba)
    md = await engine.playIteration(md)                # 物理 + 单球不变式 + 扑救解算(§4.4)
    kg.update(md, it);  poss.observe(md)
report = await report_agent(md, kg)                    # 解说 + 控球/回合/扑救统计
```

---

## 6. 这些都"可做到"的代码依据汇总

| 你的要求 | 代码依据 | 可做到? |
|---|---|---|
| 顶层由 MiroFish 组织、用本地模型 | `SimulationManager` + `SimulationConfigGenerator(base_url=Config.LLM_BASE_URL)` + `llm_client OpenAI(base_url)` | ✅ 现成机制 |
| 只能一个足球、在一个人那 | `ball.{position,Player,withTeam}` 单值 + `removeBallFromAllPlayers()` 强制全清再赋一人 + `validBallMoves.filter(hasBall)` | ✅ 引擎硬保证 |
| Qwen 与 Gemma 同步知道球/位置/控球 | 同一帧 `matchDetails` 快照 → `world` 切片注入所有 prompt | ✅ 设计保证 |
| 控球率 65/35 | `ball.withTeam` 逐拍可测 + PossessionDirector 反馈控制偏置 | ✅ 测量+引导 |
| 攻防回合 11/9 | possession sequence 段数可测 + tempo/directness 引导 | ✅ 软引导(硬脚本可选) |
| 门将 LoRA 各自 Gemma 下 | GK 专属 action 权重已存在 + GK 角色 prompt/LoRA 叠球队 LoRA | ✅ |
| 扑救成功率 | `skill.saving`/`stats.saves` 已存在 + §4.4 概率解算(仿 calcTackleScore) | ✅ 引擎解算+可校准 |

---

## Sources（代码与规格）
- [footballSimulationEngine-test](https://github.com/OWNER/footballSimulationEngine-test)（`engine.js` / `lib/{actions,playerMovement,ballMovement,common,setVariables,setPositions}.js`）
- [MiroFish-Offline-Test](https://github.com/OWNER/MiroFish-Offline-Test)（`services/{simulation_manager,simulation_config_generator,simulation_runner}.py`, `utils/llm_client.py`）
- 配套：[`00_MIROFOOTBALL_PLAN.md`](./00_MIROFOOTBALL_PLAN.md)
