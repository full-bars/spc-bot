#!/bin/bash
# Sets up the git hooks and dev dependencies for this repo.
# Run once after cloning, and again after a venv rebuild.
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo "Configuring git hooks..."
git config core.hooksPath .githooks
chmod +x .githooks/pre-push

echo "Installing dev dependencies..."
if [ -f venv/bin/activate ]; then
    # shellcheck disable=SC1091  # venv activate is intentionally sourced
    source venv/bin/activate
fi
pip install -r requirements-dev.txt -q

echo "Done. Hook: .githooks/pre-push | Dev deps: requirements-dev.txt"
