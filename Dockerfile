# DATAcube MCP + HTTP — multi-service container
FROM python:3.11-slim

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY core.py server.py api.py entrypoint.sh ./
COPY datacube-mcp.json ./
RUN mkdir -p /data && chmod +x entrypoint.sh

EXPOSE 8000

ENV DATACUBE_HOST=0.0.0.0
ENV DATACUBE_PORT=8000
ENV DATACUBE_DB_PATH=/data/datacube.db
ENV DATACUBE_CORS_ORIGINS=""
ENV DATACUBE_RATE_LIMIT="100/minute"

VOLUME ["/data"]

# Entrypoint: auto-seed DB if missing, then start API
ENTRYPOINT ["/app/entrypoint.sh"]