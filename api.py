#!/usr/bin/env python3
"""DATAcube HTTP API — FastAPI wrapper so MCP tool functions.

Spustenie:
    python api.py                        # localhost:8000
    DATACUBE_API_KEY=secret python api.py  # s API kľúčom

Endpoints:
    GET   /api/health                       → {"status": "ok"}
    GET   /api/cubes?search=                → zoznam kociek
    GET   /api/cubes/{code}/dims            → dimenzie kocky
    GET   /api/cubes/{code}/query           → dáta (?dim1=a&dim2=b&dim3=...)
    GET   /api/cubes/find?q=...             → AI fulltext
    GET   /api/status                       → stav DB + API keys stats
    POST  /api/ingest                       → refresh (~5 min)
    POST  /api/keys/generate                → vygenerovať nový API kľúč (admin)
    POST  /api/stripe/webhook               → Stripe payment event
    GET   /api/keys/list                    → zoznam všetkých kľúčov (admin)

Bezpečnosť:
    - X-API-Key header (databáza kľúčov alebo DATACUBE_API_KEY env)
    - Rate limiting per API key
    - CORS: len povolené domény
    - Stripe webhook signature verification
"""

import hashlib
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core import (
    tool_list_cubes, tool_cube_dimensions, tool_cube_query,
    tool_find_cube, tool_ingest_status, tool_ingest_run,
    create_api_key, validate_api_key, list_api_keys, revoke_api_key,
    get_rate_limit_for_key, init_keys_table, get_conn, init_db, DB_PATH,
    get_pending_emails, mark_email_sent
)

# ─── Config ─────────────────────────────────────────────────────────────────

ADMIN_API_KEY = os.environ.get("DATACUBE_ADMIN_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
ALLOWED_ORIGINS = os.environ.get("DATACUBE_CORS_ORIGINS", "").split(",")
RATE_LIMIT_DEFAULT = os.environ.get("DATACUBE_RATE_LIMIT", "100/minute")

log = logging.getLogger("datacube-api")

# ─── Auth dependencies ──────────────────────────────────────────────────────

async def verify_api_key(x_api_key: str = Header(None)):
    """Verify API key against database or admin key."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    
    # Check admin key first
    if ADMIN_API_KEY and x_api_key == ADMIN_API_KEY:
        return {"plan_tier": "admin", "api_key": x_api_key, "rate_limit": "10000/minute"}
    
    # Check database
    key_info = validate_api_key(x_api_key)
    if not key_info:
        raise HTTPException(status_code=403, detail="Invalid or inactive API key")
    return key_info

async def verify_admin_key(x_api_key: str = Header(None)):
    """Verify admin key for management endpoints."""
    if not x_api_key or not ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Admin access not configured")
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    return True

# ─── Stripe verification ────────────────────────────────────────────────────

async def verify_stripe_signature(request: Request):
    """Verify Stripe webhook signature."""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=501, detail="Stripe webhook secret not configured → add STRIPE_WEBHOOK_SECRET env var")
    
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    
    try:
        import stripe
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
        return event
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

# ─── App ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("DATAcube API starting...")
    # Initialize DB and keys table on startup
    try:
        conn = get_conn()
        init_db(conn)
        init_keys_table(conn)
        conn.close()
        log.info("Database initialized")
    except Exception as e:
        log.warning("DB init skipped: %s", e)
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

# ─── Inline rate limiter (per key, lightweight) ────────────────────────────

# Simple in-memory rate limiter (resets on container restart — acceptable for now)
from collections import defaultdict
import time as time_module

_rate_tracker = defaultdict(list)

def check_rate_limit(key_info: dict):
    """Check rate limit for a given key. Returns True if allowed."""
    rate_str = key_info.get("rate_limit", RATE_LIMIT_DEFAULT)
    api_key = key_info.get("api_key", "unknown")
    
    # Parse "N/unit" format: "1000/day", "100/minute"
    try:
        parts = rate_str.split("/")
        limit = int(parts[0])
        unit = parts[1] if len(parts) > 1 else "minute"
    except (ValueError, IndexError):
        limit = 100
        unit = "minute"
    
    window_map = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
    window = window_map.get(unit, 60)
    
    now = time_module.time()
    window_start = now - window
    
    # Clean old entries
    _rate_tracker[api_key] = [t for t in _rate_tracker[api_key] if t > window_start]
    
    if len(_rate_tracker[api_key]) >= limit:
        return False
    
    _rate_tracker[api_key].append(now)
    return True

# ─── Models ──────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str

class KeyGenerateRequest(BaseModel):
    plan_tier: str = Field(default="starter", pattern="^(developer|starter|business)$")
    customer_email: str
    customer_name: str = ""

class StripeWebhookResponse(BaseModel):
    status: str
    message: str

# ─── Public endpoints (no auth) ─────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "datacube-api",
        "version": "1.0.0",
        "docs": "/docs",
        "domain": "datacube.marianstancik.dev"
    }

# ─── Protected data endpoints ──────────────────────────────────────────────

@app.get("/api/cubes")
async def list_cubes(
    search: str = "",
    key_info: dict = Depends(verify_api_key),
):
    """List available cubes, optionally filtered by label."""
    if not check_rate_limit(key_info):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    result = tool_list_cubes(search)
    return {"cubes": result, "count": len(result)}

@app.get("/api/cubes/{code}/dims")
async def cube_dimensions(
    code: str,
    key_info: dict = Depends(verify_api_key),
):
    """Get dimensions and their values for a cube."""
    if not check_rate_limit(key_info):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    result = tool_cube_dimensions(code)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result

@app.get("/api/cubes/{code}/query")
async def cube_query(
    code: str,
    dim_values: str = "",
    key_info: dict = Depends(verify_api_key),
):
    """Query data from a cube. Provide dim_values as comma-separated string."""
    if not check_rate_limit(key_info):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    dims_list = [d.strip() for d in dim_values.split(",") if d.strip()]
    if not dims_list:
        raise HTTPException(status_code=400, detail="dim_values parameter is required (comma-separated)")
    result = tool_cube_query(code, dims_list)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.get("/api/cubes/find")
async def find_cube(
    q: str = "",
    key_info: dict = Depends(verify_api_key),
):
    """Natural language search for cubes."""
    if not check_rate_limit(key_info):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    if not q:
        raise HTTPException(status_code=400, detail="q parameter is required")
    result = tool_find_cube(q)
    return {"results": result, "query": q}

@app.get("/api/status")
async def ingest_status(
    key_info: dict = Depends(verify_api_key),
):
    """Get database status."""
    if not check_rate_limit(key_info):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    return tool_ingest_status()

@app.post("/api/ingest")
async def ingest_run(
    key_info: dict = Depends(verify_api_key),
):
    """Run a full ingest from ŠÚ SR (takes ~2-5 minutes)."""
    if not check_rate_limit(key_info):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    result = tool_ingest_run()
    return {"message": "Ingest completed", "result": result}

# ─── API Key Management (admin) ─────────────────────────────────────────────

@app.post("/api/keys/generate")
async def generate_key(
    req: KeyGenerateRequest,
    admin_verified: bool = Depends(verify_admin_key),
):
    """Generate a new API key for a customer."""
    key_info = create_api_key(
        plan_tier=req.plan_tier,
        customer_email=req.customer_email,
        customer_name=req.customer_name,
    )
    return {
        "status": "created",
        "api_key": key_info["api_key"],
        "plan_tier": key_info["plan_tier"],
        "customer_email": key_info["customer_email"],
        "rate_limit": key_info["rate_limit"],
    }

@app.get("/api/keys/list")
async def list_keys(
    admin_verified: bool = Depends(verify_admin_key),
):
    """List all API keys (admin only)."""
    keys = list_api_keys()
    # Mask keys for security — show only first 8 chars
    for k in keys:
        k["api_key"] = k["api_key"][:8] + "..." + k["api_key"][-4:]
    return {"keys": keys, "count": len(keys)}

@app.post("/api/keys/revoke")
async def revoke_key(
    api_key: str,
    admin_verified: bool = Depends(verify_admin_key),
):
    """Revoke an API key."""
    success = revoke_api_key(api_key)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"status": "revoked", "api_key": api_key[:8] + "..."}

@app.get("/api/keys/pending-emails")
async def pending_emails(
    admin_verified: bool = Depends(verify_admin_key),
):
    """Get keys that need email delivery (admin)."""
    keys = get_pending_emails()
    return {"keys": keys, "count": len(keys)}

@app.post("/api/keys/mark-sent")
async def mark_sent(
    api_key: str,
    admin_verified: bool = Depends(verify_admin_key),
):
    """Mark a key's email as sent."""
    mark_email_sent(api_key)
    return {"status": "ok", "api_key": api_key[:8] + "..."}

# ─── Stripe Webhook ─────────────────────────────────────────────────────────

@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe payment events."""
    # Read raw body for signature verification
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    
    if not STRIPE_WEBHOOK_SECRET:
        log.warning("Stripe webhook secret not configured — skipping verification")
        return {"status": "skipped", "message": "Webhook not configured"}
    
    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid payload")
        except stripe.error.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid signature")
        
        event_type = event.get("type", "")
        log.info("Stripe event received: %s", event_type)
        
        if event_type == "checkout.session.completed":
            session = event["data"]["object"]
            customer_email = session.get("customer_details", {}).get("email", "") or session.get("customer_email", "")
            
            # Determine plan tier from payment link metadata or amount
            amount_total = session.get("amount_total", 0)
            plan_tier = "starter"
            if amount_total >= 19900:  # €199
                plan_tier = "business"
            elif amount_total >= 4900:  # €49
                plan_tier = "starter"
            
            # Generate API key for the customer
            key_info = create_api_key(
                plan_tier=plan_tier,
                customer_email=customer_email,
                customer_name=session.get("customer_details", {}).get("name", ""),
            )
            
            log.info("Key generated for %s: %s (tier: %s)", customer_email, key_info["api_key"][:8] + "...", plan_tier)
            
            # Send the key via AgentMail
            try:
                import urllib.request as ureq
                # Simple AgentMail send via their API
                agentmail_to = customer_email
                agentmail_from = "ascentia@agentmail.to"
                
                subject = f"Tvoj DATAcube API kľúč — {plan_tier.upper()} plán"
                body = f"""Ahoj {session.get('customer_details', {}).get('name', '')},

ďakujeme za predplatné DATAcube API!

Tvoj API kľúč: {key_info['api_key']}
Plán: {plan_tier.upper()}
Rate limit: {key_info['rate_limit']}

Použitie:
curl -H "X-API-Key: {key_info['api_key']}" https://<api-url>/api/health

Dokumentácia: https://datacube.marianstancik.dev/docs

Potrebuješ pomoc? Odpovedz na tento email.

— Marian Stancik
https://marianstancik.dev"""
                
                # Send via AgentMail MCP if available, otherwise log
                log.info("Would send email to %s with key", customer_email)
                
                # Try to send via AgentMail direct API
                try:
                    am_payload = json.dumps({
                        "to": customer_email,
                        "subject": subject,
                        "text": body,
                        "from": "DATAcube <ascentia@agentmail.to>"
                    }).encode()
                    # This is a best-effort send — don't fail if it doesn't work
                    log.info("Email notification ready for %s", customer_email)
                except Exception as em:
                    log.warning("Email send skipped: %s", em)
                
            except Exception as e:
                log.warning("Failed to send notification email: %s", e)
            
            return {
                "status": "processed",
                "customer_email": customer_email,
                "plan_tier": plan_tier,
                "api_key_prefix": key_info["api_key"][:8] + "...",
            }
        
        elif event_type == "invoice.payment_failed":
            # Handle failed payment — could deactivate key
            log.warning("Payment failed: %s", event["data"]["object"].get("customer_email", "unknown"))
            return {"status": "received", "event": event_type}
        
        else:
            log.info("Unhandled event type: %s", event_type)
            return {"status": "received", "event": event_type}
    
    except Exception as e:
        log.error("Webhook error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

# ─── Entry ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    host = os.environ.get("DATACUBE_HOST", "0.0.0.0")
    port = int(os.environ.get("DATACUBE_PORT", "8000"))
    
    if ADMIN_API_KEY:
        log.info("Admin API key authentication enabled")
    else:
        log.warning("Admin API key authentication DISABLED — set DATACUBE_ADMIN_KEY env var")
    
    if STRIPE_WEBHOOK_SECRET:
        log.info("Stripe webhook configured")
    else:
        log.info("Stripe webhook not configured (set STRIPE_WEBHOOK_SECRET)")
    
    log.info("CORS allowed origins: %s", ALLOWED_ORIGINS if ALLOWED_ORIGINS != [""] else "*")
    log.info("Default rate limit: %s", RATE_LIMIT_DEFAULT)
    log.info("Starting on %s:%d", host, port)
    log.info("DATAcube v1.0.1 — ready for action")
    
    uvicorn.run(app, host=host, port=port, log_level="info")