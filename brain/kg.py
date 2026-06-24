"""KG —— Neo4j 知识图谱（schema = plan 00 §6）。改造自 MiroFish neo4j_storage 的连接/写入模式。

静态：Match/Team/Player + HOME/AWAY/HAS_PLAYER。动态：State(关键拍) + AT_ITER + DID(决策)。
**优雅可选**：连不上 Neo4j 就 no-op（MVP 不接 KG 也能跑，plan 05 §3.7）。
连续全量轨迹在 trajectory.jsonl；KG 只存关键拍语义 + 决策，用 iter 指针引用。
"""
import os


class KG:
    def __init__(self, uri=None, user=None, password=None, enabled=True):
        self.driver = None
        if not enabled:
            return
        uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = user or os.environ.get("NEO4J_USER", "neo4j")
        password = password or os.environ.get("NEO4J_PASSWORD", "mirofootball")
        try:
            from neo4j import GraphDatabase
            self.driver = GraphDatabase.driver(uri, auth=(user, password))
            self.driver.verify_connectivity()
        except Exception as e:
            print(f"[KG] Neo4j 不可用，KG 关闭（不影响比赛）: {e}")
            self.driver = None

    @property
    def on(self):
        return self.driver is not None

    def _run(self, cypher, **params):
        if not self.on:
            return
        with self.driver.session() as s:
            s.run(cypher, **params)

    def bootstrap(self, match_id, md):
        if not self.on:
            return
        self._run("MERGE (m:Match {id:$id}) SET m.status='running'", id=match_id)
        for role, key in (("HOME", "kickOffTeam"), ("AWAY", "secondTeam")):
            t = md[key]
            self._run("""
                MATCH (m:Match {id:$mid})
                MERGE (tm:Team {id:$tid}) SET tm.name=$name, tm.formation=$form, tm.rating=$rating, tm.intent=$intent
                MERGE (m)-[:%s]->(tm)
            """ % role, mid=match_id, tid=str(t["teamID"]), name=t.get("name"),
                 form=str(t.get("formation") or ""), rating=str(t.get("rating") or ""),
                 intent=str((self_cfg := (md.get("_cfg") or {})).get(role.lower(), {}).get("tactical_note", "")))
            for p in t.get("players", []):
                sk = p.get("skill", {})
                # 全属性覆盖（00§6）：技术 + 身体(agility/strength/perception/jumping/control) + 状态(fitness/injured)
                self._run("""
                    MATCH (tm:Team {id:$tid})
                    MERGE (pl:Player {id:$pid})
                      SET pl.name=$name, pl.position=$pos, pl.rating=$rating,
                          pl.passing=$passing, pl.shooting=$shooting, pl.tackling=$tackling,
                          pl.saving=$saving, pl.height=$height,
                          pl.agility=$agility, pl.strength=$strength, pl.perception=$perception,
                          pl.jumping=$jumping, pl.control=$control, pl.fitness=$fitness, pl.injured=$injured
                    MERGE (tm)-[:HAS_PLAYER]->(pl)
                """, tid=str(t["teamID"]), pid=str(p.get("playerID")), name=p.get("name"),
                     pos=p.get("position"), rating=str(p.get("rating")),
                     passing=str(sk.get("passing")), shooting=str(sk.get("shooting")),
                     tackling=str(sk.get("tackling")), saving=str(sk.get("saving")),
                     height=str(p.get("height")), agility=str(sk.get("agility", "")),
                     strength=str(sk.get("strength", "")), perception=str(sk.get("perception", "")),
                     jumping=str(sk.get("jumping", "")), control=str(sk.get("control", "")),
                     fitness=str(p.get("fitness", "")), injured=str(p.get("injured", False)))
        self.seed_passing(match_id, md)

    def update_state(self, match_id, md, it, minute=0.0):
        """关键拍语义快照（用 iter 指针引用 trajectory.jsonl 对应行）。"""
        if not self.on:
            return
        b = md.get("ball", {})
        self._run("""
            MATCH (m:Match {id:$mid})
            MERGE (s:State {match:$mid, iter:$it})
              SET s.ball_pos=$pos, s.possession=$team, s.holder=$holder,
                  s.score_home=$sh, s.score_away=$sa, s.minute=$min
            MERGE (m)-[:AT_ITER]->(s)
        """, mid=match_id, it=it, pos=str(b.get("position")),
             team=str(b.get("withTeam") or ""), holder=str(b.get("Player") or ""),
             sh=md["kickOffTeamStatistics"]["goals"], sa=md["secondTeamStatistics"]["goals"],
             min=minute)

    def record_decision(self, player_id, action, it, target_id=None):
        """决策边（plan 00§6 DID/PASSES_TO）：(Player)-[:DID {action,iter}]->(Action)；
        传球另记 (passer)-[:PASSES_TO {iter}]->(receiver)。KG 关则 no-op。"""
        if not self.on or not player_id:
            return
        self._run("""MATCH (p:Player {id:$pid}) MERGE (a:Action {name:$act})
                     MERGE (p)-[:DID {iter:$it}]->(a)""", pid=str(player_id), act=str(action), it=it)
        if action in ("pass", "throughBall", "cross") and target_id:
            self._run("""MATCH (p:Player {id:$pid}),(t:Player {id:$tid})
                         MERGE (p)-[:PASSES_TO {iter:$it}]->(t)""",
                      pid=str(player_id), tid=str(target_id), it=it)

    def record_mark(self, defender_id, target_id, it):
        """盯防边（00§6）：第一防守者上抢某对手 → (Player)-[:MARKS {iter}]->(Player)。"""
        if not self.on or not defender_id or not target_id:
            return
        self._run("""MATCH (d:Player {id:$did}),(o:Player {id:$oid})
                     MERGE (d)-[:MARKS {iter:$it}]->(o)""",
                  did=str(defender_id), oid=str(target_id), it=it)

    def seed_passing(self, match_id, md):
        """静态传球先验（00§6 PASSES_TO {weight}）：按开局阵型位置邻近度,给同队球员对播种
        PASSES_TO {weight}(越近权重越高)。作为传球网络的结构先验, 动态传球再叠加。"""
        if not self.on:
            return
        for key in ("kickOffTeam", "secondTeam"):
            ps = [p for p in md[key].get("players", []) if p.get("currentPOS") and p["currentPOS"][0] != "NP"]
            for i, a in enumerate(ps):
                ax, ay = a["currentPOS"][0], a["currentPOS"][1]
                # 取最近的 3 名队友作为主要传球点
                dists = sorted(((abs(b["currentPOS"][0] - ax) + abs(b["currentPOS"][1] - ay), b)
                                for b in ps if b is not a), key=lambda x: x[0])[:3]
                for d, b in dists:
                    w = round(1.0 / (1.0 + d / 200.0), 3)   # 距离→权重(越近越高)
                    self._run("""MATCH (p:Player {id:$pid}),(t:Player {id:$tid})
                                 MERGE (p)-[r:PASSES_TO]->(t) ON CREATE SET r.weight=$w
                                 ON MATCH SET r.weight=CASE WHEN r.weight IS NULL THEN $w ELSE r.weight END""",
                              pid=str(a.get("playerID")), tid=str(b.get("playerID")), w=w)

    def finalize(self, match_id, score_home, score_away):
        if not self.on:
            return
        self._run("MATCH (m:Match {id:$id}) SET m.status='completed', m.score=$sc",
                  id=match_id, sc=f"{score_home}-{score_away}")

    def close(self):
        if self.driver:
            try: self.driver.close()
            except Exception: pass
