"""Tests for footprinter.db.uploads module-level functions."""


class TestModuleLevelUploads:
    """Test module-level upload functions in footprinter.db.uploads."""

    def test_create_upload(self, temp_db):
        from footprinter.db.uploads import create_upload
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        upload_id = create_upload(
            db.conn,
            {
                "filename": "chat_export.json",
                "file_hash": "abc123hash",
                "file_size": 5000,
                "type": "chat",
                "source": "manual",
            },
        )
        assert isinstance(upload_id, int)
        assert upload_id > 0
        db.close()

    def test_get_upload_by_hash(self, temp_db):
        from footprinter.db.uploads import create_upload, get_upload_by_hash
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        create_upload(
            db.conn,
            {
                "filename": "test.json",
                "file_hash": "hash123",
                "type": "chat",
            },
        )
        db.conn.commit()

        found = get_upload_by_hash(db.conn, "hash123")
        assert found is not None
        assert found["filename"] == "test.json"

        not_found = get_upload_by_hash(db.conn, "nonexistent")
        assert not_found is None
        db.close()

    def test_update_upload(self, temp_db):
        from footprinter.db.uploads import create_upload, get_upload_by_hash, update_upload
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        uid = create_upload(
            db.conn,
            {
                "filename": "test.json",
                "file_hash": "update_hash",
                "type": "chat",
            },
        )
        db.conn.commit()

        update_upload(db.conn, uid, status="completed", items_added=42)
        found = get_upload_by_hash(db.conn, "update_hash")
        assert found["status"] == "completed"
        assert found["items_added"] == 42
        db.close()

    def test_get_recent_uploads(self, temp_db):
        from footprinter.db.uploads import create_upload, get_recent_uploads
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        create_upload(db.conn, {"filename": "a.json", "file_hash": "h1", "type": "chat"})
        create_upload(db.conn, {"filename": "b.csv", "file_hash": "h2", "type": "email"})
        db.conn.commit()

        assert len(get_recent_uploads(db.conn, upload_type="chat")) == 1
        assert len(get_recent_uploads(db.conn)) == 2
        db.close()

    def test_update_upload_ignores_disallowed_fields(self, temp_db):
        from footprinter.db.uploads import create_upload, get_upload_by_hash, update_upload
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        uid = create_upload(db.conn, {"filename": "x.json", "file_hash": "hx", "type": "chat"})
        db.conn.commit()

        update_upload(db.conn, uid, status="completed", filename="HACKED.json")
        found = get_upload_by_hash(db.conn, "hx")
        assert found["filename"] == "x.json"  # unchanged
        assert found["status"] == "completed"
        db.close()
