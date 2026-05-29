"""
Tests for source entity DB functions.

Covers: insert_visit, insert_chat, insert_message,
get_chat_id_by_uuid, delete_chat_messages, list_chats,
create_upload/get_upload_by_hash/get_recent_uploads/update_upload, insert_email,
move_messages_to_chat.
"""

import pytest

from footprinter.db import browser as browser_db
from footprinter.db import chats as chats_db
from footprinter.db import emails as emails_db
from footprinter.db import uploads as uploads_db


class TestModuleLevelBrowserWrites:
    """Test module-level insert_visit in footprinter.db.browser."""

    def test_insert_visit_module_function(self, temp_db):
        from footprinter.db.browser import insert_visit
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        row_id = insert_visit(
            db.conn,
            {
                "url": "https://example.com",
                "title": "Example",
                "visit_time": "2025-01-15 10:00:00",
                "browser": "safari",
                "visit_count": 3,
            },
        )
        assert isinstance(row_id, int)
        assert row_id > 0
        db.close()

    def test_insert_visit_duplicate_returns_false(self, temp_db):
        from footprinter.db.browser import insert_visit
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        visit = {
            "url": "https://example.com",
            "title": "Example",
            "visit_time": "2025-01-15 10:00:00",
            "browser": "safari",
        }
        first = insert_visit(db.conn, visit)
        assert first > 0
        second = insert_visit(db.conn, visit)
        assert second is False
        db.close()


class TestModuleLevelChatWrites:
    """Test module-level chat functions in footprinter.db.chats."""

    def test_insert_chat_module_function(self, temp_db):
        from footprinter.db.chats import insert_chat
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        row_id = insert_chat(
            db.conn,
            {
                "external_id": "mod-conv-001",
                "account": "claude",
                "title": "Test Chat",
                "message_count": 5,
            },
        )
        assert isinstance(row_id, int)
        db.close()

    def test_insert_message_module_function(self, temp_db):
        from footprinter.db.chats import insert_chat, insert_message
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        chat_id = insert_chat(
            db.conn,
            {
                "external_id": "mod-conv-msg",
                "account": "claude",
            },
        )
        msg_id = insert_message(
            db.conn,
            {
                "chat_id": chat_id,
                "message_id": "msg-001",
                "role": "user",
                "content": "Hello",
                "created_at": "2025-01-15 10:00:00",
            },
        )
        assert isinstance(msg_id, int)
        db.close()

    def test_get_chat_id_by_uuid_module_function(self, temp_db):
        from footprinter.db.chats import get_chat_id_by_uuid, insert_chat
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        insert_chat(
            db.conn,
            {
                "external_id": "mod-uuid-lookup",
                "account": "claude",
            },
        )
        result = get_chat_id_by_uuid(db.conn, "mod-uuid-lookup")
        assert result is not None
        assert isinstance(result, int)
        db.close()

    def test_delete_chat_messages_module_function(self, temp_db):
        from footprinter.db.chats import (
            delete_chat_messages,
            get_chat_id_by_uuid,
            insert_chat,
            insert_message,
        )
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        insert_chat(db.conn, {"external_id": "mod-del-test", "account": "claude"})
        chat_id = get_chat_id_by_uuid(db.conn, "mod-del-test")
        for i in range(3):
            insert_message(db.conn, {"chat_id": chat_id, "role": "user", "content": f"Msg {i}"})
        deleted = delete_chat_messages(db.conn, chat_id)
        assert deleted == 3
        db.close()

    def test_get_all_active_chats_module_function(self, temp_db):
        from footprinter.db.chats import get_all_active_chats, insert_chat
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        insert_chat(db.conn, {"external_id": "mod-active-1", "account": "claude"})
        result = get_all_active_chats(db.conn)
        assert isinstance(result, list)
        assert len(result) == 1
        db.close()

    def test_get_chat_message_hashes_module_function(self, temp_db):
        from footprinter.db.chats import (
            get_chat_id_by_uuid,
            get_chat_message_hashes,
            insert_chat,
            insert_message,
        )
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        insert_chat(db.conn, {"external_id": "mod-hash-test", "account": "claude"})
        chat_id = get_chat_id_by_uuid(db.conn, "mod-hash-test")
        insert_message(db.conn, {"chat_id": chat_id, "role": "user", "content": "Hello"})
        hashes = get_chat_message_hashes(db.conn, chat_id)
        assert isinstance(hashes, list)
        assert len(hashes) == 1
        db.close()

    def test_get_chat_by_id_module_function(self, temp_db):
        from footprinter.db.chats import get_chat_by_id, insert_chat
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        chat_id = insert_chat(db.conn, {"external_id": "mod-by-id", "account": "claude", "title": "Test"})
        result = get_chat_by_id(db.conn, chat_id)
        assert result is not None
        assert result["title"] == "Test"
        db.close()

    def test_mark_chat_merged_removed(self):
        """mark_chat_merged was removed when merge functionality was stripped."""
        with pytest.raises(ImportError):
            from footprinter.db.chats import mark_chat_merged  # noqa: F401

    def test_move_messages_to_chat_module_function(self, temp_db):
        from footprinter.db.chats import (
            get_chat_id_by_uuid,
            insert_chat,
            insert_message,
            move_messages_to_chat,
        )
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        insert_chat(db.conn, {"external_id": "mod-move-src", "account": "claude"})
        insert_chat(db.conn, {"external_id": "mod-move-tgt", "account": "claude"})
        src = get_chat_id_by_uuid(db.conn, "mod-move-src")
        tgt = get_chat_id_by_uuid(db.conn, "mod-move-tgt")
        insert_message(db.conn, {"chat_id": src, "role": "user", "content": "Move me"})
        db.conn.commit()
        msg_id = db.conn.execute("SELECT id FROM messages WHERE chat_id = ?", (src,)).fetchone()["id"]
        moved = move_messages_to_chat(db.conn, src, tgt, [msg_id])
        assert moved == 1
        db.close()

    def test_update_chat_message_count_module_function(self, temp_db):
        from footprinter.db.chats import (
            get_chat_id_by_uuid,
            insert_chat,
            insert_message,
            update_chat_message_count,
        )
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        insert_chat(db.conn, {"external_id": "mod-count", "account": "claude", "message_count": 0})
        chat_id = get_chat_id_by_uuid(db.conn, "mod-count")
        for i in range(3):
            insert_message(db.conn, {"chat_id": chat_id, "role": "user", "content": f"Msg {i}"})
        count = update_chat_message_count(db.conn, chat_id)
        assert count == 3
        db.close()


class TestModuleLevelEmailWrites:
    """Test module-level insert_email in footprinter.db.emails."""

    def test_insert_email_module_function(self, temp_db):
        from footprinter.db.emails import insert_email
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        row_id = insert_email(
            db.conn,
            {
                "message_id": "mod-msg-001",
                "thread_id": "thread-001",
                "account": "personal",
                "from_address": "alice@example.com",
                "from_name": "Alice",
                "subject": "Test Email",
                "body_preview": "Hello from Alice",
                "received_at": "2025-01-15 10:00:00",
                "labels": "INBOX",
                "has_attachments": False,
            },
        )
        assert isinstance(row_id, int)
        db.close()

    def test_insert_email_upsert_preserves_id(self, temp_db):
        from footprinter.db.emails import insert_email
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        data = {
            "message_id": "mod-msg-stable",
            "thread_id": "thread-001",
            "account": "personal",
            "subject": "Original",
            "received_at": "2025-01-15 10:00:00",
        }
        id1 = insert_email(db.conn, data)
        data["subject"] = "Updated"
        id2 = insert_email(db.conn, data)
        assert id1 == id2
        db.close()


class TestInsertBrowserVisit:
    """Test browser_db.insert_visit()."""

    def test_insert_and_verify(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        row_id = browser_db.insert_visit(
            db.conn,
            {
                "url": "https://example.com",
                "title": "Example",
                "visit_time": "2025-01-15 10:00:00",
                "browser": "safari",
                "visit_count": 3,
            },
        )

        assert isinstance(row_id, int)
        assert row_id > 0

        cursor = db.conn.cursor()
        cursor.execute("SELECT * FROM visits WHERE id = ?", (row_id,))
        row = cursor.fetchone()
        assert row["url"] == "https://example.com"
        assert row["title"] == "Example"
        assert row["browser"] == "safari"
        assert row["visit_count"] == 3
        db.close()

    def test_default_visit_count(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        row_id = browser_db.insert_visit(
            db.conn,
            {
                "url": "https://test.com",
                "visit_time": "2025-01-15 10:00:00",
                "browser": "chrome",
            },
        )

        cursor = db.conn.cursor()
        cursor.execute("SELECT visit_count FROM visits WHERE id = ?", (row_id,))
        assert cursor.fetchone()["visit_count"] == 1
        db.close()

    def test_duplicate_visit_ignored(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        visit = {
            "url": "https://example.com",
            "title": "Example",
            "visit_time": "2025-01-15 10:00:00",
            "browser": "safari",
        }

        first = browser_db.insert_visit(db.conn, visit)
        assert first > 0

        second = browser_db.insert_visit(db.conn, visit)
        assert second is False, f"Expected False on duplicate, got {second}"

        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM visits")
        assert cursor.fetchone()[0] == 1, "Duplicate visit was inserted"
        db.close()

    def test_different_browser_same_url_not_deduped(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        base = {
            "url": "https://example.com",
            "title": "Example",
            "visit_time": "2025-01-15 10:00:00",
        }

        browser_db.insert_visit(db.conn, {**base, "browser": "safari"})
        browser_db.insert_visit(db.conn, {**base, "browser": "chrome"})

        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM visits")
        assert cursor.fetchone()[0] == 2, "Different browsers should not be deduped"
        db.close()

    def test_different_time_same_url_not_deduped(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        base = {
            "url": "https://example.com",
            "title": "Example",
            "browser": "safari",
        }

        browser_db.insert_visit(db.conn, {**base, "visit_time": "2025-01-15 10:00:00"})
        browser_db.insert_visit(db.conn, {**base, "visit_time": "2025-01-15 11:00:00"})

        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM visits")
        assert cursor.fetchone()[0] == 2, "Different times should not be deduped"
        db.close()


class TestInsertChat:
    """Test chats_db.insert_chat()."""

    def test_insert_with_account_key(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        row_id = chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-uuid-001",
                "account": "claude",
                "title": "Test Chat",
                "message_count": 5,
            },
        )

        assert isinstance(row_id, int)
        cursor = db.conn.cursor()
        cursor.execute("SELECT * FROM chats WHERE external_id = 'conv-uuid-001'")
        row = cursor.fetchone()
        assert row["account"] == "claude"
        assert row["title"] == "Test Chat"
        assert row["message_count"] == 5
        db.close()

    def test_upsert_replaces_on_duplicate(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-uuid-003",
                "account": "claude",
                "title": "Original Title",
                "message_count": 1,
            },
        )
        chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-uuid-003",
                "account": "claude",
                "title": "Updated Title",
                "message_count": 10,
            },
        )

        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM chats WHERE external_id = 'conv-uuid-003'")
        assert cursor.fetchone()[0] == 1

        cursor.execute("SELECT title, message_count FROM chats WHERE external_id = 'conv-uuid-003'")
        row = cursor.fetchone()
        assert row["title"] == "Updated Title"
        assert row["message_count"] == 10
        db.close()

    def test_upsert_preserves_row_id(self, temp_db):
        """Re-inserting a chat with the same external_id must return the same row id.

        INSERT OR REPLACE would delete+reinsert, changing the autoincrement id.
        ON CONFLICT DO UPDATE preserves the original row.
        Mirrors test_email_reindex_returns_correct_id.
        """
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        row_id = chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-stable-id-001",
                "account": "claude",
                "title": "Original",
                "message_count": 1,
            },
        )

        row_id_2 = chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-stable-id-001",
                "account": "claude",
                "title": "Updated",
                "message_count": 5,
            },
        )

        assert row_id == row_id_2, (
            f"Row id changed on re-index: {row_id} → {row_id_2}. "
            "INSERT OR REPLACE deletes+inserts, changing the AUTOINCREMENT id."
        )
        db.close()

    def test_upsert_does_not_orphan_messages(self, temp_db):
        """Re-inserting a chat must not orphan its messages.

        INSERT OR REPLACE would delete the chat row (new id), leaving
        messages pointing at the old chat_id — silently orphaned.
        ON CONFLICT DO UPDATE preserves the row and all FK references.
        """
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        chat_id = chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-orphan-test",
                "account": "claude",
                "title": "Original Title",
                "message_count": 0,
            },
        )

        # Add messages referencing this chat
        chats_db.insert_message(
            db.conn,
            {
                "chat_id": chat_id,
                "message_id": "msg-001",
                "role": "user",
                "content": "Hello",
                "created_at": "2025-01-15 10:00:00",
            },
        )
        chats_db.insert_message(
            db.conn,
            {
                "chat_id": chat_id,
                "message_id": "msg-002",
                "role": "assistant",
                "content": "Hi there",
                "created_at": "2025-01-15 10:01:00",
            },
        )
        db.conn.commit()

        # Re-insert same chat with updated data
        chat_id_2 = chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-orphan-test",
                "account": "claude",
                "title": "Updated Title",
                "message_count": 2,
            },
        )

        # Row id must be preserved
        assert chat_id == chat_id_2

        # Messages must still be linked
        messages = chats_db.get_chat_messages(db.conn, chat_id)
        assert len(messages) == 2, (
            f"Expected 2 messages, got {len(messages)}. Messages were orphaned by chat re-insert."
        )
        assert messages[0]["content"] == "Hello"
        assert messages[1]["content"] == "Hi there"

        # Chat data must be updated
        chat = chats_db.get_chat_by_id(db.conn, chat_id)
        assert chat["title"] == "Updated Title"
        assert chat["message_count"] == 2
        db.close()


class TestInsertChatMessage:
    """Test chats_db.insert_message()."""

    def test_insert_and_verify(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        # Insert chat first (FK reference)
        chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-msg-test",
                "account": "claude",
            },
        )

        # Get internal ID
        internal_id = chats_db.get_chat_id_by_uuid(db.conn, "conv-msg-test")

        msg_id = chats_db.insert_message(
            db.conn,
            {
                "chat_id": internal_id,
                "message_id": "msg-001",
                "role": "user",
                "content": "Hello there!",
                "created_at": "2025-01-15 10:00:00",
            },
        )

        assert isinstance(msg_id, int)
        cursor = db.conn.cursor()
        cursor.execute("SELECT * FROM messages WHERE id = ?", (msg_id,))
        row = cursor.fetchone()
        assert row["role"] == "user"
        assert row["content"] == "Hello there!"
        db.close()


class TestGetChatIdByUuid:
    """Test chats_db.get_chat_id_by_uuid()."""

    def test_found(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        chats_db.insert_chat(
            db.conn,
            {
                "external_id": "uuid-lookup",
                "account": "claude",
            },
        )
        result = chats_db.get_chat_id_by_uuid(db.conn, "uuid-lookup")
        assert result is not None
        assert isinstance(result, int)
        db.close()

    def test_not_found(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        result = chats_db.get_chat_id_by_uuid(db.conn, "nonexistent-uuid")
        assert result is None
        db.close()


class TestDeleteChatMessages:
    """Test chats_db.delete_chat_messages()."""

    def test_deletes_and_returns_count(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-del-test",
                "account": "claude",
            },
        )
        chat_id = chats_db.get_chat_id_by_uuid(db.conn, "conv-del-test")

        # Insert 3 messages
        for i in range(3):
            chats_db.insert_message(
                db.conn,
                {
                    "chat_id": chat_id,
                    "role": "user",
                    "content": f"Message {i}",
                },
            )

        deleted = chats_db.delete_chat_messages(db.conn, chat_id)
        assert deleted == 3

        cursor = db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", (chat_id,))
        assert cursor.fetchone()[0] == 0
        db.close()


class TestListChats:
    """Test chats_db.list_chats()."""

    def _seed_chats(self, db):
        chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-1",
                "account": "claude",
                "title": "Claude Chat 1",
                "updated_at": "2025-01-01",
                "status": "removed",
            },
        )
        chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-2",
                "account": "chatgpt",
                "title": "GPT Chat",
                "updated_at": "2025-01-02",
            },
        )

    def test_excludes_removed_by_default(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        self._seed_chats(db)

        result = chats_db.list_chats(db.conn)
        assert len(result["chats"]) == 1
        assert result["chats"][0]["title"] == "GPT Chat"
        db.close()

    def test_includes_removed_when_requested(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        self._seed_chats(db)

        result = chats_db.list_chats(db.conn, status="all")
        assert len(result["chats"]) == 2
        db.close()

    def test_account_filter(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        self._seed_chats(db)

        result = chats_db.list_chats(db.conn, account="chatgpt")
        assert len(result["chats"]) == 1
        assert result["chats"][0]["account"] == "chatgpt"
        db.close()

    def test_no_chats_returns_empty(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        result = chats_db.list_chats(db.conn)
        assert result["chats"] == []
        db.close()


class TestUploadCRUD:
    """Test create_upload, get_upload_by_hash, get_recent_uploads, update_upload."""

    def test_full_lifecycle(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)

        # Create
        upload_id = uploads_db.create_upload(
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

        # Get by hash
        found = uploads_db.get_upload_by_hash(db.conn, "abc123hash")
        assert found is not None
        assert found["filename"] == "chat_export.json"
        assert found["status"] == "pending"

        # Not found
        assert uploads_db.get_upload_by_hash(db.conn, "nonexistent") is None

        # Update
        uploads_db.update_upload(db.conn, upload_id, status="completed", items_added=42)
        found = uploads_db.get_upload_by_hash(db.conn, "abc123hash")
        assert found["status"] == "completed"
        assert found["items_added"] == 42

        # Get recent
        uploads = uploads_db.get_recent_uploads(db.conn)
        assert len(uploads) == 1

        db.close()

    def test_get_recent_uploads_type_filter(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        uploads_db.create_upload(
            db.conn,
            {
                "filename": "a.json",
                "file_hash": "h1",
                "type": "chat",
            },
        )
        uploads_db.create_upload(
            db.conn,
            {
                "filename": "b.csv",
                "file_hash": "h2",
                "type": "email",
            },
        )

        assert len(uploads_db.get_recent_uploads(db.conn, upload_type="chat")) == 1
        assert len(uploads_db.get_recent_uploads(db.conn, upload_type="email")) == 1
        assert len(uploads_db.get_recent_uploads(db.conn)) == 2
        db.close()

    def test_update_upload_ignores_disallowed_fields(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        uid = uploads_db.create_upload(
            db.conn,
            {
                "filename": "x.json",
                "file_hash": "hx",
                "type": "chat",
            },
        )
        # Pass disallowed field — should be silently ignored
        uploads_db.update_upload(db.conn, uid, status="completed", filename="HACKED.json")
        found = uploads_db.get_upload_by_hash(db.conn, "hx")
        assert found["filename"] == "x.json"  # unchanged
        assert found["status"] == "completed"
        db.close()


class TestInsertEmail:
    """Test emails_db.insert_email()."""

    def test_insert_and_verify(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        row_id = emails_db.insert_email(
            db.conn,
            {
                "message_id": "msg-email-001",
                "thread_id": "thread-001",
                "account": "personal",
                "from_address": "alice@example.com",
                "from_name": "Alice",
                "subject": "Test Email",
                "body_preview": "Hello from Alice",
                "received_at": "2025-01-15 10:00:00",
                "labels": "INBOX,IMPORTANT",
                "has_attachments": True,
            },
        )

        assert isinstance(row_id, int)
        cursor = db.conn.cursor()
        cursor.execute("SELECT * FROM emails WHERE id = ?", (row_id,))
        row = cursor.fetchone()
        assert row["from_address"] == "alice@example.com"
        assert row["subject"] == "Test Email"
        assert row["has_attachments"] == 1
        db.close()

    def test_email_reindex_preserves_access_control(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        emails_db.insert_email(
            db.conn,
            {
                "message_id": "msg-reindex-001",
                "thread_id": "thread-001",
                "account": "personal",
                "subject": "Original Subject",
                "received_at": "2025-01-15 10:00:00",
            },
        )

        # Create referenced records for FK columns
        db.conn.execute("INSERT INTO clients (id, name, slug, client_type) VALUES (1, 'Test', 'test', 'external')")
        db.conn.execute("INSERT INTO projects (id, name, status) VALUES (2, 'TestProj', 'listed')")

        # Simulate enrichment: set access-control and AI columns
        db.conn.execute("""
            UPDATE emails SET
                mcp_read = 'allow',
                mcp_view = 'visible',
                client_id = 1,
                project_id = 2
            WHERE message_id = 'msg-reindex-001' AND account = 'personal'
        """)

        # Re-index same email with updated subject
        emails_db.insert_email(
            db.conn,
            {
                "message_id": "msg-reindex-001",
                "thread_id": "thread-001",
                "account": "personal",
                "subject": "Updated Subject",
                "received_at": "2025-01-15 10:00:00",
            },
        )

        cursor = db.conn.cursor()
        cursor.execute("SELECT * FROM emails WHERE message_id = 'msg-reindex-001' AND account = 'personal'")
        row = cursor.fetchone()

        # Ingest column updated
        assert row["subject"] == "Updated Subject"

        # Access-control and enrichment columns preserved
        assert row["mcp_read"] == "allow"
        assert row["mcp_view"] == "visible"
        assert row["client_id"] == 1
        assert row["project_id"] == 2
        db.close()

    def test_email_reindex_returns_correct_id(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        row_id = emails_db.insert_email(
            db.conn,
            {
                "message_id": "msg-stable-id-001",
                "thread_id": "thread-001",
                "account": "personal",
                "subject": "Original",
                "received_at": "2025-01-15 10:00:00",
            },
        )

        row_id_2 = emails_db.insert_email(
            db.conn,
            {
                "message_id": "msg-stable-id-001",
                "thread_id": "thread-001",
                "account": "personal",
                "subject": "Updated",
                "received_at": "2025-01-15 10:00:00",
            },
        )

        assert row_id == row_id_2, (
            f"Row id changed on re-index: {row_id} → {row_id_2}. "
            "INSERT OR REPLACE deletes+inserts, changing the AUTOINCREMENT id."
        )
        db.close()

    def test_email_reindex_with_intervening_insert(self, temp_db):
        """lastrowid must not leak from an intervening INSERT on the same connection."""
        from footprinter.ingest.database import Database

        db = Database(temp_db)

        # Bump emails sequence so email ID != chat ID (both start at 1 otherwise)
        emails_db.insert_email(
            db.conn,
            {
                "message_id": "msg-bump-sequence",
                "thread_id": "thread-000",
                "account": "personal",
                "subject": "Sequence bumper",
                "received_at": "2025-01-01 00:00:00",
            },
        )

        row_id_1 = emails_db.insert_email(
            db.conn,
            {
                "message_id": "msg-interleave-001",
                "thread_id": "thread-001",
                "account": "personal",
                "subject": "First insert",
                "received_at": "2025-01-15 10:00:00",
            },
        )

        # Intervening INSERT pollutes lastrowid on this connection
        chat_id = chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-interleave-pollute",
                "account": "claude",
            },
        )
        assert chat_id != row_id_1, f"Precondition: chat_id ({chat_id}) must differ from email row_id ({row_id_1})"

        # Re-insert same email (upsert path)
        row_id_2 = emails_db.insert_email(
            db.conn,
            {
                "message_id": "msg-interleave-001",
                "thread_id": "thread-001",
                "account": "personal",
                "subject": "Re-indexed",
                "received_at": "2025-01-15 10:00:00",
            },
        )

        assert row_id_1 == row_id_2, (
            f"insert_email() returned {row_id_2} after upsert, expected {row_id_1}. "
            f"lastrowid leaked from insert_chat() (chat_id={chat_id})."
        )
        db.close()


class TestDeadTablesRemoved:
    """Verify runs and pipeline_watermarks tables no longer exist after init_db()."""

    def test_runs_table_not_created(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        tables = [r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "runs" not in tables, "runs table should no longer be created"
        db.close()

    def test_pipeline_watermarks_table_not_created(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        tables = [r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "pipeline_watermarks" not in tables, "pipeline_watermarks table should no longer be created"
        db.close()

    def test_ingests_table_exists(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        tables = [r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "ingests" in tables, "ingests table must exist"
        db.close()


class TestDeadMethodsRemoved:
    """Verify start_indexing_run and complete_indexing_run are removed from Database."""

    def test_no_start_indexing_run(self, temp_db):
        from footprinter.ingest.database import Database

        assert not hasattr(Database, "start_indexing_run"), "start_indexing_run should be removed from Database"

    def test_no_complete_indexing_run(self, temp_db):
        from footprinter.ingest.database import Database

        assert not hasattr(Database, "complete_indexing_run"), "complete_indexing_run should be removed from Database"


class TestInsertEmailDoesNotCommit:
    """insert_email() must not auto-commit — caller controls transaction boundary."""

    def test_uncommitted_row_invisible_to_second_connection(self, tmp_path):
        import sqlite3

        from footprinter.ingest.database import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path=db_path)
        db.conn.execute("PRAGMA journal_mode=DELETE")

        emails_db.insert_email(
            db.conn,
            {
                "message_id": "msg-no-commit-001",
                "thread_id": "thread-001",
                "account": "personal",
                "from_address": "alice@example.com",
                "subject": "Test",
                "received_at": "2025-01-15 10:00:00",
            },
        )

        # Second connection — uncommitted data should be invisible
        conn2 = sqlite3.connect(db_path)
        count = conn2.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        conn2.close()

        assert count == 0, (
            f"insert_email() auto-committed — row visible from second connection "
            f"({count} row(s)). It should leave commit to the caller."
        )
        db.close()


class TestInsertBrowserVisitDoesNotCommit:
    """insert_visit() must not auto-commit — caller controls transaction boundary."""

    def test_uncommitted_row_invisible_to_second_connection(self, tmp_path):
        import sqlite3

        from footprinter.ingest.database import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path=db_path)
        db.conn.execute("PRAGMA journal_mode=DELETE")

        browser_db.insert_visit(
            db.conn,
            {
                "url": "https://example.com",
                "title": "Example",
                "visit_time": "2025-01-15 10:00:00",
                "browser": "safari",
            },
        )

        # Second connection — uncommitted data should be invisible
        conn2 = sqlite3.connect(db_path)
        count = conn2.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
        conn2.close()

        assert count == 0, (
            f"insert_visit() auto-committed — row visible from second connection "
            f"({count} row(s)). It should leave commit to the caller."
        )
        db.close()


class TestInsertChatDoesNotCommit:
    """insert_chat() must not auto-commit — caller controls transaction boundary."""

    def test_uncommitted_row_invisible_to_second_connection(self, tmp_path):
        import sqlite3

        from footprinter.ingest.database import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path=db_path)
        db.conn.execute("PRAGMA journal_mode=DELETE")

        chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-no-commit-001",
                "account": "claude",
                "title": "Should Not Commit",
            },
        )

        conn2 = sqlite3.connect(db_path)
        count = conn2.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        conn2.close()

        assert count == 0, (
            f"insert_chat() auto-committed — row visible from second connection "
            f"({count} row(s)). It should leave commit to the caller."
        )
        db.close()


class TestInsertMessageDoesNotCommit:
    """insert_message() must not auto-commit — caller controls transaction boundary."""

    def test_uncommitted_row_invisible_to_second_connection(self, tmp_path):
        import sqlite3

        from footprinter.ingest.database import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path=db_path)
        db.conn.execute("PRAGMA journal_mode=DELETE")

        # Insert and commit the chat so the FK exists
        chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-for-msg-test",
                "account": "claude",
            },
        )
        db.conn.commit()
        chat_id = chats_db.get_chat_id_by_uuid(db.conn, "conv-for-msg-test")

        chats_db.insert_message(
            db.conn,
            {
                "chat_id": chat_id,
                "message_id": "msg-no-commit-001",
                "role": "user",
                "content": "Should not commit",
            },
        )

        conn2 = sqlite3.connect(db_path)
        count = conn2.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn2.close()

        assert count == 0, (
            f"insert_message() auto-committed — row visible from second connection "
            f"({count} row(s)). It should leave commit to the caller."
        )
        db.close()


class TestDeleteChatMessagesDoesNotCommit:
    """delete_chat_messages() must not auto-commit — caller controls transaction boundary."""

    def test_uncommitted_delete_invisible_to_second_connection(self, tmp_path):
        import sqlite3

        from footprinter.ingest.database import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path=db_path)
        db.conn.execute("PRAGMA journal_mode=DELETE")

        chats_db.insert_chat(db.conn, {"external_id": "conv-del-nc", "account": "claude"})
        db.conn.commit()
        chat_id = chats_db.get_chat_id_by_uuid(db.conn, "conv-del-nc")

        for i in range(3):
            chats_db.insert_message(db.conn, {"chat_id": chat_id, "role": "user", "content": f"Msg {i}"})
        db.conn.commit()

        chats_db.delete_chat_messages(db.conn, chat_id)

        conn2 = sqlite3.connect(db_path)
        count = conn2.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", (chat_id,)).fetchone()[0]
        conn2.close()

        assert count == 3, (
            f"delete_chat_messages() auto-committed — only {count} row(s) visible from "
            f"second connection (expected 3). It should leave commit to the caller."
        )
        db.close()


class TestMoveMessagesToChatDoesNotCommit:
    """move_messages_to_chat() must not auto-commit — caller controls transaction boundary."""

    def test_uncommitted_move_invisible_to_second_connection(self, tmp_path):
        import sqlite3

        from footprinter.ingest.database import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path=db_path)
        db.conn.execute("PRAGMA journal_mode=DELETE")

        chats_db.insert_chat(db.conn, {"external_id": "conv-move-src", "account": "claude"})
        chats_db.insert_chat(db.conn, {"external_id": "conv-move-tgt", "account": "claude"})
        db.conn.commit()

        src_id = chats_db.get_chat_id_by_uuid(db.conn, "conv-move-src")
        tgt_id = chats_db.get_chat_id_by_uuid(db.conn, "conv-move-tgt")

        chats_db.insert_message(db.conn, {"chat_id": src_id, "role": "user", "content": "Move me"})
        db.conn.commit()

        msg_id = db.conn.execute("SELECT id FROM messages WHERE chat_id = ?", (src_id,)).fetchone()["id"]
        chats_db.move_messages_to_chat(db.conn, src_id, tgt_id, [msg_id])

        conn2 = sqlite3.connect(db_path)
        count = conn2.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", (src_id,)).fetchone()[0]
        conn2.close()

        assert count == 1, (
            f"move_messages_to_chat() auto-committed — {count} row(s) on source from second "
            f"connection (expected 1). It should leave commit to the caller."
        )
        db.close()


class TestUpdateChatMessageCountDoesNotCommit:
    """update_chat_message_count() must not auto-commit — caller controls transaction boundary."""

    def test_uncommitted_update_invisible_to_second_connection(self, tmp_path):
        import sqlite3

        from footprinter.ingest.database import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path=db_path)
        db.conn.execute("PRAGMA journal_mode=DELETE")

        chats_db.insert_chat(
            db.conn,
            {
                "external_id": "conv-count-nc",
                "account": "claude",
                "message_count": 0,
            },
        )
        db.conn.commit()
        chat_id = chats_db.get_chat_id_by_uuid(db.conn, "conv-count-nc")

        for i in range(5):
            chats_db.insert_message(db.conn, {"chat_id": chat_id, "role": "user", "content": f"Msg {i}"})
        db.conn.commit()

        chats_db.update_chat_message_count(db.conn, chat_id)

        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute("SELECT message_count FROM chats WHERE id = ?", (chat_id,)).fetchone()
        conn2.close()

        assert row["message_count"] == 0, (
            f"update_chat_message_count() auto-committed — message_count is "
            f"{row['message_count']} from second connection (expected 0). "
            f"It should leave commit to the caller."
        )
        db.close()


class TestCreateUploadDoesNotCommit:
    """create_upload() must not auto-commit — caller controls transaction boundary."""

    def test_uncommitted_row_invisible_to_second_connection(self, tmp_path):
        import sqlite3

        from footprinter.ingest.database import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path=db_path)
        db.conn.execute("PRAGMA journal_mode=DELETE")

        uploads_db.create_upload(
            db.conn,
            {
                "filename": "test.zip",
                "file_hash": "hash-no-commit",
                "type": "chat",
            },
        )

        conn2 = sqlite3.connect(db_path)
        count = conn2.execute("SELECT COUNT(*) FROM uploads").fetchone()[0]
        conn2.close()

        assert count == 0, (
            f"create_upload() auto-committed — row visible from second connection "
            f"({count} row(s)). It should leave commit to the caller."
        )
        db.close()


class TestMoveMessagesToChat:
    """Test move_messages_to_chat empty branch."""

    def test_empty_message_ids_returns_zero(self, temp_db):
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        result = chats_db.move_messages_to_chat(db.conn, source_id=1, target_id=2, message_ids=[])
        assert result == 0
        db.close()
