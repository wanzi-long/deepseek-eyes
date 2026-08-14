"""DeepSeek Eyes 启动器：自动探测可用端口（默认 8000 起，被占自动顺延）
用法：python run.py                  # 本机使用（绑 127.0.0.1）
      set EYES_PORT=9000 && python run.py  # 指定起始端口（Windows）
      EYES_HOST=0.0.0.0 python run.py      # 服务器模式：允许外部机器连接
"""
import os
import secrets
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

# bat 启动脚本里已 chcp 65001（UTF-8 控制台），这里同步用 UTF-8 输出，
# 保证中文提示不乱码。注意：print 里不要用 emoji（Windows 控制台不稳定）。
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

load_dotenv(Path(__file__).parent / ".env")


def port_in_use(port: int) -> bool:
    """有服务在监听 = 被占用（connect 检测比 bind 检测更可靠）"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_free_port(start: int, end: int | None = None) -> int:
    end = end or start + 200  # 探测范围扩大到 200 个，多实例/长期占用也不易撞死
    for port in range(start, end):
        if not port_in_use(port):
            return port
        if (port - start) % 50 == 0 and port != start:
            print(f"  [提示] 已连续尝试 {port - start} 个端口仍被占用...")
    raise SystemExit(f"错误：{start}-{end - 1} 端口全被占用，请释放后再试")


def auto_open_panel(url: str):
    """服务就绪后自动拉起浏览器打开监控面板（延迟 2 秒，等 uvicorn 起来）。"""
    import time
    time.sleep(2)
    try:
        webbrowser.open(url)
    except Exception as e:  # noqa: BLE001
        print(f"  [提示] 自动打开面板失败: {e}，请手动访问 {url}")


ENV_PATH = Path(__file__).parent / ".env"


def env_value(key: str) -> str:
    """读取 .env 中某个键的值（不存在返回空串）。"""
    if not ENV_PATH.exists():
        return ""
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip()
    return ""


def first_run_wizard():
    """首次运行向导：检测密钥配置，缺失则交互式引导填写。

    触发条件：.env 不存在，或 DEEPSEEK_API_KEY 为空。
    填完写入 .env 并 reload，后续启动不再询问。
    """
    deepseek_key = env_value("DEEPSEEK_API_KEY")
    if deepseek_key:
        return  # 已配置，跳过向导

    print()
    print("=" * 56)
    print("   DeepSeek Eyes · 首次运行配置向导")
    print("=" * 56)
    print()
    print("  检测到尚未配置密钥。请按提示填写（回车=跳过可选项）：")
    print()

    # 1. DeepSeek API Key（必填）
    while True:
        dk = input("  1. DeepSeek API Key (必填，platform.deepseek.com): ").strip()
        if dk:
            break
        print("     [注意] 这个是必填项，不能为空。")

    # 2. 智谱 API Key（GLM-4V 视觉，必填但可后续补）
    zk = input("  2. 智谱 API Key (GLM-4V 视觉，open.bigmodel.cn，回车可跳过): ").strip()

    # 3. MinerU API Key（可选，文档 OCR）
    mk = input("  3. MinerU API Key (可选，文档 OCR，回车跳过): ").strip()

    # 4. 代理访问密钥（自动生成）
    pk = input("  4. 代理访问密钥 (回车=自动生成随机密钥): ").strip()
    if not pk:
        pk = secrets.token_hex(16)
        print(f"     已自动生成代理密钥: {pk}")

    # 写入 .env：保留已有其他行，覆盖这 4 个密钥键
    updates = {
        "DEEPSEEK_API_KEY": dk,
        "ZHIPU_API_KEY": zk,
        "MINERU_API_KEY": mk,
        "PROXY_API_KEY": pk,
    }
    lines = []
    written = set()
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in updates:
                    lines.append(f"{k}={updates[k]}")
                    written.add(k)
                    continue  # 覆盖旧值
            lines.append(line)
    # 追加本次新填、但 .env 里原本没有的键
    for k, v in updates.items():
        if k not in written:
            lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    load_dotenv(ENV_PATH, override=True)

    print()
    print("  [OK] 密钥已保存到 .env，正在启动...")
    print()


if __name__ == "__main__":
    # 首次运行向导：检测密钥，缺失则交互式引导填写
    first_run_wizard()

    # 注意：不用通用名 PORT（很多平台会注入该变量），用 EYES_PORT / EYES_HOST
    host = os.getenv("EYES_HOST", "127.0.0.1")  # 服务器上设 0.0.0.0 允许外部访问
    start_port = int(os.getenv("EYES_PORT", "8000"))
    port = find_free_port(start_port)
    if port != start_port:
        print(f"  [提示] {start_port} 被占，已自动切换到 {port}")
    print()
    print("  [OK] DeepSeek Eyes 已启动")
    print(f"  [OK] Agent 接入地址: http://{host}:{port}/v1")
    print(f"  [OK] 监控端口： http://{host}:{port}")
    if host == "0.0.0.0":
        print("  [提示] 对外监听模式：请确认 PROXY_API_KEY 足够复杂，且已配防火墙/HTTPS")
    print()
    # 服务就绪后自动拉起监控面板（后台线程，不阻塞启动）
    threading.Thread(
        target=auto_open_panel,
        args=(f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}",),
        daemon=True,
    ).start()
    uvicorn.run("proxy:app", host=host, port=port, log_level="info")
