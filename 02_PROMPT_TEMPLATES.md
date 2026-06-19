# MiroFootball — Prompt 模板全集

> 配套 [`00_MIROFOOTBALL_PLAN`](./00_MIROFOOTBALL_PLAN.md) / [`01_SIMULATION_SEMANTICS`](./01_SIMULATION_SEMANTICS_AND_ORCHESTRATION.md)。
> 四类 prompt：**MatchDirector 配置生成（Qwen）· Gemma 跑位（含 GK）· Qwen 持球决策 · ReportAgent 解说**。
>
> **共同设计铁律**（针对 DGX Spark 带宽受限 + 小模型稳定性）：
> - **结构化 JSON 输出 + 约束解码**（vLLM `guided_json` / `response_format`）→ 小模型几乎不可能输出非法值。
> - **输出尽量短**（Gemma ~15 token、Qwen ~40 token）→ decode 时间正比输出长度。
> - **共享世界状态 `world`** 注入每个 prompt（同一帧快照）→ 三模型同步知道球/位置/控球（见 01 §2.3）。
> - **动作只从引擎的离散集里选**：`shoot, throughBall, pass, cross, tackle, intercept, slide, run, sprint, cleared, boot, penalty`。
> - **属性接地**：把该球员 `skill` + 位置 + 体能注入 → 不同能力/位置决策不同。
> - **粗网格 zone**：球场切成 `col(A–F) × row(1–9)` 的 54 格；LLM 只对 zone 推理，引擎把 zone → 精确坐标 + 按 `agility/fitness` 夹紧位移。

---

## 0. 公共 `world` 切片（注入所有 prompt，三模型一致）

```json
{
  "world": {
    "minute": 63, "score": {"home": 1, "away": 0},
    "ball": {"zone": "C6", "holder_id": 11, "holder_team": "home", "in_flight": false},
    "possession": {"home": 0.62, "away": 0.38},
    "phase": "home_attack",
    "players": [
      {"id": 7,  "team": "home", "zone": "D4", "role": "RW",  "has_ball": false},
      {"id": 11, "team": "home", "zone": "C6", "role": "CF",  "has_ball": true},
      {"id": 2,  "team": "away", "zone": "D5", "role": "LB",  "has_ball": false}
      /* …全 22 人… */
    ]
  }
}
```
> `world` 由编排器从冻结的 `matchDetails` 生成一次，**所有当拍调用共用**。坐标→zone 由编排器换算。

---

## 1. MatchDirector 配置生成（Qwen3.6-35B-A3B，赛前一次）

**作用**：顶层导演用 Qwen 把两队信息 → 一份**比赛战术配置**（控球目标/节奏/直接性/阵型意图），喂给 PossessionDirector 与决策偏置。复用 MiroFish `SimulationConfigGenerator` 的"LLM 生成 config"机制，只换 schema。

**System**
```
You are a football match director. Given two squads with ratings and recent form,
produce a realistic pre-match tactical configuration as STRICT JSON.
Base possession/tempo on the strength gap and styles. Output ONLY the JSON.
```

**User**
```json
{ "home": {"name":"...", "rating":83, "formation":"4-3-3", "key_skills":{"att":81,"mid":84,"def":80},
           "recent_form":"WWDLW", "style_hint":"possession"},
  "away": {"name":"...", "rating":78, "formation":"4-4-2", "key_skills":{"att":77,"mid":76,"def":79},
           "recent_form":"LWDLL", "style_hint":"counter"} }
```

**约束输出 schema**（`guided_json`）
```json
{
  "home": {"possession_target":0.62, "tempo":0.55, "directness":0.35,
           "press_intensity":0.60, "line_height":0.65, "tactical_note":"control through midfield"},
  "away": {"possession_target":0.38, "tempo":0.70, "directness":0.65,
           "press_intensity":0.45, "line_height":0.40, "tactical_note":"sit deep, break fast"},
  "expected": {"attacks_home":12, "attacks_away":8},   // 仅作软目标参考(见 01 §3.3)
  "narrative_seed":"home dominant possession vs away low-block counter"
}
```
> `possession_target` 之和应 ≈ 1.0。`expected.attacks_*` 仅供 ReportAgent 对照，不硬执行。

---

## 2. Gemma 跑位（外场无球球员，**每队一批 11→实际10无球**）

**作用**：给每个**无球**球员一个**目标 zone + 姿态**。各队用各自 Gemma（主 :8002 / 客 :8003）+ 球队 LoRA。一次 batch（每名球员一条并发请求，vLLM 合批）。

**System**（带球队 LoRA 时，LoRA 已编码球队风格；prompt 再给位置约束）
```
You are an off-ball positioning brain for ONE football player.
Pick the best target zone and posture from the allowed options ONLY.
Respect your role, pace and stamina. Stay compact as a team. Output ONLY JSON.
```

**User**（每球员一条；`world` 见 §0）
```json
{ "world": { /* §0 共享世界 */ },
  "me": {"id":7, "team":"home", "zone":"D4", "role":"RW",
         "pace":82, "stamina":71, "work_rate":78},
  "team_intent": "attack", "retention_bias": 0.62, "press_intensity": 0.60,
  "options": ["hold","press","drop","support","run_behind","widen","tuck_in","overlap","track_back"]
}
```

**约束输出**（~15 token）
```json
{"id":7, "target_zone":"F5", "posture":"run_behind"}
```

**位置→允许 options 映射**（编排器按 role 裁剪 `options`，小模型更稳）：

| role | 允许的 posture 子集 |
|---|---|
| CB | hold, drop, track_back, tuck_in, press |
| FB/WB | support, overlap, widen, track_back, press |
| DM/CM | support, drop, press, hold, tuck_in |
| AM/W | support, run_behind, widen, press, overlap |
| CF | run_behind, hold, support, press |

> ⭐ **role 与引擎 position 是两个字段（代码核实，2026-06）**：上表的 `role`（CB/FB/WB/DM/CM/AM/W/CF…）是**LLM 朝向的细分角色**，只进 prompt；**引擎本身只认 8 个 position 字符串 `{GK,LB,CB,RB,LM,CM,RM,ST}`**（`actions.js` GK 专属权重、`setFreekicks.js` 用 `['CB','LB','RB']`/`['CM','LM','RM']` 分组站位、`setVariables.js` GK saves）。`validate.js` **不做枚举校验**（只查字段存在），但传非标准串会被 setFreekicks 分组漏掉 → 所以**统一在 `data_map.py` 把 role→engine-position 映射**（RW→RM、LW→LM、CF→ST、CAM→CM、CDM→CM、WB→LB/RB…，见 04 §3.2）。`player.position` 给引擎，`player.role` 给 LLM，互不污染。

> `retention_bias` 高 → 引擎/编排器对 `support`(给短传选项) 加权；`press_intensity` → 无球方 `press` vs `drop` 倾向。引擎再按 `pace/stamina` 夹紧位移（累了压不动）。

---

## 3. 门将跑位（Gemma，GK 角色路径，**叠在各自球队 Gemma 下**）

**作用**：门将是各队 Gemma 控的一员，但走 **GK 角色 prompt（或 GK 角色 LoRA）**，options 完全不同（永不跑动/抢断）。**只决定站位/出击/分球**，不决定扑救结果（那是引擎 §4）。

**System**
```
You are a goalkeeper's decision brain. Choose ONE goalkeeping action from the
allowed options ONLY, based on ball position, threat, and your attributes
(saving, perception, jumping, height). Never leave the goal exposed. Output ONLY JSON.
```

**User**
```json
{ "world": { /* §0 */ },
  "me": {"id":1, "team":"home", "zone":"A1", "role":"GK",
         "saving":88, "perception":85, "jumping":80, "height":192, "agility":78},
  "threat": {"ball_zone":"B2", "shooter_id":19, "shooter_shooting":86, "distance":"close", "angle":"central"},
  "options": ["hold_line","narrow_angle","rush_out","claim_cross","set_wall",
              "distribute_short","distribute_long"]
}
```

**约束输出**
```json
{"id":1, "gk_action":"narrow_angle", "target_zone":"A2"}
```
> GK 决策**只改 `GK.currentPOS`/`action`**（`narrow_angle/rush_out` → 编排器把 GK 摆到更近封堵位）。**扑救由引擎原生 `checkGoalScored` 解算**（它已用 GK 坐标接近度 + height + jumping + saving）：站得近 → 引擎判扑出概率高。**LLM 决定站位，引擎原函数判扑没扑到，零额外公式**（见 01 §4.4）。持球时（球门球）走 `distribute_short/long`。

---

## 4. Qwen「球权争夺」决策（持球者 + 第一防守者，看 `world` 全局）

**作用**：Qwen = 跟着球走的争夺脑（球队无关），每拍为**球权争夺的 1-2 人**决策：**4a 持球进攻者**（传/射/突）+ **4b 第一防守者**（抢/封/卡位/拦截/延缓）。两者都从引擎离散集选；编排器把 holder/defender 分别喂这两个变体（可并发）。

### 4a 持球者（进攻决策）

**System**
```
You are the on-ball decision brain for the player CURRENTLY in possession.
Choose ONE action from the allowed set and a target if needed. Weigh risk vs reward
from the full field state, your skills, teammates' space, opponents' pressure, score
and time. Favor retention when retention_bias is high. Output ONLY JSON.
```

**User**
```json
{ "world": { /* §0 全场22人+球+控球+比分+分钟 */ },
  "me": {"id":11, "team":"home", "zone":"C6", "role":"CF",
         "shooting":86, "passing":79, "control":83, "strength":80, "agility":81},
  "teammates": [ {"id":7,"zone":"F5","role":"RW","marked":false,"lane":"open"},
                 {"id":9,"zone":"D6","role":"CM","marked":true,"lane":"tight"} ],
  "opponents_near": [ {"id":4,"zone":"C6","role":"CB","distance":"close"} ],
  "retention_bias": 0.62, "shot_context": {"in_box":true, "angle":"central", "gk_id":1, "gk_saving":88},
  "allowed": ["pass","throughBall","cross","shoot","dribble","clear"]
}
```

**约束输出**（~40 token）
```json
{"action":"throughBall", "target_id":7, "target_zone":"F5", "intent":"play RW in behind", "risk":0.4}
```
- `action ∈ allowed`（编排器按位置/区域裁剪：自家禁区附近加 `clear`，对方禁区内加 `shoot`/`penalty`）。
- `pass/throughBall/cross` 必带 `target_id`（合法队友）；`shoot` 不带；`dribble` 带 `target_zone`。
- `risk` 仅供引擎/解说参考；**结果（传成不成、射进不进、扑没扑到）全由引擎按技术概率解算**，Qwen 只决定"做什么"。

### 4b 第一防守者（防守决策）

**作用**：抢断/延缓决策。引擎默认让该防守者 `sprint` 逼抢；Qwen 把"何时下脚 vs 延缓"的**决策质量**提上去（鲁莽下脚=被过+可能犯规；延缓=等支援）。

> **谁是这个防守者？由编排器按几何选，不是 Qwen 猜**：防守方中**到球坐标最近**的人（引擎 `closestPlayerToBall`，`min(|Δx|+|Δy|)`，每拍重算，随站位动态）。梅西持球 → A 队此刻离球最近那个。编排器选出该人 + **注入双方 skills**（下面 `me` 防守属性 + `ball_carrier` 盘带属性），Qwen 才据此判断。

**System**
```
You are the decision brain for the DEFENDER closest to the ball. Choose ONE action.
Diving in (tackle/slide) risks being beaten or a foul; containing (jockey) buys time for
cover. Weigh your tackling skill, the dribbler's control, your position (last man?), and
support behind you. Output ONLY JSON.
```
**User**
```json
{ "world": { /* §0 */ },
  "me": {"id":4, "team":"away", "zone":"C6", "role":"CB",
         "tackling":84, "agility":78, "strength":85, "perception":80},
  "ball_carrier": {"id":11, "control":83, "agility":81, "zone":"C6", "dist":"close"},
  "cover_behind": true, "last_man": false, "team_intent":"defend", "press_intensity":0.6,
  "allowed": ["tackle","slide","intercept","jockey","sprint"]
}
```
**约束输出**（~24 token）
```json
{"id":4, "action":"jockey", "intent":"contain, wait for cover", "commit":0.3}
```
- `jockey` → 编排器映射为 `intentPOS` 贴身延缓（不下脚）；`tackle/slide/intercept` → `player.action`（引擎按 `tackling` vs `control` 概率解算成败 + 可能犯规/红黄牌）。
- `last_man=true` 时编排器从 `allowed` 去掉 `slide`（最后一人鲁莽=送点/送红牌）。
- **结果（抢成不成/犯规）由引擎按技术解算**，Qwen 只决定"上抢还是延缓"。

---

## 5. ReportAgent 解说/复盘（Qwen，赛后，可流式）

**作用**：把整场 `iterationLog` + KG 轨迹 → 解说 + 关键时刻 + 统计核对（控球/攻防回合/扑救）。

**System**
```
You are a football match analyst. Write a concise, vivid match report from the
event log and stats. Include: final score, possession, attack counts, key moments
(goals, big saves, turning points), and a one-line tactical verdict per team.
Ground every claim in the provided events. Output Markdown.
```

**User**（结构化喂入，避免幻觉）
```json
{ "final": {"home":2,"away":1},
  "stats": {"possession":{"home":0.63,"away":0.37},
            "attacks":{"home":12,"away":8},
            "shots":{"home":14,"away":7}, "saves":{"home_gk":4,"away_gk":6}},
  "key_events": [ {"min":23,"type":"goal","team":"home","scorer":11,"detail":"throughBall from 7"},
                  {"min":58,"type":"save","gk":1,"detail":"close-range from 19"},
                  {"min":71,"type":"goal","team":"away","scorer":19} ],
  "tactical": {"home":"control + width","away":"low-block counter"} }
```

---

## 6. 约束解码落地（vLLM 或 Ollama —— 二者都支持 schema）

> ⭐ **当前实跑栈是 Ollama（brain=nemotron-3-super:120b、两队=gemma4:e2b），不是 vLLM**。两条路都能做严格 schema 约束解码，按所用服务选其一（实现见 03 §4 的 serving-aware client）：

- **vLLM**：`guided_json` / `response_format={"type":"json_schema",...}`（基于 outlines/xgrammar）。
- **Ollama（≥0.5.0，本机 0.22.1 已支持）**：原生 `format=<JSON schema>`（`/api/chat`）或 OpenAI 兼容口 `response_format={"type":"json_schema","json_schema":{...}}`。MiroFish 现有 `LLMClient` 只用了 `json_object` 模式 → **要在 fork 的 `brain/llm_client.py` 加 `schema` 透传**（03 §4），现有 JSON 手工修复保留作兜底。**让小 Gemma 稳产合法 JSON 的关键。**
- **共同**：**每类输出都挂 schema** → 小模型几乎不可能输出非法值；**schema 固定复用**（grammar/CPU 编译首次有开销，复用即缓存），不要每次换 schema。
- **短输出 + `max_tokens` 收紧**（Gemma `max_tokens=24`，Qwen 决策 `max_tokens=64`）→ 省 decode、省带宽。
- **system prompt + 规则 = 共享前缀** → 开 `--enable-prefix-caching`，22 个并发请求共用前缀只算一次。

---

## 7. Prompt ↔ 引擎字段映射（确保接得上）

| Prompt 字段 | 引擎来源 |
|---|---|
| `me.role`（LLM 细分角色：RW/CF/AM/WB/DM…）| **独立字段 `player.role`**（只给 prompt）；另有 `player.position` ∈ 引擎 8 值 `{GK,LB,CB,RB,LM,CM,RM,ST}`（给引擎），二者由 `data_map.py` 映射，见 §2 注 + 04 §3.2 |
| `me.{pace,stamina,shooting,passing,saving…}` | `player.skill.*` + `player.fitness` |
| `world.ball.{zone,holder_id,holder_team,in_flight}` | `matchDetails.ball.{position→zone, Player, withTeam, ballOverIterations}` |
| `world.players[].zone` | `player.currentPOS → zone` |
| 输出 `target_zone` | → `player.intentPOS`（zone→坐标）|
| 输出 `action` / `target_id` | → `decideMovement` 注入的该球员 `action`（替换 `selectAction`）|
| 输出 GK `gk_action` | → GK 的 `action`/`currentPOS`（站位）→ 引擎原生 `checkGoalScored` 据 GK 接近度判扑救（不另写公式，01 §4.4）|

---

## Sources
- [footballSimulationEngine-test](https://github.com/OWNER/footballSimulationEngine-test) · [MiroFish-Offline-Test](https://github.com/OWNER/MiroFish-Offline-Test)
- vLLM guided decoding（`guided_json` / structured outputs）
