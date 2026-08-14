"""DeepSeek Eyes 日志系统：异步写 + 按天切片 + 内存环形缓冲。

设计目标（用户需求 A+B+C）：
- 请求日志 / 错误日志 / 系统日志统一收集
- 异步队列落盘，不阻塞请求主流程
- 落盘按天切片到 logs/eyes-YYYY-MM-DD.log
- 内存保留最近 N 条，供 UI 实时读取（无需读磁盘）
"""
import asyncio
import logging
import os
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, Optional

LOG_DIR = Path(os.getenv("EYES_LOG_DIR", Path(__file__).parent / "logs"))
MAX_MEMORY_LINES = 2000  # 内存环形缓冲上限

# 级别 → 颜色/标签（前端渲染用）
_LEVEL_LABEL = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warn",
    "ERROR": "error",
}

class LogBuffer:
    """内存环形缓冲：保存最近 N 条结构化日志，供 UI 实时轮询。"""

    def __init__(self, maxlen: int = MAX_MEMORY_LINES):
        self._lines: Deque[dict] = deque(maxlen=maxlen)
        self._seq = 0
        self._lock = asyncio.Lock()

    async def append(self, level: str, message: str, source: str = ""):
        self._seq += 1
        entry = {
            "seq": self._seq,
            "ts": time_now(),
            "level": level.lower(),
            "source": source,
            "message": message,
        }
        async with self._lock:
            self._lines.append(entry)
        return entry

    async def snapshot(self, after_seq: int = 0, limit: int = 500):
        """返回 seq > after_seq 的日志（增量），或全量（after_seq=0）。"""
        async with self._lock:
            lines = list(self._lines)
        if after_seq > 0:
            lines = [l for l in lines if l["seq"] > after_seq]
        return lines[-limit:]

    async def tail(self, limit: int = 200):
        async with self._lock:
            lines = list(self._lines)
        return lines[-limit:]


def time_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class AsyncFileWriter:
    """后台任务：把内存缓冲里的日志异步写盘，按天切片。"""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._current_date = ""

    def _log_path(self) -> Path:
        return LOG_DIR / f"eyes-{datetime.now().strftime('%Y-%m-%d')}.log"

    def start(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def enqueue(self, level: str, message: str, source: str = ""):
        """入队即可，不阻塞调用方。队列满时丢弃（日志不拖垮服务）。"""
        try:
            self._queue.put_nowait((level, message, source))
        except asyncio.QueueFull:
            pass

    async def _run(self):
        while True:
            level, message, source = await self._queue.get()
            try:
                self._write_line(level, message, source)
            except Exception:  # noqa: BLE001 写盘失败不致命
                print(f"[logger] write failed: {level} {message}", file=sys.stderr)
            finally:
                self._queue.task_done()

    def _write_line(self, level: str, message: str, source: str):
        date = datetime.now().strftime("%Y-%m-%d")
        if date != self._current_date:
            self._current_date = date  # 跨天自动切换新文件
        path = self._log_path()
        src = f"[{source}] " if source else ""
        line = f"{time_now()} {level.upper():8s} {src}{message}\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


# 单例
buffer = LogBuffer()
writer = AsyncFileWriter()


async def log(level: str, message: str, source: str = ""):
    """统一入口：写内存缓冲 + 入异步队列落盘。"""
    await buffer.append(level, message, source)
    await writer.enqueue(level, message, source)


async def info(message: str, source: str = ""):
    await log("INFO", message, source)


async def warn(message: str, source: str = ""):
    await log("WARNING", message, source)


async def error(message: str, source: str = ""):
    await log("ERROR", message, source)


def read_history_file(date: str, limit: int = 500) -> list[str]:
    """按日期读历史日志文件（date 形如 2026-08-14），返回最近 limit 行。"""
    path = LOG_DIR / f"eyes-{date}.log"
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        return [l.rstrip("\n") for l in lines[-limit:]]
    except Exception:  # noqa: BLE001
        return []


def list_log_dates() -> list[str]:
    """列出所有历史日志日期（供 UI 下拉选择）。"""
    if not LOG_DIR.exists():
        return []
    dates = []
    for p in LOG_DIR.glob("eyes-*.log"):
        name = p.stem.replace("eyes-", "")
        if len(name) == 10:  # YYYY-MM-DD
            dates.append(name)
    return sorted(dates, reverse=True)


def init_file_logging():
    """给标准 logging 也挂上文件 handler（捕获 uvicorn 等第三方库日志）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        handler = logging.FileHandler(
            LOG_DIR / f"eyes-{datetime.now().strftime('%Y-%m-%d')}.log",
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        root.addHandler(handler)
        root.setLevel(logging.INFO)
