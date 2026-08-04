import os
from pathlib import Path

from dotenv import load_dotenv

# 无论从哪个目录启动，都加载本文件旁边的 .env
load_dotenv(Path(__file__).parent / ".env")

# DeepSeek 主力模型
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE = os.getenv("DEEPSEEK_BASE", "https://api.deepseek.com/v1")

# 智谱 GLM-4V-Flash（免费视觉）
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")

# MinerU 在线 API（可选，文档 OCR）
MINERU_API_KEY = os.getenv("MINERU_API_KEY", "")

# 客户端连接本代理用的 key（自己随便定一个，留空则不校验）
PROXY_API_KEY = os.getenv("PROXY_API_KEY", "")

# Agent 发相对路径时的解析根目录（可选，一般填你的常用工作目录）
WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "")
