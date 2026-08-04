"""DeepSeek Eyes 面板：streamlit run dashboard.py"""
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

DB = Path(__file__).parent / "deepseek_eyes.db"

# deepseek-chat (v4-flash) 输入单价（元 / 百万 tokens），官方调整时改这里
PRICE_MISS = 1.0   # 缓存未命中
PRICE_HIT = 0.02   # 缓存命中（1/50 价格）

st.set_page_config(page_title="DeepSeek Eyes 面板", layout="wide")
st.title("👁 DeepSeek Eyes · 缓存与用量面板")

if not DB.exists():
    st.warning("还没有数据，先跑几个请求吧")
    st.stop()

conn = sqlite3.connect(DB)
df = pd.read_sql_query("SELECT * FROM requests WHERE model != 'mineru-ocr'", conn)
ocr_count = pd.read_sql_query(
    "SELECT COUNT(*) c FROM requests WHERE model='mineru-ocr'", conn
).iloc[0]["c"]
conn.close()

if df.empty:
    st.warning("还没有对话请求记录")
    st.stop()

df["dt"] = pd.to_datetime(df["ts"], unit="s")
now = datetime.now()
month_start = pd.Timestamp(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
today_start = pd.Timestamp(now.replace(hour=0, minute=0, second=0, microsecond=0))


def agg(d: pd.DataFrame):
    hit, miss = int(d["cache_hit"].sum()), int(d["cache_miss"].sum())
    rate = hit / (hit + miss) * 100 if hit + miss else 0.0
    saved = hit / 1e6 * (PRICE_MISS - PRICE_HIT)  # 命中部分相对未命中省下的钱
    return rate, saved


# 三个口径并排，避免"每月 1 号命中率跳水"的误解
for col, (label, d) in zip(
    st.columns(3),
    [("今天", df[df["dt"] >= today_start]),
     ("本月", df[df["dt"] >= month_start]),
     ("累计", df)],
):
    rate, saved = agg(d)
    col.metric(
        f"{label}缓存命中率",
        f"{rate:.1f}%",
        f"{len(d)} 次请求 · 缓存省 ¥{saved:.2f}",
    )

st.caption(
    f"图片识别 {int(df['had_image'].sum())} 次（GLM-4V-Flash 免费）· "
    f"MinerU 文档解析 {ocr_count} 次 · "
    f"输入总量 {int(df['cache_hit'].sum() + df['cache_miss'].sum()):,} tokens"
)

st.subheader("每日缓存命中率")
daily = df.set_index("dt").resample("D").agg(
    hit=("cache_hit", "sum"), miss=("cache_miss", "sum")
)
daily["命中率 %"] = (daily["hit"] / (daily["hit"] + daily["miss"]) * 100).fillna(0)
st.line_chart(daily["命中率 %"])
