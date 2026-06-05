#!/usr/bin/env bash
#
# QA tier dispatcher.
#
# Routes to individual QA scripts by tier name.
# Run with --list to see available tiers.
#
# Usage:
#   bash scripts/qa.sh <tier> [args...]
#   bash scripts/qa.sh --list
#   bash scripts/qa.sh all

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
    --list)
        echo "QA Tiers:"
        echo ""
        echo "  Tier  Name              Description"
        echo "  ────  ────              ───────────"
        echo "  1     pytest            Unit + integration tests (~3,600 TDD tests)"
        echo "  2     smoke             Post-install canary (entry points, imports, fixture boundary)"
        echo "  3     cli-verify        Full CLI surface verification (every command's behavior + error UX)"
        echo "  4     verify-upgrade    Upgrade-path verification (requires version args)"
        echo "  5     verify-install    Installed-package verification (requires version arg)"
        echo ""
        echo "Usage:"
        echo "  bash scripts/qa.sh smoke"
        echo "  bash scripts/qa.sh cli-verify"
        echo "  bash scripts/qa.sh verify-upgrade <target-version> --from <base-version>"
        echo "  bash scripts/qa.sh verify-install <version> [--with-pytest]"
        echo "  bash scripts/qa.sh all              (runs tiers that need no extra args)"
        echo ""
        echo "Excluded from 'all': verify-upgrade, verify-install (need version args)"
        echo "and cli-verify (heavy — clones the source and builds a throwaway venv; run on demand)."
        ;;
    pytest)
        shift
        exec "$SCRIPT_DIR/../venv/bin/python3" -m pytest "$@"
        ;;
    smoke)
        exec bash "$SCRIPT_DIR/snapshot-qa/smoke.sh"
        ;;
    cli-verify)
        exec bash "$SCRIPT_DIR/cli_verify.sh"
        ;;
    verify-upgrade)
        shift
        exec bash "$SCRIPT_DIR/release/verify_upgrade.sh" "$@"
        ;;
    verify-install)
        shift
        exec bash "$SCRIPT_DIR/release/verify_install.sh" "$@"
        ;;
    all)
        echo "=== Tier 1: pytest ==="
        "$SCRIPT_DIR/../venv/bin/python3" -m pytest
        echo ""
        echo "=== Tier 2: smoke ==="
        bash "$SCRIPT_DIR/snapshot-qa/smoke.sh"
        echo ""
        echo "--- all argument-free tiers passed ---"
        ;;
    *)
        echo "Usage: bash scripts/qa.sh <tier> [args...]" >&2
        echo "Run 'bash scripts/qa.sh --list' for available tiers." >&2
        exit 1
        ;;
esac
