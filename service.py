"""服务生命周期管理：运行开关（软停止）。

- ServiceState.running 是全局软开关：关闭后代理路由返回 503，但 UI 路由常驻。
- 仅保留代理版核心所需的最小能力。
"""


class ServiceState:
    """全局服务开关（单例）。running=False 时代理路由拒绝服务。"""

    def __init__(self):
        self.running = True

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def toggle(self) -> bool:
        self.running = not self.running
        return self.running

    def status(self) -> dict:
        return {"running": self.running}


service = ServiceState()
