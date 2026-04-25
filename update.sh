#!/bin/bash
# Lightweight in-place update — for use by CI/CD after git pull.
# Not a replacement for deploy.sh (first-time setup).

set -e

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[update] Installing dependencies..."
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" --quiet

echo "[update] Restarting spcbot service..."
sudo systemctl restart spcbot

echo "[update] Done."
