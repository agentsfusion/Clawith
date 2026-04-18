#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== [post-merge] Backend: applying alembic migrations ==="
( cd backend && alembic upgrade head )

# Frontend deps: only install when the lockfile actually changed in this merge.
# Use `npm ci` (deterministic, fails fast) instead of `npm install` so we never
# mutate the lockfile or partially upgrade packages mid-merge — that left the
# tree with a half-installed @tabler/icons-react ESM bundle once before.
if [ -f frontend/package.json ] && [ -f frontend/package-lock.json ]; then
    LOCK_HASH_FILE=".data/.frontend-lock.sha256"
    mkdir -p .data
    NEW_HASH=$(sha256sum frontend/package-lock.json | awk '{print $1}')
    OLD_HASH=$(cat "$LOCK_HASH_FILE" 2>/dev/null || echo "")
    if [ "$NEW_HASH" != "$OLD_HASH" ]; then
        echo "=== [post-merge] Frontend: lockfile changed — running npm ci ==="
        ( cd frontend && npm ci --no-audit --no-fund --silent )
        echo "$NEW_HASH" > "$LOCK_HASH_FILE"
    else
        echo "=== [post-merge] Frontend: lockfile unchanged — skipping install ==="
    fi
fi

echo "=== [post-merge] Done ==="
