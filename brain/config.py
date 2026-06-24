"""mirofootball 配置 —— 端点/模型/旋钮（默认 = 已实跑验证的本机配置）。

铁律：brain(:11434) 与认证代理(:11435) 是共享的，只读发请求，绝不 reload/重启/改 num_ctx。
两 gemma(:11436/:11437) 是 mirofootball 自有实例。详见 plan 03 §2.0 / 记忆 ops-constraints。
"""
import os

def _env(k, d): return os.environ.get(k, d)

# ── 模型端点（Ollama 原生 /api，base_url 不带 /v1）──
BRAIN_URL       = _env("BRAIN_URL", "http://localhost:11434")      # 共享 nemotron，只读
GEMMA_HOME_URL  = _env("GEMMA_HOME_URL", "http://localhost:11436") # 自有 home 实例
GEMMA_AWAY_URL  = _env("GEMMA_AWAY_URL", "http://localhost:11437") # 自有 away 实例

BRAIN_MODEL = _env("BRAIN_MODEL", "nemotron-3-super:120b")
GEMMA_MODEL = _env("GEMMA_MODEL", "gemma4:e2b-it-qat")

# ── reasoning 已定：brain 开 / gemma 关 ──
BRAIN_THINK = _env("BRAIN_THINK", "1") == "1"     # True
GEMMA_THINK = _env("GEMMA_THINK", "0") == "1"     # False

# ── 引擎 ──
ENGINE_URL = _env("ENGINE_URL", "http://localhost:7000")

# ── 速度/保真旋钮 ──
ITER_PER_HALF = int(_env("ITER_PER_HALF", "2000"))
GEMMA_EVERY   = int(_env("GEMMA_EVERY", "12"))

# ── 输出目录（轨迹 jsonl 等）──
DATA_DIR = _env("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
