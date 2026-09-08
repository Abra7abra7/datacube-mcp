# DATAcube MCP + HTTP — multi-service container
FROM python:3.11-slim

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY core.py server.py api.py test_server.py ./
COPY datacube-mcp.json ./
RUN mkdir -p /data

# Default: HTTP API on port 8000
# Override with CMD ["python", "server.py"] for MCP stdio
# Or CMD ["python", "server.py", "--ingest"] for ingest

EXPOSE 8000

ENV DATACUBE_HOST=0.0.0.0
ENV DATACUBE_PORT=8000
ENV DATACUBE_API_KEY=""
ENV DATACUBE_CORS_ORIGINS=""
ENV DATACUBE_RATE_LIMIT="100/minute"

VOLUME ["/data"]
CMD ["python", "api.py"]