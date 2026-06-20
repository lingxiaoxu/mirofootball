# MiroFootball — LoRA 训练 + 国家队切换（世界杯 16 强用法）

> 配套 00–03。**用法定稿**：模拟**世界杯国家队**，约 **16 支（淘汰赛 16 强）**；**淘汰赛第一轮后你给我一部分真实数据**，我据此训各队 LoRA。开发环境恒定：**DGX Spark ARM64 + Blackwell sm_121**，**全本地模型**。
>
> 本文已**重读两 repo 代码**核实：整合合理、本地模型可用、国家队数据能映射进引擎。

---

## 0. 整合合理性 + 本地模型（重读代码核实）

| 要确认的 | 代码证据 | 结论 |
|---|---|---|
| MiroFish 用本地模型 | `config.py`: `LLM_BASE_URL = env('LLM_BASE_URL','http://localhost:11434/v1')`、`LLM_MODEL_NAME`、`EMBEDDING_BASE_URL` 本地、`NEO4J_URI=bolt://localhost:7687`；`llm_client.py` `OpenAI(base_url)` | ✅ **默认就本地**；改 3 个 env 指向本机 vLLM 即可 |
| 顶层编排可驱动本地模型 | `SimulationManager` + `SimulationConfigGenerator(base_url=Config.LLM_BASE_URL)` 用 LLM 生成整场配置 | ✅ MatchDirector 直接复用 |
| 引擎吃得下国家队数据 | `validate.js`: team`{name,teamID,rating,恰好11人}`；player`{playerID,name,position,rating,currentPOS,fitness,height,injured, skill{passing,shooting,tackling,saving,agility,strength,penalty_taking,jumping(+perception,control)}, stats{cards,goals,tackles,passes,shots}}` | ✅ 字段明确，国家队可映射(§3) |
| 决策接缝/单球/控球 | 见 01：`decideMovement` 按整队、`ball` 单一真相 | ✅ 已确认 |

> **一句话**：MiroFish 顶层（SimulationManager + 本地 OpenAI 兼容客户端）当导演、足球引擎当物理、两 Gemma + 一 Qwen 当大脑——**整条链路本地、可行、已对代码核实**。

---

## 1. 国家队场景的两个现实（决定训练策略）

1. **球队少而固定**：16 强 = 16 支国家队，**对阵从这 16 支里任选 2**（淘汰赛对阵表）。→ **16 个球队 LoRA**，开局热加载 2 个（§6）。
2. **真实数据极稀**：每支国家队到 16 强只踢了**~3 小组赛 + 1 淘汰 = ~4 场**。→ **不能只靠真实事件做行为克隆**（样本太少）。真实数据的正确用法是**抽"球队风格统计"当锚点/奖励**，主体数据靠**自博弈蒸馏**生成（§4-5）。

---

## 2. 粒度（国家队版）：队 LoRA + 球员数据 + 角色 LoRA

| 区分 | 用什么 | 切换 |
|---|---|---|
| **国家队战术风格**（巴西控球 / 摩洛哥低位反击）| **每队 1 个 LoRA（16 个）** | 开局热加载 2 个 |
| **球员个体**（姆巴佩 vs 角色球员）| **属性注入(KG) + 名单数据** | 换名单数据，零重训 |
| **位置行为**（门将/中卫/边锋）| **角色 LoRA（含 GK，5-8 个共享）** | 随请求选 |

> **不给每个球员训 LoRA**。国家队 23 人名单、首发 11 人会变 → **球员是数据**（属性 + KG），换人不重训。

---

## 3. 数据映射：世界杯国家队 → 引擎 + LoRA（你给我什么 / 我怎么用）

### 3.1 你需要给我的数据（淘汰赛后那"一部分"）

| 类别 | 字段 | 来源（你的预测盘已有！）|
|---|---|---|
| **名单/首发** | 每队 11 首发 + 替补，每人 `position`（GK/CB/LB/RB/CDM/CM/CAM/LW/RW/ST…）| WC 阵容数据 / API-Football lineups |
| **球员属性** | `passing,shooting,tackling,saving,agility,strength,penalty_taking,jumping,perception,control,rating,height` | **EA FC26 评分（你的 `fc_strength`）** → 直接映射 skill；FIFA rank → `rating`；体测 → height |
| **球队风格统计**（LoRA 锚点）| 控球率、传球方向性(短/长)、压迫高度(PPDA)、build-up 区域、定位球倾向、阵型 | **真实 WC 小组赛+R16 比赛统计**（API-Football fixture stats / 你的 DB）|
| **真实赛果/事件**（可选增强）| 进球/射门/关键事件的 (区域→动作) | WC 事件数据（稀疏，做锚点不做主体）|

> **隔离**：你**导出**这些到 `mirofootball/data/`（只读副本），**绝不**让模拟反向依赖预测盘生产。FC26→skill 的映射正好把你的数据优势注入模拟。

### 3.2 映射成引擎 team JSON（满足 validate.js）

⭐ **role→engine-position 映射（#1，代码核实 2026-06）**：引擎只认 8 个 position 字符串 `{GK,LB,CB,RB,LM,CM,RM,ST}`（`actions.js` GK 权重、`setFreekicks.js` 用 `['CB','LB','RB']`/`['CM','LM','RM']` 分组站位）。FC26/WC 名单的细分角色（RW/LW/CF/CAM/CDM/LWB/RWB…）**必须在此映射成 8 值给引擎**，同时**原样保留 `role` 字段给 LLM prompt**（双字段，见 02 §2/§7）。`validate.js` 不做枚举校验，但非标准串会被 setFreekicks 分组漏掉 → 映射是为站位逻辑正确，非为过校验。

```python
# data_map.py —— role→engine position 映射表 (8 值)
ROLE_TO_POS = {
  "GK":"GK",
  "CB":"CB","LCB":"CB","RCB":"CB",          "SW":"CB",
  "LB":"LB","LWB":"LB",  "RB":"RB","RWB":"RB",
  "CDM":"CM","CM":"CM","CAM":"CM","LCM":"CM","RCM":"CM","DM":"CM","AM":"CM",
  "LM":"LM","LW":"LM",   "RM":"RM","RW":"RM",
  "ST":"ST","CF":"ST","LF":"ST","RF":"ST","SS":"ST",
}
def to_engine_pos(role): return ROLE_TO_POS.get(role.upper(), "CM")   # 兜底 CM

# data_map.py 骨架: WC国家队 → 引擎 team JSON
def to_engine_team(team_id, lineup, fc_ratings, fifa_rank, formation):
    players = []
    for i, p in enumerate(lineup):            # 恰好 11 人
        fc = fc_ratings[p.player_id]          # EA FC26 → skill
        eng_pos = to_engine_pos(p.position)   # 细分角色 → 引擎 8 值
        players.append({
          "playerID": i+1, "name": p.name,
          "position": eng_pos,                # ← 引擎用(8 值之一)
          "role": p.position,                 # ← LLM prompt 用(原始细分角色, 02 §2/§7)
          "rating": str(fc.overall),
          "skill": {"passing":str(fc.passing),"shooting":str(fc.finishing),
                    "tackling":str(fc.defending),"saving":str(fc.gk_diving if eng_pos=='GK' else 20),
                    "agility":str(fc.agility),"strength":str(fc.physical),
                    "penalty_taking":str(fc.penalties),"jumping":str(fc.jumping),
                    "perception":str(fc.positioning),"control":str(fc.ball_control)},
          "currentPOS": formation_pos(formation, eng_pos, i),   # 阵型→坐标(用引擎 position)
          "fitness":100, "height":p.height, "injured":False,
          "stats":{"cards":0,"goals":0,"tackles":0,"passes":0,"shots":0}}    # validate 必填
    return {"name":team_id, "teamID":team_id, "rating":str(team_rating(fifa_rank)), "players":players}
```
- **`position` 给引擎、`role` 给 LLM**：引擎物理/站位只看 `position`(8 值)；prompt 的 role-based options(02 §2)读 `role`(细分)。互不污染，引擎零改。
- **GK 的 `saving`** 用 FC26 门将扑救项；外场用低值（引擎 GK 才需要高 saving）。
- `formation_pos`：把 4-3-3/4-4-2/5-3-2 等阵型 → 11 个 `currentPOS` 起始坐标（一张阵型→坐标表），输入用引擎 `position`。
- **名单可换**：同一队不同场次首发不同 → 换 `lineup` 即可，LoRA 不动。

---

## 4. 用稀疏真实数据抽"球队风格"（LoRA 的锚点）

每队 ~4 场，**不做逐事件克隆**，而是抽**风格向量**当训练的目标/奖励：

```json
// team_style[brazil]  (从真实 WC 统计抽)
{ "possession_target": 0.58, "directness": 0.30, "tempo": 0.55,
  "press_height": 0.62, "ppda": 9.5, "buildup_zone":"central",
  "width": 0.55, "setpiece_threat": 0.40, "formation":"4-2-3-1",
  "counter_speed": 0.45 }
```
- 这组向量**喂进 MatchDirector 配置**（02 §1）+ 当 LoRA 训练的**风格奖励**（让该队 LoRA 的决策分布逼近这组统计）。
- **数据稀疏也稳**：风格统计是聚合量（4 场够估），比逐事件克隆鲁棒得多。

---

## 5. 训练流程（真实风格锚定的自博弈蒸馏，国家队版）

```
Stage 0  零 LoRA: Gemma 底座 + prompt + 属性注入(FC26) 跑该队比赛
            └ 用 §4 的 team_style 注入 MatchDirector → 决策已大致像该队
Stage 1  自博弈生成: 让该队打 16 强里各对手, 收集 (world, me, options, decision, 结果) 轨迹
Stage 2  打标(双信号):
            • 结果信号: 该决策所在控球段是否推进/创造射门 (引擎 log 可算)
            • 风格信号: 该场聚合统计 vs team_style 的接近度 (KL/距离)
            • (可选) 真实锚: 与该队真实区域→动作频率对齐度
Stage 3  拒绝采样: 留「推进 且 像该队风格」的决策 → 正样本 (chat SFT 格式, 见 §5.1)
Stage 4  LoRA SFT: 在 Gemma E2B 上为该队训一个 LoRA (行为克隆 + 风格偏好)
Stage 5  评估→注册→上线 (§8); 用带LoRA版再自博弈 → 数据更像该队 → 迭代自提升
```

### 5.1 训练样本（对齐 02 §2 的 Gemma I/O）
```json
{"messages":[
 {"role":"system","content":"<OFFBALL_SYS>"},
 {"role":"user","content":"{world..., me{...FC26 skill...}, options..., team_style:'brazil'}"},
 {"role":"assistant","content":"{\"id\":7,\"target_zone\":\"F5\",\"posture\":\"support\"}"}]}
```
- 每队一个数据集；门将样本单独成 "GK 角色" 子集 → 训共享 GK 角色 LoRA。
- **为什么自博弈合理**：真实事件太少，但"该队风格 + FC26 球员能力"已能让 Stage 0 跑出像样的该队比赛；自博弈是**在风格锚点约束下扩样本**，结果/风格双过滤保证质量。

---

## 6. vLLM 多 LoRA：16 队任选 2 + 热切换（为什么不紊乱）

### 6.1 机制 —— 当前 Ollama 派生模型（两个独立 gemma + LoRA），目标态 vLLM 热加载

**当前实跑（Ollama，承接 03 §2.0 的两个独立模型名）**：两队区分 = **两个派生模型名**；LoRA 通过 Modelfile 的 `ADAPTER` 行挂上（Stage0 无 LoRA 时 home/away 即纯底座，仍是两个独立实例）。
```bash
# serving/Modelfile.home   (对阵确定后按队填 ADAPTER; 换场重 create 即换队)
#   FROM gemma4:e2b-it-q4_K_M
#   ADAPTER ./lora/brazil          # ← 该队 LoRA(safetensors→gguf); 无 LoRA 则删此行=纯底座
OLLAMA_HOST=localhost:11435 ollama create gemma-home -f serving/Modelfile.home   # 主队=巴西
OLLAMA_HOST=localhost:11435 ollama create gemma-away -f serving/Modelfile.away   # 客队=摩洛哥
# 编排器请求 model="gemma-home"/"gemma-away"; 换场 swap = 改 Modelfile 的 ADAPTER 重 create(秒级)
# 16 队适配器都备在 lora/, 派生 = 拼底座+该队 ADAPTER(底座 blob 复用, 磁盘几乎零增)
```
**目标态（vLLM 运行时热加载，16 队任选 2）**：
```bash
VLLM_ALLOW_RUNTIME_LORA_UPDATING=1 vllm serve google/gemma-4-E2B-it \
  --enable-lora --max-loras 1 --max-cpu-loras 16 --max-lora-rank 16 ... --port 8002
```
```python
# 对阵确定(如 巴西 vs 摩洛哥)时, 编排器开局热加载这两队:
await load_lora(GEMMA_HOME_URL, "brazil",  REG["brazil"])    # :8002
await load_lora(GEMMA_AWAY_URL, "morocco", REG["morocco"])   # :8003
# 请求里 model="brazil"/"morocco" 选用; 换场再 swap
```

### 6.2 为什么切换球队/球员**不会紊乱**（你问的）
**Gemma 是无状态纯函数**：身份/球队/球员状态**不在模型里**，在 prompt(属性) / KG(记忆) / 每请求选的 LoRA。

| 切换 | 为什么不乱 |
|---|---|
| **换球队 (热加载 LoRA)** | LoRA 是**按请求**生效的小增量，底座权重不变；每请求显式指定适配器(`model="brazil"`)；**请求间零持久状态**，换适配器=请求指向不同 delta，不串 |
| **换球员 (换名单数据)** | 模型**跨请求无记忆**；球员属性当场从 prompt/KG 读；换人=换数据，模型从不持有球员状态 |
| **并发 11 人** | vLLM **PagedAttention**：每请求独立 KV，A 的 token 绝不注意 B 的 → 机制上不可能串味 |

**唯一纪律**：**在比赛边界切换适配器，不在中途**（开局 load、整场只用、换场再 swap）→ 编排器在 `simulate_match` 开头 load、结束才换 → 无竞态。Prefix cache 也安全（按 token+适配器双 key）。

> 真正要防的是**相反的"同质化"**——靠"队 LoRA + 角色 prompt + KG 属性"三层注入解决；注入越足，区分越强。

---

## 7. 在哪训（DGX ARM64 + sm_121）

| 项 | 状况 | 对策 |
|---|---|---|
| torch arm64+sm_121 | ⚠️ | 用 **NVIDIA NGC PyTorch 容器**(GB10/arm64) |
| bitsandbytes 4-bit | ⚠️ arm64 可能缺 | 用 **bf16 LoRA**(5B 在 128GB 够) |
| PEFT/TRL/transformers | ✅ 纯 Python | — |
| 同时占满推理 | ⚠️ | **错峰**：训练在无比赛时段 |

**两条路**：① DGX 上训（NGC + bf16 LoRA，Gemma 小，单队几十分钟）；② **别处训、DGX 部署**（LoRA 是可移植 safetensors，拷过来直接 serve）。**起步推荐 ②**，避开 arm64 训练栈；稳定后 ① 本机自提升。

```python
# train_team_lora.py 骨架 (PEFT+TRL, bf16)
peft = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05,
                  target_modules=["q_proj","k_proj","v_proj","o_proj"], task_type="CAUSAL_LM")
SFTTrainer(model=Gemma_bf16, peft_config=peft, train_dataset=team_ds,
           args=SFTConfig(output_dir=f"lora/{team_id}", num_train_epochs=2,
                          per_device_train_batch_size=8, learning_rate=2e-4, bf16=True,
                          max_seq_length=2048)).train()
```

---

## 8. 评估门槛 + 注册表（部署前）

| 指标 | 门槛 |
|---|---|
| JSON 合法率（约束解码）| =100% |
| **风格一致性**：带 LoRA 跑 N 场的统计 vs `team_style` | KL 下降、逼近真实 |
| 比赛统计合理性（控球/射门/越位/攻防回合）| 落真实区间、不劣于 Stage 0 |
| 不退化 vs 纯 prompt | ≥ baseline，否则回退 |

```json
// lora/registry.json
{ "brazil":{"path":"lora/brazil","style_kl":0.09,"version":2,"ok":true},
  "morocco":{...}, ... 16 队 ...,
  "_roles":{"gk":{"path":"lora/role_gk"},"cb":{...}} }
```
> 像你预测盘的 calibration gate：**LoRA 没让模拟更像该队就不上线**，回退 prompt+FC26 注入。名单与 LoRA 解耦，换人不动 LoRA。

---

## 9. 淘汰赛后的工作流（你给数据 → 我做什么）

```
你: "做 X、Y…这几支队, 这是它们的 阵容 + FC26评分 + WC统计"  (导出到 mirofootball/data/)
我:
 1. data_map.py: 每队 → 引擎 team JSON (§3.2, FC26→skill, 阵型→坐标)
 2. style_extract.py: WC 统计 → team_style 向量 (§4)
 3. Stage 0 自博弈: 该队 vs 16强对手, 收集轨迹 (§5)
 4. 结果+风格双过滤 → SFT → 该队 LoRA (§5/§7)
 5. 评估过门槛 → 注册进 registry (§8)
 6. 对阵任选两队 → 热加载2个LoRA + 载入名单 → 整场模拟 + ReportAgent 解说
```
- **数据来多少做多少**：先给几支就先训几支，registry 增量扩充；没 LoRA 的队先用"底座+FC26 注入+team_style"也能跑（只是不那么专精）。

---

## 10. 双 DGX Spark：在 Box B 训练 → 迁移回 Box A（详细 guide · 2026-06）

> **现实**：本机(Box A)显存被 nemotron(94G)+两 gemma 占着,**不在 Box A 上训练**。用户有**两台 DGX Spark,桥接**。**Box A = 服务/跑比赛(本机, :11434 nemotron + :11436/:11437 gemma + :7000 engine + neo4j,绝不停)**;**Box B = 训练机**。LoRA 训好(小文件)再搬回 Box A 挂载。
>
> ⚠️ **此文档是 public 仓库,严禁写入任何真实 token/key**。HF token、API-FOOTBALL key **只存在 `~/mirofootball/.env`(gitignored)**;下面一律以变量名引用,真实值从 .env 读/搬。

### 10.0 角色与数据流
```
Box A(本机/服务)                         Box B(训练)
  备 SFT 数据(不耗 GPU)  ──rsync(桥, 小)──▶  下底座(HF, 直连不走桥) + 训 LoRA
  挂 LoRA + 跑比赛       ◀──rsync(桥, 几MB)── 产出 LoRA gguf(每队几 MB)
```
**走桥的只有小件**(repo/SFT 数据/.env/LoRA gguf,KB~MB 级);**大件(10G 底座)Box B 自己从 HF 下,不走慢桥**(对齐 00 §9.2 / 06 §3.2)。

### 10.1 密钥(从 Box A 的 .env 搬,绝不入库/打印)
`~/mirofootball/.env`(gitignored,chmod 600)含:
```
API_FOOTBALL_KEY=<在 .env 里,见 dashboard.api-football.com>
HF_TOKEN=<在 .env 里,见 huggingface.co/settings/tokens; 已对 google/gemma-4-E2B-it 有访问权>
```
搬到 Box B:`rsync -a Box_A:~/mirofootball/.env ~/mirofootball/.env`(走桥,一次)。**Box B 上的 assistant：开工先读 `~/mirofootball/.env` 拿 token/key,不要 echo、不要写进任何 tracked 文件。**

### 10.2 Box A 备料(现在就能做,不耗 GPU、不碰模型)
1. `brain/pull_api.py` → `data/api/`(WC2026 + 8 队全端点,已拉)。
2. `brain/data_map.py` → `data/teams_engine/<Team>.json`(真实 11 人含门将 + skill,已生成)。
3. `brain/style_extract.py` → `data/styles/<Team>.json`(从 team stats 抽 team_style 向量,§4)。**待写**。
4. `brain/build_sft.py` → `data/sft/<Team>.jsonl`(SFT 样本,§5.1 格式:system=OFFBALL_SYS / user=world+me+team_style / assistant=合法 JSON 决策;由 Stage-0 自博弈轨迹 + 结果/风格双过滤生成)。**待写**。

### 10.3 搬到 Box B
```bash
rsync -a Box_A:~/mirofootball/ ~/mirofootball/ --exclude .venv --exclude 'serving/ollama*' --exclude data/api
# 关键带上: brain/ data/sft/ data/teams_engine/ data/styles/ .env serving/Modelfile.* serving/make_team_model.sh
```

### 10.4 Box B 训练栈 + 训练
```bash
# arm64 训练栈(plan §7): 优先 NVIDIA NGC PyTorch 容器(GB10/arm64); 或 pip
pip install torch transformers peft trl accelerate datasets
huggingface-cli login --token "$HF_TOKEN"     # 从 .env 读, 别明文
# 下可训练底座(全精度, ~10G, Box B 直连 HF):
huggingface-cli download google/gemma-4-E2B-it --local-dir models/gemma4-e2b-it
# 每队一个 LoRA(train_team_lora.py, §7 骨架: PEFT LoraConfig r=16 + SFTTrainer, bf16)
for T in France Argentina Netherlands Japan Norway Germany Spain Brazil; do
  python brain/train_team_lora.py --base models/gemma4-e2b-it --data data/sft/$T.jsonl --out lora/$T
done
# 显存: Box B 也只剩有限显存 → bf16 LoRA(冻底座, 仅训适配器)约需 ~12-16G;
#   不够则 4-bit QLoRA(bitsandbytes, arm64 留意 §7); 或减小 batch/seq_len。
```
产出:`lora/<Team>/`(adapter_model.safetensors + adapter_config.json,每个**几 MB~几十 MB**)。

### 10.5 LoRA → GGUF(给 Ollama ADAPTER)
```bash
# llama.cpp 把 safetensors LoRA 转成 Ollama 能挂的 gguf
python llama.cpp/convert_lora_to_gguf.py lora/$T --base models/gemma4-e2b-it --outfile serving/lora/$T.gguf
```
⚠️ gemma4 是新架构,确认 llama.cpp 的 convert_lora_to_gguf 支持;不支持则跟进 llama.cpp 版本。

### 10.6 搬回 Box A
```bash
rsync -a Box_B:~/mirofootball/serving/lora/ Box_A:~/mirofootball/serving/lora/   # 每队几 MB, 走桥秒级
```

### 10.7 Box A 挂载 + 切换(零额外改动,机制已就位)
```bash
# 为每队从 QAT 底座派生挂 LoRA 的 Ollama 模型(serving/make_team_model.sh 已写)
serving/make_team_model.sh France serving/lora/France.gguf 11436
serving/make_team_model.sh Germany serving/lora/Germany.gguf 11437
# 对阵切换(如 France↔Germany → Brazil↔Argentina): 换 model 名重 create 即可(秒级, 比赛边界切换)
```
MatchDirector 把 home/away 的 `model` 指向 `gemma-<team>`(config.py 加 per-team 覆盖;现在是固定 `gemma4:e2b-it-qat`)。换名单(22人)= 载入 `data/teams_engine/<Team>.json`(已能做)。

### 10.8 体积:训练后**不会变大**
底座仍是 **QAT ~2.5G(加载)**;LoRA 适配器 **几 MB~几十 MB**。`gemma-<team>` = QAT 底座 + 小适配器 ≈ **还是 ~2.5G/实例**。两 gemma + nemotron 的内存账不变(用户的 2-3GB 担忧不成立)。
> 注:LoRA 在 **bf16 全精度底座**上训,挂到 **QAT(int4)底座**上推理——可行且小;若发现质量损失,可改在 QAT 兼容底座上训或评估门槛(§8)回退。

### 10.9 验证(§8 门槛,在 Box A 跑)
带 LoRA 跑 N 场 → JSON 合法率 100% + 风格统计逼近 team_style(KL↓) + 不劣于纯 prompt;过门槛才进 registry,否则回退 prompt。

### 10.10 Box B 上的 assistant 开工须知(到那台机让我读本节)
1. **读 `~/mirofootball/.env`** 拿 `HF_TOKEN`/`API_FOOTBALL_KEY`(别 echo/入库)。
2. 数据在 `data/sft/`(训练集)、`data/teams_engine/`(名单)、`data/styles/`(风格);没有就先在 Box A 用 §10.2 的脚本生成再 rsync。
3. 训练栈见 §10.4;每队产 `lora/<Team>`;转 gguf 见 §10.5;搬回见 §10.6。
4. **Box B 是训练机**,可自由用 GPU;但**别去碰 Box A 的 :11434/:11435/:11436/:11437**(那是服务机的常驻模型,见记忆 ops-constraints)。
5. 严格按本 plan(04 §3-§9)的数据映射/风格锚定/双过滤/评估门槛,不简化。

---

## Sources
- 代码核实：[footballSimulationEngine `validate.js`/`init_config`](https://github.com/OWNER/footballSimulationEngine-test) · [MiroFish `config.py`/`simulation_manager.py`/`llm_client.py`](https://github.com/OWNER/MiroFish-Offline-Test)
- vLLM Multi-LoRA / 运行时加载 · PEFT/TRL · NVIDIA NGC PyTorch(GB10/arm64)
- [Gemma 4 E2B](https://huggingface.co/google/gemma-4-E2B) · 配套 00–03
