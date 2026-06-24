# OASIS agent_graph 能否套到 22 名足球球员？（诚实评估）

> 评估对象：MiroFish 用的 camel-ai/**OASIS** 多 agent 框架。问题：能否复用它的 `agent_graph`
> 来管 22 名球员、且 `env.step` 改去驱动**我们的足球引擎**（而非社交平台 env）。

## OASIS 实际提供什么（读 MiroFish `scripts/run_twitter_simulation.py` + `oasis_profile_generator.py`）
1. **`agent_graph`**：agent 存储 + `agent_graph.get_agent(agent_id)` 取 agent（带 social profile）。
2. **批量步进**：每轮收集 `{agent: action}` → `await env.step(actions)` 一次性推进所有 agent。
3. **`oasis.make(...)`**：构造 env——但这是一个**社交平台模拟器**（Twitter/Reddit 的数据库 + 时间线 + 推荐）。
4. **动作空间**：`post / like / repost / follow / comment` 等**社交动作**。
5. profile 生成器产 OASIS agent profile（user_id 等社交字段）。

## 哪些能迁移 / 哪些是社交专属
| OASIS 组件 | 能否用于足球 | 说明 |
|---|---|---|
| **`env.step(actions)` 批量步进范式** | ✅ **核心价值,且我们已实现** | 我们 `match_director.run_match` 每拍:`asyncio.gather` 收齐两队所有球员决策 → 注入 → `engine.iterate`。这**就是** `collect {agent:action} → env.step` 的足球版。 |
| `agent_graph` 存储/取 agent | 🟡 价值很低 | 球员就是 `matchDetails` 里的 22 个 dict，`W.players(md, side)` 直接拿；不需要一个图结构。 |
| `oasis.make` 的社交 env | ❌ **不能用** | 它是 Twitter/Reddit 平台模拟（DB/时间线/推荐),与足球物理引擎完全不同——**我们的 env 就是足球引擎**，已经有了。 |
| 社交动作空间(post/like/follow) | ❌ **不能用** | 足球动作是 move/pass/shoot/tackle，由我们的引擎+注入接缝定义。 |
| OASIS profile(social user) | ❌ 不适用 | 球员"profile"= 真实名单 + skill + LoRA（已由 data_map/style_extract/LoRA 提供）。 |

## 结论与建议（诚实）
- **OASIS 唯一对足球有价值的是"批量多 agent 步进"范式,而这个我们已经用 `asyncio.gather + engine.iterate` 实现了**（全 22 人并行批量,两 daemon 并行）。
- OASIS 的其余部分（社交 env、社交动作空间、agent_graph、social profile）**都是社交媒体专属,无法套到足球**——硬套等于把 OASIS 的核心 env 整个换掉,等于重写 OASIS,**高成本、零额外收益**。
- 这也正是 plan 03 §1.5 当初"丢弃 camel/oasis、自写 tick 循环调引擎"的原因——**该决策是对的**。
- **建议:不引入 OASIS。** 我们已正确提取了它唯一可迁移的精华(批量步进),其余按足球语义自建。MiroFish 的真正可复用资产是 **SimulationManager(生命周期/状态)、SimulationConfigGenerator(健壮 config 生成)、ReportAgent(ReACT)**——这三者已**真移植**进 `brain/`（match_director / match_config_generator / report_agent）。

> 一句话:**"多 agent 能力"= OASIS 的批量 env.step 范式,我们已用足球版实现;OASIS 框架本体不适合足球,不套。**
