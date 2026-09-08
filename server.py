#!/usr/bin/env python3
"""DATAcube MCP Server — Štatistický úrad SR data cubes ako MCP tools.

Spustenie:
    python server.py              # MCP stdio server
    python server.py --ingest     # refresh databázy

Pre pripojenie z MCP clienta:
    {
      "mcpServers": {
        "datacube": {
          "command": "python",
          "args": ["/path/to/server.py"]
        }
      }
    }
"""

import json
import logging
import sys

from core import (
    tool_list_cubes, tool_cube_dimensions, tool_cube_query,
    tool_find_cube, tool_ingest_status, tool_ingest_run, run_ingest
)

log = logging.getLogger("datacube")

# ─── MCP server — stdio protocol ────────────────────────────────────────────

def handle_request(request: dict) -> dict:
    method = request.get("method", "")
    req_id = request.get("id", 0)
    params = request.get("params", {})

    if method == "list_tools":
        return {
            "jsonrpc": "2.0", "id": req_id, "result": {
                "tools": [
                    {
                        "name": "list_cubes",
                        "description": "List available statistical cubes. Optional search filters by label.",
                        "inputSchema": {"type": "object", "properties": {
                            "search": {"type": "string", "description": "Optional search term for cube label"}
                        }}
                    },
                    {
                        "name": "cube_dimensions",
                        "description": "Get dimensions and their possible values for a specific cube.",
                        "inputSchema": {"type": "object", "properties": {
                            "code": {"type": "string", "description": "Cube code (e.g. np1105rs)"}
                        }, "required": ["code"]}
                    },
                    {
                        "name": "cube_query",
                        "description": "Query actual data from a cube. Provide dimension values in the order from cube_dimensions.",
                        "inputSchema": {"type": "object", "properties": {
                            "code": {"type": "string", "description": "Cube code"},
                            "dim_values": {"type": "array", "items": {"type": "string"},
                                "description": "Dimension values in order. Example: ['SK021', '2023', 'E_PRIEM_HR_MZDA', '7']"}
                        }, "required": ["code", "dim_values"]}
                    },
                    {
                        "name": "find_cube",
                        "description": "Natural language search: find the best matching cubes for a question about statistics.",
                        "inputSchema": {"type": "object", "properties": {
                            "question": {"type": "string", "description": "Natural language question"}
                        }, "required": ["question"]}
                    },
                    {
                        "name": "ingest_status",
                        "description": "Get current DB status — cube count, dimension count, last update time.",
                        "inputSchema": {"type": "object", "properties": {}}
                    },
                    {
                        "name": "ingest_run",
                        "description": "Run a full metadata ingest from ŠÚ SR (takes ~2-5 minutes). Call this first if DB is empty.",
                        "inputSchema": {"type": "object", "properties": {}}
                    }
                ]
            }
        }

    if method == "call_tool":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        tools_map = {
            "list_cubes": lambda: tool_list_cubes(tool_args.get("search", "")),
            "cube_dimensions": lambda: tool_cube_dimensions(tool_args.get("code", "")),
            "cube_query": lambda: tool_cube_query(tool_args.get("code", ""), tool_args.get("dim_values", [])),
            "find_cube": lambda: tool_find_cube(tool_args.get("question", "")),
            "ingest_status": lambda: tool_ingest_status(),
            "ingest_run": lambda: tool_ingest_run(),
        }
        fn = tools_map.get(tool_name)
        if not fn:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"}}
        try:
            return {"jsonrpc": "2.0", "id": req_id, "result": fn()}
        except Exception as e:
            log.error("Tool %s failed: %s", tool_name, e)
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}}

    return {"jsonrpc": "2.0", "id": req_id, "method": method}

def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError:
            continue

# ─── Entry ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "--ingest":
        run_ingest()
    else:
        log.info("DATAcube MCP server starting (stdio)...")
        serve_stdio()