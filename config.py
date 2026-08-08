import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent / ".env"


def reload():
    """从 .env 重新加载全部配置（override=True 支持面板热更新）"""
    load_dotenv(ENV_PATH, override=True)
    global DEEPSEEK_API_KEY, DEEPSEEK_BASE, ZHIPU_API_KEY
    global MINERU_API_KEY, PROXY_API_KEY, WORKSPACE_ROOT
    # DeepSeek 主力模型
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE = os.getenv("DEEPSEEK_BASE", "https://api.deepseek.com/v1")
    # 智谱 GLM-4V-Flash（免费视觉）
    ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")
    # MinerU 在线 API（可选，文档 OCR）
    MINERU_API_KEY = os.getenv("MINERU_API_KEY", "")
    # 客户端连接本代理用的 key（留空则不校验）
    PROXY_API_KEY = os.getenv("PROXY_API_KEY", "")
    # Agent 发相对路径时的解析根目录（可选）
    WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "")


reload()
