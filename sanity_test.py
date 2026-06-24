"""sanity-test: 验证 per-team LoRA 真的让 gemma4 产生不同决策(按各队风格分化)。
同一局面 → base / 各队 LoRA 分别生成, 对比。在 Box B 跑(已有底座+adapter)。"""
import torch, json
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

BASE = "models/gemma4-e2b-it"
tok = AutoTokenizer.from_pretrained(BASE)
base = AutoModelForCausalLM.from_pretrained(BASE, dtype=torch.bfloat16, device_map="auto")

OFFBALL_SYS = ("You are an off-ball positioning brain for ONE football player. Pick target_zone "
               "(grid col A-F x row 1-9, e.g. C6) and posture. Reflect THIS PLAYER's real in-match "
               "tendencies and the team style. Output ONLY JSON.")
ONBALL_SYS = ("You are the on-ball decision brain for ONE football player who HAS the ball. Pick action "
              "(shoot/pass/dribble/clear) reflecting THIS PLAYER's real in-match action tendencies and "
              "field position. Output ONLY JSON.")

# 局面里不放球队风格标签 → 差异只能来自 LoRA(而非 prompt 告诉它)
PROBES = [
    ("【无球·我方持球·中场】", OFFBALL_SYS,
     {"world": {"ball_zone": "C4", "holder_team": "home"},
      "me": {"pos": "Midfielder", "shoot90": 0.6, "pass90": 38, "drib90": 1.2, "def90": 1.5, "zone": "C5"},
      "team_style": {"team": "?"}}),
    ("【无球·失球·中前场】", OFFBALL_SYS,
     {"world": {"ball_zone": "C6", "holder_team": "away"},
      "me": {"pos": "Midfielder", "shoot90": 0.6, "pass90": 38, "drib90": 1.2, "def90": 2.5, "zone": "C5"},
      "team_style": {"team": "?"}}),
    ("【持球·攻区·前锋】", ONBALL_SYS,
     {"world": {"ball_zone": "C8", "i_have_ball": True},
      "me": {"pos": "Attacker", "shoot90": 1.5, "pass90": 20, "drib90": 3.0, "def90": 0.3},
      "team_style": {"team": "?"}}),
]

def gen(m):
    outs = []
    for _, sys, user in PROBES:
        msgs = [{"role": "system", "content": sys}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]
        enc = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True,
                                      return_tensors="pt", return_dict=True)
        enc = {k: v.to(m.device) for k, v in enc.items()}
        n = enc["input_ids"].shape[1]
        with torch.no_grad():
            out = m.generate(**enc, max_new_tokens=40, do_sample=False, pad_token_id=tok.eos_token_id)
        outs.append(tok.decode(out[0][n:], skip_special_tokens=True).strip().replace("\n", " ")[:80])
    return outs

print("局面:", [p[0] for p in PROBES], flush=True)
print("=== BASE (无 LoRA) ===", flush=True)
for o in gen(base): print("   ", o, flush=True)

m = PeftModel.from_pretrained(base, "lora/Spain", adapter_name="Spain")
for t in ["France", "Norway", "Japan", "Germany", "Brazil"]:
    m.load_adapter(f"lora/{t}", adapter_name=t)
for t in ["Spain", "France", "Norway", "Japan", "Germany", "Brazil"]:
    m.set_adapter(t)
    print(f"=== {t} LoRA ===", flush=True)
    for o in gen(m): print("   ", o, flush=True)
print("[SANITY DONE]", flush=True)
