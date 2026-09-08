#!/bin/bash
# Deploy DATAcube MCP server to a target server
# Usage: ./deploy.sh user@server

set -euo pipefail

SERVER="${1:?Usage: $0 user@server}"
REMOTE_DIR="/opt/datacube-mcp"

echo "=== DATAcube MCP Deploy ==="
echo "Target: $SERVER:$REMOTE_DIR"

# Create remote directory
ssh "$SERVER" "mkdir -p $REMOTE_DIR"

# Copy files
rsync -avz --exclude='datacube.db' \
  ./server.py \
  ./requirements.txt \
  ./AGENTS.md \
  ./deploy.sh \
  "$SERVER:$REMOTE_DIR/"

# Install deps
ssh "$SERVER" "cd $REMOTE_DIR && pip install -r requirements.txt"

# Check if we can copy an existing DB
if [ -f datacube.db ]; then
  echo "Copying existing database (${DB_SIZE})..."
  rsync -avz datacube.db "$SERVER:$REMOTE_DIR/"
else
  echo "No local DB — will be created on first ingest_run()."
  echo "Run: ssh $SERVER 'cd $REMOTE_DIR && python server.py --ingest'"
fi

echo "=== Done ==="
echo ""
echo "To start the MCP server (stdio):"
echo "  ssh $SERVER 'cd $REMOTE_DIR && python server.py'"
echo ""
echo "To run a fresh ingest:"
echo "  ssh $SERVER 'cd $REMOTE_DIR && python server.py --ingest'"
echo ""
echo "To configure in MCP client (Claude Desktop / Hermes):"
echo '  {
    "mcpServers": {
      "datacube": {
        "command": "python",
        "args": ["'$REMOTE_DIR'/server.py"]
      }
    }
  }'