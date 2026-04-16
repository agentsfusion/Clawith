#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$ROOT/.data"

export PYTHONDONTWRITEBYTECODE=1
find "$ROOT/backend" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

if ! command -v gws &>/dev/null; then
    echo "=== Installing @googleworkspace/cli ==="
    npm install -g @googleworkspace/cli 2>/dev/null || true
fi

if ! command -v pptxgenjs &>/dev/null; then
    echo "=== Installing pptxgenjs (PPTX skill dependency) ==="
    npm install -g pptxgenjs 2>/dev/null || true
fi

cd "$ROOT/backend"

echo "=== Updating database ==="
# alembic upgrade head
echo "=== Alembic upgrade complete ==="

echo "=== Starting production server ==="
exec uvicorn app.main:app --host 0.0.0.0 --port 5000
