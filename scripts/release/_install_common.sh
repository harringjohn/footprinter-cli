#!/usr/bin/env bash
#
# Shared helpers for install.sh and install-full.sh.
#
# Sourced — not executed directly. Defines:
#   ensure_python_3_11        - find or install Python ≥ 3.11
#   pip_install_footprinter   - pip install footprinter-cli with optional extras
#   verify_fp                 - confirm `fp --version` works
#
# PEP 668 note: python.org's CPython installer does NOT ship the
# EXTERNALLY-MANAGED marker that Homebrew/Debian/Fedora add to protect
# their package managers. We prefer python.org Python so `pip install
# --user` works without --break-system-packages. _find_python_3_11_plus
# skips any interpreter that ships the marker — Homebrew is the common
# case on macOS, but the check is marker-based, not vendor-based, so it
# also covers Debian/Fedora-style interpreters and future distros that
# adopt PEP 668. UAT Env 2 (Mac Mini, 2026-04-28) was burned by exactly
# this trap on Homebrew Python.

set -euo pipefail

# Pin a known-stable Python release. Universal2 .pkg covers arm64 + x86_64.
PYTHON_VERSION="3.12.7"
PYTHON_PKG_URL="https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-macos11.pkg"

_min_python_satisfied() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

# True if the interpreter at $1 ships PEP 668's EXTERNALLY-MANAGED marker.
# Homebrew / Debian / Fedora set this; python.org and pyenv do not.
_python_externally_managed() {
    "$1" -c '
import os, sys, sysconfig
stdlib = sysconfig.get_paths()["stdlib"]
sys.exit(0 if os.path.exists(os.path.join(stdlib, "EXTERNALLY-MANAGED")) else 1)
' 2>/dev/null
}

_python_version_str() {
    "$1" -c 'import sys; print(".".join(str(p) for p in sys.version_info[:3]))' 2>/dev/null
}

_find_python_3_11_plus() {
    local candidate path
    for candidate in python3.13 python3.12 python3.11 python3; do
        if ! command -v "$candidate" >/dev/null 2>&1; then
            continue
        fi
        path="$(command -v "$candidate")"
        if ! _min_python_satisfied "$path"; then
            continue
        fi
        # Skip externally-managed interpreters — pip install --user fails on them.
        if _python_externally_managed "$path"; then
            continue
        fi
        echo "$path"
        return 0
    done
    return 1
}

ensure_python_3_11() {
    local found version
    if found="$(_find_python_3_11_plus)"; then
        version="$(_python_version_str "$found")"
        echo "==> Python ${version} is already installed (${found})."
        PYTHON_BIN="$found"
        return 0
    fi

    echo "==> Installing Python ${PYTHON_VERSION} from python.org..."
    local pkg="/tmp/footprinter-python-${PYTHON_VERSION}.pkg"
    curl -fsSL "$PYTHON_PKG_URL" -o "$pkg"
    sudo installer -pkg "$pkg" -target /
    rm -f "$pkg"

    # The installer drops symlinks in /usr/local/bin; pick them up without a shell restart.
    export PATH="/usr/local/bin:/Library/Frameworks/Python.framework/Versions/${PYTHON_VERSION%.*}/bin:$PATH"

    if ! found="$(_find_python_3_11_plus)"; then
        echo "ERROR: Python ${PYTHON_VERSION} install completed but no python3 ≥ 3.11 is on PATH." >&2
        echo "  Try opening a new terminal and re-running this script." >&2
        exit 1
    fi
    PYTHON_BIN="$found"
}

pip_install_footprinter() {
    local extras="${1:-}"  # "" or "[full]"
    local spec="footprinter-cli${extras}"

    echo "==> Installing ${spec}..."
    "$PYTHON_BIN" -m pip install --user --upgrade pip >/dev/null
    # Stale cache from an older Python emits ~30 "Cache entry deserialization
    # failed" warnings that dominate the output. Purge before install.
    # `|| true` keeps `set -e` happy on ancient pip without `cache purge`.
    "$PYTHON_BIN" -m pip cache purge >/dev/null 2>&1 || true
    # --no-warn-script-location: we add USER_BIN to PATH ourselves below;
    # pip's per-script warnings are redundant noise.
    "$PYTHON_BIN" -m pip install --user --upgrade --no-warn-script-location "$spec"

    USER_BIN="$("$PYTHON_BIN" -m site --user-base)/bin"
}

# Append `export PATH=...USER_BIN...` to the user's shell rc so future
# terminal sessions find `fp`. Idempotent — skips if the line is already
# present. Returns the rc path on stdout, or empty if no rc was patched.
_persist_user_bin_on_path() {
    local target_line="export PATH=\"${USER_BIN}:\$PATH\""
    local rc=""
    case "${SHELL:-}" in
        */zsh)  rc="$HOME/.zshrc" ;;
        */bash) rc="$HOME/.bash_profile" ;;
        *)      [ -f "$HOME/.zshrc" ] && rc="$HOME/.zshrc" \
                || { [ -f "$HOME/.bash_profile" ] && rc="$HOME/.bash_profile"; } \
                || rc="$HOME/.profile" ;;
    esac
    if [ -f "$rc" ] && grep -Fq "$target_line" "$rc"; then
        echo "$rc"
        return 0
    fi
    {
        echo ""
        echo "# Added by footprinter install ($(date '+%Y-%m-%d'))"
        echo "$target_line"
    } >> "$rc"
    echo "$rc"
}

verify_fp() {
    # Check the user's *real* PATH first — before our inline export — so
    # we know whether future shell sessions will find `fp` on their own.
    local user_path_has_fp=0
    if command -v fp >/dev/null 2>&1; then
        user_path_has_fp=1
    fi

    local rc=""
    if [ "$user_path_has_fp" -eq 0 ]; then
        rc="$(_persist_user_bin_on_path)"
    fi

    # Make this run's freshly-installed user bin findable so `fp --version`
    # below works without forcing the user to start a new shell.
    export PATH="${USER_BIN}:$PATH"

    echo "==> Verifying install..."
    fp --version

    # Closing message must be the literal last line on screen. When fp
    # isn't already on PATH, that means a bordered ACTION REQUIRED block
    # — telling users to "Run fp setup" before they've opened a new
    # terminal would just hand them a `command not found` (UAT F3).
    # Callers (install.sh, install-full.sh) intentionally print nothing
    # after verify_fp so this stays the final word.
    echo ""
    if [ "$user_path_has_fp" -eq 0 ]; then
        echo "────────────────────────────────────────────────────────────"
        echo "  ACTION REQUIRED — open a new terminal before using \`fp\`"
        echo "  (or run:  source ${rc})"
        echo ""
        echo "  Added ${USER_BIN} to PATH in ${rc};"
        echo "  existing shells haven't picked it up yet."
        echo ""
        echo "  Then run:  fp setup"
        echo "────────────────────────────────────────────────────────────"
    else
        echo "==> Done! Run 'fp setup' to get started."
    fi
}
