#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"

cat > "$HOOKS_DIR/pre-push" << 'HOOK'
#!/bin/bash
while read local_ref local_sha remote_ref remote_sha; do
    if [ "$local_sha" = "0000000000000000000000000000000000000000" ]; then
        exit 0
    fi
    if echo "$remote_ref" | grep -q "^refs/tags/"; then
        exit 0
    fi
done

echo "Running pre-push checks..."
cd "$(git rev-parse --show-toplevel)"
source venv/bin/activate 2>/dev/null || true

# 1. Lint check (Ruff)
# This catches unused imports, which have caused multiple CI failures recently.
echo "Linting..."
ruff check --select=E9,F63,F7,F82,F401 --exclude=venv,lib,cache .
if [ $? -ne 0 ]; then
    echo "LINT ERRORS FOUND — push aborted"
    exit 1
fi

# 2. Syntax check
failed=0
for f in $(git diff --name-only HEAD @{upstream} 2>/dev/null | grep '\.py$'); do
    if [ -f "$f" ]; then
        python3 -m py_compile "$f" 2>&1
        if [ $? -ne 0 ]; then
            echo "SYNTAX ERROR in $f — push aborted"
            failed=1
        fi
    fi
done
[ $failed -ne 0 ] && exit 1

# 3. Tests
echo "Testing..."
python -m pytest tests/ -q 2>&1 | tail -3
if [ ${PIPESTATUS[0]} -ne 0 ]; then
    echo "TESTS FAILED — push aborted"
    exit 1
fi

echo "All checks passed."
HOOK

chmod +x "$HOOKS_DIR/pre-push"
echo "Hooks installed."
