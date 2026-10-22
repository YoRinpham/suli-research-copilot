FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
ENV NEXT_TELEMETRY_DISABLED=1
ENV NEXT_PUBLIC_API_BASE_URL=""
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production \
    STORAGE_ROOT=/mnt/workspace/suli/pdfs \
    M5_STORAGE_ROOT=/mnt/workspace/suli/m5 \
    MPLCONFIGDIR=/tmp/suli-matplotlib
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=frontend-build /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY backend/ /app/backend/
RUN pip install --no-cache-dir /app/backend
COPY --from=frontend-build /build/frontend/.next/standalone/ /app/frontend/
COPY --from=frontend-build /build/frontend/.next/static/ /app/frontend/.next/static/
COPY --from=frontend-build /build/frontend/public/ /app/frontend/public/
COPY deploy/ /app/deploy/
EXPOSE 7860
CMD ["python", "/app/deploy/bootstrap.py"]
