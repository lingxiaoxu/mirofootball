"""轨迹记录器 —— 每拍把球 + 全 22 人坐标/动作写一行 jsonl（连续、可回溯、可回放）。

满足 plan 01 §0.4：球员步进有界(无瞬移)、球带 z 飞行弧线，逐拍全量记录 → 任一球员路径/
任一时刻全场都能重建。jsonl = 每拍全量轨迹(回放)；Neo4j State/DID = 关键拍语义(用 iter 引用)。
"""
import json
import os
from typing import Optional


def _players(team: dict, side: str) -> list:
    out = []
    for p in team.get("players", []):
        out.append({
            "id": p.get("playerID"),
            "team": side,
            "pos": p.get("currentPOS"),
            "action": p.get("action"),
            "hasBall": p.get("hasBall", False),
        })
    return out


class TrajectoryWriter:
    """每场一个 trajectory.jsonl；append_tick 每拍追加一行。"""

    def __init__(self, match_id: str, data_dir: str, iter_per_half: int = 2000):
        self.dir = os.path.join(data_dir, str(match_id))
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "trajectory.jsonl")
        self.iter_per_half = iter_per_half
        self._f = open(self.path, "w", encoding="utf-8")  # 新场覆盖
        self.n = 0

    def append_tick(self, md: dict, it: int) -> None:
        ball = md.get("ball", {})
        rec = {
            "iter": it,
            "min": round(it / max(self.iter_per_half / 45.0, 1e-9), 2),  # 折算分钟（01 §0.3）
            "ball": {
                "pos": ball.get("position"),
                "holder": ball.get("Player") or None,
                "team": ball.get("withTeam") or None,
                "in_flight": bool(ball.get("ballOverIterations")),
                "flight": ball.get("ballOverIterations") or [],
            },
            "players": _players(md.get("kickOffTeam", {}), "home") +
                       _players(md.get("secondTeam", {}), "away"),
            "events": list(md.get("iterationLog", [])),
        }
        self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.n += 1

    def close(self) -> None:
        try:
            self._f.flush(); self._f.close()
        except Exception:
            pass
