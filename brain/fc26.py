"""EA FC26 球员评分整合（comprehensive 升级的核心）。

来源: data/raw/fc26/ea_fc26_players.csv(16228 球员, 50+ 字段)。EA 球探校准的【内在能力】,
比 API 比赛统计(产量/噪声)稠密、完整、一致。两用途:
1. fc_to_skill(): FIFA 6大+子项 → 引擎 player.skill(物理层变准)。
2. fc_to_profile(): FIFA 属性 → SFT 决策画像(比 shoot90/pass90 细且全覆盖)。

匹配: WC 真实阵容(squad_<team>) 按 (国籍 + 姓名归一) 查 FIFA。实测 Spain/Ger/Fra 26/26。
"""
import csv, os, re, unicodedata
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(__file__), "..")
CSV = os.path.join(ROOT, "data", "raw", "fc26", "ea_fc26_players.csv")

# Mac/WC 队名 → FIFA nationality 名
NAT_ALIAS = {
    "Netherlands": "Holland", "Cote d'Ivoire": "Côte d'Ivoire", "Czechia": "Czech Republic",
    "DR Congo": "Congo DR", "Curacao": "Curaçao", "Cape Verde": "Cape Verde Islands",
    "Korea Republic": "Korea Republic", "Bosnia and Herzegovina": "Bosnia and Herzegovina",
}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("-", " ").replace(".", " ")          # 韩/日连字符 + 缩写点 → 空格
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s)).strip()

_STOP = {"de", "da", "do", "dos", "van", "der", "den", "el", "al", "le", "la"}  # 常见连接词, 不算独立 token

def _tokens(s):
    return set(t for t in _norm(s).split() if len(t) > 1 and t not in _STOP)


def _f(v, d=0.0):
    try: return float(v)
    except (TypeError, ValueError): return d


class FC26:
    def __init__(self, path=CSV):
        self.rows = list(csv.DictReader(open(path)))
        self.by_nat = defaultdict(list)   # nationality → [(toks, full_norm, last_tok, row)]
        for r in self.rows:
            full = _norm(r.get("commonName") or f"{r.get('firstName','')} {r.get('lastName','')}")
            toks = _tokens(r.get("commonName") or f"{r.get('firstName','')} {r.get('lastName','')}")
            lastn = _norm(r.get("lastName", "")); lastt = lastn.split()[-1] if lastn else ""
            self.by_nat[r["nationality"]].append((toks, full, lastt, r))

    def match(self, name, wc_team_name):
        """按 (国籍 + 姓名 token 集合) 匹配。优先级: 全等 > 子集(Son⊆Son Heung Min) > ≥2共享 > 姓+名首字母。"""
        nat = NAT_ALIAS.get(wc_team_name, wc_team_name)
        cand = self.by_nat.get(nat, [])
        pt = _tokens(name); pn = _norm(name)
        if not pt or not cand:
            return None
        plast = pn.split()[-1] if pn else ""
        sub_hit = two_hit = last_hit = None
        for toks, full, lastt, r in cand:
            if not toks:
                continue
            if toks == pt or full == pn:
                return r                              # 全等
            if toks <= pt or pt <= toks:
                sub_hit = sub_hit or r                # 子集(单名 commonName)
            elif len(toks & pt) >= 2:
                two_hit = two_hit or r                # ≥2 token 共享
            elif lastt and lastt == plast and (toks & pt):
                last_hit = last_hit or r              # 姓相同 + 至少1其他词共享
        return sub_hit or two_hit or last_hit

    @staticmethod
    def to_skill(r):
        """FIFA → 引擎 player.skill(0-99 字符串, 和现有 skill 字段对齐)。"""
        avg = lambda *ks: round(sum(_f(r.get(k)) for k in ks) / len(ks))
        is_gk = (r.get("position") == "GK")
        return {
            "passing": str(avg("pas", "shortPassing", "longPassing", "vision")),
            "shooting": str(avg("sho", "finishing", "shotPower")),
            "tackling": str(avg("def", "standingTackle", "interceptions")),
            "saving": str(avg("gkReflexes", "gkHandling", "gkDiving") if is_gk else int(_f(r.get("def")) * 0.3)),
            "agility": str(avg("agility", "acceleration", "balance")),
            "strength": str(avg("phy", "strength")),
            "penalty_taking": str(int(_f(r.get("penalties")))),
            "perception": str(avg("composure", "reactions", "vision")),
            "control": str(avg("dri", "ballControl", "dribbling")),
            "jumping": str(int(_f(r.get("jumping")))),
        }

    @staticmethod
    def to_profile(r):
        """FIFA → SFT 决策画像(0-99 内在能力 + playStyles + position)。比 shoot90/pass90 全且稳。"""
        return {
            "name": r.get("commonName") or f"{r.get('firstName','')} {r.get('lastName','')}".strip(),
            "overall": int(_f(r.get("overallRating"))),
            "pos_fc": r.get("position"), "pos_type": r.get("positionType"),
            "pace": int(_f(r.get("pac"))), "shooting": int(_f(r.get("sho"))),
            "passing": int(_f(r.get("pas"))), "dribbling": int(_f(r.get("dri"))),
            "defending": int(_f(r.get("def"))), "physical": int(_f(r.get("phy"))),
            "finishing": int(_f(r.get("finishing"))), "vision": int(_f(r.get("vision"))),
            "interceptions": int(_f(r.get("interceptions"))),
            "tackle": int(_f(r.get("standingTackle"))),
            "play_styles": [s.strip() for s in (r.get("playStyles") or "").split(",") if s.strip()][:5],
        }


_INST = None
def get():
    global _INST
    if _INST is None:
        _INST = FC26()
    return _INST
