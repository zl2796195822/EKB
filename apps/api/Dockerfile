# EKB API · 生产镜像（Python 3.11 + 离线依赖 + SQLite/PostgreSQL 双兼容）
# 构建注意：
#  docker build --platform linux/amd64 -t ekb/ekb-api:latest .
#  docker save ekb/ekb-api:latest | gzip > ekb-api-amd64.tar.gz
#  远程：
#  docker load < ekb-api-amd64.tar.gz
#  docker run -d --name ekb-api --restart unless-stopped \
#    -p 127.0.0.1:8000:8000 \
#    -v /opt/ekb/data:/data \
#    --env-file /opt/ekb/api/.env \
#    ekb/ekb-api:latest

FROM python:3.11-slim-bookworm AS runtime

WORKDIR /app

# 系统依赖（tiktoken 需要 gcc / libffi / libssl）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gcc libffi-dev libssl-dev pkg-config make \
    && rm -rf /var/lib/apt/lists/*

# Python 层缓存：先放 pyproject 安装依赖
# 注意：不要在 buildx 构建阶段强依赖国内 PyPI 镜像——跨境构建时 TLS 握手常抛 SSLEOFError，
# 导致 pip install build-deps 失败。生产镜像内安装默认用官方 PyPI；
# 若服务器侧需要国内源，由 entrypoint / runtime 通过 PIP_INDEX_URL 环境变量覆盖。
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
 && pip install --no-cache-dir --retries 3 --timeout 60 -e ".[prod]" \
 || pip install --no-cache-dir --retries 3 --timeout 60 -e .

# The cl100k_base ranks are fetched by tiktoken upstream on first use.  Warm
# them while building the image so document ingestion never depends on egress.
ENV TIKTOKEN_CACHE_DIR=/app/.cache/tiktoken
RUN mkdir -p "$TIKTOKEN_CACHE_DIR" \
 && python3 -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")'

# 应用代码 + 迁移 + entrypoint + admin 脚本
COPY ekb_api/ ./ekb_api/
# 全局代理补丁：让 urllib（阻塞式 LLM/embedding 调用）默认走 mihomo 出口。
# 仅当运行时传入 HTTPS_PROXY/https_proxy 才生效，否则不影响内网直连。
COPY sitecustomize.py /usr/local/lib/python3.11/site-packages/sitecustomize.py
COPY entrypoint.sh ./entrypoint.sh
COPY ensure_admin.py ./ensure_admin.py
COPY repair_admin_access.py ./repair_admin_access.py
RUN chmod +x /app/entrypoint.sh /app/ensure_admin.py /app/repair_admin_access.py 2>/dev/null || true

# 生产默认 env（启动时用 --env-file 覆盖，见上方示例）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EKB_ENV=production \
    EKB_API_HOST=127.0.0.1 \
    EKB_API_PORT=8000 \
    EKB_DATABASE_URL=sqlite:////data/ekb.sqlite3 \
    EKB_ALLOWED_ORIGINS=http://127.0.0.1:5173,http://localhost:5173

# 数据目录（挂载点）
RUN mkdir -p /data && chmod 700 /data 2>/dev/null || true
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=25s --retries=4 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1

# 非 root 用户
RUN useradd -m -u 1000 ekb && chown -R ekb:ekb /app /data 2>/dev/null || true
USER ekb

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn ekb_api.main:app --host \"${EKB_API_HOST:-127.0.0.1}\" --port \"${EKB_API_PORT:-8000}\" --workers 2"]
