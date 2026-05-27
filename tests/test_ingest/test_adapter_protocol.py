"""Tests for the adapter protocol types.

Validates PipeStatus, ErrorType, PipeResult, PipeAdapter Protocol,
and concrete BrowserAdapter / ChatAdapter implementations.
"""

from enum import Enum
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch


class TestPipeStatus:
    """PipeStatus enum covers all orchestrator status strings."""

    def test_has_all_five_values(self):
        from footprinter.ingest.adapters import PipeStatus

        expected = {"completed", "completed_with_errors", "skipped", "error", "info"}
        actual = {s.value for s in PipeStatus}
        assert actual == expected

    def test_value_roundtrip(self):
        from footprinter.ingest.adapters import PipeStatus

        for member in PipeStatus:
            assert PipeStatus(member.value) is member

    def test_is_enum(self):
        from footprinter.ingest.adapters import PipeStatus

        assert issubclass(PipeStatus, Enum)


class TestErrorType:
    """ErrorType enum covers orchestrator error categories."""

    def test_has_all_four_values(self):
        from footprinter.ingest.adapters import ErrorType

        expected = {"missing_dependency", "database", "config", "runtime"}
        actual = {e.value for e in ErrorType}
        assert actual == expected

    def test_is_enum(self):
        from footprinter.ingest.adapters import ErrorType

        assert issubclass(ErrorType, Enum)


class TestPipeResult:
    """PipeResult dataclass construction and defaults."""

    def test_minimal_construction(self):
        from footprinter.ingest.adapters import PipeResult, PipeStatus

        result = PipeResult(stage="browser", status=PipeStatus.COMPLETED)
        assert result.stage == "browser"
        assert result.status == PipeStatus.COMPLETED
        assert result.elapsed_seconds == 0.0
        assert result.data == {}
        assert result.error is None
        assert result.error_type is None

    def test_full_construction(self):
        from footprinter.ingest.adapters import ErrorType, PipeResult, PipeStatus

        result = PipeResult(
            stage="email",
            status=PipeStatus.ERROR,
            elapsed_seconds=12.5,
            data={"emails_indexed": 42},
            error="IMAP connection failed",
            error_type=ErrorType.RUNTIME,
        )
        assert result.stage == "email"
        assert result.status == PipeStatus.ERROR
        assert result.elapsed_seconds == 12.5
        assert result.data == {"emails_indexed": 42}
        assert result.error == "IMAP connection failed"
        assert result.error_type == ErrorType.RUNTIME

    def test_data_dict_is_independent(self):
        """Each instance gets its own data dict (no shared mutable default)."""
        from footprinter.ingest.adapters import PipeResult, PipeStatus

        a = PipeResult(stage="a", status=PipeStatus.COMPLETED)
        b = PipeResult(stage="b", status=PipeStatus.COMPLETED)
        a.data["key"] = "value"
        assert "key" not in b.data


class TestPipeResultFactories:
    """Factory classmethods produce correct status and data."""

    def test_completed(self):
        from footprinter.ingest.adapters import PipeResult, PipeStatus

        result = PipeResult.completed("browser", files_indexed=100)
        assert result.stage == "browser"
        assert result.status == PipeStatus.COMPLETED
        assert result.data == {"files_indexed": 100}
        assert result.error is None

    def test_completed_with_errors(self):
        from footprinter.ingest.adapters import PipeResult, PipeStatus

        result = PipeResult.completed_with_errors("email", error="3 messages failed", emails_indexed=97)
        assert result.stage == "email"
        assert result.status == PipeStatus.COMPLETED_WITH_ERRORS
        assert result.data == {"emails_indexed": 97}
        assert result.error == "3 messages failed"

    def test_skipped(self):
        from footprinter.ingest.adapters import PipeResult, PipeStatus

        result = PipeResult.skipped("drive_files", reason="Not installed: google-api")
        assert result.stage == "drive_files"
        assert result.status == PipeStatus.SKIPPED
        assert result.data == {"reason": "Not installed: google-api"}
        assert result.error is None

    def test_make_error(self):
        from footprinter.ingest.adapters import ErrorType, PipeResult, PipeStatus

        result = PipeResult.make_error(
            "local_files",
            error="disk full",
            error_type=ErrorType.RUNTIME,
        )
        assert result.stage == "local_files"
        assert result.status == PipeStatus.ERROR
        assert result.error == "disk full"
        assert result.error_type == ErrorType.RUNTIME

    def test_info(self):
        from footprinter.ingest.adapters import PipeResult, PipeStatus

        result = PipeResult.info("chat", chats=5, messages=120)
        assert result.stage == "chat"
        assert result.status == PipeStatus.INFO
        assert result.data == {"chats": 5, "messages": 120}


class TestPipeResultSerialization:
    """to_dict() output matches legacy orchestrator dict shape."""

    def test_completed_dict_shape(self):
        from footprinter.ingest.adapters import PipeResult, PipeStatus

        result = PipeResult(
            stage="browser",
            status=PipeStatus.COMPLETED,
            elapsed_seconds=2.3,
            data={"history_count": 500, "new_entries": 42},
        )
        d = result.to_dict()
        assert d["stage"] == "browser"
        assert d["status"] == "completed"
        assert d["elapsed_seconds"] == 2.3
        # data keys are flattened to top level
        assert d["history_count"] == 500
        assert d["new_entries"] == 42
        # error fields omitted when None
        assert "error" not in d
        assert "error_type" not in d

    def test_error_dict_shape(self):
        from footprinter.ingest.adapters import ErrorType, PipeResult, PipeStatus

        result = PipeResult(
            stage="email",
            status=PipeStatus.ERROR,
            error="connection refused",
            error_type=ErrorType.RUNTIME,
        )
        d = result.to_dict()
        assert d["stage"] == "email"
        assert d["status"] == "error"
        assert d["error"] == "connection refused"
        assert d["error_type"] == "runtime"

    def test_data_does_not_overwrite_reserved_keys(self):
        """If data contains a key like 'stage', the reserved field wins."""
        from footprinter.ingest.adapters import PipeResult, PipeStatus

        result = PipeResult(
            stage="browser",
            status=PipeStatus.COMPLETED,
            data={"stage": "should_not_overwrite", "count": 10},
        )
        d = result.to_dict()
        assert d["stage"] == "browser"  # reserved key wins
        assert d["count"] == 10


class TestPipeAdapterProtocol:
    """PipeAdapter is runtime-checkable."""

    def test_conforming_class_passes(self):
        from footprinter.ingest.adapters import PipeAdapter, PipeContext, PipeResult

        class GoodAdapter:
            @property
            def name(self) -> str:
                return "test"

            @property
            def pipe_name(self) -> str:
                return "test_stage"

            @property
            def required_extras(self) -> List[str]:
                return []

            def run(self, db, ctx: PipeContext) -> PipeResult:
                return PipeResult.completed("test_stage")

            def status(self, db) -> Dict[str, Any]:
                return {}

        assert isinstance(GoodAdapter(), PipeAdapter)

    def test_non_conforming_class_fails(self):
        from footprinter.ingest.adapters import PipeAdapter

        class BadAdapter:
            pass

        assert not isinstance(BadAdapter(), PipeAdapter)

    def test_partial_conforming_fails(self):
        """Missing required methods should fail isinstance check."""
        from footprinter.ingest.adapters import PipeAdapter

        class PartialAdapter:
            @property
            def name(self) -> str:
                return "partial"

            # Missing pipe_name, required_extras, run, status

        assert not isinstance(PartialAdapter(), PipeAdapter)


class TestBrowserAdapterProtocol:
    """BrowserAdapter conforms to PipeAdapter and wraps _run_browser() logic."""

    def test_browser_adapter_conforms(self):
        from footprinter.ingest.adapters import PipeAdapter
        from footprinter.ingest.adapters.browser import BrowserAdapter

        assert isinstance(BrowserAdapter(), PipeAdapter)

    def test_browser_adapter_metadata(self):
        from footprinter.ingest.adapters.browser import BrowserAdapter

        adapter = BrowserAdapter()
        assert adapter.name == "browser"
        assert adapter.pipe_name == "browser"
        assert adapter.required_extras == []

    @patch("footprinter.ingest.adapters.browser.browser_db.insert_visit")
    @patch("footprinter.ingest.adapters.browser.BrowserIndexer")
    def test_browser_adapter_run_returns_stage_result(self, MockManager, mock_insert):
        from footprinter.ingest.adapters import PipeContext, PipeResult, PipeStatus
        from footprinter.ingest.adapters.browser import BrowserAdapter

        entries = [{"url": "https://a.com"}, {"url": "https://b.com"}]
        MockManager.return_value.parse_all.return_value = iter(entries)

        db = MagicMock()
        ctx = PipeContext(source_config={"browsers": ["safari"]})

        adapter = BrowserAdapter()
        result = adapter.run(db, ctx)

        assert isinstance(result, PipeResult)
        assert result.status == PipeStatus.COMPLETED
        assert result.data["urls_indexed"] == 2
        assert result.data["errors"] == 0
        assert mock_insert.call_count == 2

    @patch("footprinter.ingest.adapters.browser.browser_db.insert_visit")
    @patch("footprinter.ingest.adapters.browser.BrowserIndexer")
    def test_browser_adapter_run_with_errors(self, MockManager, mock_insert):
        from footprinter.ingest.adapters import PipeContext, PipeStatus
        from footprinter.ingest.adapters.browser import BrowserAdapter

        entries = [{"url": "https://a.com"}, {"url": "https://b.com"}, {"url": "https://c.com"}]
        MockManager.return_value.parse_all.return_value = iter(entries)

        db = MagicMock()
        mock_insert.side_effect = [None, Exception("dup"), None]
        ctx = PipeContext(source_config={"browsers": ["safari"]})

        adapter = BrowserAdapter()
        result = adapter.run(db, ctx)

        assert result.status == PipeStatus.COMPLETED_WITH_ERRORS
        assert result.data["urls_indexed"] == 2
        assert result.data["errors"] == 1

    def test_browser_adapter_status(self):
        from footprinter.ingest.adapters.browser import BrowserAdapter

        db = MagicMock()
        db.conn.cursor.return_value.fetchone.return_value = [150]

        adapter = BrowserAdapter()
        status = adapter.status(db)

        assert status == {"visits": 150}


class TestChatAdapterProtocol:
    """ChatAdapter conforms to PipeAdapter and wraps _run_chat() logic."""

    def test_chat_adapter_conforms(self):
        from footprinter.ingest.adapters import PipeAdapter
        from footprinter.ingest.adapters.chat import ChatAdapter

        assert isinstance(ChatAdapter(), PipeAdapter)

    def test_chat_adapter_metadata(self):
        from footprinter.ingest.adapters.chat import ChatAdapter

        adapter = ChatAdapter()
        assert adapter.name == "chat"
        assert adapter.pipe_name == "chat"
        assert adapter.required_extras == []

    @patch("footprinter.ingest.adapters.chat.ChatIndexer")
    def test_chat_adapter_run_returns_stage_result(self, MockManager):
        from footprinter.ingest.adapters import PipeContext, PipeResult, PipeStatus
        from footprinter.ingest.adapters.chat import ChatAdapter

        MockManager.return_value.get_stats.return_value = {
            "total_chats": 5,
            "total_messages": 120,
            "by_account": {"claude": 3, "chatgpt": 2},
        }

        db = MagicMock()
        ctx = PipeContext(source_config={}, scan_roots=[])

        adapter = ChatAdapter()
        result = adapter.run(db, ctx)

        assert isinstance(result, PipeResult)
        assert result.status == PipeStatus.COMPLETED
        assert result.data["current_chats"] == 5
        assert result.data["current_messages"] == 120
        assert result.data["by_account"] == {"claude": 3, "chatgpt": 2}
        assert "by_source" not in result.data

    def test_chat_adapter_status(self):
        from footprinter.ingest.adapters.chat import ChatAdapter

        db = MagicMock()
        cursor = MagicMock()
        db.conn.cursor.return_value = cursor
        cursor.fetchone.side_effect = [[10], [250]]

        adapter = ChatAdapter()
        status = adapter.status(db)

        assert status == {"chats": 10, "messages": 250}

    @patch("footprinter.ingest.adapters.chat.ChatIndexer")
    def test_chat_adapter_run_error_returns_stage_result(self, MockManager):
        from footprinter.ingest.adapters import ErrorType, PipeContext, PipeStatus
        from footprinter.ingest.adapters.chat import ChatAdapter

        MockManager.side_effect = Exception("database locked")

        db = MagicMock()
        ctx = PipeContext(source_config={}, scan_roots=[])

        adapter = ChatAdapter()
        result = adapter.run(db, ctx)

        assert result.status == PipeStatus.ERROR
        assert result.error_type == ErrorType.RUNTIME
        assert "database locked" in result.error


class TestBrowserAdapterLastRun:
    """BrowserAdapter extracts last_run from PipeContext and passes to BrowserIndexer."""

    @patch("footprinter.ingest.adapters.browser.BrowserIndexer")
    def test_adapter_passes_last_run_to_manager(self, MockManager):
        from datetime import datetime

        from footprinter.ingest.adapters.browser import BrowserAdapter
        from footprinter.ingest.adapters.protocol import PipeContext

        MockManager.return_value.parse_all.return_value = iter([])

        cutoff = datetime(2026, 4, 1, 12, 0, 0)
        ctx = PipeContext(
            source_config={"browsers": ["safari"]},
            last_run=cutoff,
        )

        BrowserAdapter().run(MagicMock(), ctx)

        MockManager.assert_called_once_with(ctx.source_config, since=cutoff)

    @patch("footprinter.ingest.adapters.browser.BrowserIndexer")
    def test_adapter_full_mode_ignores_last_run(self, MockManager):
        from datetime import datetime

        from footprinter.ingest.adapters.browser import BrowserAdapter
        from footprinter.ingest.adapters.protocol import PipeContext

        MockManager.return_value.parse_all.return_value = iter([])

        ctx = PipeContext(
            source_config={"browsers": ["safari"]},
            last_run=datetime(2026, 4, 1, 12, 0, 0),
            full_mode=True,
        )

        BrowserAdapter().run(MagicMock(), ctx)

        MockManager.assert_called_once_with(ctx.source_config, since=None)

    @patch("footprinter.ingest.adapters.browser.BrowserIndexer")
    def test_adapter_no_last_run_passes_none(self, MockManager):
        from footprinter.ingest.adapters.browser import BrowserAdapter
        from footprinter.ingest.adapters.protocol import PipeContext

        MockManager.return_value.parse_all.return_value = iter([])

        ctx = PipeContext(source_config={"browsers": ["safari"]})

        BrowserAdapter().run(MagicMock(), ctx)

        MockManager.assert_called_once_with(ctx.source_config, since=None)


class TestPipeContext:
    """PipeContext dataclass construction and defaults."""

    def test_construction_with_all_fields(self):
        from datetime import datetime

        from footprinter.ingest.adapters import PipeContext

        def cb(n): return None
        ctx = PipeContext(
            source_config={"directories": ["~/Work"]},
            config_path="/etc/fp.yaml",
            full_mode=True,
            last_run=datetime(2026, 4, 1),
            on_progress=cb,
        )
        assert ctx.source_config == {"directories": ["~/Work"]}
        assert ctx.config_path == "/etc/fp.yaml"
        assert ctx.full_mode is True
        assert ctx.last_run == datetime(2026, 4, 1)
        assert ctx.on_progress is cb

    def test_defaults(self):
        from footprinter.ingest.adapters import PipeContext

        ctx = PipeContext(source_config={"key": "val"})
        assert ctx.source_config == {"key": "val"}
        assert ctx.config_path == ""
        assert ctx.full_mode is False
        assert ctx.last_run is None
        assert ctx.on_progress is None

    def test_source_config_required(self):
        import pytest

        from footprinter.ingest.adapters import PipeContext

        with pytest.raises(TypeError):
            PipeContext()  # type: ignore[call-arg]
