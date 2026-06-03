#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Start Redis in background if not running
if ! redis-cli ping > /dev/null 2>&1; then
    redis-server --daemonize yes --logfile /tmp/redis.log
    echo "Redis started"
fi

# Start FastAPI backend
cd backend
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
