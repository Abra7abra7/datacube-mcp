"""DATAcube core — shared logic between MCP and HTTP layers.

Config, API helpers, DB access, ingest, and all tool functions.
Both server.py (MCP stdio) and api.py (FastAPI) import from here.
"""

import json
import logging
import sqlite3
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ─── Config ─────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent / "datacube.db"
API_BASE = "https://data.statistics.sk/api/v2"
LANG = "sk"
API_TIMEOUT = 30
REQUEST_DELAY = 0.05
USER_AGENT = "DATAcube-MCP/1.0"

log = logging.getLogger("datacube")

# ─── API helpers ────────────────────────────────────────────────────────────

def api_get(path: str) -> dict:
    """GET JSON from ŠÚ SR API with retry."""
    url = f"{API_BASE}{path}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            if attempt == 2:
                raise
            time.sleep(1.0 * (attempt + 1))
    return {}

# ─── DB helpers ─────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 3000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

def db_exists() -> bool:
    return DB_PATH.exists() and DB_PATH.stat().st_size > 1024

# ─── Ingest ─────────────────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cubes (
            code        TEXT PRIMARY KEY,
            label       TEXT NOT NULL,
            href        TEXT,
            updated     TEXT,
            domains     TEXT,
            dims        TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dim_values (
            cube_code   TEXT NOT NULL,
            dim_code    TEXT NOT NULL,
            value_key   TEXT NOT NULL,
            value_label TEXT,
            PRIMARY KEY (cube_code, dim_code, value_key)
        );
        CREATE TABLE IF NOT EXISTS data_cache (
            cube_code   TEXT NOT NULL,
            query_hash  TEXT NOT NULL,
            response    TEXT NOT NULL,
            fetched_at  TEXT NOT NULL,
            PRIMARY KEY (cube_code, query_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_cubes_label ON cubes(label);
        CREATE INDEX IF NOT EXISTS idx_dim_values_cube ON dim_values(cube_code);
        PRAGMA journal_mode = WAL;
    """)
    conn.commit()

def sync_collection(conn: sqlite3.Connection) -> list[dict]:
    data = api_get("/collection?lang=sk")
    items = data.get("link", {}).get("item", [])
    log.info("found %d cubes", len(items))
    cubes = []
    for item in items:
        code = item["href"].split("/dataset/")[1].split("/")[0]
        dims = {}
        for dk, dv in item.get("dimension", {}).items():
            dims[dk] = {"note": dv.get("note", ""), "href": dv.get("href", "")}
        conn.execute(
            "INSERT OR REPLACE INTO cubes (code, label, href, updated, domains, dims) VALUES (?, ?, ?, ?, ?, ?)",
            (code, item.get("label", ""), item.get("href", ""),
             item.get("update", ""), json.dumps([]), json.dumps(dims))
        )
        cubes.append({"code": code, "dims": dims})
    conn.commit()
    return cubes

def sync_dimensions(conn: sqlite3.Connection, cubes: list[dict]):
    for idx, cube in enumerate(cubes):
        code, dims = cube["code"], cube["dims"]
        if not dims:
            continue
        conn.execute("DELETE FROM dim_values WHERE cube_code = ?", (code,))
        row_count = 0
        for dim_code in dims:
            try:
                dim_data = api_get(f"/dimension/{code}/{dim_code}?lang={LANG}")
            except Exception:
                continue
            cats = dim_data.get("category", {})
            labels_raw = cats.get("label", {})
            index = cats.get("index", {})
            if isinstance(labels_raw, list):
                labels = {k: labels_raw[i] if i < len(labels_raw) else k for i, k in enumerate(index)}
            else:
                labels = labels_raw
            for vkey in index:
                vlabel = labels.get(vkey, vkey)
                conn.execute(
                    "INSERT OR REPLACE INTO dim_values (cube_code, dim_code, value_key, value_label) VALUES (?, ?, ?, ?)",
                    (code, dim_code, vkey, vlabel)
                )
                row_count += 1
            time.sleep(REQUEST_DELAY)
        if idx % 20 == 0 or idx == len(cubes) - 1:
            conn.commit()
        log.info("  dims: %d/%d (cube %s: %d vals)", idx + 1, len(cubes), code, row_count)
    conn.commit()

def run_ingest() -> dict:
    """Full ingest: collection → dimensions. Returns stats."""
    log.info("DATAcube ingest starting at %s", datetime.now(timezone.utc).isoformat())
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    init_db(conn)
    t0 = time.time()

    log.info("[1/2] Syncing collection...")
    cubes = sync_collection(conn)
    log.info("  done in %.1fs", time.time() - t0)

    log.info("[2/2] Syncing dimensions...")
    sync_dimensions(conn, cubes)
    log.info("  done in %.1fs", time.time() - t0)

    cur = conn.execute("SELECT COUNT(*) FROM cubes")
    c_count = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM dim_values")
    v_count = cur.fetchone()[0]
    conn.close()

    log.info("Done: %d cubes, %d dim values (%.1fs)", c_count, v_count, time.time() - t0)
    return {"cubes": c_count, "dim_values": v_count, "duration_s": round(time.time() - t0, 1)}

# ─── Tool functions (shared by MCP + HTTP) ──────────────────────────────────

def tool_list_cubes(search: str = "") -> list[dict]:
    """List available cubes, optionally filtered by label search."""
    if not db_exists():
        return [{"error": "DB not initialized. Run ingest first."}]
    conn = get_conn()
    if search:
        cur = conn.execute(
            "SELECT code, label, updated FROM cubes WHERE label LIKE ? ORDER BY updated DESC LIMIT 50",
            (f"%{search}%",)
        )
    else:
        cur = conn.execute("SELECT code, label, updated FROM cubes ORDER BY updated DESC LIMIT 50")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def tool_cube_dimensions(code: str) -> dict:
    """Get dimensions and their possible values for a cube."""
    if not db_exists():
        return {"error": "DB not initialized. Run ingest first."}
    conn = get_conn()
    cur = conn.execute("SELECT code, label, dims FROM cubes WHERE code = ?", (code,))
    cube = cur.fetchone()
    if not cube:
        conn.close()
        return {"error": f"Cube {code} not found"}
    dims = json.loads(cube["dims"])
    result = {"code": cube["code"], "label": cube["label"], "dimensions": {}}
    for dk, dv in dims.items():
        cur2 = conn.execute(
            "SELECT value_key, value_label FROM dim_values WHERE cube_code = ? AND dim_code = ? ORDER BY value_key LIMIT 50",
            (code, dk)
        )
        values = [{"key": r["value_key"], "label": r["value_label"]} for r in cur2.fetchall()]
        result["dimensions"][dk] = {"note": dv.get("note", ""), "values": values}
    conn.close()
    return result

def tool_cube_query(code: str, dim_values: list[str]) -> dict:
    """Query data from a cube via ŠÚ SR API."""
    if not db_exists():
        return {"error": "DB not initialized. Run ingest first."}
    conn = get_conn()
    cur = conn.execute("SELECT dims FROM cubes WHERE code = ?", (code,))
    cube = cur.fetchone()
    if not cube:
        conn.close()
        return {"error": f"Cube {code} not found"}
    dims = json.loads(cube["dims"])
    dim_keys = [k for k in dims if not k.endswith("_data")]
    if len(dim_values) != len(dim_keys):
        conn.close()
        return {"error": f"Expected {len(dim_keys)} dimension values, got {len(dim_values)}", "expected_dims": dim_keys}
    conn.close()

    encoded = [urllib.parse.quote(v, safe="_,.") for v in dim_values]
    path = f"/dataset/{code}/{'/'.join(encoded)}?lang={LANG}&type=json"
    try:
        data = api_get(path)
        return data
    except Exception as e:
        return {"error": str(e), "url": f"{API_BASE}{path}"}

def tool_find_cube(question: str) -> list[dict]:
    """Natural language search: find best matching cubes."""
    if not db_exists():
        return [{"error": "DB not initialized. Run ingest first."}]
    conn = get_conn()
    words = question.lower().split()
    cur = conn.execute("SELECT code, label, dims FROM cubes")
    results = []
    for cube in cur.fetchall():
        label_lower = cube["label"].lower()
        score = sum(1 for w in words if w in label_lower)
        if score > 0:
            dims = json.loads(cube["dims"])
            dim_keys = [k for k in dims if not k.endswith("_data")]
            sample_values = []
            for dk in dim_keys:
                cur2 = conn.execute(
                    "SELECT value_key FROM dim_values WHERE cube_code = ? AND dim_code = ? LIMIT 1",
                    (cube["code"], dk)
                )
                r2 = cur2.fetchone()
                sample_values.append(r2["value_key"] if r2 else "")
            results.append({
                "code": cube["code"], "label": cube["label"], "score": score,
                "dims": dim_keys, "sample_query": "/".join(sample_values)
            })
    conn.close()
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:10]

def tool_ingest_status() -> dict:
    """Get DB status: cube count, dim values, last update."""
    if not db_exists():
        return {"status": "not_initialized", "message": "Run ingest first."}
    conn = get_conn()
    cur = conn.execute("SELECT COUNT(*) FROM cubes")
    cubes = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM dim_values")
    dims = cur.fetchone()[0]
    cur = conn.execute("SELECT MAX(fetched_at) FROM data_cache")
    last_cache = cur.fetchone()[0]
    cur = conn.execute("SELECT MAX(updated) FROM cubes")
    last_update = cur.fetchone()[0]
    conn.close()
    return {
        "status": "ready", "cubes": cubes, "dim_values": dims,
        "last_cube_update": last_update, "last_cache_fetch": last_cache,
        "db_size_mb": round(DB_PATH.stat().st_size / 1_048_576, 1)
    }

def tool_ingest_run() -> dict:
    """Run full ingest. Takes ~2-5 minutes."""
    return run_ingest()