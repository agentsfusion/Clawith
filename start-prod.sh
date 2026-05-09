#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$ROOT/.data"

export PYTHONDONTWRITEBYTECODE=1
find "$ROOT/backend" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

if ! command -v gws &>/dev/null; then
    echo "=== Installing @googleworkspace/cli ==="
    npm install -g @googleworkspace/cli 2>/dev/null || true
fi

cd "$ROOT/backend"

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    if command -v python3 &>/dev/null; then
        PYTHON_BIN="python3"
    elif command -v python &>/dev/null; then
        PYTHON_BIN="python"
    else
        echo "ERROR: no python interpreter found on PATH" >&2
        exit 1
    fi
fi

if ! "$PYTHON_BIN" -m pip --version &>/dev/null; then
    echo "ERROR: '$PYTHON_BIN -m pip' is not available" >&2
    exit 1
fi

DEPS_STAMP_DIR="$ROOT/.data"
DEPS_STAMP_FILE="$DEPS_STAMP_DIR/.backend-deps.sha256"
DEPS_FINGERPRINT="$(sha256sum "$ROOT/backend/pyproject.toml" 2>/dev/null | awk '{print $1}')"

needs_install=1
if [ "${SKIP_DEPS_INSTALL:-0}" = "1" ]; then
    needs_install=0
    echo "=== Skipping backend dependency install (SKIP_DEPS_INSTALL=1) ==="
elif [ "${FORCE_DEPS_INSTALL:-0}" = "1" ]; then
    echo "=== FORCE_DEPS_INSTALL=1 set — reinstalling backend dependencies ==="
elif [ -f "$DEPS_STAMP_FILE" ] && [ -n "$DEPS_FINGERPRINT" ] \
     && [ "$(cat "$DEPS_STAMP_FILE" 2>/dev/null)" = "$DEPS_FINGERPRINT" ] \
     && "$PYTHON_BIN" -c "import fastapi, uvicorn, sqlalchemy" &>/dev/null; then
    needs_install=0
    echo "=== Backend dependencies up-to-date (cache hit) — skipping pip install ==="
fi

if [ "$needs_install" = "1" ]; then
    echo "=== Installing backend dependencies (pip install -e .) ==="
    PIP_ARGS=(--disable-pip-version-check --quiet)
    if [ -n "${PIP_INDEX_URL:-}" ]; then
        PIP_ARGS+=(--index-url "$PIP_INDEX_URL")
    fi
    if [ -n "${PIP_TRUSTED_HOST:-}" ]; then
        PIP_ARGS+=(--trusted-host "$PIP_TRUSTED_HOST")
    fi
    if ! "$PYTHON_BIN" -m pip install "${PIP_ARGS[@]}" -e .; then
        echo "ERROR: backend dependency installation failed" >&2
        exit 1
    fi
    if [ -n "$DEPS_FINGERPRINT" ]; then
        mkdir -p "$DEPS_STAMP_DIR"
        echo "$DEPS_FINGERPRINT" > "$DEPS_STAMP_FILE"
    fi
    echo "=== Backend dependencies installed ==="
fi

echo "=== Updating database ==="
# alembic upgrade head
echo "=== Alembic upgrade complete ==="

PORT="${PORT:-5000}"
echo "=== Starting production server on 0.0.0.0:${PORT} ==="
exec "$PYTHON_BIN" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
