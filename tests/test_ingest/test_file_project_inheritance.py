"""
Tests for project_id inheritance at ingest time.

Verifies that insert_file() and insert_drive_file() inherit project_id
from the parent folder when no explicit project match exists.
"""


class TestLocalFileProjectInheritance:
    """Test project_id inheritance in insert_file()."""

    def _create_project(self, db, name="Test Project", root_path=None):
        """Create a project and return its id."""
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT INTO projects (project_name, root_path) VALUES (?, ?)",
            (name, root_path),
        )
        db.conn.commit()
        return cursor.lastrowid

    def _create_folder(self, db, path, source="local", project_id=None):
        """Create a folder and return its id."""
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT INTO folders (source, path, relative_path, name, project_id) VALUES (?, ?, ?, ?, ?)",
            (source, path, path, path.split("/")[-1], project_id),
        )
        db.conn.commit()
        return cursor.lastrowid

    def test_local_file_inherits_project_from_folder(self, temp_db):
        from footprinter.db import files as files_db
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        project_id = self._create_project(db, "MyProject")
        self._create_folder(db, "/tmp/test", project_id=project_id)

        result = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/readme.txt",
                "file_name": "readme.txt",
                "file_type": "txt",
                "file_size": 100,
            },
        )
        _, file_id = result

        cursor = db.conn.cursor()
        cursor.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        assert row["project_id"] == project_id
        db.close()

    def test_local_file_path_match_takes_precedence(self, temp_db):
        from footprinter.db import files as files_db
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        path_project = self._create_project(db, "PathProject", root_path="/tmp/test")
        folder_project = self._create_project(db, "FolderProject")
        self._create_folder(db, "/tmp/test", project_id=folder_project)

        result = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/data.csv",
                "file_name": "data.csv",
                "file_type": "csv",
                "file_size": 200,
            },
        )
        _, file_id = result

        cursor = db.conn.cursor()
        cursor.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        assert row["project_id"] == path_project
        db.close()

    def test_local_file_no_folder_no_project(self, temp_db):
        from footprinter.db import files as files_db
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        result = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/orphan/file.txt",
                "file_name": "file.txt",
                "file_type": "txt",
                "file_size": 50,
            },
        )
        _, file_id = result

        cursor = db.conn.cursor()
        cursor.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        assert row["project_id"] is None
        db.close()


class TestDriveFileProjectInheritance:
    """Test project_id inheritance in insert_drive_file()."""

    def _seed_drive_source(self, db, name="test_drive"):
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO sources (name, source_type, account, label, enabled)"
            " VALUES (?, 'remote', 'test_account', 'Test Drive', 1)",
            (name,),
        )
        db.conn.commit()

    def _create_project(self, db, name="Test Project"):
        cursor = db.conn.cursor()
        cursor.execute("INSERT INTO projects (project_name) VALUES (?)", (name,))
        db.conn.commit()
        return cursor.lastrowid

    def _create_folder(self, db, path, source="test_drive", project_id=None):
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT INTO folders (source, path, relative_path, name, project_id) VALUES (?, ?, ?, ?, ?)",
            (source, path, path, path.split("/")[-1], project_id),
        )
        db.conn.commit()
        return cursor.lastrowid

    def _make_drive_data(self, **overrides):
        base = {
            "source": "test_drive",
            "external_id": "drive_001",
            "account": "test_account",
            "name": "report.pdf",
            "path": "/Work/reports/report.pdf",
            "content_type": "pdf",
            "mime_type": "application/pdf",
            "size_bytes": 5000,
            "created_at": "2025-01-01",
            "modified_at": "2025-01-02",
            "md5_hash": "abc123",
            "metadata": "{}",
        }
        base.update(overrides)
        return base

    def test_drive_file_inherits_project_from_folder(self, temp_db):
        from footprinter.db import files as files_db
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        self._seed_drive_source(db)
        project_id = self._create_project(db, "DriveProject")
        # Drive folders use source:/ prefix format
        self._create_folder(db, "test_drive:/Work/reports", project_id=project_id)

        file_id = files_db.insert_drive_file(db.conn, self._make_drive_data())

        cursor = db.conn.cursor()
        cursor.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        assert row["project_id"] == project_id
        db.close()

    def test_drive_file_update_inherits_when_null(self, temp_db):
        from footprinter.db import files as files_db
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        self._seed_drive_source(db)

        # Insert file first — no project on folder yet
        file_id = files_db.insert_drive_file(db.conn, self._make_drive_data())

        cursor = db.conn.cursor()
        cursor.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        assert cursor.fetchone()["project_id"] is None

        # Now create folder with project
        project_id = self._create_project(db, "LateProject")
        self._create_folder(db, "test_drive:/Work/reports", project_id=project_id)

        # Re-insert (update path)
        files_db.insert_drive_file(db.conn, self._make_drive_data())

        cursor.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        assert cursor.fetchone()["project_id"] == project_id
        db.close()

    def test_drive_file_update_preserves_existing_project(self, temp_db):
        from footprinter.db import files as files_db
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        self._seed_drive_source(db)

        original_project = self._create_project(db, "OriginalProject")
        different_project = self._create_project(db, "DifferentProject")

        # Insert file, then manually set its project_id
        file_id = files_db.insert_drive_file(db.conn, self._make_drive_data())
        cursor = db.conn.cursor()
        cursor.execute(
            "UPDATE files SET project_id = ? WHERE id = ?",
            (original_project, file_id),
        )
        db.conn.commit()

        # Create folder with a different project
        self._create_folder(db, "test_drive:/Work/reports", project_id=different_project)

        # Re-insert (update path) — should NOT overwrite
        files_db.insert_drive_file(db.conn, self._make_drive_data())

        cursor.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        assert cursor.fetchone()["project_id"] == original_project
        db.close()
