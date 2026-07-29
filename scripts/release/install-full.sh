#!/usr/bin/env bash
#
# ============================================================================
# RETIRED — DO NOT RUN.
#
# footprinter-cli is no longer published: every released version has been
# yanked from PyPI. This script installs a bare `footprinter-cli[full]` spec,
# which no longer resolves, so a curl-pipe run of it fails partway through.
#
# This file is retained for historical reference only. Its commands are
# preserved exactly as they shipped and are NOT current instructions.
# ============================================================================
#
# Footprinter — full install
#
# Installs footprinter-cli[full]: base package plus semantic search
# (sentence-transformers, chromadb, ONNX runtime) and document parsing
# extras. Pulls roughly 250MB of additional dependencies on first install.
#
# Curl-pipe usage:
#   curl -fsSL https://raw.githubusercontent.com/harringjohn/footprinter-cli/main/scripts/release/install-full.sh | bash
#
# What this script does:
#   1. Ensures Python ≥ 3.11 is installed (downloads python.org .pkg if not)
#   2. pip installs footprinter-cli[full] into the user site-packages
#   3. Verifies `fp --version` works

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"

# When run as `curl … | bash`, BASH_SOURCE[0] is empty, so SCRIPT_DIR
# falls back to the user's cwd and the helper isn't beside us. Always
# refetch in that case so we get a fresh copy and avoid stale /tmp
# caches between runs of install.sh and install-full.sh.
COMMON="${SCRIPT_DIR}/_install_common.sh"
if [ ! -f "$COMMON" ]; then
    # mktemp creates the file; curl writes into it. Reusing the same path
    # avoids the orphan tempfile that "$(mktemp).sh" leaves behind.
    COMMON="$(mktemp -t footprinter-install-common)"
    curl -fsSL "https://raw.githubusercontent.com/harringjohn/footprinter-cli/main/scripts/release/_install_common.sh" -o "$COMMON"
fi
# shellcheck source=_install_common.sh
source "$COMMON"

echo ""
echo "  Footprinter — Full Install"
echo "  (includes semantic search + document parsing — ~250MB of extras)"
echo ""

ensure_python_3_11
pip_install_footprinter "[full]"
verify_fp
# verify_fp owns the closing message — see UAT F3 in _install_common.sh.
