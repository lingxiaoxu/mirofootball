# MiroFootball — Agentic 足球比赛模拟（设计与规划）

> 把 [`footballSimulationEngine`](https://github.com/OWNER/footballSimulationEngine-test)（Node.js 回合制物理引擎）与 [`MiroFish-Offline`](https://github.com/OWNER/MiroFish-Offline-Test)（多 agent + Neo4j KG + 本地 LLM + ReportAgent）**融合**成新项目 `mirofootball`，用**本机模型**驱动一场可解释的足球比赛模拟。
>
> **本仓库只含设计与规划文档**（7 份），不含实现代码。引擎与 MiroFish 为第三方开源项目，见上方链接（本项目以 vendored + 复用方式整合，物理引擎一行不改）。

## 锁定架构（一页）

- **引擎**（Node.js）= 物理唯一真相，原封不动复用；LLM 只写 `player.action` / `player.intentPOS` 两个引擎已有字段注入决策，射门/扑救/点球/抢断/越位一律走引擎原函数。
- **三模型分工**（本地，OpenAI 兼容）：
  - **战术 brain** — 持球决策 + 第一防守者 + 赛前 config + 赛后解说（reasoning 大模型）。
  - **两个独立 Gemma 4 E2B** — 主队 / 客队各一，批量决无球跑位（队级 LoRA + 位置 prompt + KG 属性三层区分）。
- **知识图谱**（Neo4j，可选）承载每场/每队/每球员/每状态轨迹，供决策检索与赛后复盘。
- **目标硬件**：单台统一内存机（GB10 级，128GB），整场比赛在一台机本地跑完；多台用于数据并行（各跑各场）。

## 文档索引

| # | 文件 | 内容 |
|---|---|---|
| 00 | [00_MIROFOOTBALL_PLAN.md](./00_MIROFOOTBALL_PLAN.md) | 融合架构 · 模型分工 · 硬件/内存/速度 · tick 循环 · KG schema · 落地 checklist |
| 01 | [01_SIMULATION_SEMANTICS_AND_ORCHESTRATION.md](./01_SIMULATION_SEMANTICS_AND_ORCHESTRATION.md) | 顶层编排 · 单球不变式 · 共享世界 · 控球率 · 攻防回合 · 门将/扑救 |
| 02 | [02_PROMPT_TEMPLATES.md](./02_PROMPT_TEMPLATES.md) | 全套 prompt 模板 · 约束解码 · role↔引擎 position 映射 · prompt↔引擎字段映射 |
| 03 | [03_DEPLOYMENT_SKELETON.md](./03_DEPLOYMENT_SKELETON.md) | 模型 serving（Ollama/vLLM）· engine/server.js · orchestrator · 起步验证 |
| 04 | [04_LORA_TRAINING_AND_ROSTER.md](./04_LORA_TRAINING_AND_ROSTER.md) | 球队 LoRA 训练 · 名单→引擎映射 · 多 LoRA 热切换 |
| 05 | [05_PLAN_INDEX_COVERAGE_AND_GAPS.md](./05_PLAN_INDEX_COVERAGE_AND_GAPS.md) | 索引 · 覆盖矩阵 · 缺口补全 · 实现细节核实 · 路线图 |
| 06 | [06_ORCHESTRATION_CONTROL_AND_BOX_MIRROR.md](./06_ORCHESTRATION_CONTROL_AND_BOX_MIRROR.md) | 决策注入接缝（代码确证）· 多模型协同 · 多机镜像与一致性 |

## 致谢 / 上游

- [footballSimulationEngine-test](https://github.com/OWNER/footballSimulationEngine-test)
- [MiroFish-Offline-Test](https://github.com/OWNER/MiroFish-Offline-Test)
