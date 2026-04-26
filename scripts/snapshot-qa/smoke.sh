#!/bin/bash
#
# Public-repo smoke driver: minimal post-install check.
#
# Runs against an installed footprinter-cli wheel (pip install -e . or
# pip install footprinter-cli). Verifies the four ship-surface invariants
# the public repo cares about:
#
#   1. fp entry point loads
#   2. fp ingest subcommand loads
#   3. footprinter.mcp module imports without error
#   4. The installed package does not contain footprinter.fixture
#      (test infrastructure stays in the dev repo)
#
# Exit code: 0 on full pass, 1 on any failure.
#
# Not a substitute for pytest. Pytest covers ~3,600 organic TDD tests.
# This is the post-install canary that runs before pytest in CI for the
# public repo.

set -euo pipefail

if [ "$(uname -s)" = "Darwin" ] \
    && [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ] \
    && [ "$(arch)" != "arm64" ]; then
    exec arch -arm64 "$0" "$@"
fi

PY="${PY:-python3}"

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

pass() {
    echo "OK: $1"
}

# 1. fp --help
fp --help >/dev/null 2>&1 || fail "fp --help did not exit 0"
pass "fp --help"

# 2. fp ingest --help
fp ingest --help >/dev/null 2>&1 || fail "fp ingest --help did not exit 0"
pass "fp ingest --help"

# 3. footprinter.mcp imports
"$PY" -c "import footprinter.mcp" >/dev/null 2>&1 || fail "footprinter.mcp does not import"
pass "footprinter.mcp imports"

# 4. No footprinter.fixture in the installed package
if "$PY" -c "import footprinter.fixture" 2>/dev/null; then
    fail "footprinter.fixture is importable — dev-tier infrastructure leaked into the wheel"
fi
pass "footprinter.fixture is absent (dev-tier boundary intact)"

echo "Smoke driver: all checks passed"
