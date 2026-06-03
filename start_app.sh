#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Start Redis in background if not running
if ! redis-cli ping > /dev/null 2>&1; then
    redis-server --daemonize yes --logfile /tmp/redis.log
    echo "Redis started"
fi

# Start backend in background
cd backend
.venv/bin/uvicorn app.main:app --host localhost --port 8000 &
BACKEND_PID=$!
cd ..

# Ensure backend is cleaned up on exit
cleanup() { kill "$BACKEND_PID" 2>/dev/null; }
trap cleanup EXIT INT TERM

# Wait for backend to be ready
echo "Waiting for backend..."
for i in $(seq 1 30); do
    if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
        echo "Backend ready!"
        break
    fi
    sleep 1
done

# Check backend actually started
if ! curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "ERROR: Backend failed to start within 30 seconds"
    exit 1
fi

# Start frontend
cd frontend
exec npx vite --port 5000 --host 0.0.0.0
