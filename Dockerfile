# NOREN：Cloud Run 用のイメージ（フロントエンドをビルドし、FastAPI から配信する 1 サービス構成）

# 1) フロントエンドのビルド
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# 2) バックエンド
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
COPY --from=web /web/dist /app/frontend/dist
ENV PATH="/app/backend/.venv/bin:$PATH"
# Cloud Run は PORT を渡す。WebSocket を長く保つため ping を送る
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --ws-ping-interval 20 --proxy-headers --forwarded-allow-ips='*'"]
