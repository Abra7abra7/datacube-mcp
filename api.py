#!/usr/bin/env python3
"""DATAcube HTTP API — FastAPI wrapper so MCP tool functions.

Spustenie:
    python api.py                        # localhost:8000
    DATACUBE_API_KEY=secret python api.py  # s API kľúčom

Endpoints:
    GET  /api/health                    → {"status": "ok"}
    GET  /api/cubes?search=             → zoznam kociek
    GET  /api/cubes/{code}/dims         → dimenzie kocky
    GET  /api/cubes/{code}/query        → dáta (?dim1=a&dim2=b&dim3=...)
    GET  /api/cubes/find?q=...          → AI fulltext
    GET  /api/status                    → stav DB
    POST /api/ingest                    → refresh (~5 min)

Bezpečnosť:
    - X-API-Key header (cez DATACUBE_API_KEY env)
    - Rate limiting: 100 req/min na IP
    - CORS: len povolené domény
    - Input validácia
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from pydantic import BaseModel, Field

from core import (
    tool_list_cubes, tool_cube_dimensions, tool_cube_query,
    tool_find_cube, tool_ingest_status, tool_ingest_run
)

# ─── Config ─────────────────────────────────────────────────────────────────

API_KEY = os.environ.get("DATACUBE_API_KEY", "")
ALLOWED_ORIGINS = os.environ.get("DATACUBE_CORS_ORIGINS", "").split(",")
RATE_LIMIT = os.environ.get("DATACUBE_RATE_LIMIT", "100/minute")

log = logging.getLogger("datacube-api")

# ─── Rate limiting ──────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)

# ─── Auth dependency ────────────────────────────────────────────────────────

async def verify_api_key(x_api_key: str = Header(None)):
    """Verify API key if configured."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return x_api_key

# ─── App ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("DATAcube API starting...")
    yield
    log.info("DATAcube API shutting down.")

app = FastAPI(
    title="DATAcube API",
    description="Štatistický úrad SR — Open Data cubes ako REST API",
    version="1.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != [""] else ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Rate limit error handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ─── Error handler ──────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str

# ─── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "datacube-api", "version": "1.0.0"}

@app.get("/api/cubes")
@limiter.limit(RATE_LIMIT)
async def list_cubes(
    request: Request,
    search: str = "",
    api_key: str = Depends(verify_api_key),
):
    """List available cubes, optionally filtered by label."""
    result = tool_list_cubes(search)
    return {"cubes": result, "count": len(result)}

@app.get("/api/cubes/{code}/dims")
@limiter.limit(RATE_LIMIT)
async def cube_dimensions(
    request: Request,
    code: str,
    api_key: str = Depends(verify_api_key),
):
    """Get dimensions and their values for a cube."""
    result = tool_cube_dimensions(code)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result

@app.get("/api/cubes/{code}/query")
@limiter.limit(RATE_LIMIT)
async def cube_query(
    request: Request,
    code: str,
    dim_values: str = "",
    api_key: str = Depends(verify_api_key),
):
    """Query data from a cube. Provide dim_values as comma-separated string.
    
    Example: dim_values=SK021,2023,E_PRIEM_HR_MZDA,7
    Order matches the dimensions from /api/cubes/{code}/dims (excluding _data dim).
    """
    dims_list = [d.strip() for d in dim_values.split(",") if d.strip()]
    if not dims_list:
        raise HTTPException(status_code=400, detail="dim_values parameter is required (comma-separated)")
    result = tool_cube_query(code, dims_list)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.get("/api/cubes/find")
@limiter.limit(RATE_LIMIT)
async def find_cube(
    request: Request,
    q: str = "",
    api_key: str = Depends(verify_api_key),
):
    """Natural language search for cubes."""
    if not q:
        raise HTTPException(status_code=400, detail="q parameter is required")
    result = tool_find_cube(q)
    return {"results": result, "query": q}

@app.get("/api/status")
@limiter.limit(RATE_LIMIT)
async def ingest_status(
    request: Request,
    api_key: str = Depends(verify_api_key),
):
    """Get database status."""
    return tool_ingest_status()

@app.post("/api/ingest")
@limiter.limit("1/minute")
async def ingest_run(
    request: Request,
    api_key: str = Depends(verify_api_key),
):
    """Run a full ingest from ŠÚ SR (takes ~2-5 minutes)."""
    result = tool_ingest_run()
    return {"message": "Ingest completed", "result": result}

# ─── Entry ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    host = os.environ.get("DATACUBE_HOST", "0.0.0.0")
    port = int(os.environ.get("DATACUBE_PORT", "8000"))
    
    if API_KEY:
        log.info("API key authentication enabled")
    else:
        log.warning("API key authentication DISABLED — set DATACUBE_API_KEY env var")
    
    log.info("CORS allowed origins: %s", ALLOWED_ORIGINS if ALLOWED_ORIGINS != [""] else "*")
    log.info("Rate limit: %s", RATE_LIMIT)
    log.info("Starting on %s:%d", host, port)
    
    uvicorn.run(app, host=host, port=port, log_level="info")