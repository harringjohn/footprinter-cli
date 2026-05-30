# CSV import — clients and projects

`fp setup` (and `fp upsert`) can import clients and projects from CSV files. Two starter templates ship with Footprinter; copy and edit them to match your data.

- [`reference/clients-template.csv`](clients-template.csv)
- [`reference/projects-template.csv`](projects-template.csv)

Import order matters: **import clients first**, then projects. The projects CSV references clients by name, so the client records must already exist when the project import runs.

Post-setup, the same files import via:

```bash
fp upsert clients path/to/clients.csv --commit
fp upsert projects path/to/projects.csv --commit
```

## Clients

Header (template): `name,client_type,slug`

| Column | Required | Notes |
|--------|----------|-------|
| `name` | yes | Display name. Used by `projects.csv` to resolve the `client` column. |
| `client_type` | yes | One of `external`, `internal`, `personal`. |
| `slug` | no | URL-safe identifier. Auto-derived from `name` if omitted. |
| `path_pattern` | no | Glob pattern matched against file paths to associate files with this client. |
| `status` | no | One of `listed`, `unlisted`, `removed`. Defaults to `listed`. |

## Projects

Header (template): `project_name,project_type,client,description`

| Column | Required | Notes |
|--------|----------|-------|
| `project_name` | yes | Display name. |
| `project_type` | no | Free-form label, e.g. `python`, `node`, `docs`, `code`. |
| `client` | no | Client name (resolved against `clients` table). Use this OR `client_id`, not both. |
| `client_id` | no | Numeric client ID. Use this OR `client`, not both. |
| `description` | no | Free-form description. |
| `root_path` | no | Filesystem path to the project root. |
| `github_url` | no | GitHub repository URL. |
| `status` | no | One of `listed`, `unlisted`, `removed`. Defaults to `listed`. |

If a row sets the `client` column to a name that does not exist in the database, that row is skipped with an error and the rest of the file continues to import.
