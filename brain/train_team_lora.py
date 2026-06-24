"""train_team_lora —— 在 Box B 训 per-team off-ball LoRA (gemma-4-E2B-it)。plan 04 §7/§10。

用法(在 Box B):
  python brain/train_team_lora.py --base google/gemma-4-E2B-it --data data/sft/Spain.jsonl --out lora/Spain
数据 = build_sft.py 的 chat 格式 jsonl(messages: system/user/assistant)。
HF_TOKEN 从环境/.env(huggingface-cli login)。bf16 LoRA, 冻底座只训适配器(~几 MB 产出)。
"""
import argparse
import torch
from datasets import load_dataset
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig
from trl import SFTTrainer, SFTConfig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="HF id 或本地路径, 如 google/gemma-4-E2B-it")
    ap.add_argument("--data", required=True, help="data/sft/<Team>.jsonl")
    ap.add_argument("--out", required=True, help="lora/<Team>")
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--bsz", type=int, default=8)
    ap.add_argument("--maxlen", type=int, default=1024)
    a = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(a.base)
    model = AutoModelForCausalLM.from_pretrained(a.base, torch_dtype=torch.bfloat16, device_map="auto")
    ds = load_dataset("json", data_files=a.data, split="train")  # 含 "messages" → TRL 自动套 chat template

    # gemma4 是多模态: Gemma4ClippableLinear 继承 nn.Module(非 nn.Linear) → PEFT 类型检查崩。
    # 修法(官方): target_modules="all-linear" 递归安全包裹嵌套 Linear + exclude 多模态塔。
    # 需 peft>=0.19 + torch>=2.7(Box B: peft 0.19.2.dev0 / torch 2.12, 满足)。bf16 LoRA(内存够, 不用 QLoRA)。
    peft = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05,
                      target_modules="all-linear",
                      exclude_modules=["vision_tower", "audio_tower"],
                      bias="none", task_type="CAUSAL_LM")
    cfg = SFTConfig(output_dir=a.out, num_train_epochs=a.epochs, per_device_train_batch_size=a.bsz,
                    gradient_accumulation_steps=2, learning_rate=2e-4, bf16=True, logging_steps=20,
                    max_length=a.maxlen, save_strategy="epoch", packing=False, report_to=[])
    trainer = SFTTrainer(model=model, train_dataset=ds, peft_config=peft, args=cfg, processing_class=tok)
    trainer.train()
    trainer.save_model(a.out)
    print(f"[OK] LoRA saved → {a.out}")


if __name__ == "__main__":
    main()
