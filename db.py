import os
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).parent / "deepseek_eyes.db"))
_lock = threading.Lock()


def _get():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with _lock, _get() as conn:
        conn.execute(
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
        # 图片识别结果缓存：同一图片永远返回同一文本，保住 DeepSeek 前缀缓存
        conn.execute(
            """CREATE TABLE IF NOT EXISTS image_cache(
                img_hash TEXT PRIMARY KEY,
                result TEXT,
                ts REAL
            )"""
        )
        conn.commit()


def log_request(model: str, usage: dict, had_image: bool, ocr_used: bool, latency_ms: int):
    with _lock, _get() as conn:
        conn.execute(
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
        conn.commit()


def query(sql: str, args=()):
    with _lock, _get() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def get_image_cache(img_hash: str):
    with _lock, _get() as conn:
        row = conn.execute(
            "SELECT result FROM image_cache WHERE img_hash=?", (img_hash,)
        ).fetchone()
        return row["result"] if row else None


def set_image_cache(img_hash: str, result: str):
    with _lock, _get() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO image_cache(img_hash,result,ts) VALUES(?,?,?)",
            (img_hash, result, time.time()),
        )
        conn.commit()
