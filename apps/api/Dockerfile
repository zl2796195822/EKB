# EKB API · 生产镜像
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（tiktoken 需要 gcc）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 先复制 pyproject.toml 利用 Docker 层缓存
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[prod]" 2>/dev/null || pip install --no-cache-dir -e .

# 复制应用代码
COPY ekb_api/ ./ekb_api/

# 非 root 用户运行
RUN useradd -m -u 1000 ekb && chown -R ekb:ekb /app
USER ekb

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -f http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "ekb_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
