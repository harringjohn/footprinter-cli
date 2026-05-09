#!/usr/bin/env bash
#
# Footprinter — base install
#
# Installs the footprinter-cli package: CLI + MCP server + thin HTTP API.
# Does NOT include semantic search or document parsing — use install-full.sh
# for that.
#
# Curl-pipe usage:
#   curl -fsSL https://raw.githubusercontent.com/swellcitygroup/footprinter/main/scripts/release/install.sh | bash
#
# What this script does:
#   1. Ensures Python ≥ 3.11 is installed (downloads python.org .pkg if not)
#   2. pip installs footprinter-cli into the user site-packages
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
echo "  Footprinter — Install"
echo ""

ensure_python_3_11
pip_install_footprinter ""
verify_fp
# verify_fp owns the closing message — see UAT F3 in _install_common.sh.
