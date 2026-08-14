"""DeepSeek Eyes 配置：从 .env 加载，提供单例访问、密钥掩码与计费单价表。

- 保留 module-level 大写常量的访问方式，兼容旧代码（`config.DEEPSEEK_API_KEY`）。
- `reload()` 重新加载 .env 并刷新全局；面板保存后热生效。
- `mask_secret()` 用于 UI 回显，避免把真实密钥打进 HTML。
"""
import os
from pathlib import Path
from threading import RLock

from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent / ".env"

# ── 模型计费单价（人民币 元 / 百万 tokens）────────────────────────────
# 官方调整价格时改这里即可。key 为模型 ID 前缀，按「最长前缀」匹配。
# deepseek-v4-flash（即官方 deepseek-chat）：
#   输入未命中 ¥1.0 / 缓存命中 ¥0.02 / 输出 ¥2.0
# deepseek-v4-pro 约为 flash 的三倍。
PRICING = {
    "deepseek-v4-pro":    {"input": 3.0, "output": 6.0, "cache_hit": 0.06},
    "deepseek-v4-flash":  {"input": 1.0, "output": 2.0, "cache_hit": 0.02},
    "deepseek-chat":      {"input": 1.0, "output": 2.0, "cache_hit": 0.02},
    "deepseek-reasoner":  {"input": 1.0, "output": 2.0, "cache_hit": 0.02},
}
DEFAULT_PRICE = {"input": 1.0, "output": 2.0, "cache_hit": 0.02}

# 客户端模型名 → DeepSeek 真实模型名 的归一映射（可配置，新增模型在此扩展）
MODEL_MAP = {
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "deepseek-v4-pro": "deepseek-v4-pro",
}
DEFAULT_MODEL = "deepseek-v4-flash"

# 对外暴露的模型列表（/v1/models 返回）
EXPOSED_MODELS = [
    {"id": "deepseek-v4-flash", "object": "model", "owned_by": "deepseek"},
    {"id": "deepseek-v4-pro", "object": "model", "owned_by": "deepseek"},
]

# 简单令牌桶限流（防 key 泄露后余额被打爆）
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "120"))
RATE_LIMIT_WINDOW_SEC = 60.0

# 图片识别缓存保留天数（过期自动清理）
IMAGE_CACHE_TTL_DAYS = int(os.getenv("IMAGE_CACHE_TTL_DAYS", "30"))

# MinerU 轮询参数
MINERU_POLL_MAX = int(os.getenv("MINERU_POLL_MAX", "120"))
MINERU_POLL_EARLY_SEC = 5      # 前 30 秒每 5 秒查一次
MINERU_POLL_LATE_SEC = 15      # 之后每 15 秒查一次

# ── 运行时全局状态（reload 时刷新）──────────────────────────────────
_lock = RLock()

DEEPSEEK_API_KEY = ""
DEEPSEEK_BASE = ""
ZHIPU_API_KEY = ""
MINERU_API_KEY = ""
PROXY_API_KEY = ""
WORKSPACE_ROOT = ""


def reload() -> None:
    """从 .env 重新加载全部配置（override=True 支持面板热更新）。"""
    load_dotenv(ENV_PATH, override=True)
    global DEEPSEEK_API_KEY, DEEPSEEK_BASE, ZHIPU_API_KEY
    global MINERU_API_KEY, PROXY_API_KEY, WORKSPACE_ROOT
    with _lock:
        DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
        DEEPSEEK_BASE = os.getenv("DEEPSEEK_BASE", "https://api.deepseek.com/v1")
        ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
        MINERU_API_KEY = os.getenv("MINERU_API_KEY", "")
        PROXY_API_KEY = os.getenv("PROXY_API_KEY", "")
        WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "")


def mask_secret(value: str, keep: int = 4) -> str:
    """把密钥脱敏为 `sk-****abcd` 形式，未配置返回空串。"""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}****{value[-keep:]}"


def is_masked(value: str) -> bool:
    """判断一个输入值是否为掩码形式（含 `****`），用于面板「留空=不修改」。"""
    return "****" in (value or "")


def pricing_for(model: str) -> dict:
    """按最长前缀匹配模型单价，未命中返回默认价。"""
    best, best_len = None, -1
    for prefix, price in PRICING.items():
        if model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = price, len(prefix)
    return best if best is not None else DEFAULT_PRICE


def normalize_model(client_model: str) -> str:
    """把客户端传来的模型名归一为 DeepSeek 真实模型名。"""
    if not client_model:
        return DEFAULT_MODEL
    return MODEL_MAP.get(client_model, DEFAULT_MODEL)


reload()
