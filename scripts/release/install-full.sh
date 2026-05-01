#!/usr/bin/env bash
#
# Footprinter — full install
#
# Installs footprinter-cli[full]: base package plus semantic search
# (sentence-transformers, chromadb, ONNX runtime) and document parsing
# extras. Pulls roughly 250MB of additional dependencies on first install.
#
# Curl-pipe usage:
#   curl -fsSL https://raw.githubusercontent.com/swellcitygroup/footprinter/main/scripts/release/install-full.sh | bash
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
    curl -fsSL "https://raw.githubusercontent.com/swellcitygroup/footprinter/main/scripts/release/_install_common.sh" -o "$COMMON"
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

echo ""
echo "==> Done! Run 'fp setup' to get started."
