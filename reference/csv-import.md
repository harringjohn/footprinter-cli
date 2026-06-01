# CSV import — clients and projects

`fp setup` (and `fp add`) can import clients and projects from CSV files. Two starter templates ship with Footprinter; copy and edit them to match your data.

- [`reference/clients-template.csv`](clients-template.csv)
- [`reference/projects-template.csv`](projects-template.csv)

Import order matters: **import clients first**, then projects. The projects CSV references clients by name, so the client records must already exist when the project import runs.

Post-setup, the same files import via:

```bash
fp add clients path/to/clients.csv
fp add projects path/to/projects.csv
```

## Clients

Header (template): `name,client_type,slug`

| Column | Required | Notes |
|--------|----------|-------|
| `name` | yes | Display name. Used by `projects.csv` to resolve the `client` column. |
| `client_type` | yes | One of `external`, `internal`, `personal`. |
| `slug` | no | URL-safe identifier. Auto-derived from `name` if omitted. |
| `status` | no | One of `listed`, `unlisted`, `removed`. Defaults to `listed`. |

## Projects

Header (template): `name,client,description`

| Column | Required | Notes |
|--------|----------|-------|
| `name` | yes | Display name. |
| `client` | no | Client name (resolved against `clients` table). Use this OR `client_id`, not both. |
| `client_id` | no | Numeric client ID. Use this OR `client`, not both. |
| `description` | no | Free-form description. |
| `status` | no | One of `listed`, `unlisted`, `removed`. Defaults to `listed`. |

If a row sets the `client` column to a name that does not exist in the database, that row is skipped with an error and the rest of the file continues to import.

## Generating templates and exporting

To produce a starter CSV with the correct headers for any entity, or to export existing records, use the format flags on `fp view`:

```bash
fp view projects --template    # write a header-only CSV starter
fp view clients --csv          # export current records as CSV
```

The `--template` output omits non-writable columns (such as `visibility`/`access`), so a template is always safe to fill in and re-import via `fp add`. Bulk *updates* to existing records use `fp update <entities> file.csv`.
