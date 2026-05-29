"""Tests for the per-entity status breakdown table in ``fp status``.

Validates:
  1. ``print_status`` renders an ``Entity Counts`` table with all 8 entities
     in canonical order, with zero-count cells shown as ``0``.
  2. Status columns are derived from data, ordered current-first
     (listed, unlisted, removed) then legacy (active, hidden).
  3. ``fp status --json`` includes ``entity_breakdown`` under ``counts`` with
     non-zero-only ``by_status`` entries summing to ``total``.
"""

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from conftest import run_fp
from rich.console import Console

_STATUS_MOD = "footprinter.cli.status"
_CMD_MOD = "footprinter.cli.status"

_ENTITY_ORDER = (
    "clients",
    "projects",
    "folders",
    "files",
    "chats",
    "messages",
    "emails",
    "visits",
)


def _build_data(entity_breakdown: dict) -> dict:
    """Minimal ``data`` dict for ``print_status`` carrying entity_breakdown."""
    return {
        "database": {"path": "/tmp/test.db", "size_mb": 0.1},
        "config": {"path": "/tmp/config.yaml", "exists": True},
        "counts": {
            "files": {},
            "files_total": 0,
            "folders": {},
            "visits": 0,
            "emails": 0,
            "chats": {},
            "messages": 0,
            "top_chats": [],
            "chat_date_range": {"earliest": None, "latest": None},
            "remote_source_accounts": {},
            "recent_files": [],
            "recent_uploads": [],
            "last_run": None,
            "entity_breakdown": entity_breakdown,
            "access_resolution": {},
        },
        "last_run": None,
    }


def _capture_status(data: dict, health: dict) -> str:
    from footprinter.cli.status import print_status

    buf = StringIO()
    test_console = Console(file=buf, width=200, no_color=True)
    with patch(f"{_STATUS_MOD}.console", test_console):
        print_status(data, health)
    return buf.getvalue()


def _full_breakdown() -> dict:
    """Mimic the example in the issue: all 8 entities present."""
    return {
        "clients":  {"total": 4,  "by_status": {"listed": 4}},
        "projects": {"total": 7,  "by_status": {"listed": 7}},
        "folders":  {"total": 17, "by_status": {"listed": 15, "removed": 2}},
        "files":    {"total": 34, "by_status": {"listed": 17, "unlisted": 5, "removed": 12}},
        "chats":    {"total": 4,  "by_status": {"listed": 4}},
        "messages": {"total": 10, "by_status": {"listed": 10}},
        "emails":   {"total": 11, "by_status": {"listed": 9, "unlisted": 2}},
        "visits":   {"total": 3,  "by_status": {"listed": 3}},
    }


def test_print_status_renders_entity_table():
    """Entity Counts table appears with all 8 rows in canonical order; zero cells show 0."""
    data = _build_data(_full_breakdown())
    health = {"connector_rows": [], "remote_enabled": False}

    output = _capture_status(data, health)

    assert "Entity Counts" in output
    # Each entity must appear, in canonical order
    last_idx = -1
    for entity in _ENTITY_ORDER:
        idx = output.find(entity)
        assert idx != -1, f"entity {entity!r} missing from output"
        assert idx > last_idx, f"entity {entity!r} out of canonical order"
        last_idx = idx

    # Zero-count cell: clients has only 'listed', so 'unlisted' column shows 0
    clients_line = next(
        line for line in output.splitlines() if "clients" in line and "│" in line
    )
    cells = [c.strip() for c in clients_line.split("│") if c.strip()]
    # cells = ['clients', '4', '4', '0', '0', '0'] when columns are
    # Entity, Total, Listed, Unlisted, Removed (no legacy here)
    assert cells[0] == "clients"
    assert cells[1] == "4"
    assert "0" in cells[2:], "zero-count cell should render as 0, not blank"


def test_entity_table_column_order_current_first_legacy_last():
    """Column order: Total, listed, unlisted, removed, then legacy active, hidden."""
    breakdown = {
        "clients":  {"total": 1, "by_status": {"listed": 1}},
        "projects": {"total": 1, "by_status": {"listed": 1}},
        "folders":  {"total": 2, "by_status": {"listed": 1, "hidden": 1}},
        "files":    {"total": 4, "by_status": {"active": 1, "listed": 1, "unlisted": 1, "removed": 1}},
        "chats":    {"total": 0, "by_status": {}},
        "messages": {"total": 0, "by_status": {}},
        "emails":   {"total": 0, "by_status": {}},
        "visits":   {"total": 0, "by_status": {}},
    }
    data = _build_data(breakdown)
    health = {"connector_rows": [], "remote_enabled": False}

    output = _capture_status(data, health)

    # Find the header line (contains "Entity" and "Total")
    header_line = next(
        line for line in output.splitlines() if "Entity" in line and "Total" in line
    )
    # Locate column positions for current-then-legacy ordering
    pos_listed = header_line.find("Listed")
    pos_unlisted = header_line.find("Unlisted")
    pos_removed = header_line.find("Removed")
    pos_active = header_line.find("Active")
    pos_hidden = header_line.find("Hidden")

    assert pos_listed != -1
    assert pos_unlisted != -1
    assert pos_removed != -1
    assert pos_active != -1, "legacy 'active' must appear when present in data"
    assert pos_hidden != -1, "legacy 'hidden' must appear when present in data"

    # Current values come first, legacy values last
    assert pos_listed < pos_unlisted < pos_removed < pos_active < pos_hidden


def _seed_populated_db(db_path: Path) -> None:
    """Initialize a real Database schema and insert a row per entity table."""
    from footprinter.ingest.database import Database

    db = Database(str(db_path))
    conn = db.conn
    conn.execute(
        "INSERT INTO clients (name, slug, client_type, status) "
        "VALUES ('Acme', 'acme', 'external', 'listed')"
    )
    conn.execute(
        "INSERT INTO projects (project_name, project_type, root_path, status) "
        "VALUES ('Alpha', 'python', '/tmp/alpha', 'listed')"
    )
    conn.execute(
        "INSERT INTO folders (path, relative_path, name, source, status) "
        "VALUES ('/tmp/a', 'a', 'a', 'local', 'listed')"
    )
    conn.execute(
        "INSERT INTO folders (path, relative_path, name, source, status) "
        "VALUES ('/tmp/b', 'b', 'b', 'local', 'removed')"
    )
    conn.execute(
        "INSERT INTO files (name, path, source, status, content_type, size_bytes) "
        "VALUES ('a.md', '/tmp/a.md', 'local', 'listed', 'markdown', 100)"
    )
    conn.execute(
        "INSERT INTO chats (external_id, account, title, status) "
        "VALUES ('c1', 'claude', 'visible', 'listed')"
    )
    conn.execute(
        "INSERT INTO messages (chat_id, role, content, status) "
        "VALUES (1, 'user', 'hi', 'listed')"
    )
    conn.execute(
        "INSERT INTO emails (message_id, thread_id, account, from_address, "
        "subject, received_at, status) "
        "VALUES ('m1', 't1', 'work', 'a@b.c', 'sub', '2026-01-15', 'listed')"
    )
    conn.execute(
        "INSERT INTO visits (url, visit_time, browser, status) "
        "VALUES ('https://example.com', '2026-01-15 10:00:00', 'safari', 'listed')"
    )
    conn.commit()
    conn.close()


def test_json_output_includes_by_status_nonzero_only(tmp_path):
    """``--json`` carries entity_breakdown; by_status omits zero counts; total = sum."""
    db_path = tmp_path / "footprinter.db"
    _seed_populated_db(db_path)

    with (
        patch(f"{_CMD_MOD}.get_db_path", return_value=db_path),
        patch(f"{_CMD_MOD}.get_config_path", return_value=tmp_path / "config.yaml"),
        patch(f"{_CMD_MOD}.get_config", return_value={}),
        patch(f"{_CMD_MOD}.get_source_health", return_value={
            "connector_rows": [],
            "remote_enabled": False,
            "semantic": {"installed": False, "available": False},
        }),
    ):
        stdout, _stderr, code = run_fp("status", "--json")

    assert code == 0
    payload = json.loads(stdout)
    breakdown = payload["counts"]["entity_breakdown"]

    assert tuple(breakdown.keys()) == _ENTITY_ORDER
    for entity, info in breakdown.items():
        assert "total" in info and "by_status" in info
        # Non-zero-only: every entry in by_status must have a positive count
        for status, count in info["by_status"].items():
            assert count > 0, f"{entity}.by_status[{status!r}] is zero — should be omitted"
        assert info["total"] == sum(info["by_status"].values())
