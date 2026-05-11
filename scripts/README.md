# Scripts Directory

Utility scripts for Footprinter operations. All scripts should be run from the project root.

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

---

## Related Documentation

- `reference/pipeline.md` — Pipeline stage reference
- `reference/interfaces.md` — CLI commands, MCP tools, Python API
- `reference/data-model.md` — Database schema
