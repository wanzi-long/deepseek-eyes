FROM python:3.11-slim

WORKDIR /app

# 服务器只跑代理，不装 streamlit/pandas（面板在本机跑，读同一个 db 文件即可）
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" httpx python-dotenv pydantic

COPY config.py db.py vision.py ocr.py proxy.py ./

ENV DB_PATH=/data/deepseek_eyes.db
VOLUME /data

EXPOSE 8000
CMD ["uvicorn", "proxy:app", "--host", "0.0.0.0", "--port", "8000"]
