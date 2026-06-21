#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Start Redis locally (used for caching, presence, distributed locks).
if ! redis-cli ping > /dev/null 2>&1; then
    redis-server --daemonize yes --logfile /tmp/redis.log
    echo "Redis started"
fi

# Serve the FastAPI backend + built frontend on the public port (mapped to :80).
cd backend
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 5000
