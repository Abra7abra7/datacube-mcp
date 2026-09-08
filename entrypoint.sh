#!/bin/sh
# DATAcube entrypoint — auto-seed DB if missing
set -e

DB="${DATACUBE_DB_PATH:-/data/datacube.db}"

if [ ! -f "$DB" ]; then
    echo "DB not found at $DB — running ingest..."
    python3 /app/server.py --ingest 2>&1
    echo "Ingest complete."
else
    echo "DB found at $DB ($(du -h "$DB" | cut -f1))"
fi

exec python3 /app/api.py