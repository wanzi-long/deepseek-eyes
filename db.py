"""SQLite 数据层（aiosqlite 异步，适配 uvicorn 事件循环）。

两张表：
- requests：每次请求的 token/缓存/图片/耗时明细（计费面板数据源）
- image_cache：图片识别结果哈希缓存（同图同文，保 DeepSeek 前缀缓存）

写操作全部走 `_write()` 串行化，避免 SQLite 并发写报错；
读操作各自独立连接，互不阻塞。
"""
import os
import time
from pathlib import Path

import aiosqlite

import config

DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).parent / "deepseek_eyes.db"))

# 写串行锁 + 幂等初始化标记
_write_lock = None  # 延迟创建 asyncio.Lock（绑定运行事件循环）
_initialized = False


def _lock():
    global _write_lock
    if _write_lock is None:
        import asyncio
        _write_lock = asyncio.Lock()
    return _write_lock


async def _connect():
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    return conn


async def init():
    """建表 + 清理过期图片缓存。幂等，可重复调用。"""
    global _initialized
    async with _lock():
        conn = await _connect()
        try:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS requests(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL,
                    model TEXT,
                    prompt_tokens INT DEFAULT 0,
                    completion_tokens INT DEFAULT 0,
                    cache_hit INT DEFAULT 0,
                    cache_miss INT DEFAULT 0,
                    had_image INT DEFAULT 0,
                    ocr_used INT DEFAULT 0,
                    latency_ms INT DEFAULT 0
                )"""
            )
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS image_cache(
                    img_hash TEXT PRIMARY KEY,
                    result TEXT,
                    ts REAL
                )"""
            )
            # 创建索引加速按模型/日期聚合
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts)"
            )
            await conn.commit()
        finally:
            await conn.close()
    _initialized = True
    await prune_image_cache()


async def log_request(model: str, usage: dict, had_image: bool, ocr_used: bool, latency_ms: int):
    """记录一次请求的用量明细。usage 来自上游 DeepSeek 返回。"""
    async with _lock():
        conn = await _connect()
        try:
            await conn.execute(
                "INSERT INTO requests(ts,model,prompt_tokens,completion_tokens,"
                "cache_hit,cache_miss,had_image,ocr_used,latency_ms) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    time.time(),
                    model,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    usage.get("prompt_cache_hit_tokens", 0),
                    usage.get("prompt_cache_miss_tokens", 0),
                    int(had_image),
                    int(ocr_used),
                    latency_ms,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()


async def query(sql: str, args=()):
    """只读查询，返回 list[dict]。"""
    conn = await _connect()
    try:
        cur = await conn.execute(sql, args)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def get_image_cache(img_hash: str):
    conn = await _connect()
    try:
        cur = await conn.execute(
            "SELECT result FROM image_cache WHERE img_hash=?", (img_hash,)
        )
        row = await cur.fetchone()
        return row["result"] if row else None
    finally:
        await conn.close()


async def set_image_cache(img_hash: str, result: str):
    async with _lock():
        conn = await _connect()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO image_cache(img_hash,result,ts) VALUES(?,?,?)",
                (img_hash, result, time.time()),
            )
            await conn.commit()
        finally:
            await conn.close()


async def prune_image_cache():
    """清理超过 TTL 的图片缓存，防止长期膨胀。"""
    cutoff = time.time() - config.IMAGE_CACHE_TTL_DAYS * 86400
    async with _lock():
        conn = await _connect()
        try:
            await conn.execute("DELETE FROM image_cache WHERE ts < ?", (cutoff,))
            await conn.commit()
        finally:
            await conn.close()
