"""PossessionDirector —— 控球率反馈控制（plan 01 §3.2）。

把"控球目标"转成每队 retention_bias∈[0,1] + press_intensity，注入两队决策；用实测控球反馈微调（PI）。
不改物理：bias 只调决策倾向（gemma 跑位 support 多/少、brain 持球 pass vs clear、无球方逼抢强弱）。
"""

def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


class PossessionDirector:
    def __init__(self, target_home: float = 0.5, kp: float = 0.6):
        self.target = _clip(target_home)
        self.kp = kp
        # retention_bias：保球倾向（高→短传/控制/支援跑动）
        self.bias_home = self.target
        self.bias_away = 1.0 - self.target

    def observe(self, measured_home: float):
        """用实测控球率反馈微调（PI 的 P 项，夹在 [0,1]）。"""
        err = self.target - measured_home
        self.bias_home = _clip(self.bias_home + self.kp * err)
        self.bias_away = _clip(self.bias_away - self.kp * err)

    def biases(self, side: str) -> dict:
        """注入决策的偏置：retention（保球）+ press（无球逼抢，= 对手保球的反向）。"""
        if side == "home":
            return {"retention_bias": round(self.bias_home, 3),
                    "press_intensity": round(1.0 - self.bias_away, 3)}
        return {"retention_bias": round(self.bias_away, 3),
                "press_intensity": round(1.0 - self.bias_home, 3)}
