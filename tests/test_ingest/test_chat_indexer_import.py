"""Verify the renamed chat_indexer module and ChatIndexer class are importable."""

from pathlib import Path
from unittest.mock import MagicMock, patch


class TestChatIndexerImport:
    """ChatIndexer must be importable from the new module path."""

    def test_import_chat_indexer(self):
        from footprinter.ingest.chat_indexer import ChatIndexer

        assert ChatIndexer is not None

    def test_chat_indexer_instantiation(self, temp_db):
        from footprinter.ingest.chat_indexer import ChatIndexer
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        manager = ChatIndexer(db)
        assert manager.db is db
        db.close()


class TestProgressConsoleWiring:
    """upload() and _import_with_dedup() accept an optional console for progress UI."""

    def test_upload_accepts_console_none(self, temp_db):
        from footprinter.ingest.chat_indexer import ChatIndexer
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        manager = ChatIndexer(db)

        try:
            manager.upload(Path("/nonexistent/path.zip"), console=None)
            raise AssertionError("expected FileNotFoundError")
        except FileNotFoundError:
            pass
        finally:
            db.close()

    def test_upload_accepts_console_instance(self, temp_db):
        from rich.console import Console

        from footprinter.ingest.chat_indexer import ChatIndexer
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        manager = ChatIndexer(db)

        try:
            manager.upload(Path("/nonexistent/path.zip"), console=Console(quiet=True))
            raise AssertionError("expected FileNotFoundError")
        except FileNotFoundError:
            pass
        finally:
            db.close()

    def test_import_with_dedup_uses_track_when_console_given(self, temp_db, tmp_path):
        from footprinter.ingest.chat_indexer import ChatIndexer
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        manager = ChatIndexer(db)

        fake_parser = MagicMock()
        fake_parser.get_stats.return_value = {
            "total_chats": 0,
            "chats_with_messages": 0,
            "total_messages": 0,
            "earliest_chat": "",
            "latest_chat": "",
        }
        fake_parser.parse_chats.return_value = iter([])

        console = MagicMock()
        console.status.return_value.__enter__ = MagicMock()
        console.status.return_value.__exit__ = MagicMock(return_value=False)

        with patch("footprinter.ingest.chat_indexer.ClaudeParser", return_value=fake_parser), patch(
            "footprinter.ingest.chat_indexer.track", return_value=iter([])
        ) as mock_track, patch(
            "footprinter.ingest.chat_indexer._chat_vectorization_enabled", return_value=True
        ), patch.object(ChatIndexer, "_get_vector_store"):
            manager._import_with_dedup(tmp_path, "claude", console=console)

        assert mock_track.called, "track() should be invoked when console is provided"
        assert console.status.called, "console.status should wrap the vector-store pre-warm"
        db.close()

    def test_import_with_dedup_skips_prewarm_when_vectorization_disabled(self, temp_db, tmp_path):
        from footprinter.ingest.chat_indexer import ChatIndexer
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        manager = ChatIndexer(db)

        fake_parser = MagicMock()
        fake_parser.get_stats.return_value = {
            "total_chats": 0,
            "chats_with_messages": 0,
            "total_messages": 0,
            "earliest_chat": "",
            "latest_chat": "",
        }
        fake_parser.parse_chats.return_value = iter([])

        with patch("footprinter.ingest.chat_indexer.ClaudeParser", return_value=fake_parser), patch(
            "footprinter.ingest.chat_indexer._chat_vectorization_enabled", return_value=False
        ), patch("footprinter.ingest.chat_indexer.track", return_value=iter([])), patch.object(
            ChatIndexer, "_get_vector_store"
        ) as mock_get_store:
            manager._import_with_dedup(tmp_path, "claude", console=MagicMock())

        assert not mock_get_store.called, "_get_vector_store must not be called when vectorization is disabled"
        db.close()

    def test_import_with_dedup_no_progress_when_console_none(self, temp_db, tmp_path):
        from footprinter.ingest.chat_indexer import ChatIndexer
        from footprinter.ingest.database import Database

        db = Database(temp_db)
        manager = ChatIndexer(db)

        fake_parser = MagicMock()
        fake_parser.get_stats.return_value = {
            "total_chats": 0,
            "chats_with_messages": 0,
            "total_messages": 0,
            "earliest_chat": "",
            "latest_chat": "",
        }
        fake_parser.parse_chats.return_value = iter([])

        with patch("footprinter.ingest.chat_indexer.ClaudeParser", return_value=fake_parser), patch(
            "footprinter.ingest.chat_indexer.track"
        ) as mock_track:
            manager._import_with_dedup(tmp_path, "claude", console=None)

        assert not mock_track.called, "track() must not be invoked when console is None"
        db.close()
