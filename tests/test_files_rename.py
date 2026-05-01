"""
Functional tests for the files table and DB methods.

Verifies that the `files` table, `files_fts` virtual table, and renamed
DB methods (insert_file, insert_drive_file, mark_removed_files) work
correctly on a fresh database.
"""


class TestFilesTableExists:
    """Verify the `files` table and FTS virtual table exist on fresh install."""

    def test_files_table_created(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files'")
        assert cursor.fetchone() is not None, "files table should exist"
        db.close()

    def test_files_fts_exists(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files_fts'")
        assert cursor.fetchone() is not None, "files_fts virtual table should exist"
        db.close()


class TestFilesMethodsExist:
    """Verify DB methods exist and work."""

    def test_insert_file(self, temp_db):
        from footprinter.db import files as files_db
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        result = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/guard.txt",
                "file_name": "guard.txt",
                "file_type": "txt",
                "file_size": 42,
            },
        )
        assert isinstance(result, tuple)
        assert result[0] == "inserted"
        assert isinstance(result[1], int)
        assert result[1] > 0
        db.close()

    def test_insert_drive_file(self, temp_db):
        from footprinter.db import files as files_db
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        # Seed a drive source
        db.conn.execute(
            "INSERT OR IGNORE INTO sources (name, source_type, account, label, enabled)"
            " VALUES ('test_drive', 'remote', 'test_account', 'Test Drive', 1)"
        )
        db.conn.commit()

        result = files_db.insert_drive_file(
            db.conn,
            {
                "source": "test_drive",
                "external_id": "drive_guard_001",
                "account": "test_account",
                "name": "guard.pdf",
                "path": "/Work/guard.pdf",
                "content_type": "pdf",
                "mime_type": "application/pdf",
                "size_bytes": 5000,
                "created_at": "2025-01-01",
                "modified_at": "2025-01-02",
                "md5_hash": "guard_hash",
                "metadata": "{}",
            },
        )
        assert isinstance(result, int)
        assert result > 0
        db.close()

    def test_mark_removed_files(self, temp_db):
        from footprinter.db import files as files_db
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        result = files_db.mark_removed_files(db.conn, set())
        assert result == []
        db.close()


# ---------------------------------------------------------------------------
# Data model doc accuracy
# ---------------------------------------------------------------------------


class TestDataModelDocAccuracy:
    def test_data_model_no_stale_drive_columns(self):
        """reference/data-model.md should use renamed remote_* column names."""
        from pathlib import Path

        doc = Path("reference/data-model.md").read_text()
        lines = doc.splitlines()
        for i, line in enumerate(lines, 1):
            # Skip lines that are clearly legacy notes
            if "legacy" in line.lower() or "renamed" in line.lower() or "formerly" in line.lower():
                continue
            assert "indexed_drive_id" not in line, (
                f"reference/data-model.md line {i} still references 'indexed_drive_id' — should be 'remote_file_id'"
            )
            assert "indexed_remote_id" not in line, (
                f"reference/data-model.md line {i} still references 'indexed_remote_id' — should be 'remote_file_id'"
            )
            assert "indexed_remote_folder_id" not in line, (
                f"reference/data-model.md line {i} still references 'indexed_remote_folder_id' — "
                f"should be 'remote_folder_id'"
            )

    def test_file_scanner_no_content_hash_variable(self):
        """file_scanner.py should not use 'content_hash' as a variable name."""
        from pathlib import Path

        source = Path("footprinter/ingest/file_scanner.py").read_text()
        for i, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "content_hash" not in stripped, (
                f"file_scanner.py line {i} still uses 'content_hash' — should be 'sha256_hash'"
            )
