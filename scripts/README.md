# Scripts Directory

Utility scripts for Footprinter operations. All scripts should be run from the project root.

---

## `qa.sh` — QA tier dispatcher

| Command | Purpose |
|---------|---------|
| `bash scripts/qa.sh --list` | List available QA tiers |
| `bash scripts/qa.sh smoke` | Run post-install smoke checks |
| `bash scripts/qa.sh verify-upgrade 1.0.4 --from 1.0.3` | Verify upgrade path from previous release |
| `bash scripts/qa.sh all` | Run all tiers that need no extra args |

Tiers requiring arguments (`verify-upgrade`) are excluded from `all`.

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

```bash
bash scripts/release/verify_upgrade.sh 1.0.4 --from 1.0.3
```

---

## Related Documentation

- `reference/pipeline.md` — Pipeline stage reference
- `reference/interfaces.md` — CLI commands, MCP tools, Python API
- `reference/data-model.md` — Database schema
