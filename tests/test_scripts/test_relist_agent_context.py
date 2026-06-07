"""Tests for scripts/migrate/relist_agent_context_files.py."""

from unittest.mock import patch

from footprinter.ingest.database import Database


def _insert_unlisted_file(conn, path: str, name: str, reason: str = "in_dot_folder"):
    """Insert a file row with controlled status/status_reason for migration testing."""
    conn.execute(
        """
        INSERT INTO files (source, name, path, content_type, size_bytes, status, status_reason)
        VALUES ('local', ?, ?, 'text', 10, 'unlisted', ?)
        """,
        (name, path, reason),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


class TestRelistAgentContextFiles:
    """Test the one-time relist migration for .claude/ and .context/ files."""

    def test_relists_claude_dir_files(self, temp_db):
        db = Database(temp_db)
        ids = [
            _insert_unlisted_file(db.conn, "/home/user/project/.claude/CLAUDE.md", "CLAUDE.md"),
            _insert_unlisted_file(db.conn, "/home/user/project/.claude/settings.json", "settings.json"),
            _insert_unlisted_file(db.conn, "/home/user/project/.claude/skills/plan.md", "plan.md"),
        ]
        db.conn.commit()

        from scripts.migrate.relist_agent_context_files import relist_agent_context_files

        with patch("scripts.migrate.relist_agent_context_files.stamp_entities"):
            relist_agent_context_files(db.conn, dry_run=False, limit=None)

        cursor = db.conn.cursor()
        for fid in ids:
            cursor.execute("SELECT status, status_reason, status_changed_at FROM files WHERE id = ?", (fid,))
            row = cursor.fetchone()
            assert row["status"] == "listed", f"File {fid} should be listed"
            assert row["status_reason"] is None
            assert row["status_changed_at"] is not None
        db.close()

    def test_relists_context_dir_files(self, temp_db):
        db = Database(temp_db)
        ids = [
            _insert_unlisted_file(db.conn, "/home/user/project/.context/plans/plan.md", "plan.md"),
            _insert_unlisted_file(db.conn, "/home/user/project/.context/status.md", "status.md"),
        ]
        db.conn.commit()

        from scripts.migrate.relist_agent_context_files import relist_agent_context_files

        with patch("scripts.migrate.relist_agent_context_files.stamp_entities"):
            relist_agent_context_files(db.conn, dry_run=False, limit=None)

        cursor = db.conn.cursor()
        for fid in ids:
            cursor.execute("SELECT status, status_reason FROM files WHERE id = ?", (fid,))
            row = cursor.fetchone()
            assert row["status"] == "listed"
            assert row["status_reason"] is None
        db.close()

    def test_skips_local_files(self, temp_db):
        db = Database(temp_db)
        fid = _insert_unlisted_file(
            db.conn, "/home/user/project/.claude/settings.local.json", "settings.local.json"
        )
        db.conn.commit()

        from scripts.migrate.relist_agent_context_files import relist_agent_context_files

        with patch("scripts.migrate.relist_agent_context_files.stamp_entities"):
            relist_agent_context_files(db.conn, dry_run=False, limit=None)

        cursor = db.conn.cursor()
        cursor.execute("SELECT status, status_reason FROM files WHERE id = ?", (fid,))
        row = cursor.fetchone()
        assert row["status"] == "unlisted"
        assert row["status_reason"] == "in_dot_folder"
        db.close()

    def test_skips_other_dot_folders(self, temp_db):
        db = Database(temp_db)
        fid = _insert_unlisted_file(db.conn, "/home/user/project/.git/config", "config")
        db.conn.commit()

        from scripts.migrate.relist_agent_context_files import relist_agent_context_files

        with patch("scripts.migrate.relist_agent_context_files.stamp_entities"):
            relist_agent_context_files(db.conn, dry_run=False, limit=None)

        cursor = db.conn.cursor()
        cursor.execute("SELECT status, status_reason FROM files WHERE id = ?", (fid,))
        row = cursor.fetchone()
        assert row["status"] == "unlisted"
        assert row["status_reason"] == "in_dot_folder"
        db.close()

    def test_skips_user_set_status(self, temp_db):
        db = Database(temp_db)
        fid = _insert_unlisted_file(
            db.conn, "/home/user/project/.claude/CLAUDE.md", "CLAUDE.md", reason="cli:delete"
        )
        db.conn.commit()

        from scripts.migrate.relist_agent_context_files import relist_agent_context_files

        with patch("scripts.migrate.relist_agent_context_files.stamp_entities"):
            relist_agent_context_files(db.conn, dry_run=False, limit=None)

        cursor = db.conn.cursor()
        cursor.execute("SELECT status, status_reason FROM files WHERE id = ?", (fid,))
        row = cursor.fetchone()
        assert row["status"] == "unlisted"
        assert row["status_reason"] == "cli:delete"
        db.close()

    def test_dry_run_no_changes(self, temp_db):
        db = Database(temp_db)
        fid = _insert_unlisted_file(db.conn, "/home/user/project/.claude/CLAUDE.md", "CLAUDE.md")
        db.conn.commit()

        from scripts.migrate.relist_agent_context_files import relist_agent_context_files

        with patch("scripts.migrate.relist_agent_context_files.stamp_entities"):
            relist_agent_context_files(db.conn, dry_run=True, limit=None)

        cursor = db.conn.cursor()
        cursor.execute("SELECT status, status_reason FROM files WHERE id = ?", (fid,))
        row = cursor.fetchone()
        assert row["status"] == "unlisted"
        assert row["status_reason"] == "in_dot_folder"
        db.close()

    def test_limit_restricts_count(self, temp_db):
        db = Database(temp_db)
        for i in range(5):
            _insert_unlisted_file(db.conn, f"/home/user/project/.claude/file{i}.md", f"file{i}.md")
        db.conn.commit()

        from scripts.migrate.relist_agent_context_files import relist_agent_context_files

        with patch("scripts.migrate.relist_agent_context_files.stamp_entities"):
            relist_agent_context_files(db.conn, dry_run=False, limit=2)

        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) as n FROM files WHERE status = 'listed'")
        assert cursor.fetchone()["n"] == 2
        cursor.execute("SELECT COUNT(*) as n FROM files WHERE status = 'unlisted'")
        assert cursor.fetchone()["n"] == 3
        db.close()

    def test_returns_stats(self, temp_db):
        db = Database(temp_db)
        for i in range(3):
            _insert_unlisted_file(db.conn, f"/home/user/project/.claude/file{i}.md", f"file{i}.md")
        _insert_unlisted_file(db.conn, "/home/user/project/.context/plan.md", "plan.md")
        _insert_unlisted_file(db.conn, "/home/user/project/.context/status.md", "status.md")
        db.conn.commit()

        from scripts.migrate.relist_agent_context_files import relist_agent_context_files

        with patch("scripts.migrate.relist_agent_context_files.stamp_entities"):
            result = relist_agent_context_files(db.conn, dry_run=False, limit=None)

        assert result["found"] == 5
        assert result["updated"] == 5
        db.close()

    def test_stamps_access_for_changed_rows(self, temp_db):
        db = Database(temp_db)
        ids = [
            _insert_unlisted_file(db.conn, "/home/user/project/.claude/CLAUDE.md", "CLAUDE.md"),
            _insert_unlisted_file(db.conn, "/home/user/project/.context/plan.md", "plan.md"),
        ]
        db.conn.commit()

        from scripts.migrate.relist_agent_context_files import relist_agent_context_files

        with patch("scripts.migrate.relist_agent_context_files.stamp_entities") as mock_stamp:
            relist_agent_context_files(db.conn, dry_run=False, limit=None)

        mock_stamp.assert_called_once()
        call_args = mock_stamp.call_args
        stamped_ids = call_args[0][1]["file"]
        assert sorted(stamped_ids) == sorted(ids)
        db.close()


class TestRestampLocalConfigReason:
    """Test the re-stamp migration for .local.* files under agent-context dirs."""

    def test_restamps_local_config_reason(self, temp_db):
        db = Database(temp_db)
        fid = _insert_unlisted_file(
            db.conn, "/home/user/project/.claude/settings.local.json", "settings.local.json"
        )
        db.conn.commit()

        cursor = db.conn.cursor()
        cursor.execute("SELECT status_changed_at FROM files WHERE id = ?", (fid,))
        ts_before = cursor.fetchone()["status_changed_at"]

        from scripts.migrate.relist_agent_context_files import restamp_local_config_reason

        restamp_local_config_reason(db.conn, dry_run=False, limit=None)

        cursor.execute(
            "SELECT status, status_reason, status_changed_at FROM files WHERE id = ?", (fid,)
        )
        row = cursor.fetchone()
        assert row["status"] == "unlisted"
        assert row["status_reason"] == "local_config"
        assert row["status_changed_at"] == ts_before
        db.close()

    def test_restamp_preserves_status_changed_at(self, temp_db):
        db = Database(temp_db)
        fid = _insert_unlisted_file(
            db.conn, "/home/user/project/.claude/CLAUDE.local.md", "CLAUDE.local.md"
        )
        db.conn.execute(
            "UPDATE files SET status_changed_at = '2025-01-01 00:00:00' WHERE id = ?", (fid,)
        )
        db.conn.commit()

        from scripts.migrate.relist_agent_context_files import restamp_local_config_reason

        restamp_local_config_reason(db.conn, dry_run=False, limit=None)

        cursor = db.conn.cursor()
        cursor.execute("SELECT status_changed_at FROM files WHERE id = ?", (fid,))
        assert cursor.fetchone()["status_changed_at"] == "2025-01-01 00:00:00"
        db.close()

    def test_restamp_skips_non_local_files(self, temp_db):
        db = Database(temp_db)
        fid = _insert_unlisted_file(
            db.conn, "/home/user/project/.claude/CLAUDE.md", "CLAUDE.md"
        )
        db.conn.commit()

        from scripts.migrate.relist_agent_context_files import restamp_local_config_reason

        restamp_local_config_reason(db.conn, dry_run=False, limit=None)

        cursor = db.conn.cursor()
        cursor.execute("SELECT status, status_reason FROM files WHERE id = ?", (fid,))
        row = cursor.fetchone()
        assert row["status"] == "unlisted"
        assert row["status_reason"] == "in_dot_folder"
        db.close()

    def test_restamp_skips_other_dot_folders(self, temp_db):
        db = Database(temp_db)
        fid = _insert_unlisted_file(
            db.conn, "/home/user/project/.git/config.local.json", "config.local.json"
        )
        db.conn.commit()

        from scripts.migrate.relist_agent_context_files import restamp_local_config_reason

        restamp_local_config_reason(db.conn, dry_run=False, limit=None)

        cursor = db.conn.cursor()
        cursor.execute("SELECT status, status_reason FROM files WHERE id = ?", (fid,))
        row = cursor.fetchone()
        assert row["status"] == "unlisted"
        assert row["status_reason"] == "in_dot_folder"
        db.close()

    def test_restamp_dry_run(self, temp_db):
        db = Database(temp_db)
        fid = _insert_unlisted_file(
            db.conn, "/home/user/project/.claude/CLAUDE.local.md", "CLAUDE.local.md"
        )
        db.conn.commit()

        from scripts.migrate.relist_agent_context_files import restamp_local_config_reason

        restamp_local_config_reason(db.conn, dry_run=True, limit=None)

        cursor = db.conn.cursor()
        cursor.execute("SELECT status, status_reason FROM files WHERE id = ?", (fid,))
        row = cursor.fetchone()
        assert row["status"] == "unlisted"
        assert row["status_reason"] == "in_dot_folder"
        db.close()
