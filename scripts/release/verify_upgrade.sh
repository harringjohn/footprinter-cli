#!/usr/bin/env bash
#
# Tier 4 release gate: upgrade-path verification.
#
# Installs a previous footprinter-cli release from PyPI, populates
# synthetic data, upgrades to the local wheel, and asserts that data
# survives the upgrade — entity counts, status values, CLI commands.
#
# Usage:
#   bash scripts/release/verify_upgrade.sh <target-version> --from <base-version>
#
# Example:
#   bash scripts/release/verify_upgrade.sh 1.0.4 --from 1.0.3
#
# Exit code: 0 on full pass, 1 on any failure.

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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
    echo "Usage: bash $0 <target-version> --from <base-version>" >&2
    echo "" >&2
    echo "  <target-version>  Version being released (must have a local wheel in dist/)" >&2
    echo "  --from <version>  Previous PyPI release to upgrade from" >&2
    echo "" >&2
    echo "Example:" >&2
    echo "  bash $0 1.0.4 --from 1.0.3" >&2
    exit 1
}

# ── Phase 0: Arg parsing & environment ────────────────────────────────

[ $# -ge 3 ] || usage

TARGET_VERSION="$1"
shift

FROM_VERSION=""
while [ $# -gt 0 ]; do
    case "$1" in
        --from)
            [ $# -ge 2 ] || usage
            FROM_VERSION="$2"
            shift 2
            ;;
        *)
            usage
            ;;
    esac
done

[ -n "$FROM_VERSION" ] || usage

# Locate local wheel
WHEEL_PATH=""
for whl in "${SCRIPT_DIR}/../../dist/footprinter_cli-${TARGET_VERSION}"*.whl; do
    [ -f "$whl" ] && WHEEL_PATH="$whl" && break
done

if [ -z "$WHEEL_PATH" ]; then
    echo "ERROR: No wheel found for version ${TARGET_VERSION} in dist/" >&2
    echo "Build it first:  python -m build" >&2
    exit 1
fi

WHEEL_PATH="$(cd "$(dirname "$WHEEL_PATH")" && pwd)/$(basename "$WHEEL_PATH")"

echo ""
echo "  verify-upgrade: ${FROM_VERSION} → ${TARGET_VERSION}"
echo "  wheel: ${WHEEL_PATH}"
echo ""

# Source shared helpers for Python discovery
# shellcheck source=_install_common.sh
source "${SCRIPT_DIR}/_install_common.sh"
ensure_python_3_11

WORKDIR="$(mktemp -d -t verify-upgrade-XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "==> Workspace: ${WORKDIR}"

# ── Phase 1: Install previous release from PyPI ──────────────────────

echo ""
echo "==> Phase 1: Installing footprinter-cli==${FROM_VERSION} from PyPI..."

"$PYTHON_BIN" -m venv "$WORKDIR/venv"
VENV_PY="$WORKDIR/venv/bin/python3"
VENV_FP="$WORKDIR/venv/bin/fp"

"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install "footprinter-cli==${FROM_VERSION}" >/dev/null 2>&1 \
    || { echo "ERROR: Failed to install footprinter-cli==${FROM_VERSION} from PyPI" >&2; exit 1; }

# Isolate from user's real data
export FOOTPRINTER_HOME="$WORKDIR/home"
export FOOTPRINTER_DB_PATH="$WORKDIR/home/footprinter.db"
mkdir -p "$WORKDIR/home"

OLD_VERSION=$("$VENV_FP" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
echo "  Installed: ${OLD_VERSION}"

# ── Phase 2: Populate synthetic data ─────────────────────────────────

echo ""
echo "==> Phase 2: Populating synthetic data under v${FROM_VERSION}..."

"$VENV_PY" <<'PYEOF'
import os
import sqlite3

from footprinter.ingest.database import Database

db_path = os.environ["FOOTPRINTER_DB_PATH"]
os.makedirs(os.path.dirname(db_path), exist_ok=True)

db = Database(db_path)
db.conn.close()

conn = sqlite3.connect(db_path)

# Sources (FK target for files/folders)
conn.execute(
    "INSERT OR IGNORE INTO sources (name, source_type, adapter, account, label, icon, enabled) "
    "VALUES ('local', 'file', 'local_fs', NULL, 'Local Files', 'folder', 1)"
)

# Clients: 2 rows (1 listed, 1 unlisted)
conn.execute(
    "INSERT INTO clients (name, slug, client_type, status) "
    "VALUES ('Acme Corp', 'acme-corp', 'business', 'listed')"
)
conn.execute(
    "INSERT INTO clients (name, slug, client_type, status) "
    "VALUES ('Retired LLC', 'retired-llc', 'business', 'unlisted')"
)

# Projects: 2 rows (1 listed, 1 removed)
conn.execute(
    "INSERT INTO projects (name, status) "
    "VALUES ('Alpha Project', 'listed')"
)
conn.execute(
    "INSERT INTO projects (name, status) "
    "VALUES ('Dead Project', 'removed')"
)

# Folders: 3 rows (2 listed, 1 unlisted) — critical for status migration
conn.execute(
    "INSERT INTO folders (path, relative_path, name, source, status) "
    "VALUES ('/tmp/upgrade-test/docs', 'docs', 'docs', 'local', 'listed')"
)
conn.execute(
    "INSERT INTO folders (path, relative_path, name, source, status) "
    "VALUES ('/tmp/upgrade-test/src', 'src', 'src', 'local', 'listed')"
)
conn.execute(
    "INSERT INTO folders (path, relative_path, name, source, status) "
    "VALUES ('/tmp/upgrade-test/old', 'old', 'old', 'local', 'unlisted')"
)

# Files: 5 rows (3 listed, 1 unlisted, 1 removed) — critical for status migration
conn.execute(
    "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
    "VALUES ('readme.md', '/tmp/upgrade-test/docs/readme.md', 'local', 'listed', 'text', 1024)"
)
conn.execute(
    "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
    "VALUES ('main.py', '/tmp/upgrade-test/src/main.py', 'local', 'listed', 'text', 2048)"
)
conn.execute(
    "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
    "VALUES ('utils.py', '/tmp/upgrade-test/src/utils.py', 'local', 'listed', 'text', 512)"
)
conn.execute(
    "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
    "VALUES ('draft.txt', '/tmp/upgrade-test/old/draft.txt', 'local', 'unlisted', 'text', 256)"
)
conn.execute(
    "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
    "VALUES ('deleted.log', '/tmp/upgrade-test/deleted.log', 'local', 'removed', 'text', 128)"
)

# Chats: 2 rows (1 listed, 1 removed)
conn.execute(
    "INSERT INTO chats (external_id, account, title, message_count, status) "
    "VALUES ('chat-upgrade-1', 'personal', 'Planning Chat', 5, 'listed')"
)
conn.execute(
    "INSERT INTO chats (external_id, account, title, message_count, status) "
    "VALUES ('chat-upgrade-2', 'personal', 'Old Chat', 2, 'removed')"
)

# Messages: 3 rows under the chats (all listed)
chat1_id = conn.execute("SELECT id FROM chats WHERE external_id='chat-upgrade-1'").fetchone()[0]
conn.execute(
    "INSERT INTO messages (chat_id, role, content, status) "
    "VALUES (?, 'user', 'Hello world', 'listed')",
    (chat1_id,),
)
conn.execute(
    "INSERT INTO messages (chat_id, role, content, status) "
    "VALUES (?, 'assistant', 'Hi there', 'listed')",
    (chat1_id,),
)
chat2_id = conn.execute("SELECT id FROM chats WHERE external_id='chat-upgrade-2'").fetchone()[0]
conn.execute(
    "INSERT INTO messages (chat_id, role, content, status) "
    "VALUES (?, 'user', 'Old message', 'listed')",
    (chat2_id,),
)

# Emails: 2 rows (1 listed, 1 unlisted)
conn.execute(
    "INSERT INTO emails (message_id, thread_id, account, subject, from_address, received_at, status) "
    "VALUES ('msg-001', 'thread-001', 'work', 'Project Update', 'alice@example.com', '2026-01-15 10:00:00', 'listed')"
)
conn.execute(
    "INSERT INTO emails (message_id, thread_id, account, subject, from_address, received_at, status) "
    "VALUES ('msg-002', 'thread-002', 'work', 'Old Newsletter', 'news@example.com', '2025-06-01 08:00:00', 'unlisted')"
)

# Visits: 2 rows (1 listed, 1 removed)
conn.execute(
    "INSERT INTO visits (url, title, visit_time, browser, status) "
    "VALUES ('https://example.com/docs', 'Example Docs', '2026-01-20 14:30:00', 'safari', 'listed')"
)
conn.execute(
    "INSERT INTO visits (url, title, visit_time, browser, status) "
    "VALUES ('https://old-site.com', 'Old Site', '2025-03-10 09:00:00', 'safari', 'removed')"
)

conn.commit()
conn.close()
print("  Synthetic seed: 8 entity tables populated")
PYEOF

# ── Phase 3: Snapshot pre-upgrade state ───────────────────────────────

echo ""
echo "==> Phase 3: Capturing pre-upgrade snapshot..."

PRE_SNAPSHOT=$("$VENV_PY" -c "
import os, json, sqlite3

conn = sqlite3.connect(os.environ['FOOTPRINTER_DB_PATH'])
conn.row_factory = sqlite3.Row

entities = [
    ('clients', 'clients'),
    ('projects', 'projects'),
    ('folders', 'folders'),
    ('files', 'files'),
    ('chats', 'chats'),
    ('messages', 'messages'),
    ('emails', 'emails'),
    ('visits', 'visits'),
]

snapshot = {}
for name, table in entities:
    rows = conn.execute(
        f'SELECT COALESCE(status, \"listed\") AS status, COUNT(*) AS count FROM {table} GROUP BY 1'
    ).fetchall()
    by_status = {row['status']: row['count'] for row in rows}
    snapshot[name] = {'total': sum(by_status.values()), 'by_status': by_status}

conn.close()
print(json.dumps(snapshot))
" 2>&1) || true

if [ -z "$PRE_SNAPSHOT" ]; then
    echo "  ERROR: could not capture pre-upgrade entity counts (Python error)"
    echo "  Continuing without pre/post comparison..."
    PRE_SNAPSHOT="{}"
fi

echo "  Pre-upgrade counts:"
echo "$PRE_SNAPSHOT" | "$VENV_PY" -c "
import json, sys
snap = json.loads(sys.stdin.read())
for entity, data in snap.items():
    print(f'    {entity}: {data[\"total\"]} ({data[\"by_status\"]})')
"

# ── Phase 4: Upgrade to local wheel ──────────────────────────────────

echo ""
echo "==> Phase 4: Upgrading to v${TARGET_VERSION} from local wheel..."

"$VENV_PY" -m pip install "$WHEEL_PATH" >/dev/null 2>&1 \
    || { echo "ERROR: Failed to install wheel ${WHEEL_PATH}" >&2; exit 1; }

NEW_VERSION=$("$VENV_FP" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
echo "  Upgraded: ${OLD_VERSION} → ${NEW_VERSION}"

# ── Phase 5: Assertions ──────────────────────────────────────────────

echo ""
echo "==> Phase 5: Running assertions..."
echo ""

# 1. Entity counts survive
POST_SNAPSHOT=$("$VENV_PY" -c "
import os, json, sqlite3

conn = sqlite3.connect(os.environ['FOOTPRINTER_DB_PATH'])
conn.row_factory = sqlite3.Row

entities = [
    ('clients', 'clients'),
    ('projects', 'projects'),
    ('folders', 'folders'),
    ('files', 'files'),
    ('chats', 'chats'),
    ('messages', 'messages'),
    ('emails', 'emails'),
    ('visits', 'visits'),
]

snapshot = {}
for name, table in entities:
    rows = conn.execute(
        f'SELECT COALESCE(status, \"listed\") AS status, COUNT(*) AS count FROM {table} GROUP BY 1'
    ).fetchall()
    by_status = {row['status']: row['count'] for row in rows}
    snapshot[name] = {'total': sum(by_status.values()), 'by_status': by_status}

conn.close()
print(json.dumps(snapshot))
" 2>&1) || true

if [ -z "$POST_SNAPSHOT" ]; then
    fail "could not capture post-upgrade entity counts (Python error)"
else
    COUNTS_OK=$(PRE_SNAPSHOT="$PRE_SNAPSHOT" POST_SNAPSHOT="$POST_SNAPSHOT" "$VENV_PY" -c "
import json, os, sys
pre = json.loads(os.environ['PRE_SNAPSHOT'])
post = json.loads(os.environ['POST_SNAPSHOT'])
ok = True
for entity in pre:
    pre_total = pre[entity]['total']
    post_total = post.get(entity, {}).get('total', 0)
    if post_total < pre_total:
        print(f'  {entity}: {pre_total} -> {post_total} (LOST ROWS)', file=sys.stderr)
        ok = False
print('yes' if ok else 'no')
" 2>&1) || true

    if [ "$COUNTS_OK" = "yes" ]; then
        pass "entity counts survived upgrade"
    else
        fail "entity counts changed after upgrade"
    fi
fi

# 2. No NULL status values
NULL_COUNT=$("$VENV_PY" -c "
import os, sys, sqlite3
conn = sqlite3.connect(os.environ['FOOTPRINTER_DB_PATH'])
tables = ['clients', 'projects', 'folders', 'files', 'chats', 'messages', 'emails', 'visits']
total_null = 0
for t in tables:
    row = conn.execute(f'SELECT COUNT(*) FROM {t} WHERE status IS NULL').fetchone()
    count = row[0]
    if count > 0:
        print(f'  {t}: {count} NULL status rows', file=sys.stderr)
    total_null += count
conn.close()
print(total_null)
" 2>&1) || true

if [ "$NULL_COUNT" = "0" ]; then
    pass "no NULL status values post-upgrade"
elif [ -z "$NULL_COUNT" ] || ! [[ "$NULL_COUNT" =~ ^[0-9]+$ ]]; then
    fail "could not check NULL status values (Python error)"
else
    fail "${NULL_COUNT} rows have NULL status after upgrade"
fi

# 3. No legacy status values (active/hidden)
LEGACY_COUNT=$("$VENV_PY" -c "
import os, sys, sqlite3
conn = sqlite3.connect(os.environ['FOOTPRINTER_DB_PATH'])
tables = ['clients', 'projects', 'folders', 'files', 'chats', 'messages', 'emails', 'visits']
total_legacy = 0
for t in tables:
    row = conn.execute(
        f\"SELECT COUNT(*) FROM {t} WHERE status NOT IN ('listed', 'unlisted', 'removed')\"
    ).fetchone()
    count = row[0]
    if count > 0:
        print(f'  {t}: {count} legacy status rows', file=sys.stderr)
    total_legacy += count
conn.close()
print(total_legacy)
" 2>&1) || true

if [ "$LEGACY_COUNT" = "0" ]; then
    pass "no legacy status values post-upgrade"
elif [ -z "$LEGACY_COUNT" ] || ! [[ "$LEGACY_COUNT" =~ ^[0-9]+$ ]]; then
    fail "could not check legacy status values (Python error)"
else
    fail "${LEGACY_COUNT} rows have legacy status values after upgrade"
fi

# 4. fp status --json works
if STATUS_JSON=$("$VENV_FP" status --json 2>/dev/null); then
    # Validate it's parseable JSON
    if echo "$STATUS_JSON" | "$VENV_PY" -c "import json, sys; json.loads(sys.stdin.read())" 2>/dev/null; then
        pass "fp status --json produces valid JSON post-upgrade"
    else
        fail "fp status --json output is not valid JSON"
    fi
else
    fail "fp status --json exited non-zero post-upgrade"
fi

# 5. CLI commands work
CLI_OK=true
if ! "$VENV_FP" --help >/dev/null 2>&1; then
    fail "fp --help failed post-upgrade"
    CLI_OK=false
fi
if ! "$VENV_FP" ingest --help >/dev/null 2>&1; then
    fail "fp ingest --help failed post-upgrade"
    CLI_OK=false
fi
if [ "$CLI_OK" = true ]; then
    pass "CLI commands work post-upgrade (--help, ingest --help)"
fi

# ── Phase 6: Summary ─────────────────────────────────────────────────

TOTAL=$((PASSED + FAILED))
echo ""
echo "────────────────────────────────────────────────────────────"
echo "  verify-upgrade: ${PASSED}/${TOTAL} assertions passed"
echo "  upgrade path: ${FROM_VERSION} → ${TARGET_VERSION}"
echo "  workspace: ${WORKDIR}"
echo "────────────────────────────────────────────────────────────"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
