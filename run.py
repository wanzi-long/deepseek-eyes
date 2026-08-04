"""DeepSeek Eyes 启动器：自动探测可用端口（默认 8000 起，被占自动顺延）
用法：python run.py                  # 本机使用（绑 127.0.0.1）
      set EYES_PORT=9000 && python run.py  # 指定起始端口（Windows）
      EYES_HOST=0.0.0.0 python run.py      # 服务器模式：允许外部机器连接
"""
import os
import socket
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def port_in_use(port: int) -> bool:
    """有服务在监听 = 被占用（connect 检测比 bind 检测更可靠）"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_free_port(start: int, end: int | None = None) -> int:
    end = end or start + 20
    for port in range(start, end):
        if not port_in_use(port):
            return port
        print(f"  [端口] {port} 被占用，尝试下一个...")
    raise SystemExit(f"错误：{start}-{end - 1} 端口全被占用，请释放后再试")


if __name__ == "__main__":
    # 注意：不用通用名 PORT（很多平台会注入该变量），用 EYES_PORT / EYES_HOST
    host = os.getenv("EYES_HOST", "127.0.0.1")  # 服务器上设 0.0.0.0 允许外部访问
    start_port = int(os.getenv("EYES_PORT", "8000"))
    port = find_free_port(start_port)
    if port != start_port:
        print(f"  [提示] {start_port} 被占，已自动切换到 {port}")
    print()
    print("  [OK] DeepSeek Eyes 已启动")
    print(f"  [OK] Agent 接入地址: http://{host}:{port}/v1")
    if host == "0.0.0.0":
        print("  [提示] 对外监听模式：请确认 PROXY_API_KEY 足够复杂，且已配防火墙/HTTPS")
    print()
    uvicorn.run("proxy:app", host=host, port=port, log_level="info")
