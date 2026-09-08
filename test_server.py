#!/usr/bin/env python3
"""Test the DATAcube MCP server via subprocess."""
import subprocess
import json
import sys

server_cmd = [sys.executable, "/root/datacube-mcp/server.py"]

# Start the server process
proc = subprocess.Popen(
    server_cmd,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

def send_request(request: dict) -> dict:
    """Send a JSON-RPC request and get response."""
    line = json.dumps(request, ensure_ascii=False) + "\n"
    proc.stdin.write(line)
    proc.stdin.flush()
    response_line = proc.stdout.readline()
    return json.loads(response_line)

print("=== Testing MCP server ===")

# 1. list_tools
print("\n--- list_tools ---")
resp = send_request({"jsonrpc": "2.0", "id": 1, "method": "list_tools"})
tools = resp.get("result", {}).get("tools", [])
print(f"Tools: {[t['name'] for t in tools]}")

# 2. ingest_status (DB may or may not exist)
print("\n--- ingest_status ---")
resp = send_request({"jsonrpc": "2.0", "id": 2, "method": "call_tool", "params": {"name": "ingest_status", "arguments": {}}})
status = resp.get("result", {})
print(f"Status: {json.dumps(status, indent=2)}")

# 3. list_cubes (should return if DB exists)
if status.get("status") == "ready":
    print(f"\n--- list_cubes (limit 5) ---")
    resp = send_request({"jsonrpc": "2.0", "id": 3, "method": "call_tool", "params": {"name": "list_cubes", "arguments": {}}})
    cubes = resp.get("result", [])
    for c in cubes[:5]:
        print(f"  {c['code']:15s} | {c['updated']:12s} | {c['label'][:60]}")

    # 4. cube_dimensions — pick first cube
    if cubes:
        code = cubes[0]["code"]
        print(f"\n--- cube_dimensions ({code}) ---")
        resp = send_request({"jsonrpc": "2.0", "id": 4, "method": "call_tool", "params": {"name": "cube_dimensions", "arguments": {"code": code}}})
        dims = resp.get("result", {})
        dim_keys = list(dims.get("dimensions", {}).keys())
        print(f"Dims: {dim_keys}")
        for dk in dim_keys[:3]:
            vals = dims["dimensions"][dk].get("values", [])
            print(f"  {dk}: {len(vals)} values, first: {vals[0] if vals else 'none'}")

    # 5. find_cube
    print(f"\n--- find_cube (mzda) ---")
    resp = send_request({"jsonrpc": "2.0", "id": 5, "method": "call_tool", "params": {"name": "find_cube", "arguments": {"question": "priemerná mzda v Bratislave 2023"}}})
    matches = resp.get("result", [])
    for m in matches[:5]:
        print(f"  {m['code']:15s} score={m['score']:2d} | {m['label'][:60]}")

proc.stdin.close()
proc.wait()
print("\n=== Tests done ===")