# TaskMirror: 前端构建 → 单容器跑 FastAPI(静态托管 + REST/SSE)
FROM node:22-alpine AS fe
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /tm
# 保持 repo 形状: app/config.py 的 repo_root()=parents[2] 定位 frontend/dist
COPY backend/pyproject.toml backend/
COPY backend/app backend/app
RUN pip install --no-cache-dir /tm/backend
COPY --from=fe /fe/dist frontend/dist
ENV TASKBOARD_PORT=8801 TASKBOARD_HOME=/data PYTHONUTF8=1
VOLUME /data
EXPOSE 8801
CMD ["python", "-m", "app.main"]
