#!/bin/bash
# Runs automatically after a task is merged. Installs backend and frontend
# dependencies so newly merged code picks up any new packages.
# Must be idempotent, non-interactive, and fail fast.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# pip in the project venv must not use the user site dir.
export PIP_USER=0

# Backend Python deps. The venv already exists; `pip install -e .` is a fast
# no-op when nothing changed and picks up any new pins on dependency changes.
if [ -x backend/.venv/bin/pip ]; then
    echo "[post-merge] installing backend dependencies..."
    backend/.venv/bin/pip install -e ./backend
fi

# Frontend deps. node_modules + lockfile are present, so this is fast when
# unchanged.
if [ -f frontend/package.json ] && command -v npm >/dev/null 2>&1; then
    echo "[post-merge] installing frontend dependencies..."
    (cd frontend && npm install --no-audit --no-fund)
fi

echo "[post-merge] done."
