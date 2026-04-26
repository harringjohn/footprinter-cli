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

## Root — Testing and verification

| Script | Purpose |
|--------|---------|
| `fresh_install_test.sh` | Clone, venv, install, verify. Tests base, full, wheel, or all modes. |
| `cli_verify.sh` | Quick smoke test of installed CLI commands. |

```bash
bash scripts/fresh_install_test.sh              # base mode (default)
bash scripts/fresh_install_test.sh full          # all extras
bash scripts/fresh_install_test.sh wheel         # non-editable wheel install
bash scripts/fresh_install_test.sh all           # run base + full sequentially

bash scripts/cli_verify.sh                      # verify CLI entry points
```

---

## Related Documentation

- `reference/pipeline.md` — Pipeline stage reference
- `reference/interfaces.md` — CLI commands, MCP tools, Python API
- `reference/data-model.md` — Database schema
