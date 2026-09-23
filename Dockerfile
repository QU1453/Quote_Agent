# syntax=docker/dockerfile:1
# Quote Agent —— 单服务镜像（FastAPI + LangGraph + 前端页面）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8623

WORKDIR /app

# 先装 Python 依赖（利用镜像层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 仅拷贝运行所需文件；.env 等密钥文件不进镜像（见 .dockerignore）
COPY server.py config.py ./
COPY core ./core
COPY agents ./agents
COPY tools ./tools
COPY memory ./memory
COPY ui ./ui

EXPOSE 8623

CMD ["python", "server.py"]
