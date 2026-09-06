# ai-assistant — 生产可用多阶段镜像
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 使用非 root 用户运行，降低容器逃逸风险。
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser

WORKDIR /app

# 老 Office 格式（doc / ppt）依赖：headless LibreOffice 用于转换为现代格式。
# 仅安装 writer 与 impress 组件，并清理 apt 缓存以控制镜像体积。
RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-writer libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*

# 先安装依赖（利用层缓存，仅依赖变更时重建）。
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用源码。
COPY . .

# 数据目录（SQLite 默认存放处；使用 Postgres 时可忽略）。
RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
