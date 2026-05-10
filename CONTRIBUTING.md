# Contributing to Footprinter

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/harringjohn/footprinter-cli.git
   cd footprinter
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv venv
   ./venv/bin/pip install -e ".[dev]"
   ```

3. Configure:
   ```bash
   fp setup
   ```

## Optional Extras

Footprinter uses optional dependency groups defined in `pyproject.toml`. Install only what you need:

| Extra | What it adds | When to install |
|-------|-------------|-----------------|
| `[dev]` | pytest, pytest-cov, ruff, httpx | Always — required for contributing |
| `[semantic]` | chromadb, onnxruntime | Working on semantic search or vectorization |
| `[parse]` | pypdf, python-docx, openpyxl, python-pptx | Working on document content extraction |
| `[full]` | `[semantic]` + `[parse]` | Full runtime environment |

Examples:

```bash
./venv/bin/pip install -e ".[dev]"           # minimum for contributing
./venv/bin/pip install -e ".[dev,semantic]"  # contributing + semantic search
./venv/bin/pip install -e ".[full,dev]"      # everything
```

## Running Tests

```bash
./venv/bin/python3 -m pytest tests/ -v --tb=short
```

## Code Style

This project uses [ruff](https://docs.astral.sh/ruff/) for linting and formatting. Run both before submitting a PR:

```bash
./venv/bin/ruff check .       # lint — reports errors and warnings
./venv/bin/ruff format .      # format — auto-fixes style
```

The ruff configuration lives in `pyproject.toml`: line length 120, Python 3.11+ target, rules E/F/W/I enabled.

Other conventions:
- Use type hints on function signatures
- Use `logging` instead of `print()` in library modules

## Development Workflow

Development typically uses Claude Code worktrees, or your preferred branching workflow, with `main` as the main branch.

### Branch Naming

Branches follow the pattern `username/description`, e.g.:

- `username/docs-refresh`
- `username/fix-retention-classifier`

### Typical Flow

1. Create a worktree branch from `main`
2. Make changes, co-authoring with Claude Code
3. Write tests (TDD preferred — tests before implementation)
4. Run `./venv/bin/python3 -m pytest tests/ -v --tb=short` to verify
5. Commit with descriptive messages
6. Submit a PR targeting `main`

### Direct Commits

Small, low-risk changes (config updates, doc fixes) can go directly to `main` without a PR.

## Security

- Never commit API keys, tokens, or credentials
- Secrets belong in `.env` (gitignored); `config/config.yaml` is tracked
- OAuth tokens live in `~/.config/footprinter/` (not in the repo)
- Report security vulnerabilities privately via GitHub issues

## Questions?

Open an issue for questions or discussion.
