"""
Tests for file CRUD functions in footprinter.db.files.

Tests module-level functions (insert_file, insert_drive_file,
mark_removed_files, build_folder_maps) rather than raw SQL.
"""

from footprinter.db import files as files_db


class TestInsertFile:
    """Test files_db.insert_file() for local files."""

    def test_creates_record_returns_id(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        result = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/hello.txt",
                "file_name": "hello.txt",
                "file_type": "txt",
                "file_size": 42,
            },
        )

        assert isinstance(result, tuple)
        assert result[0] == "inserted"
        file_id = result[1]
        assert isinstance(file_id, int)
        assert file_id > 0

        cursor = db.conn.cursor()
        cursor.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        assert row is not None
        assert row["name"] == "hello.txt"
        assert row["path"] == "/tmp/test/hello.txt"
        assert row["content_type"] == "txt"
        assert row["size_bytes"] == 42
        assert row["source"] == "local"
        db.close()

    def test_duplicate_path_updates_existing(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/dup.txt",
                "file_name": "dup.txt",
                "file_type": "txt",
                "file_size": 100,
            },
        )
        files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/dup.txt",
                "file_name": "dup.txt",
                "file_type": "txt",
                "file_size": 200,
            },
        )

        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM files WHERE source = 'local' AND path = '/tmp/test/dup.txt'")
        assert cursor.fetchone()[0] == 1

        cursor.execute("SELECT size_bytes FROM files WHERE source = 'local' AND path = '/tmp/test/dup.txt'")
        assert cursor.fetchone()[0] == 200
        db.close()

    def test_reindex_preserves_manual_project_id(self, temp_db):
        """Manual project_id must survive re-indexing."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        _, file_id = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/proj.txt",
                "file_name": "proj.txt",
                "file_type": "txt",
                "file_size": 100,
            },
        )

        # Create a real project to reference
        cursor = db.conn.cursor()
        cursor.execute("INSERT INTO projects (id, name, status) VALUES (99, 'test-proj', 'listed')")

        # Simulate manual project override
        cursor.execute("UPDATE files SET project_id = 99 WHERE id = ?", (file_id,))
        db.conn.commit()

        # Re-index same file (simulates fp run)
        files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/proj.txt",
                "file_name": "proj.txt",
                "file_type": "txt",
                "file_size": 105,
            },
        )

        cursor.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        assert row["project_id"] == 99, "Manual project_id was overwritten by re-index"
        db.close()

    def test_reindex_sets_project_id_when_null(self, temp_db):
        """Auto-detected project_id should populate NULL project_id."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        _, file_id = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/noproj.txt",
                "file_name": "noproj.txt",
                "file_type": "txt",
                "file_size": 100,
            },
        )

        # Confirm project_id is NULL after first insert (no matching project)
        cursor = db.conn.cursor()
        cursor.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        assert cursor.fetchone()["project_id"] is None

        # No folder maps to this path, so folder inheritance yields no project.
        # Re-insert with a path that resolves to no folder — project assignment
        # returns None, which the CASE WHEN project_id IS NULL THEN ? backfill
        # keeps as NULL. This confirms the CASE expression handles NULL→NULL.
        files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/noproj.txt",
                "file_name": "noproj.txt",
                "file_type": "txt",
                "file_size": 200,
            },
        )

        cursor.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        assert cursor.fetchone()["project_id"] is None
        db.close()

    def test_reactivates_removed_files(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        _, file_id = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/removed.txt",
                "file_name": "removed.txt",
                "file_type": "txt",
                "file_size": 50,
            },
        )

        # Mark as removed
        cursor = db.conn.cursor()
        cursor.execute("UPDATE files SET status = 'removed' WHERE id = ?", (file_id,))
        db.conn.commit()

        # Re-insert same path — should reactivate the record
        result = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/removed.txt",
                "file_name": "removed.txt",
                "file_type": "txt",
                "file_size": 999,
            },
        )
        assert result == ("inserted", file_id)

        # Row updated and reactivated
        cursor.execute("SELECT status, size_bytes FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        assert row["status"] == "listed"
        assert row["size_bytes"] == 999
        db.close()


class TestInsertFileUnchanged:
    """Re-inserting an identical row should skip the UPDATE and return 'unchanged'."""

    def _payload(self, sha="abc", size=100):
        return {
            "file_path": "/tmp/test/u.txt",
            "file_name": "u.txt",
            "file_type": "txt",
            "file_size": size,
            "sha256_hash": sha,
            "md5_hash": "m",
        }

    def test_identical_hash_and_size_returns_unchanged(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        first = files_db.insert_file(db.conn, self._payload())
        assert first[0] == "inserted"
        file_id = first[1]

        # Capture updated_at to verify no UPDATE ran on the second call
        cursor = db.conn.cursor()
        cursor.execute("SELECT updated_at FROM files WHERE id = ?", (file_id,))
        updated_at_before = cursor.fetchone()["updated_at"]

        second = files_db.insert_file(db.conn, self._payload())
        assert second == ("unchanged", file_id)

        cursor.execute("SELECT updated_at FROM files WHERE id = ?", (file_id,))
        updated_at_after = cursor.fetchone()["updated_at"]
        assert updated_at_after == updated_at_before, "unchanged path must not issue an UPDATE"
        db.close()

    def test_changed_hash_returns_updated(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        first = files_db.insert_file(db.conn, self._payload(sha="abc"))
        file_id = first[1]

        second = files_db.insert_file(db.conn, self._payload(sha="def"))
        assert second == ("updated", file_id)

        cursor = db.conn.cursor()
        cursor.execute("SELECT sha256_hash FROM files WHERE id = ?", (file_id,))
        assert cursor.fetchone()["sha256_hash"] == "def"
        db.close()

    def test_changed_size_returns_updated(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        first = files_db.insert_file(db.conn, self._payload(size=100))
        file_id = first[1]

        second = files_db.insert_file(db.conn, self._payload(size=200))
        assert second == ("updated", file_id)

        cursor = db.conn.cursor()
        cursor.execute("SELECT size_bytes FROM files WHERE id = ?", (file_id,))
        assert cursor.fetchone()["size_bytes"] == 200
        db.close()

    def test_reactivation_wins_over_unchanged(self, temp_db):
        """A removed row with identical hash must reactivate, not short-circuit as 'unchanged'."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        first = files_db.insert_file(db.conn, self._payload())
        file_id = first[1]

        cursor = db.conn.cursor()
        cursor.execute("UPDATE files SET status = 'removed' WHERE id = ?", (file_id,))
        db.conn.commit()

        second = files_db.insert_file(db.conn, self._payload())
        assert second == ("inserted", file_id)

        cursor.execute("SELECT status FROM files WHERE id = ?", (file_id,))
        assert cursor.fetchone()["status"] == "listed"
        db.close()

    def test_null_project_id_fires_unchanged_when_no_project_resolves(self, temp_db):
        """NULL project_id is fine on the fast-path when no folder resolves a project —
        UPDATE backfill would have set NULL→NULL anyway."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        first = files_db.insert_file(db.conn, self._payload())
        file_id = first[1]

        cursor = db.conn.cursor()
        cursor.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        assert cursor.fetchone()["project_id"] is None, "precondition: no project detected yet"

        second = files_db.insert_file(db.conn, self._payload())
        assert second == ("unchanged", file_id)
        db.close()

    def test_null_project_id_backfills_from_folder_when_unchanged(self, temp_db):
        """An unchanged file with NULL project_id backfills a folder-inherited
        project on re-ingest (the folder gained a project after first index)."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        first = files_db.insert_file(db.conn, self._payload())
        file_id = first[1]

        cursor = db.conn.cursor()
        cursor.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        assert cursor.fetchone()["project_id"] is None, "precondition: no project yet"

        # The folder covering the file's directory now gains a project.
        cursor.execute("INSERT INTO projects (name) VALUES ('Late')")
        project_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO folders (source, path, relative_path, name, project_id) "
            "VALUES ('local', '/tmp/test', '/tmp/test', 'test', ?)",
            (project_id,),
        )
        db.conn.commit()

        # Re-ingest identical content (same sha + size): the fast-path must fall
        # through so the folder-inherited project is backfilled.
        action, _ = files_db.insert_file(db.conn, self._payload())
        assert action == "updated", "fast-path should fall through to backfill the project"
        cursor.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        assert cursor.fetchone()["project_id"] == project_id
        db.close()

    def test_missing_sha256_falls_through_to_update(self, temp_db):
        """None == None must not incorrectly mark rows unchanged."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        payload = {
            "file_path": "/tmp/test/nohash.txt",
            "file_name": "nohash.txt",
            "file_type": "txt",
            "file_size": 100,
        }
        first = files_db.insert_file(db.conn, payload)
        file_id = first[1]

        second = files_db.insert_file(db.conn, payload)
        assert second == ("updated", file_id)
        db.close()

    def test_zero_byte_file_returns_unchanged(self, temp_db):
        """0-byte files must hit the fast path — file_size=0 is valid, not falsy-None."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        first = files_db.insert_file(db.conn, self._payload(sha="abc", size=0))
        assert first[0] == "inserted"
        file_id = first[1]

        cursor = db.conn.cursor()
        cursor.execute("UPDATE files SET size_bytes = 0 WHERE id = ?", (file_id,))
        db.conn.commit()

        cursor.execute("SELECT updated_at FROM files WHERE id = ?", (file_id,))
        updated_at_before = cursor.fetchone()["updated_at"]

        second = files_db.insert_file(db.conn, self._payload(sha="abc", size=0))
        assert second == ("unchanged", file_id)

        cursor.execute("SELECT updated_at FROM files WHERE id = ?", (file_id,))
        updated_at_after = cursor.fetchone()["updated_at"]
        assert updated_at_after == updated_at_before, "unchanged path must not issue an UPDATE"
        db.close()


class TestInsertFileZeroByteSize:
    """0-byte files must store size_bytes=0 in the DB, not NULL."""

    def _payload(self, sha="abc", size=0, path="/tmp/test/zero.txt"):
        return {
            "file_path": path,
            "file_name": "zero.txt",
            "file_type": "txt",
            "file_size": size,
            "sha256_hash": sha,
            "md5_hash": "m",
        }

    def test_zero_byte_file_stores_size_zero(self, temp_db):
        """INSERT path: file_size=0 must persist as size_bytes=0, not NULL."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        action, file_id = files_db.insert_file(db.conn, self._payload(size=0))
        assert action == "inserted"

        cursor = db.conn.cursor()
        cursor.execute("SELECT size_bytes FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        assert row["size_bytes"] == 0, f"Expected 0, got {row['size_bytes']}"
        db.close()

    def test_zero_byte_file_update_path_stores_size_zero(self, temp_db):
        """UPDATE path: re-insert with file_size=0 must persist as size_bytes=0, not NULL."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        action, file_id = files_db.insert_file(
            db.conn, self._payload(sha="aaa", size=100)
        )
        assert action == "inserted"

        files_db.insert_file(db.conn, self._payload(sha="bbb", size=0))

        cursor = db.conn.cursor()
        cursor.execute("SELECT size_bytes FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        assert row["size_bytes"] == 0, f"Expected 0, got {row['size_bytes']}"
        db.close()


class TestFileStatus:
    """Test automatic status assignment in insert_file()."""

    def test_normal_file_active(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        _, aid = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/normal.txt",
                "file_name": "normal.txt",
                "file_type": "txt",
                "file_size": 10,
            },
        )

        cursor = db.conn.cursor()
        cursor.execute("SELECT status, status_reason FROM files WHERE id = ?", (aid,))
        row = cursor.fetchone()
        assert row["status"] == "listed"
        assert row["status_reason"] is None
        db.close()

    def test_dotfile_hidden(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        _, aid = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/.env",
                "file_name": ".env",
                "file_type": "env",
                "file_size": 10,
            },
        )

        cursor = db.conn.cursor()
        cursor.execute("SELECT status, status_reason FROM files WHERE id = ?", (aid,))
        row = cursor.fetchone()
        assert row["status"] == "unlisted"
        assert row["status_reason"] == "dot_file"
        db.close()

    def test_file_in_dot_folder_hidden(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        _, aid = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/.hidden/config.json",
                "file_name": "config.json",
                "file_type": "json",
                "file_size": 10,
            },
        )

        cursor = db.conn.cursor()
        cursor.execute("SELECT status, status_reason FROM files WHERE id = ?", (aid,))
        row = cursor.fetchone()
        assert row["status"] == "unlisted"
        assert row["status_reason"] == "in_dot_folder"
        db.close()


class TestMarkRemovedFiles:
    """Test files_db.mark_removed_files()."""

    def test_marks_missing_paths_as_removed(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        paths = ["/tmp/a.txt", "/tmp/b.txt", "/tmp/c.txt"]
        for p in paths:
            files_db.insert_file(
                db.conn,
                {
                    "file_path": p,
                    "file_name": p.split("/")[-1],
                    "file_type": "txt",
                    "file_size": 10,
                },
            )

        # Only a.txt and b.txt were "just indexed" — c.txt is missing
        removed = files_db.mark_removed_files(db.conn, {"/tmp/a.txt", "/tmp/b.txt"})
        assert len(removed) == 1

        cursor = db.conn.cursor()
        cursor.execute("SELECT status, status_reason FROM files WHERE path = '/tmp/c.txt'")
        row = cursor.fetchone()
        assert row["status"] == "removed"
        assert row["status_reason"] == "file_deleted"

        # a.txt and b.txt still active
        for p in ["/tmp/a.txt", "/tmp/b.txt"]:
            cursor.execute("SELECT status FROM files WHERE path = ?", (p,))
            assert cursor.fetchone()["status"] == "listed"
        db.close()

    def test_returns_removed_ids(self, temp_db):
        """mark_removed_files returns list of removed file IDs, not just a count."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        ids = []
        for p in ["/tmp/x.txt", "/tmp/y.txt", "/tmp/z.txt"]:
            _, fid = files_db.insert_file(
                db.conn,
                {
                    "file_path": p,
                    "file_name": p.split("/")[-1],
                    "file_type": "txt",
                    "file_size": 10,
                },
            )
            ids.append(fid)
        db.conn.commit()

        # Only x.txt indexed — y.txt and z.txt should be removed
        removed = files_db.mark_removed_files(db.conn, {"/tmp/x.txt"})
        assert isinstance(removed, list)
        assert set(removed) == {ids[1], ids[2]}
        db.close()

    def test_clears_vectorized_metadata(self, temp_db):
        """mark_removed_files clears vectorized_at and vectorized_chunks on removed files."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        _, fid = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/vec.txt",
                "file_name": "vec.txt",
                "file_type": "txt",
                "file_size": 10,
            },
        )
        # Simulate prior vectorization
        db.conn.execute(
            "UPDATE files SET vectorized_at = '2025-01-01', vectorized_chunks = 5 WHERE id = ?",
            (fid,),
        )
        db.conn.commit()

        # Remove the file (not in indexed_paths — use a dummy path so set is non-empty)
        files_db.mark_removed_files(db.conn, {"/tmp/other.txt"})

        cursor = db.conn.cursor()
        cursor.execute("SELECT vectorized_at, vectorized_chunks FROM files WHERE id = ?", (fid,))
        row = cursor.fetchone()
        assert row["vectorized_at"] is None
        assert row["vectorized_chunks"] == 0
        db.close()

    def test_empty_paths_noop(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        result = files_db.mark_removed_files(db.conn, set())
        assert result == []
        db.close()


class TestConditionalVectorizedAtClearing:
    """Test that insert_file() only clears vectorized_at when content genuinely changed."""

    def test_update_preserves_vectorized_at_when_hash_unchanged(self, temp_db):
        """Same sha256 but different size bypasses fast-path; UPDATE must preserve vectorized_at."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        _, file_id = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/p.txt",
                "file_name": "p.txt",
                "file_type": "txt",
                "file_size": 100,
                "sha256_hash": "abc123",
            },
        )
        db.conn.execute(
            "UPDATE files SET vectorized_at = '2025-01-01', vectorized_chunks = 5 WHERE id = ?",
            (file_id,),
        )
        db.conn.commit()

        result = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/p.txt",
                "file_name": "p.txt",
                "file_type": "txt",
                "file_size": 200,
                "sha256_hash": "abc123",
            },
        )
        assert result == ("updated", file_id)

        row = db.conn.execute(
            "SELECT vectorized_at, vectorized_chunks FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        assert row["vectorized_at"] == "2025-01-01"
        assert row["vectorized_chunks"] == 5
        db.close()

    def test_update_preserves_vectorized_at_when_incoming_sha_null(self, temp_db):
        """Missing incoming sha256 must not wipe vectorized_at."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        _, file_id = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/n.txt",
                "file_name": "n.txt",
                "file_type": "txt",
                "file_size": 100,
                "sha256_hash": "abc123",
            },
        )
        db.conn.execute(
            "UPDATE files SET vectorized_at = '2025-01-01', vectorized_chunks = 5 WHERE id = ?",
            (file_id,),
        )
        db.conn.commit()

        result = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/n.txt",
                "file_name": "n.txt",
                "file_type": "txt",
                "file_size": 100,
            },
        )

        row = db.conn.execute(
            "SELECT vectorized_at, vectorized_chunks FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        assert row["vectorized_at"] == "2025-01-01"
        assert row["vectorized_chunks"] == 5
        db.close()

    def test_update_clears_vectorized_at_when_hash_changes(self, temp_db):
        """Different sha256 means content changed — vectorized_at must be cleared."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        _, file_id = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/c.txt",
                "file_name": "c.txt",
                "file_type": "txt",
                "file_size": 100,
                "sha256_hash": "abc123",
            },
        )
        db.conn.execute(
            "UPDATE files SET vectorized_at = '2025-01-01', vectorized_chunks = 5 WHERE id = ?",
            (file_id,),
        )
        db.conn.commit()

        result = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/c.txt",
                "file_name": "c.txt",
                "file_type": "txt",
                "file_size": 200,
                "sha256_hash": "def456",
            },
        )
        assert result == ("updated", file_id)

        row = db.conn.execute(
            "SELECT vectorized_at, vectorized_chunks FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        assert row["vectorized_at"] is None
        assert row["vectorized_chunks"] == 0
        db.close()

    def test_update_clears_vectorized_at_on_reactivation(self, temp_db):
        """Reactivated (removed -> listed) file must clear vectorized_at."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        _, file_id = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/r.txt",
                "file_name": "r.txt",
                "file_type": "txt",
                "file_size": 100,
                "sha256_hash": "abc123",
            },
        )
        db.conn.execute(
            "UPDATE files SET vectorized_at = '2025-01-01', vectorized_chunks = 5,"
            " status = 'removed' WHERE id = ?",
            (file_id,),
        )
        db.conn.commit()

        files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/r.txt",
                "file_name": "r.txt",
                "file_type": "txt",
                "file_size": 100,
                "sha256_hash": "abc123",
            },
        )

        row = db.conn.execute(
            "SELECT vectorized_at, vectorized_chunks FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        assert row["vectorized_at"] is None
        assert row["vectorized_chunks"] == 0
        db.close()

    def test_repair_vectorized_at(self, temp_db):
        """repair_vectorized_at restores timestamps for files with chunks in vector store."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        _, id_a = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/a.txt",
                "file_name": "a.txt",
                "file_type": "txt",
                "file_size": 10,
            },
        )
        _, id_b = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/b.txt",
                "file_name": "b.txt",
                "file_type": "txt",
                "file_size": 20,
            },
        )
        db.conn.commit()

        repaired = files_db.repair_vectorized_at(db.conn, {id_a: 3})
        db.conn.commit()

        row_a = db.conn.execute(
            "SELECT vectorized_at, vectorized_chunks FROM files WHERE id = ?", (id_a,)
        ).fetchone()
        row_b = db.conn.execute(
            "SELECT vectorized_at, vectorized_chunks FROM files WHERE id = ?", (id_b,)
        ).fetchone()

        assert repaired == 1
        assert row_a["vectorized_at"] is not None
        assert row_a["vectorized_chunks"] == 3
        assert row_b["vectorized_at"] is None
        db.close()

    def test_repair_vectorized_at_includes_unlisted(self, temp_db):
        """Unlisted files with vector store chunks should also be repaired."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        _, fid = files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/.hidden.txt",
                "file_name": ".hidden.txt",
                "file_type": "txt",
                "file_size": 10,
            },
        )
        db.conn.execute("UPDATE files SET status = 'unlisted' WHERE id = ?", (fid,))
        db.conn.commit()

        repaired = files_db.repair_vectorized_at(db.conn, {fid: 2})
        db.conn.commit()

        row = db.conn.execute(
            "SELECT vectorized_at, vectorized_chunks FROM files WHERE id = ?", (fid,)
        ).fetchone()
        assert repaired == 1
        assert row["vectorized_at"] is not None
        assert row["vectorized_chunks"] == 2
        db.close()


class TestDriveFiles:
    """Test files_db.insert_drive_file()."""

    def _seed_drive_source(self, db, name="test_drive"):
        """Insert a test drive source into the sources table."""
        cursor = db.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO sources (name, source_type, account, label, enabled)"
            " VALUES (?, 'remote', 'test_account', 'Test Drive', 1)",
            (name,),
        )
        db.conn.commit()

    def test_insert_drive_file_creates_record(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        self._seed_drive_source(db)

        aid = files_db.insert_drive_file(
            db.conn,
            {
                "source": "test_drive",
                "external_id": "drive_file_001",
                "account": "test_account",
                "name": "report.pdf",
                "path": "/Work/reports/report.pdf",
                "content_type": "pdf",
                "mime_type": "application/pdf",
                "size_bytes": 5000,
                "created_at": "2025-01-01",
                "modified_at": "2025-01-02",
                "md5_hash": "abc123",
                "metadata": '{"owner": "test"}',
            },
        )

        assert isinstance(aid, int)
        assert aid > 0

        cursor = db.conn.cursor()
        cursor.execute("SELECT * FROM files WHERE id = ?", (aid,))
        row = cursor.fetchone()
        assert row["source"] == "test_drive"
        assert row["external_id"] == "drive_file_001"
        assert row["account"] == "test_account"
        assert row["name"] == "report.pdf"
        assert row["status"] == "listed"
        db.close()

    def test_insert_drive_file_uses_schema_defaults(self, temp_db):
        """status + indexed_at + updated_at must come from
        the schema DEFAULTs after the hardcoded literals are removed."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        self._seed_drive_source(db)

        aid = files_db.insert_drive_file(
            db.conn,
            {
                "source": "test_drive",
                "external_id": "drive_defaults",
                "account": "test_account",
                "name": "x.pdf",
                "path": "/Work/x.pdf",
            },
        )

        row = db.conn.execute(
            "SELECT status, indexed_at, updated_at FROM files WHERE id = ?",
            (aid,),
        ).fetchone()
        assert row["status"] == "listed"
        assert row["indexed_at"] is not None
        assert row["updated_at"] is not None
        db.close()

    def test_insert_drive_file_duplicate_updates(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        self._seed_drive_source(db)

        data = {
            "source": "test_drive",
            "external_id": "drive_file_002",
            "account": "test_account",
            "name": "doc.txt",
            "path": "/Work/doc.txt",
            "content_type": "txt",
            "mime_type": "text/plain",
            "size_bytes": 100,
            "created_at": "2025-01-01",
            "modified_at": "2025-01-02",
            "md5_hash": "hash1",
            "metadata": "{}",
        }

        id1 = files_db.insert_drive_file(db.conn, data)

        # Update with new size
        data["size_bytes"] = 200
        data["md5_hash"] = "hash2"
        id2 = files_db.insert_drive_file(db.conn, data)

        assert id1 == id2

        cursor = db.conn.cursor()
        cursor.execute("SELECT size_bytes, md5_hash FROM files WHERE id = ?", (id1,))
        row = cursor.fetchone()
        assert row["size_bytes"] == 200
        assert row["md5_hash"] == "hash2"
        db.close()


class TestModuleLevelFileWrites:
    """Test module-level write functions in footprinter.db.files."""

    def test_insert_file_module_function(self, temp_db):
        from footprinter.db.files import insert_file
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        result = insert_file(
            db.conn,
            {
                "file_path": "/tmp/test/mod_hello.txt",
                "file_name": "mod_hello.txt",
                "file_type": "txt",
                "file_size": 42,
            },
        )

        assert isinstance(result, tuple)
        assert result[0] == "inserted"
        assert isinstance(result[1], int)
        db.close()

    def test_insert_drive_file_module_function(self, temp_db):
        from footprinter.db.files import insert_drive_file
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute(
            "INSERT OR IGNORE INTO sources (name, source_type, account, label, enabled)"
            " VALUES ('test_drive', 'remote', 'test_account', 'Test Drive', 1)"
        )
        db.conn.commit()

        aid = insert_drive_file(
            db.conn,
            {
                "source": "test_drive",
                "external_id": "mod_drive_001",
                "account": "test_account",
                "name": "report.pdf",
                "path": "/Work/reports/report.pdf",
                "content_type": "pdf",
                "mime_type": "application/pdf",
                "size_bytes": 5000,
                "created_at": "2025-01-01",
                "modified_at": "2025-01-02",
                "md5_hash": "abc123",
                "metadata": '{"owner": "test"}',
            },
        )
        assert isinstance(aid, int)
        assert aid > 0
        db.close()

    def test_mark_removed_files_module_function(self, temp_db):
        from footprinter.db.files import insert_file, mark_removed_files
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        insert_file(
            db.conn,
            {
                "file_path": "/tmp/a.txt",
                "file_name": "a.txt",
                "file_type": "txt",
                "file_size": 10,
            },
        )
        insert_file(
            db.conn,
            {
                "file_path": "/tmp/b.txt",
                "file_name": "b.txt",
                "file_type": "txt",
                "file_size": 10,
            },
        )
        db.conn.commit()

        removed = mark_removed_files(db.conn, {"/tmp/a.txt"})
        assert len(removed) == 1
        db.close()

    def test_build_folder_maps_module_function(self, temp_db):
        from footprinter.db.files import build_folder_maps
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute(
            "INSERT INTO folders (source, path, relative_path, name)"
            " VALUES ('local', '/Users/john/Work', '/Work', 'Work')"
        )
        db.conn.commit()

        path_map, project_map = build_folder_maps(db.conn)
        assert isinstance(path_map, dict)
        assert isinstance(project_map, dict)
        db.close()


class TestGetKnownLocalPaths:
    """Test files_db.get_known_local_paths() for move detection."""

    def test_returns_paths_of_listed_files(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        paths = ["/tmp/a.txt", "/tmp/b.txt", "/tmp/c.txt"]
        for p in paths:
            files_db.insert_file(
                db.conn,
                {
                    "file_path": p,
                    "file_name": p.split("/")[-1],
                    "file_type": "txt",
                    "file_size": 10,
                },
            )
        db.conn.commit()

        result = files_db.get_known_local_paths(db.conn)
        assert result == set(paths)
        db.close()

    def test_excludes_removed_files(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        for p in ["/tmp/keep.txt", "/tmp/gone.txt"]:
            files_db.insert_file(
                db.conn,
                {
                    "file_path": p,
                    "file_name": p.split("/")[-1],
                    "file_type": "txt",
                    "file_size": 10,
                },
            )
        db.conn.execute("UPDATE files SET status = 'removed' WHERE path = '/tmp/gone.txt'")
        db.conn.commit()

        result = files_db.get_known_local_paths(db.conn)
        assert "/tmp/keep.txt" in result
        assert "/tmp/gone.txt" not in result
        db.close()

    def test_includes_unlisted_files(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        files_db.insert_file(
            db.conn,
            {
                "file_path": "/tmp/.env",
                "file_name": ".env",
                "file_type": "env",
                "file_size": 10,
            },
        )
        db.conn.commit()

        # .env gets status='unlisted' automatically — verify it's still in known paths
        result = files_db.get_known_local_paths(db.conn)
        assert "/tmp/.env" in result
        db.close()

    def test_excludes_non_local_files(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        db.conn.execute(
            "INSERT OR IGNORE INTO sources (name, source_type, account, label, enabled)"
            " VALUES ('test_drive', 'remote', 'test_account', 'Test Drive', 1)"
        )
        files_db.insert_drive_file(
            db.conn,
            {
                "source": "test_drive",
                "external_id": "drive_001",
                "account": "test_account",
                "name": "report.pdf",
                "path": "/Work/report.pdf",
            },
        )
        db.conn.commit()

        result = files_db.get_known_local_paths(db.conn)
        assert "/Work/report.pdf" not in result
        db.close()

    def test_empty_db_returns_empty_set(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        result = files_db.get_known_local_paths(db.conn)
        assert result == set()
        db.close()


class TestPrefixMaps:
    """Test in-memory prefix map building and resolution."""

    def test_build_folder_maps(self, temp_db):
        """build_folder_maps() returns (path_map, project_map) dicts."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        # Insert a project
        cursor.execute(
            "INSERT INTO projects (name, status)"
            " VALUES ('proj', 'listed')"
        )
        proj_id = cursor.lastrowid

        # Insert folders
        cursor.execute(
            "INSERT INTO folders (source, path, relative_path, name, project_id)"
            " VALUES ('local', '/Users/john/Work', '/Work', 'Work', ?)",
            (proj_id,),
        )
        f1_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO folders (source, path, relative_path, name, project_id)"
            " VALUES ('local', '/Users/john/Work/sub', '/Work/sub', 'sub', NULL)"
        )
        f2_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO folders (source, path, relative_path, name, project_id)"
            " VALUES ('test_drive', 'test_drive:/Personal', '/Personal', 'Personal', NULL)"
        )
        f3_id = cursor.lastrowid
        db.conn.commit()

        path_map, project_map = files_db.build_folder_maps(db.conn)

        assert isinstance(path_map, dict)
        assert isinstance(project_map, dict)
        assert path_map[("local", "/Users/john/Work")] == f1_id
        assert path_map[("local", "/Users/john/Work/sub")] == f2_id
        assert path_map[("test_drive", "test_drive:/Personal")] == f3_id
        assert project_map[f1_id] == proj_id
        assert f2_id not in project_map  # NULL project_id excluded
        db.close()

    def test_insert_file_with_maps_matches_sql(self, temp_db):
        """Map-based resolution produces same project_id/folder_id as SQL."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        # Seed project and folder
        cursor.execute(
            "INSERT INTO projects (name, status)"
            " VALUES ('myproj', 'listed')"
        )
        proj_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO folders (source, path, relative_path, name, project_id)"
            " VALUES ('local', '/Users/john/Work/myproj', '/Work/myproj', 'myproj', ?)",
            (proj_id,),
        )
        cursor.lastrowid  # folder inserted for FK; ID not needed
        db.conn.commit()

        file_data = {
            "file_path": "/Users/john/Work/myproj/readme.md",
            "file_name": "readme.md",
            "file_type": "md",
            "file_size": 100,
        }

        # Insert via SQL path (no maps)
        result_sql = files_db.insert_file(db.conn, file_data)
        assert result_sql is not None
        sql_file_id = result_sql[1]
        cursor.execute("SELECT project_id, folder_id FROM files WHERE id = ?", (sql_file_id,))
        sql_row = cursor.fetchone()
        sql_project_id = sql_row["project_id"]
        sql_folder_id = sql_row["folder_id"]

        # Delete the file record so we can re-insert with maps
        cursor.execute("DELETE FROM files WHERE id = ?", (sql_file_id,))
        db.conn.commit()

        # Build maps and insert via map path
        maps = {
            "folder_path_map": files_db.build_folder_maps(db.conn)[0],
            "folder_project_map": files_db.build_folder_maps(db.conn)[1],
        }
        result_map = files_db.insert_file(db.conn, file_data, relationship_maps=maps)
        assert result_map is not None
        map_file_id = result_map[1]
        cursor.execute("SELECT project_id, folder_id FROM files WHERE id = ?", (map_file_id,))
        map_row = cursor.fetchone()

        assert map_row["project_id"] == sql_project_id
        assert map_row["folder_id"] == sql_folder_id
        db.close()

    def test_folder_map_ancestor_walk(self, temp_db):
        """File in unmapped subdir finds ancestor folder via map."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        cursor.execute(
            "INSERT INTO folders (source, path, relative_path, name)"
            " VALUES ('local', '/Users/john/Work', '/Work', 'Work')"
        )
        folder_id = cursor.lastrowid
        db.conn.commit()

        path_map, project_map = files_db.build_folder_maps(db.conn)
        maps = {
            "project_prefix_map": [],
            "folder_path_map": path_map,
            "folder_project_map": project_map,
        }

        # File is deep inside — parent dir not in map, but ancestor is
        result = files_db.insert_file(
            db.conn,
            {
                "file_path": "/Users/john/Work/sub/deep/file.txt",
                "file_name": "file.txt",
                "file_type": "txt",
                "file_size": 10,
            },
            relationship_maps=maps,
        )
        assert result is not None
        cursor.execute("SELECT folder_id FROM files WHERE id = ?", (result[1],))
        assert cursor.fetchone()["folder_id"] == folder_id
        db.close()

    def test_drive_folder_map_resolution(self, temp_db):
        """Drive files resolve folder_id via map with source: prefix."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        # Seed drive source
        cursor.execute(
            "INSERT OR IGNORE INTO sources (name, source_type, account, label, enabled)"
            " VALUES ('test_drive', 'remote', 'test_account', 'Test Drive', 1)"
        )
        # Seed drive folder with source: prefix path
        cursor.execute(
            "INSERT INTO folders (source, path, relative_path, name)"
            " VALUES ('test_drive', 'test_drive:/Personal', '/Personal', 'Personal')"
        )
        folder_id = cursor.lastrowid
        db.conn.commit()

        path_map, project_map = files_db.build_folder_maps(db.conn)
        maps = {
            "folder_path_map": path_map,
            "folder_project_map": project_map,
            "remote_source_names": frozenset(["test_drive"]),
        }

        drive_id = files_db.insert_drive_file(
            db.conn,
            {
                "source": "test_drive",
                "external_id": "drive_map_test",
                "account": "test_account",
                "name": "photo.jpg",
                "path": "/Personal/photo.jpg",
                "content_type": "image",
                "mime_type": "image/jpeg",
                "size_bytes": 5000,
                "created_at": "2025-01-01",
                "modified_at": "2025-01-02",
                "md5_hash": "xyz",
                "metadata": "{}",
            },
            relationship_maps=maps,
        )

        assert drive_id is not None
        cursor.execute("SELECT folder_id FROM files WHERE id = ?", (drive_id,))
        assert cursor.fetchone()["folder_id"] == folder_id
        db.close()

    def test_drive_empty_map_still_resolves(self, temp_db):
        """Drive source with empty folder map gets folder_id=None without error."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        cursor = db.conn.cursor()

        cursor.execute(
            "INSERT OR IGNORE INTO sources (name, source_type, account, label, enabled)"
            " VALUES ('test_drive', 'remote', 'test_account', 'Test Drive', 1)"
        )
        db.conn.commit()

        # Empty maps but remote_source_names populated — should not crash
        maps = {
            "folder_path_map": {},
            "folder_project_map": {},
            "remote_source_names": frozenset(["test_drive"]),
        }

        drive_id = files_db.insert_drive_file(
            db.conn,
            {
                "source": "test_drive",
                "external_id": "drive_empty_test",
                "account": "test_account",
                "name": "doc.txt",
                "path": "/Personal/doc.txt",
                "content_type": "text",
                "mime_type": "text/plain",
                "size_bytes": 100,
                "created_at": "2025-01-01",
                "modified_at": "2025-01-02",
                "md5_hash": "abc",
                "metadata": "{}",
            },
            relationship_maps=maps,
        )

        assert drive_id is not None
        cursor.execute("SELECT folder_id FROM files WHERE id = ?", (drive_id,))
        assert cursor.fetchone()["folder_id"] is None
        db.close()
