FROM python:3.11-slim

WORKDIR /app

# 锁定版本 + 新增 aiosqlite；服务器只跑代理，不装 streamlit/pandas
RUN pip install --no-cache-dir \
    fastapi==0.115.6 \
    "uvicorn[standard]==0.34.0" \
    httpx==0.28.1 \
    python-dotenv==1.0.1 \
    pydantic==2.10.4 \
    aiosqlite==0.20.0

COPY config.py db.py vision.py ocr.py proxy.py ./
COPY dashboard.html static/echarts.min.js ./
COPY static ./static

ENV DB_PATH=/data/deepseek_eyes.db
VOLUME /data

EXPOSE 8000
CMD ["uvicorn", "proxy:app", "--host", "0.0.0.0", "--port", "8000"]
