#!/usr/bin/env bash
#
# Tier 5 release gate: installed-package verification.
#
# Installs the local wheel into a clean venv, verifies entry points and
# import boundaries, then optionally copies the test suite and runs pytest
# against the INSTALLED package (not the dev tree).  Catches collection
# errors, missing entry points, and packaging drift that in-tree pytest
# misses.
#
# Usage:
#   bash scripts/release/verify_install.sh <version>
#   bash scripts/release/verify_install.sh <version> --with-pytest
#
# Example:
#   bash scripts/release/verify_install.sh 1.0.5
#   bash scripts/release/verify_install.sh 1.0.5 --with-pytest
#
# Exit code: 0 on full pass, 1 on any failure.

set -euo pipefail

if [ "$(uname -s)" = "Darwin" ] \
    && [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ] \
    && [ "$(arch)" != "arm64" ]; then
    exec arch -arm64 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ── Helpers ───────────────────────────────────────────────────────────
PASSED=0
FAILED=0

fail() {
    echo "FAIL: $1" >&2
    FAILED=$((FAILED + 1))
}

pass() {
    echo "OK: $1"
    PASSED=$((PASSED + 1))
}

usage() {
    echo "Usage: bash $0 <version> [--with-pytest]" >&2
    echo "" >&2
    echo "  <version>       Version being released (must have a local wheel in dist/)" >&2
    echo "  --with-pytest   Copy the test suite and run pytest against the installed package" >&2
    echo "" >&2
    echo "Example:" >&2
    echo "  bash $0 1.0.5" >&2
    echo "  bash $0 1.0.5 --with-pytest" >&2
    exit 1
}

# ── Phase 0: Arg parsing & environment ────────────────────────────────

[ $# -ge 1 ] || usage

TARGET_VERSION="$1"
shift

WITH_PYTEST=false
while [ $# -gt 0 ]; do
    case "$1" in
        --with-pytest)
            WITH_PYTEST=true
            shift
            ;;
        *)
            usage
            ;;
    esac
done

# Locate local wheel
WHEEL_PATH=""
for whl in "${REPO_ROOT}/dist/footprinter_cli-${TARGET_VERSION}"*.whl; do
    [ -f "$whl" ] && WHEEL_PATH="$whl" && break
done

if [ -z "$WHEEL_PATH" ]; then
    echo "ERROR: No wheel found for version ${TARGET_VERSION} in dist/" >&2
    echo "Build it first:  python -m build" >&2
    exit 1
fi

WHEEL_PATH="$(cd "$(dirname "$WHEEL_PATH")" && pwd)/$(basename "$WHEEL_PATH")"

echo ""
echo "  verify-install: v${TARGET_VERSION}"
echo "  wheel: ${WHEEL_PATH}"
echo "  pytest: ${WITH_PYTEST}"
echo ""

# Source shared helpers for Python discovery
# shellcheck source=_install_common.sh
source "${SCRIPT_DIR}/_install_common.sh"
ensure_python_3_11

WORKDIR="$(mktemp -d -t verify-install-XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "==> Workspace: ${WORKDIR}"

# ── Phase 1: Create venv & install wheel ──────────────────────────────

echo ""
echo "==> Phase 1: Installing footprinter-cli v${TARGET_VERSION} from local wheel..."

"$PYTHON_BIN" -m venv "$WORKDIR/venv"
VENV_PY="$WORKDIR/venv/bin/python3"
VENV_FP="$WORKDIR/venv/bin/fp"

"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install "$WHEEL_PATH" >/dev/null 2>&1 \
    || { echo "ERROR: Failed to install wheel ${WHEEL_PATH}" >&2; exit 1; }

INSTALLED_VERSION=$("$VENV_FP" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1) || true

if [ -z "$INSTALLED_VERSION" ]; then
    echo "  ERROR: could not determine installed version (fp --version failed)"
    fail "fp --version did not return a version string"
    INSTALLED_VERSION="unknown"
else
    echo "  Installed: ${INSTALLED_VERSION}"
fi

# ── Phase 2: Baseline assertions (always) ─────────────────────────────

echo ""
echo "==> Phase 2: Baseline assertions..."
echo ""

# 1. Version matches
if [ "$INSTALLED_VERSION" = "$TARGET_VERSION" ]; then
    pass "fp --version reports ${TARGET_VERSION}"
else
    fail "fp --version reports ${INSTALLED_VERSION}, expected ${TARGET_VERSION}"
fi

# 2. Entry points load
for ep in fp fp-mcp fp-api; do
    if "$WORKDIR/venv/bin/$ep" --help >/dev/null 2>&1; then
        pass "${ep} --help exits 0"
    else
        fail "${ep} --help failed"
    fi
done

# 3. Core package importable
if "$VENV_PY" -c "import footprinter.mcp" 2>/dev/null; then
    pass "import footprinter.mcp succeeds"
else
    fail "import footprinter.mcp failed"
fi

# 4. Fixture boundary — footprinter.fixture must NOT be importable
if "$VENV_PY" -c "import footprinter.fixture" 2>/dev/null; then
    fail "footprinter.fixture is importable (should be excluded from wheel)"
else
    pass "footprinter.fixture correctly excluded from wheel"
fi

# ── Phase 3: Copy test tree (conditional) ─────────────────────────────

if [ "$WITH_PYTEST" = true ]; then

    echo ""
    echo "==> Phase 3: Copying test tree to neutral directory..."

    WORKSPACE="$WORKDIR/workspace"
    mkdir -p "$WORKSPACE"

    # Copy the entire tests/ directory (preserves __init__.py, conftest.py, subdirs)
    cp -R "$REPO_ROOT/tests" "$WORKSPACE/tests"

    # Copy pyproject.toml so pytest discovers its config (testpaths, pythonpath)
    cp "$REPO_ROOT/pyproject.toml" "$WORKSPACE/pyproject.toml"

    # The session-scoped _repo_local_paths fixture in conftest.py resolves
    # config.example.yaml at footprinter/bundled/config.example.yaml relative
    # to Path(__file__).parent.parent, which in the neutral dir is $WORKSPACE.
    # Create that path from the installed package so the fixture works without
    # modification.
    INSTALLED_CONFIG=$("$VENV_PY" -c "
from importlib.resources import files
print(files('footprinter.bundled').joinpath('config.example.yaml'))
" 2>&1) || true

    if [ -z "$INSTALLED_CONFIG" ] || [ ! -f "$INSTALLED_CONFIG" ]; then
        fail "could not locate bundled config.example.yaml in installed package"
    else
        mkdir -p "$WORKSPACE/footprinter/bundled"
        cp "$INSTALLED_CONFIG" "$WORKSPACE/footprinter/bundled/config.example.yaml"

        EXPECTED_CONFIG="$WORKSPACE/footprinter/bundled/config.example.yaml"
        if [ ! -f "$EXPECTED_CONFIG" ]; then
            fail "bundled config not at ${EXPECTED_CONFIG} — conftest fixture will break"
        else
            pass "bundled config.example.yaml in place for conftest"
        fi
    fi

    # Install test dependencies (not in the base wheel)
    "$VENV_PY" -m pip install pytest httpx >/dev/null 2>&1 \
        || { echo "ERROR: Failed to install test dependencies (pytest, httpx)" >&2; exit 1; }

    echo "  Copied: tests/, pyproject.toml, bundled config"

# ── Phase 4: Run pytest (conditional) ─────────────────────────────────

    echo ""
    echo "==> Phase 4: Running pytest against installed package..."
    echo ""

    # PYTHONPATH puts the workspace root on sys.path so `from tests.conftest
    # import run_fp` resolves — same role as pythonpath = ["."] in the dev
    # tree's pyproject.toml.
    if PYTHONPATH="$WORKSPACE" "$VENV_PY" -m pytest \
        "$WORKSPACE/tests" \
        --rootdir="$WORKSPACE" \
        -q --tb=short 2>&1; then
        pass "pytest against installed package passed"
    else
        fail "pytest against installed package had failures"
    fi

fi

# ── Phase 5: Summary ─────────────────────────────────────────────────

TOTAL=$((PASSED + FAILED))
echo ""
echo "────────────────────────────────────────────────────────────"
echo "  verify-install: ${PASSED}/${TOTAL} assertions passed"
echo "  version: ${TARGET_VERSION}"
echo "  pytest: ${WITH_PYTEST}"
echo "  workspace: ${WORKDIR}"
echo "────────────────────────────────────────────────────────────"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
