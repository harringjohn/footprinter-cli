"""Tests for footprinter.db.messages query functions.

Verifies that list_messages(), get_message(), and search_messages() include
visibility and access in returned dicts.
"""

from footprinter.db.messages import get_message, list_messages, search_messages


class TestMessagesAccessColumns:
    """Access control columns must appear in message query results."""

    def _seed_chat_and_message(self, conn):
        conn.execute(
            """
            INSERT INTO chats (id, external_id, account, title, message_count)
            VALUES (1, 'chat-001', 'claude', 'Test Chat', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO messages
                (chat_id, message_id, role, content, created_at,
                 visibility, access)
            VALUES
                (1, 'msg-001', 'user', 'hello world', '2025-01-01 12:00:00',
                 'full', 'allow')
            """
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def test_list_messages_includes_access_columns(self, tool_db):
        self._seed_chat_and_message(tool_db)
        result = list_messages(tool_db)
        msg = result["messages"][0]
        assert msg["visibility"] == "full"
        assert msg["access"] == "allow"

    def test_get_message_includes_access_columns(self, tool_db):
        msg_id = self._seed_chat_and_message(tool_db)
        msg = get_message(tool_db, msg_id)
        assert msg is not None
        assert msg["visibility"] == "full"
        assert msg["access"] == "allow"

    def test_search_messages_includes_access_columns(self, tool_db):
        self._seed_chat_and_message(tool_db)
        result = search_messages(tool_db, "hello")
        msg = result["results"][0]
        assert msg["visibility"] == "full"
        assert msg["access"] == "allow"
