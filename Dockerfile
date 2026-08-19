# Policy Poster — single-container build (frontend + API)
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app/backend
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev && \
    uv run python -m spacy download en_core_web_sm
COPY --from=frontend /app/frontend/dist /app/frontend/dist
ENV POLICY_POSTER_DIST=/app/frontend/dist \
    POLICY_POSTER_DATA=/data
VOLUME ["/data"]
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "policy_poster.api.serve:app", \
     "--host", "0.0.0.0", "--port", "8000"]
