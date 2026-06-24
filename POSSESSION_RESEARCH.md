# 控球率真实化 研究 + home/away 偏差调查状态(2026-06-24)

## 网上研究(FIFA游戏/FM/学术)怎么做真实控球
- 学术(arxiv 2005.04020): 控球涌现于 传球概率 + 行动半径 + **对抗(duels)是换手主因**; 用控球时长/传球数分布校验。
- Football Manager 引擎: 控球涌现自**每个动作按球员 attribute 评估**(传球/抢断/移动); **控球型打法=减少失误(短传安全→高完成率→保球)**; 高逼抢=多夺球耗体力; mentality/tempo 决定冒险度/直接性。
- 核心: 控球 = f(传球完成率(受压), 对抗/抢断胜负(技能), 逼抢, tempo/风格). 强队/控球队靠 **少失误+赢对抗** 保球。

## home/away 偏差调查(未解决, 已耗大量尝试)
症状: home(kickOffTeam) 系统性控球低(France home 25% vs away 89%; Spain home 19%)。
已查/已试(均未根治):
- movePlayers 顺序(先KO后ST, ST后处理覆盖夺散球)→ 改距离回收(playerMovement.setClosePlayerTakesBall 加距离门)→ 纯引擎一度 42% 但方差大、LLM下复发。
- 实测: 散球回收 away 8 : home 2(关键不对称证据)。
- 越位/犯规/扑救 各队对称(非根因)。
- 试过: 拦截吃技能(反效果)、抢断±6频繁(治了方差但home仍低)、随机顺序(治方差未治home)、control入retention(回归更差,已撤)。
## ★下一个强嫌疑(未验证): actions.js 动作向量是【按半场】的
- findPossActions: originPOS[1] < 半场 → topTeamPlayerHasBall; > 半场 → bottomTeamPlayerHasBall。
- home/away 起始在不同半场 → 用不同向量函数。若 topTeam vs bottomTeam 向量不对称(某个数不同)→ 就是 home/away 偏差根因。
- 下一步: 逐行对比 topTeamPlayerHasBall vs bottomTeamPlayerHasBall(+ InMiddle/InPenaltyBox)是否镜像对称。

## 已修好(committed, 勿动): 扑救(saving)/射正(shooting roll0-50+瞄门内)/越位计数/进球转化/散球距离回收。
