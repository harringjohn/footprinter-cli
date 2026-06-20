#!/bin/bash
#
# CLI Surface Verification for Footprinter.
#
# Lightweight outside-in check of every user-facing command. No fixture
# dependency — creates its own minimal sample data. Catches help-text
# regressions, missing commands, tracebacks in error paths, and output
# format drift.
#
# Usage:
#     bash scripts/cli_verify.sh
#
# Exit code: 0 if all pass, 1 if any fail.

set -euo pipefail

if [ "$(uname -s)" = "Darwin" ] \
    && [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ] \
    && [ "$(arch)" != "arm64" ]; then
    if [ -z "${FPR_ARCH_REEXEC:-}" ]; then
        # Re-exec once to force native arm64. The sentinel is exported BEFORE
        # the exec so the re-exec'd process inherits it and the guard below
        # cannot fire a second time — making the loop structurally impossible
        # under a single-arch x86_64 bash (e.g. Intel Homebrew under Rosetta).
        export FPR_ARCH_REEXEC=1
        exec arch -arm64 "$0" "$@"
    else
        echo "WARN: could not obtain a native arm64 bash (current bash is x86_64-only, e.g. Intel Homebrew); continuing under the current interpreter. Install an arm64 bash or run under /bin/bash for native execution." >&2
    fi
fi

SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLONE_DIR="/tmp/footprinter-cli-verify"
PYTHON_BIN="python3.11"

PASS=0
FAIL=0
SKIP=0

# ─── Output ─────────────────────────────────────────────────────────

green()  { printf "\033[32m✓ %s\033[0m\n" "$1"; }
red()    { printf "\033[31m✗ %s\033[0m\n" "$1"; }
yellow() { printf "\033[33m⊘ %s\033[0m\n" "$1"; }
header() { printf "\n\033[1;36m── %s ──\033[0m\n" "$1"; }
banner() { printf "\n\033[1;35m═══ %s ═══\033[0m\n" "$1"; }

pass() { green "$1"; PASS=$((PASS + 1)); }
fail() { red "$1";   FAIL=$((FAIL + 1)); }
skip() { yellow "$1"; SKIP=$((SKIP + 1)); }

BIN() { echo "$CLONE_DIR/venv/bin/$1"; }

# ─── Assertions ──────────────────────────────────────────────────────

assert_exit_code() {
    local label="$1" expected="$2"
    shift 2
    local actual
    "$@" >/dev/null 2>&1 && actual=0 || actual=$?
    if [ "$actual" -eq "$expected" ]; then
        pass "$label (exit $expected)"
    else
        fail "$label — expected exit $expected, got $actual"
    fi
}

assert_output_contains() {
    local label="$1" expected="$2"
    shift 2
    local output
    output=$("$@" 2>&1 || true)
    if echo "$output" | grep -qi "$expected"; then
        pass "$label"
    else
        fail "$label — expected '$expected' in output"
        echo "    got: ${output:0:200}"
    fi
}

assert_output_excludes() {
    local label="$1" excluded="$2"
    shift 2
    local output
    output=$("$@" 2>&1 || true)
    if echo "$output" | grep -qi "$excluded"; then
        fail "$label — found '$excluded' in output"
        echo "    got: ${output:0:200}"
    else
        pass "$label"
    fi
}

assert_valid_json() {
    local label="$1"
    shift
    local output
    output=$("$@" 2>/dev/null || true)
    if echo "$output" | "$(BIN python3)" -c "
import sys, json
text = sys.stdin.read()
obj = text.find('{')
arr = text.find('[')
candidates = [i for i in (obj, arr) if i >= 0]
if candidates:
    json.loads(text[min(candidates):])
    sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
        pass "$label"
    else
        fail "$label — invalid JSON"
        echo "    got: ${output:0:200}"
    fi
}

assert_no_traceback() {
    local label="$1"
    shift
    local output
    output=$("$@" 2>&1 || true)
    if echo "$output" | grep -q "Traceback"; then
        fail "$label — raw traceback"
        echo "    ${output:0:300}"
    else
        pass "$label"
    fi
}

# ─── Setup ───────────────────────────────────────────────────────────

setup() {
    banner "CLI Surface Verification"

    header "Setup: clone + venv + install"

    rm -rf "$CLONE_DIR"
    git clone --quiet "$SOURCE_DIR" "$CLONE_DIR"
    echo "  Cloned to $CLONE_DIR"

    if ! command -v "$PYTHON_BIN" &>/dev/null; then
        echo "ERROR: $PYTHON_BIN not found on PATH"
        exit 2
    fi
    "$PYTHON_BIN" -m venv "$CLONE_DIR/venv"
    "$(BIN pip)" install --quiet --upgrade pip
    "$(BIN pip)" install --quiet -e "$CLONE_DIR"
    echo "  Installed: pip install -e ."

    export FOOTPRINTER_HOME="$CLONE_DIR/.footprinter-test"
    mkdir -p "$FOOTPRINTER_HOME"

    # Seed minimal config from bundled example
    cp "$CLONE_DIR/footprinter/bundled/config.example.yaml" \
       "$FOOTPRINTER_HOME/config.yaml"

    # Create a small file tree for ingest tests
    local content_dir="$FOOTPRINTER_HOME/Work/sample"
    mkdir -p "$content_dir"
    echo "Hello from CLI verify" > "$content_dir/readme.txt"
    echo "def main(): pass" > "$content_dir/app.py"

    # Point config at our content
    "$(BIN python3)" - "$FOOTPRINTER_HOME/config.yaml" "$content_dir" <<'PY'
import sys, yaml
config_path, content_dir = sys.argv[1], sys.argv[2]
with open(config_path) as f:
    cfg = yaml.safe_load(f)
cfg["directories"] = [content_dir]
with open(config_path, "w") as f:
    yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
PY
    echo "  FOOTPRINTER_HOME=$FOOTPRINTER_HOME"
}

cleanup() {
    rm -rf "$CLONE_DIR"
    unset FOOTPRINTER_HOME
}

# ═══════════════════════════════════════════════════════════════════════
# Phase 1: Entry Point
# ═══════════════════════════════════════════════════════════════════════

phase_entry_point() {
    header "Phase 1: Entry Point"

    if [ -f "$(BIN fp)" ]; then
        pass "fp is installed"
    else
        fail "fp not found in venv/bin/"
    fi

    assert_exit_code "fp --help exits 0" 0 "$(BIN fp)" --help
    assert_exit_code "fp --version exits 0" 0 "$(BIN fp)" --version

    for old_cmd in fp-setup fp-orchestrator fp-search fp-status; do
        if [ -f "$(BIN "$old_cmd")" ]; then
            fail "$old_cmd still exists (should be removed)"
        else
            pass "$old_cmd absent (correct)"
        fi
    done
}

# ═══════════════════════════════════════════════════════════════════════
# Phase 2: Help Text — every command's --help exits 0
# ═══════════════════════════════════════════════════════════════════════

phase_help_text() {
    header "Phase 2: Help Text"

    local commands=(
        setup ingest status search connect
        view add update delete doctor permission uninstall
    )
    for cmd in "${commands[@]}"; do
        assert_exit_code "fp $cmd --help exits 0" 0 "$(BIN fp)" "$cmd" --help
    done

    # Subcommands
    assert_exit_code "fp setup mcp --help exits 0" 0 "$(BIN fp)" setup mcp --help
    assert_exit_code "fp setup folders --help exits 0" 0 "$(BIN fp)" setup folders --help
    assert_exit_code "fp ingest refresh --help exits 0" 0 "$(BIN fp)" ingest refresh --help
    assert_exit_code "fp doctor search --help exits 0" 0 "$(BIN fp)" doctor search --help
    assert_exit_code "fp doctor semantic --help exits 0" 0 "$(BIN fp)" doctor semantic --help
    assert_exit_code "fp permission list --help exits 0" 0 "$(BIN fp)" permission list --help
    assert_exit_code "fp permission set --help exits 0" 0 "$(BIN fp)" permission set --help
    assert_exit_code "fp permission reset --help exits 0" 0 "$(BIN fp)" permission reset --help
    assert_exit_code "fp permission check --help exits 0" 0 "$(BIN fp)" permission check --help
    assert_exit_code "fp permission recalculate --help exits 0" 0 "$(BIN fp)" permission recalculate --help
    assert_exit_code "fp connect list --help exits 0" 0 "$(BIN fp)" connect list --help
    assert_exit_code "fp connect install --help exits 0" 0 "$(BIN fp)" connect install --help

    # Removed commands should NOT exist
    for gone in mcp upsert data vectorize api; do
        if "$(BIN fp)" "$gone" --help >/dev/null 2>&1; then
            fail "fp $gone still exists (expected removed)"
        else
            pass "fp $gone absent (correct)"
        fi
    done
}

# ═══════════════════════════════════════════════════════════════════════
# Phase 3: fp setup
# ═══════════════════════════════════════════════════════════════════════

phase_setup() {
    header "Phase 3: fp setup"

    # MCP subcommand — bare invocation prints snippet
    local output
    output=$("$(BIN fp)" setup mcp 2>&1 || true)
    if echo "$output" | grep -qi "footprinter\|mcpServers\|mcp"; then
        pass "fp setup mcp produces MCP config output"
    else
        fail "fp setup mcp — expected MCP-related output"
        echo "    got: ${output:0:200}"
    fi

    # Folders: add + remove (use a subfolder not already in config)
    local test_folder="$FOOTPRINTER_HOME/Work/sample/subdir"
    mkdir -p "$test_folder"
    assert_exit_code "fp setup folders add exits 0" 0 \
        "$(BIN fp)" setup folders add "$test_folder" --no-index

    # Duplicate should error
    local dup_output dup_exit
    dup_output=$("$(BIN fp)" setup folders add "$test_folder" --no-index 2>&1) && dup_exit=0 || dup_exit=$?
    if [ "$dup_exit" -ne 0 ] || echo "$dup_output" | grep -qi "already\|duplicate\|exists"; then
        pass "fp setup folders add (duplicate) rejected"
    else
        fail "fp setup folders add (duplicate) — expected error"
    fi

    assert_exit_code "fp setup folders remove exits 0" 0 \
        "$(BIN fp)" setup folders remove "$test_folder"

    # Bad path should error
    local bad_output bad_exit
    bad_output=$("$(BIN fp)" setup folders add "/tmp/nonexistent-path-xyz" --no-index 2>&1) && bad_exit=0 || bad_exit=$?
    if [ "$bad_exit" -ne 0 ] || echo "$bad_output" | grep -qi "not found\|does not exist\|invalid\|error\|no such"; then
        pass "fp setup folders add (bad path) rejected"
    else
        fail "fp setup folders add (bad path) — expected error"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
# Phase 4: fp status
# ═══════════════════════════════════════════════════════════════════════

phase_status() {
    header "Phase 4: fp status"

    assert_exit_code "fp status exits 0" 0 "$(BIN fp)" status
    assert_valid_json "fp status --json is valid JSON" "$(BIN fp)" status --json

    # Should contain expected top-level keys
    local json_output
    json_output=$("$(BIN fp)" status --json 2>/dev/null || true)
    for key in counts database config; do
        if echo "$json_output" | grep -q "\"$key\""; then
            pass "fp status --json contains '$key'"
        else
            fail "fp status --json missing '$key'"
        fi
    done
}

# ═══════════════════════════════════════════════════════════════════════
# Phase 5: fp ingest
# ═══════════════════════════════════════════════════════════════════════

phase_ingest() {
    header "Phase 5: fp ingest"

    # Core pipes should complete
    for pipe in local_folders local_files browser; do
        local output exit_code
        output=$("$(BIN fp)" ingest --pipe "$pipe" --quiet 2>&1) && exit_code=0 || exit_code=$?
        if [ "$exit_code" -eq 0 ]; then
            pass "fp ingest --pipe $pipe completes (exit 0)"
        else
            fail "fp ingest --pipe $pipe failed (exit $exit_code)"
            echo "    ${output:0:200}"
        fi
    done

    # Multi-pipe
    assert_exit_code "fp ingest --pipe local_folders,local_files completes" 0 \
        "$(BIN fp)" ingest --pipe local_folders,local_files --quiet

    # --full flag
    assert_exit_code "fp ingest --full completes" 0 \
        "$(BIN fp)" ingest --pipe local_folders --full --quiet

    # Invalid pipe rejected
    local output exit_code
    output=$("$(BIN fp)" ingest --pipe nonexistent_pipe 2>&1) && exit_code=0 || exit_code=$?
    if [ "$exit_code" -ne 0 ] || echo "$output" | grep -qi "unknown\|invalid\|error\|not.*valid\|skipping"; then
        pass "fp ingest rejects invalid pipe name"
    else
        fail "fp ingest accepted invalid pipe without error"
    fi

    # Refresh subcommand
    assert_exit_code "fp ingest refresh local exits 0" 0 "$(BIN fp)" ingest refresh local
    assert_exit_code "fp ingest refresh browser exits 0" 0 "$(BIN fp)" ingest refresh browser

    # Refresh invalid source rejected
    output=$("$(BIN fp)" ingest refresh invalidxyz 2>&1) && exit_code=0 || exit_code=$?
    if [ "$exit_code" -ne 0 ] || echo "$output" | grep -qi "unknown\|invalid\|error\|valid sources"; then
        pass "fp ingest refresh (invalid source) rejected"
    else
        fail "fp ingest refresh (invalid source) — expected error"
    fi

    # Import with no path should error
    output=$("$(BIN fp)" add chats 2>&1) && exit_code=0 || exit_code=$?
    if [ "$exit_code" -ne 0 ]; then
        pass "fp add chats (no path) exits non-zero"
    else
        fail "fp add chats (no path) — expected error"
    fi

    # Import with bad path should error
    output=$("$(BIN fp)" add chats /tmp/nonexistent-export.zip 2>&1) && exit_code=0 || exit_code=$?
    if [ "$exit_code" -ne 0 ]; then
        pass "fp add chats (bad path) exits non-zero"
    else
        fail "fp add chats (bad path) — expected error"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
# Phase 6: fp search
# ═══════════════════════════════════════════════════════════════════════

phase_search() {
    header "Phase 6: fp search"

    # Base mode (no [semantic]) should show install hint and exit 0
    assert_output_contains \
        "fp search without [semantic] shows install hint" \
        "pip install" \
        "$(BIN fp)" search "test query"

    local exit_code
    "$(BIN fp)" search "test query" >/dev/null 2>&1 && exit_code=0 || exit_code=$?
    if [ "$exit_code" -eq 0 ]; then
        pass "fp search falls back to keyword (exit 0)"
    else
        fail "fp search exited $exit_code (should fall back)"
    fi

    # No raw traceback
    assert_no_traceback "fp search — no traceback" "$(BIN fp)" search "test query"

    # Bare invocation should show help cleanly
    local output
    output=$("$(BIN fp)" search 2>&1) && exit_code=0 || exit_code=$?
    if ! echo "$output" | grep -q "the following arguments are required"; then
        pass "fp search (no query) shows clean help"
    else
        fail "fp search (no query) shows raw argparse error"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
# Phase 7: fp doctor
# ═══════════════════════════════════════════════════════════════════════

phase_doctor() {
    header "Phase 7: fp doctor"

    # doctor exits 0 even with WARNs; only FAILs produce non-zero
    local doctor_exit
    "$(BIN fp)" doctor >/dev/null 2>&1 && doctor_exit=0 || doctor_exit=$?
    if [ "$doctor_exit" -le 1 ]; then
        pass "fp doctor exits cleanly ($doctor_exit)"
    else
        fail "fp doctor exited $doctor_exit"
    fi
    assert_valid_json "fp doctor --json is valid JSON" "$(BIN fp)" doctor --json
    assert_no_traceback "fp doctor — no traceback" "$(BIN fp)" doctor
}

# ═══════════════════════════════════════════════════════════════════════
# Phase 8: fp permission
# ═══════════════════════════════════════════════════════════════════════

phase_permission() {
    header "Phase 8: fp permission"

    assert_exit_code "fp permission list exits 0" 0 "$(BIN fp)" permission list
    assert_exit_code "fp permission recalculate exits 0" 0 "$(BIN fp)" permission recalculate

    # Set a policy
    assert_exit_code "fp permission set exits 0" 0 \
        "$(BIN fp)" permission set "folder:$FOOTPRINTER_HOME/Work" \
        --visibility full --access allow

    # Check resolution
    assert_exit_code "fp permission check exits 0" 0 \
        "$(BIN fp)" permission check "$FOOTPRINTER_HOME/Work/sample/readme.txt"

    # Reset the policy
    assert_exit_code "fp permission reset exits 0" 0 \
        "$(BIN fp)" permission reset "folder:$FOOTPRINTER_HOME/Work"

    # Invalid scope should error
    local output exit_code
    output=$("$(BIN fp)" permission set "global" --visibility invalidxyz 2>&1) && exit_code=0 || exit_code=$?
    if [ "$exit_code" -ne 0 ] || echo "$output" | grep -qi "invalid\|error\|unknown\|choose\|argument"; then
        pass "fp permission set (invalid visibility) rejected"
    else
        fail "fp permission set (invalid visibility) — expected error"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
# Phase 9: fp view
# ═══════════════════════════════════════════════════════════════════════

phase_view() {
    header "Phase 9: fp view"

    # List views (plural nouns) should all exit 0
    for noun in folders files projects clients chats emails visits; do
        assert_exit_code "fp view $noun exits 0" 0 "$(BIN fp)" view "$noun"
    done

    # JSON output for key list views
    for noun in folders projects clients chats; do
        assert_valid_json "fp view $noun --json" "$(BIN fp)" view "$noun" --json
    done

    # --limit flag
    assert_exit_code "fp view files --limit 5 exits 0" 0 \
        "$(BIN fp)" view files --limit 5

    # Single view with bad ID should error gracefully
    for noun in project client chat file; do
        assert_no_traceback "fp view $noun 999999 — no traceback" \
            "$(BIN fp)" view "$noun" 999999
    done
}

# ═══════════════════════════════════════════════════════════════════════
# Phase 10: fp add / fp update / fp delete
# ═══════════════════════════════════════════════════════════════════════

phase_entity_crud() {
    header "Phase 10: fp add / fp update / fp delete"

    # Create a project
    assert_exit_code "fp add project --name exits 0" 0 \
        "$(BIN fp)" add project --name "CLI-Verify-Test"

    # Create a client
    assert_exit_code "fp add client --name exits 0" 0 \
        "$(BIN fp)" add client --name "CLI-Verify-Client" --type external

    # Update (need IDs from the DB)
    local project_id client_id
    project_id=$("$(BIN python3)" -c "
import sqlite3, os
db = os.path.join(os.environ['FOOTPRINTER_HOME'], 'footprinter.db')
conn = sqlite3.connect(db)
row = conn.execute(\"SELECT id FROM projects WHERE name = 'CLI-Verify-Test'\").fetchone()
print(row[0] if row else '')
" 2>/dev/null)

    client_id=$("$(BIN python3)" -c "
import sqlite3, os
db = os.path.join(os.environ['FOOTPRINTER_HOME'], 'footprinter.db')
conn = sqlite3.connect(db)
row = conn.execute(\"SELECT id FROM clients WHERE name = 'CLI-Verify-Client'\").fetchone()
print(row[0] if row else '')
" 2>/dev/null)

    if [ -n "$project_id" ]; then
        assert_exit_code "fp update project exits 0" 0 \
            "$(BIN fp)" update project "$project_id" --name "CLI-Verify-Renamed"
    else
        skip "fp update project — could not resolve ID"
    fi

    # fp add with no flags should error
    local output exit_code
    output=$("$(BIN fp)" add project 2>&1) && exit_code=0 || exit_code=$?
    if [ "$exit_code" -ne 0 ] || echo "$output" | grep -qi "required\|error\|name"; then
        pass "fp add project (no flags) rejected"
    else
        fail "fp add project (no flags) — expected error"
    fi

    # fp delete --help (don't actually delete)
    assert_exit_code "fp delete client --help exits 0" 0 \
        "$(BIN fp)" delete client --help
    assert_exit_code "fp delete project --help exits 0" 0 \
        "$(BIN fp)" delete project --help
}

# ═══════════════════════════════════════════════════════════════════════
# Phase 11: fp connect
# ═══════════════════════════════════════════════════════════════════════

phase_connect() {
    header "Phase 11: fp connect"

    assert_exit_code "fp connect list exits 0" 0 "$(BIN fp)" connect list
    assert_no_traceback "fp connect list — no traceback" "$(BIN fp)" connect list
}

# ═══════════════════════════════════════════════════════════════════════
# Phase 12: Error UX — no raw tracebacks
# ═══════════════════════════════════════════════════════════════════════

phase_error_ux() {
    header "Phase 12: Error UX"

    # Bad DB path
    assert_no_traceback "fp status with bad DB — no traceback" \
        env FOOTPRINTER_DB_PATH="/tmp/nonexistent/path/db.sqlite" "$(BIN fp)" status

    # Bad DB + JSON should still be graceful
    assert_no_traceback "fp status --json with bad DB — no traceback" \
        env FOOTPRINTER_DB_PATH="/tmp/nonexistent/path/db.sqlite" "$(BIN fp)" status --json

    # Common error commands
    assert_no_traceback "fp add chats (no path) — no traceback" "$(BIN fp)" add chats
    assert_no_traceback "fp delete client (no ID) — no traceback" "$(BIN fp)" delete client
}

# ═══════════════════════════════════════════════════════════════════════
# Phase 13: Module Importability
# ═══════════════════════════════════════════════════════════════════════

phase_modules() {
    header "Phase 13: Module Importability"

    local modules=(
        "footprinter.cli"
        "footprinter.cli.setup"
        "footprinter.cli.ingest"
        "footprinter.cli.status"
        "footprinter.cli.search"
        "footprinter.mcp"
    )

    for mod in "${modules[@]}"; do
        local output
        output=$("$(BIN python3)" -c "import ${mod}" 2>&1 || true)
        if ! echo "$output" | grep -q "ModuleNotFoundError"; then
            pass "$mod importable"
        else
            fail "$mod import failed"
        fi
    done

    # MCP server should have main + _build_server
    local output
    output=$("$(BIN python3)" -c "from footprinter.mcp.server import main, _build_server; print('OK')" 2>&1)
    if echo "$output" | grep -q "OK"; then
        pass "footprinter.mcp.server exports main + _build_server"
    else
        fail "footprinter.mcp.server missing expected exports"
    fi

    # MCP tool count
    output=$("$(BIN python3)" -c "
import os
os.environ.setdefault('FOOTPRINTER_HOME', '$FOOTPRINTER_HOME')
from footprinter.mcp.server import _build_server
server = _build_server()
tools = list(server._tool_manager._tools.keys())
print(f'TOOLS:{len(tools)}')
" 2>&1 || true)
    local tool_count
    tool_count=$(echo "$output" | grep "TOOLS:" | sed 's/TOOLS://')
    if [ -n "$tool_count" ] && [ "$tool_count" -ge 6 ]; then
        pass "MCP server: $tool_count tools registered (>= 6)"
    else
        fail "MCP server: expected >= 6 tools"
        echo "    got: ${output:0:200}"
    fi

    # footprinter.fixture should NOT be importable
    if "$(BIN python3)" -c "import footprinter.fixture" 2>/dev/null; then
        fail "footprinter.fixture is importable (should be excluded)"
    else
        pass "footprinter.fixture absent (correct)"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

setup

phase_entry_point
phase_help_text
phase_setup
phase_status
phase_ingest
phase_search
phase_doctor
phase_permission
phase_view
phase_entity_crud
phase_connect
phase_error_ux
phase_modules

cleanup

echo ""
echo "═══════════════════════════════════════"
printf "  \033[32m%d passed\033[0m" "$PASS"
if [ "$SKIP" -gt 0 ]; then
    printf ", \033[33m%d skipped\033[0m" "$SKIP"
fi
if [ "$FAIL" -gt 0 ]; then
    printf ", \033[31m%d failed\033[0m" "$FAIL"
fi
echo ""
echo "═══════════════════════════════════════"

exit $((FAIL > 0 ? 1 : 0))
