# Scripts Directory

Utility scripts for Footprinter operations. All scripts should be run from the project root.

---

## `qa.sh` — QA tier dispatcher

> **Canonical reference.** This section is the single authoritative description
> of the QA tier model. `qa.sh --list` is the runtime source of truth; this
> table is its human-readable counterpart.

| Command | Purpose |
|---------|---------|
| `bash scripts/qa.sh --list` | List available QA tiers |
| `bash scripts/qa.sh smoke` | Run post-install smoke checks |
| `bash scripts/qa.sh cli-verify` | Run the full CLI surface verification |
| `bash scripts/qa.sh verify-upgrade 1.1.0 --from 1.0.5` | Verify upgrade path from previous release |
| `bash scripts/qa.sh verify-install 1.1.0 --with-pytest` | Verify installed wheel (entry points + optional pytest against installed package) |
| `bash scripts/qa.sh all` | Run all tiers that need no extra args |

Excluded from `all`: `verify-upgrade`, `verify-install` (need version arguments)
and `cli-verify` (heavy — clones the source and builds a throwaway venv; run on demand).

### What each QA layer tests

The layers overlap only on a thin slice (entry points resolve + the dev-tier
fixture boundary). Otherwise each answers a different question:

| Layer | Question it answers | How it runs |
|-------|---------------------|-------------|
| **pytest** (`tests/`, incl. `test_e2e_*.py`) | Does the *logic* work? Data pipeline (file → ingest → DB → search), config/MCP plumbing, no-traceback subprocess calls | In-process / subprocess against the dev tree; fast, CI |
| **smoke** (`snapshot-qa/smoke.sh`) | Did the *package install* without breaking the basics? Entry points load, `footprinter.mcp` imports, `footprinter.fixture` absent | Against an already-installed wheel; seconds |
| **cli-verify** (`cli_verify.sh`) | Does *every command behave*? Every command's `--help`, removed commands absent, real CRUD/ingest/search/permission/view, error UX (no raw tracebacks), output shape | Clones source → throwaway venv (base editable) → isolated `FOOTPRINTER_HOME` + own sample data |
| **verify-upgrade** (`release/verify_upgrade.sh`) | Does *data survive an upgrade*? Install previous release, populate, upgrade to local wheel, assert counts + migrated status values | Installs previous version from PyPI + local wheel; pre-publish gate |
| **verify-install** (`release/verify_install.sh`) | Does the *wheel install cleanly* and do tests pass against the installed package? Entry points resolve, fixture boundary intact, pytest collection/runtime errors absent | Installs local wheel into clean venv → copies test tree → runs pytest with PYTHONPATH set; pre-publish gate |

`cli-verify` is the layer that catches **command-surface drift** — renamed, removed,
or restructured commands — which is what motivated rebuilding it for the v1.0.5 CLI
changes. It does *not* cover the install-mode matrix (`.[full]` extras, non-editable
wheel); that coverage lives in `verify-upgrade` (local wheel) and `smoke` (snapshot wheel).

### Resolved limitations

- **`--with-pytest` collection errors:** v1.0.1 post-release
  verification hit 3 pytest collection errors when running tests against the
  installed package. Fixed by copying the full `tests/` tree to a neutral
  directory and setting `PYTHONPATH` so `from tests.conftest import` resolves.
  `verify_install.sh` now handles this automatically.

---

## `migrate/` — Database migration and maintenance

| Script | Purpose |
|--------|---------|
| `backfill_md5_hashes.py` | Compute MD5 hashes for hash-based Drive linking |

```bash
./venv/bin/python3 scripts/migrate/backfill_md5_hashes.py --dry-run  # Preview
./venv/bin/python3 scripts/migrate/backfill_md5_hashes.py             # Execute
```

## `snapshot-qa/` — Post-install validation

| Script | Purpose |
|--------|---------|
| `smoke.sh` | Minimal post-install canary (entry points, imports, fixture boundary). |

```bash
PY=./venv/bin/python3 bash scripts/snapshot-qa/smoke.sh
```

## `release/` — Install and release scripts

| Script | Purpose |
|--------|---------|
| `install.sh` | Base install (Python check + pip install + verify) |
| `install-full.sh` | Full install with semantic search extras |
| `_install_common.sh` | Shared helpers sourced by install scripts |
| `verify_upgrade.sh` | Tier 4: upgrade-path verification (install previous, seed data, upgrade, assert survival) |
| `verify_install.sh` | Tier 5: installed-package verification (clean venv, entry points, optional pytest) |

```bash
bash scripts/release/verify_upgrade.sh 1.1.0 --from 1.0.5
bash scripts/release/verify_install.sh 1.1.0 --with-pytest
```

---

## Related Documentation

- `reference/pipeline.md` — Pipeline stage reference
- `reference/interfaces.md` — CLI commands, MCP tools, Python API
- `reference/data-model.md` — Database schema
