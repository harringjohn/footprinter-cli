"""Tests for --rebuild-vectors: phase isolation, signal handling, write decoupling,
progress output, pre-flight validation, and enable-flag guards."""

import signal
from io import StringIO
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_conn(files=None, messages=None, chats=None):
    """Build a mock sqlite3 connection with canned query results.

    Args:
        files: list of dicts with "id" and "file_path" keys.
        messages: list of dicts for the messages JOIN query.
        chats: list of dicts for the chats query.
    """
    files = files or []
    messages = messages or []
    chats = chats or []

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    def _fetchall():
        """Return canned data based on the most recent execute() call."""
        last_sql = mock_cursor.execute.call_args[0][0] if mock_cursor.execute.call_args else ""
        if "FROM files" in last_sql:
            return [_row(f) for f in files]
        # Cleanup queries (status='removed') return empty — tests that need
        # removed items should call _cleanup_removed_vectors directly.
        if "FROM messages" in last_sql:
            if "status = 'removed'" in last_sql:
                return []
            return [_row(m) for m in messages]
        if "FROM chats" in last_sql:
            if "status = 'removed'" in last_sql:
                return []
            return [_row(c) for c in chats]
        return []

    def _fetchone():
        last_sql = mock_cursor.execute.call_args[0][0] if mock_cursor.execute.call_args else ""
        if "COUNT" in last_sql and "files" in last_sql:
            return [len(files)]
        if "SUM(vectorized_chunks)" in last_sql and "messages" in last_sql:
            return [0]
        if "vectorized_chunks = 0" in last_sql:
            return [0]  # no stale rows in mocked data
        if "COUNT" in last_sql and "messages" in last_sql:
            return [len(messages)]
        if "SUM(vectorized_chunks)" in last_sql and "files" in last_sql:
            return [0]
        if "COUNT" in last_sql and "chats" in last_sql:
            return [len(chats)]
        return [0]

    mock_cursor.fetchall.side_effect = _fetchall
    mock_cursor.fetchone.side_effect = _fetchone

    return mock_conn, mock_cursor


def _row(d):
    """Create a sqlite3.Row-like object from a dict."""
    mock = MagicMock()
    mock.__getitem__ = lambda self, key: d[key]
    mock.__contains__ = lambda self, key: key in d
    return mock


def _mock_store():
    """Build a mock VectorStore class and instance."""
    mock_inst = MagicMock()
    mock_inst.ef.return_value = [[0.1] * 384]
    mock_inst.check_integrity.return_value = {"status": "ok", "files": 0, "chats": 0}

    mock_cls = MagicMock()
    mock_cls.get_instance.return_value = mock_inst
    mock_cls.reset_instance = MagicMock()
    return mock_cls, mock_inst


def _run_rebuild(
    source="all",
    phase=None,
    mode=None,
    quiet=True,
    file_vec=True,
    chat_vec=True,
    files=None,
    messages=None,
    chats=None,
    store_inst=None,
    chroma_exists=False,
):
    """Run _rebuild_vectors with full mocking. Returns (mock_store_instance, mock_conn, mock_cursor)."""
    mock_vs_cls, mock_inst = _mock_store()
    if store_inst:
        mock_inst = store_inst
        mock_vs_cls.get_instance.return_value = mock_inst

    mock_conn, mock_cursor = _make_mock_conn(files=files, messages=messages, chats=chats)

    mock_chroma_path = MagicMock()
    mock_chroma_path.exists.return_value = chroma_exists

    with (
        patch("footprinter.ingest.vector_ops.sqlite3") as mock_sqlite,
        patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
        patch("footprinter.source_registry.get_config", return_value={"indexing": {"max_file_size_mb": 10}}),
        patch("footprinter.paths.get_chroma_path", return_value=mock_chroma_path),
        patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=file_vec),
        patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=chat_vec),
        patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
    ):
        mock_sqlite.connect.return_value = mock_conn

        from footprinter.ingest.vector_ops import _rebuild_vectors

        kwargs = dict(quiet=quiet, source=source, phase=phase)
        if mode is not None:
            kwargs["mode"] = mode
        _rebuild_vectors(**kwargs)

    return mock_inst, mock_conn, mock_cursor


# ---------------------------------------------------------------------------
# Phase isolation (TDD cycle 1)
# ---------------------------------------------------------------------------


class TestPhaseIsolation:
    """Each phase should run independently when --phase is specified."""

    def test_phase_files_only(self):
        """--phase files should only vectorize files, not messages or chat info."""
        mock_inst, _, mock_cursor = _run_rebuild(
            phase="files",
            files=[{"id": 1, "file_path": "/nonexistent"}],
            messages=[
                {
                    "id": 1,
                    "chat_id": 1,
                    "role": "user",
                    "content": "hi",
                    "created_at": "",
                    "title": "t",
                    "source": "claude",
                }
            ],
            chats=[{"id": 1, "title": "t", "source": "claude", "created_at": "", "message_count": 1}],
        )
        # Messages query should not have been executed
        executed_sqls = [str(c) for c in mock_cursor.execute.call_args_list]
        msg_queries = [s for s in executed_sqls if "FROM messages message" in s]
        chat_queries = [s for s in executed_sqls if "FROM chats" in s and "COUNT" not in s]
        assert len(msg_queries) == 0, "Message vectorization ran during --phase files"
        assert len(chat_queries) == 0, "Chat info vectorization ran during --phase files"

    def test_phase_messages_only(self):
        """--phase messages should only vectorize messages."""
        mock_inst, _, mock_cursor = _run_rebuild(
            phase="messages",
            messages=[
                {
                    "id": 1,
                    "chat_id": 1,
                    "role": "user",
                    "content": "hi",
                    "created_at": "",
                    "title": "t",
                    "source": "claude",
                }
            ],
        )
        # File query should not have run
        executed_sqls = [str(c) for c in mock_cursor.execute.call_args_list]
        file_queries = [s for s in executed_sqls if "FROM files" in s and "COUNT" not in s]
        assert len(file_queries) == 0, "File vectorization ran during --phase messages"

    def test_phase_chat_info_only(self):
        """--phase chat_info should only vectorize chat info."""
        mock_inst, _, mock_cursor = _run_rebuild(
            phase="chat_info",
            chats=[{"id": 1, "title": "t", "source": "claude", "created_at": "", "message_count": 1}],
        )
        executed_sqls = [str(c) for c in mock_cursor.execute.call_args_list]
        file_queries = [s for s in executed_sqls if "FROM files" in s and "COUNT" not in s]
        msg_queries = [s for s in executed_sqls if "FROM messages message" in s]
        assert len(file_queries) == 0, "File vectorization ran during --phase chat_info"
        assert len(msg_queries) == 0, "Message vectorization ran during --phase chat_info"

    def test_no_phase_runs_all(self):
        """No --phase should run all three phases."""
        mock_inst, _, mock_cursor = _run_rebuild(
            files=[{"id": 1, "file_path": "/nonexistent"}],
            messages=[
                {
                    "id": 1,
                    "chat_id": 1,
                    "role": "user",
                    "content": "hi",
                    "created_at": "",
                    "title": "t",
                    "source": "claude",
                }
            ],
            chats=[{"id": 1, "title": "t", "source": "claude", "created_at": "", "message_count": 1}],
        )
        executed_sqls = [str(c) for c in mock_cursor.execute.call_args_list]
        file_queries = [s for s in executed_sqls if "FROM files" in s and "COUNT" not in s]
        msg_queries = [s for s in executed_sqls if "FROM messages message" in s]
        _chat_queries = [s for s in executed_sqls if "FROM chats\n" in s or "FROM chats " in s]  # noqa: F841
        assert len(file_queries) > 0, "File vectorization did not run"
        assert len(msg_queries) > 0, "Message vectorization did not run"

    def test_phase_files_does_not_delete_chroma(self):
        """Single-phase rebuild should not delete chroma directory."""
        mock_vs_cls, mock_inst = _mock_store()
        mock_chroma_path = MagicMock()
        mock_chroma_path.exists.return_value = True
        mock_conn, _ = _make_mock_conn()

        with (
            patch("footprinter.ingest.vector_ops.sqlite3") as mock_sqlite,
            patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
            patch("footprinter.source_registry.get_config", return_value={"indexing": {"max_file_size_mb": 10}}),
            patch("footprinter.paths.get_chroma_path", return_value=mock_chroma_path),
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
            patch("shutil.rmtree") as mock_rmtree,
        ):
            mock_sqlite.connect.return_value = mock_conn

            from footprinter.ingest.vector_ops import _rebuild_vectors

            _rebuild_vectors(quiet=True, phase="files")

        mock_rmtree.assert_not_called()
        mock_vs_cls.reset_instance.assert_not_called()


# ---------------------------------------------------------------------------
# Signal handling (TDD cycle 2)
# ---------------------------------------------------------------------------


class TestSignalHandling:
    """SIGINT/SIGTERM should result in clean shutdown."""

    def test_shutdown_flag_stops_file_phase(self):
        """Setting _shutdown flag should stop file vectorization cleanly."""
        import footprinter.ingest.vector_ops as cli_mod

        mock_vs_cls, mock_inst = _mock_store()
        mock_conn, mock_cursor = _make_mock_conn(
            files=[{"id": i, "file_path": f"/file{i}.txt"} for i in range(10)],
        )
        mock_chroma_path = MagicMock()
        mock_chroma_path.exists.return_value = False

        # Set shutdown after first file processes
        call_count = [0]

        def index_and_shutdown(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 1:
                cli_mod._shutdown = True
            return 1  # chunks indexed

        mock_inst.index_file.side_effect = index_and_shutdown

        with (
            patch("footprinter.ingest.vector_ops.sqlite3") as mock_sqlite,
            patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
            patch("footprinter.source_registry.get_config", return_value={"indexing": {"max_file_size_mb": 10}}),
            patch("footprinter.paths.get_chroma_path", return_value=mock_chroma_path),
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=False),
            patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
            patch("pathlib.Path.exists", return_value=True),
            patch("footprinter.ingest.full_content_extractor.FullContentExtractor") as mock_ext,
        ):
            mock_sqlite.connect.return_value = mock_conn
            mock_ext_inst = MagicMock()
            mock_ext_inst.extract_with_chunking.return_value = [
                {"content": "chunk", "chunk_index": 0, "total_chunks": 1}
            ]
            mock_ext.return_value = mock_ext_inst

            cli_mod._shutdown = False
            cli_mod._rebuild_vectors(quiet=True, source="files")

        # Should have stopped before processing all 10 files
        assert call_count[0] < 10, "Shutdown flag did not stop file processing"
        # DB should have been committed (clean shutdown)
        mock_conn.commit.assert_called()

    def test_signal_handler_sets_flag(self):
        """_handle_shutdown should set the _shutdown flag."""
        import footprinter.ingest.vector_ops as cli_mod

        cli_mod._shutdown = False
        cli_mod._handle_shutdown(signal.SIGINT, None)
        assert cli_mod._shutdown is True
        cli_mod._shutdown = False  # cleanup

    def test_signal_handlers_restored_after_rebuild(self):
        """Original signal handlers should be restored after rebuild."""
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)

        _run_rebuild()

        assert signal.getsignal(signal.SIGINT) is original_sigint
        assert signal.getsignal(signal.SIGTERM) is original_sigterm


# ---------------------------------------------------------------------------
# Write decoupling (TDD cycle 3)
# ---------------------------------------------------------------------------


class TestWriteDecoupling:
    """Chroma and SQLite writes should be decoupled."""

    def test_chroma_failure_skips_sqlite_update(self):
        """When chroma write fails, SQLite timestamp should not be updated."""
        mock_inst = MagicMock()
        mock_inst.ef.return_value = [[0.1] * 384]
        mock_inst.check_integrity.return_value = {"status": "ok", "files": 0, "chats": 0}
        # Make chroma writes raise (upsert for incremental, add for full)
        mock_inst._chats.add.side_effect = RuntimeError("chroma corrupted")
        mock_inst._chats.upsert.side_effect = RuntimeError("chroma corrupted")

        mock_vs_cls = MagicMock()
        mock_vs_cls.get_instance.return_value = mock_inst
        mock_vs_cls.reset_instance = MagicMock()

        messages = [
            {
                "id": 1,
                "chat_id": 1,
                "role": "user",
                "content": "hello world",
                "created_at": "",
                "title": "Test",
                "source": "claude",
            }
        ]
        mock_conn, mock_cursor = _make_mock_conn(messages=messages)
        mock_chroma_path = MagicMock()
        mock_chroma_path.exists.return_value = False

        with (
            patch("footprinter.ingest.vector_ops.sqlite3") as mock_sqlite,
            patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
            patch("footprinter.source_registry.get_config", return_value={"indexing": {"max_file_size_mb": 10}}),
            patch("footprinter.paths.get_chroma_path", return_value=mock_chroma_path),
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=False),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
        ):
            mock_sqlite.connect.return_value = mock_conn

            from footprinter.ingest.vector_ops import _rebuild_vectors

            _rebuild_vectors(quiet=True, source="chats", phase="messages")

        # SQLite UPDATE for vectorized_at should NOT have been called
        update_calls = [
            c
            for c in mock_cursor.execute.call_args_list
            if len(c[0]) > 0 and "UPDATE" in str(c[0][0]) and "vectorized_at" in str(c[0][0])
        ]
        assert len(update_calls) == 0, f"SQLite vectorized_at was updated despite chroma failure: {update_calls}"

    def test_chroma_success_updates_sqlite(self):
        """When chroma write succeeds, SQLite timestamp should be updated."""
        mock_inst, mock_conn, mock_cursor = _run_rebuild(
            phase="messages",
            file_vec=False,
            messages=[
                {
                    "id": 1,
                    "chat_id": 1,
                    "role": "user",
                    "content": "hello",
                    "created_at": "",
                    "title": "Test",
                    "source": "claude",
                }
            ],
        )
        # SQLite UPDATE for vectorized_at SHOULD have been called
        update_calls = [
            c for c in mock_cursor.execute.call_args_list if len(c[0]) > 0 and "vectorized_at" in str(c[0][0])
        ]
        assert len(update_calls) > 0, "SQLite vectorized_at was not updated after chroma success"


# ---------------------------------------------------------------------------
# Progress output (TDD cycle 4)
# ---------------------------------------------------------------------------


class TestProgressOutput:
    """All phases should produce progress output."""

    def test_preflight_reports_counts(self, capsys):
        """Pre-flight should report what will be processed."""
        mock_vs_cls, mock_inst = _mock_store()
        mock_conn, mock_cursor = _make_mock_conn(
            files=[{"id": i, "file_path": f"/f{i}"} for i in range(3)],
            messages=[
                {"id": i, "chat_id": 1, "role": "user", "content": "x", "created_at": "", "title": "t", "source": "c"}
                for i in range(5)
            ],
            chats=[
                {"id": i, "title": "t", "source": "c", "created_at": "", "message_count": 1}
                for i in range(2)
            ],
        )
        mock_chroma_path = MagicMock()
        mock_chroma_path.exists.return_value = False

        _output = StringIO()  # noqa: F841
        with (
            patch("footprinter.ingest.vector_ops.sqlite3") as mock_sqlite,
            patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
            patch("footprinter.source_registry.get_config", return_value={"indexing": {"max_file_size_mb": 10}}),
            patch("footprinter.paths.get_chroma_path", return_value=mock_chroma_path),
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
            patch("rich.console.Console.print") as mock_print,
        ):
            mock_sqlite.connect.return_value = mock_conn

            from footprinter.ingest.vector_ops import _rebuild_vectors

            _rebuild_vectors(quiet=False, source="all")

        # Check that pre-flight summary was printed
        printed = [str(c) for c in mock_print.call_args_list]
        preflight_lines = [p for p in printed if "Will vectorize" in p or "Will process" in p]
        assert len(preflight_lines) > 0, f"Pre-flight summary not printed. Printed: {printed}"


# ---------------------------------------------------------------------------
# Pre-flight validation (TDD cycle 5)
# ---------------------------------------------------------------------------


class TestPreflightValidation:
    """Pre-flight checks should catch issues before destructive operations."""

    def test_preflight_runs_before_chroma_delete(self):
        """Pre-flight (DB open + validation) must happen before shutil.rmtree."""
        call_order = []

        mock_vs_cls, mock_inst = _mock_store()
        mock_conn, mock_cursor = _make_mock_conn()
        mock_chroma_path = MagicMock()
        mock_chroma_path.exists.return_value = True

        original_connect = MagicMock(return_value=mock_conn)

        def track_connect(*args, **kwargs):
            call_order.append("db_connect")
            return original_connect(*args, **kwargs)

        def track_rmtree(*args, **kwargs):
            call_order.append("rmtree")

        with (
            patch("footprinter.ingest.vector_ops.sqlite3") as mock_sqlite,
            patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
            patch("footprinter.source_registry.get_config", return_value={"indexing": {"max_file_size_mb": 10}}),
            patch("footprinter.paths.get_chroma_path", return_value=mock_chroma_path),
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
            patch("shutil.rmtree", side_effect=track_rmtree),
        ):
            mock_sqlite.connect.side_effect = track_connect

            from footprinter.ingest.vector_ops import _rebuild_vectors

            _rebuild_vectors(quiet=True, source="all", mode="full")

        assert "db_connect" in call_order, "DB was never connected"
        assert "rmtree" in call_order, "Chroma was never deleted"
        assert call_order.index("db_connect") < call_order.index("rmtree"), (
            f"DB connect must happen before rmtree. Order: {call_order}"
        )


# ---------------------------------------------------------------------------
# VectorStore.check_integrity() (TDD cycle 6)
# ---------------------------------------------------------------------------


class TestCheckIntegrity:
    """VectorStore.check_integrity() should detect corruption."""

    def test_healthy_returns_ok(self):
        """Healthy collections should return ok status."""
        mock_files = MagicMock()
        mock_files.count.return_value = 100
        mock_files.query.return_value = {"ids": [[]], "documents": [[]], "distances": [[]]}

        mock_chats = MagicMock()
        mock_chats.count.return_value = 50
        mock_chats.query.return_value = {"ids": [[]], "documents": [[]], "distances": [[]]}

        store = MagicMock()
        store._files = mock_files
        store._chats = mock_chats
        store._embedding_dim = 384

        from footprinter.semantic.vector_store import VectorStore

        result = VectorStore.check_integrity(store)

        assert result["status"] == "ok"
        assert result["files"] == 100
        assert result["chats"] == 50

    def test_corrupted_count_returns_corrupted(self):
        """FTS5 corruption in count() should return corrupted status."""
        mock_files = MagicMock()
        mock_files.count.side_effect = Exception("database disk image is malformed")

        store = MagicMock()
        store._files = mock_files
        store._embedding_dim = 384

        from footprinter.semantic.vector_store import VectorStore

        result = VectorStore.check_integrity(store)

        assert result["status"] == "corrupted"
        assert "malformed" in result["error"]

    def test_corrupted_query_returns_corrupted(self):
        """FTS5 corruption in query() should return corrupted status."""
        mock_files = MagicMock()
        mock_files.count.return_value = 10
        mock_files.query.side_effect = Exception("malformed inverted index for FTS5 table")

        mock_chats = MagicMock()
        mock_chats.count.return_value = 0

        store = MagicMock()
        store._files = mock_files
        store._chats = mock_chats
        store._embedding_dim = 384

        from footprinter.semantic.vector_store import VectorStore

        result = VectorStore.check_integrity(store)

        assert result["status"] == "corrupted"
        assert "fts5" in result["error"].lower()

    def test_empty_returns_empty(self):
        """Empty collections should return empty status."""
        mock_files = MagicMock()
        mock_files.count.return_value = 0

        mock_chats = MagicMock()
        mock_chats.count.return_value = 0

        store = MagicMock()
        store._files = mock_files
        store._chats = mock_chats
        store._embedding_dim = 384

        from footprinter.semantic.vector_store import VectorStore

        result = VectorStore.check_integrity(store)

        assert result["status"] == "empty"
        assert result["files"] == 0
        assert result["chats"] == 0

    def test_non_corruption_error_reraises(self):
        """Non-corruption errors should propagate."""
        import pytest

        mock_files = MagicMock()
        mock_files.count.side_effect = ConnectionError("network failure")

        store = MagicMock()
        store._files = mock_files
        store._embedding_dim = 384

        from footprinter.semantic.vector_store import VectorStore

        with pytest.raises(ConnectionError):
            VectorStore.check_integrity(store)


# ---------------------------------------------------------------------------
# CLI --phase argument (TDD cycle 7)
# ---------------------------------------------------------------------------


class TestCliPhaseArgument:
    """--phase should be wired through to _rebuild_vectors."""

    def test_phase_argument_parsed(self):
        """fp doctor semantic --phase chat_info should pass phase."""
        with patch("footprinter.ingest.vector_ops._rebuild_vectors") as mock_rebuild:
            from conftest import run_fp

            run_fp("doctor", "semantic", "--phase", "chat_info")

        mock_rebuild.assert_called_once()
        _, kwargs = mock_rebuild.call_args
        assert kwargs.get("phase") == "chat_info"

    def test_phase_default_is_none(self):
        """Without --phase, phase should be None."""
        with patch("footprinter.ingest.vector_ops._rebuild_vectors") as mock_rebuild:
            from conftest import run_fp

            run_fp("doctor", "semantic")

        _, kwargs = mock_rebuild.call_args
        assert kwargs.get("phase") is None


# ---------------------------------------------------------------------------
# Existing tests: source filtering and enable-flag guards
# (preserved from original test file)
# ---------------------------------------------------------------------------


def test_rebuild_vectors_source_files_only():
    """--rebuild-vectors --source files should only rebuild file vectors."""
    mock_inst, _, _ = _run_rebuild(source="files")
    # Chat methods should not have been called via the phase functions
    # (no messages or chat_info queries)


def test_rebuild_vectors_source_chats_only():
    """--rebuild-vectors --source chats should only rebuild chat vectors."""
    mock_inst, _, mock_cursor = _run_rebuild(source="chats")
    # File query should not have run (beyond count in preflight)
    executed_sqls = [str(c) for c in mock_cursor.execute.call_args_list]
    file_select_queries = [s for s in executed_sqls if "FROM files" in s and "COUNT" not in s]
    assert len(file_select_queries) == 0


def test_rebuild_vectors_both_disabled_does_not_delete_chroma():
    """When both vectorization flags are False, chroma must NOT be deleted."""
    mock_vs_cls = MagicMock()

    with (
        patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
        patch("footprinter.source_registry.get_config", return_value={}),
        patch("footprinter.paths.get_chroma_path") as mock_chroma_path,
        patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=False),
        patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=False),
        patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
    ):
        from footprinter.ingest.vector_ops import _rebuild_vectors

        _rebuild_vectors(quiet=True, source="all")

    # get_chroma_path should never be called (early return before delete)
    mock_chroma_path.assert_not_called()
    # VectorStore singleton must not be reset
    mock_vs_cls.reset_instance.assert_not_called()


def test_rebuild_vectors_file_disabled_chat_enabled_still_runs():
    """When only chat vectorization is enabled, rebuild should proceed."""
    mock_inst, mock_conn, _ = _run_rebuild(
        source="all",
        file_vec=False,
        chat_vec=True,
    )
    # DB should have been connected (rebuild proceeded past early return)
    mock_conn.commit.assert_called()


def test_rebuild_vectors_source_files_both_disabled_no_delete():
    """--source files with file vectorization disabled must not delete chroma."""
    mock_vs_cls = MagicMock()

    with (
        patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
        patch("footprinter.source_registry.get_config", return_value={}),
        patch("footprinter.paths.get_chroma_path") as mock_chroma_path,
        patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=False),
        patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=True),
        patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
    ):
        from footprinter.ingest.vector_ops import _rebuild_vectors

        _rebuild_vectors(quiet=True, source="files")

    # source=files + file_vec=False → early exit, no delete
    mock_chroma_path.assert_not_called()
    mock_vs_cls.reset_instance.assert_not_called()


# ---------------------------------------------------------------------------
# Incremental vectorization mode
# ---------------------------------------------------------------------------


class TestIncrementalMode:
    """Tests for incremental/sync/full vectorization modes."""

    def test_incremental_skips_already_vectorized_files(self):
        """Incremental mode should skip files where vectorized_at >= modified_at."""
        # Files with vectorized_at set (already done) — should be skipped
        files = [
            {"id": 1, "file_path": "/already_done.txt", "vectorized_at": "2026-04-01", "modified_at": "2026-03-01"},
        ]
        mock_inst, _, mock_cursor = _run_rebuild(
            mode="incremental",
            files=files,
            chat_vec=False,
        )
        # index_file / upsert_file should NOT have been called
        mock_inst.index_file.assert_not_called()
        mock_inst.upsert_file.assert_not_called()
        # Chroma should NOT have been deleted (no rmtree)

    def test_incremental_processes_new_files(self):
        """Incremental mode should process files where vectorized_at IS NULL."""
        mock_vs_cls, mock_inst = _mock_store()
        files = [
            {"id": 1, "file_path": "/new_file.txt", "vectorized_at": None, "modified_at": "2026-04-01"},
        ]
        mock_conn, mock_cursor = _make_mock_conn(files=files)
        mock_chroma_path = MagicMock()
        mock_chroma_path.exists.return_value = True

        with (
            patch("footprinter.ingest.vector_ops.sqlite3") as mock_sqlite,
            patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
            patch("footprinter.source_registry.get_config", return_value={"indexing": {"max_file_size_mb": 10}}),
            patch("footprinter.paths.get_chroma_path", return_value=mock_chroma_path),
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=False),
            patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
            patch("pathlib.Path.exists", return_value=True),
            patch("footprinter.ingest.full_content_extractor.FullContentExtractor") as mock_ext,
        ):
            mock_sqlite.connect.return_value = mock_conn
            mock_ext_inst = MagicMock()
            mock_ext_inst.extract_with_chunking.return_value = [
                {"content": "chunk", "chunk_index": 0, "total_chunks": 1}
            ]
            mock_ext.from_config.return_value = mock_ext_inst

            from footprinter.ingest.vector_ops import _rebuild_vectors

            _rebuild_vectors(quiet=True, source="files", mode="incremental")

        assert mock_inst.upsert_file.called, "New file was not vectorized in incremental mode"

    def test_incremental_processes_modified_files(self):
        """Incremental mode should re-vectorize files where modified_at > vectorized_at."""
        mock_vs_cls, mock_inst = _mock_store()
        files = [
            {"id": 1, "file_path": "/modified.txt", "vectorized_at": "2026-03-01", "modified_at": "2026-04-01"},
        ]
        mock_conn, mock_cursor = _make_mock_conn(files=files)
        mock_chroma_path = MagicMock()
        mock_chroma_path.exists.return_value = True

        with (
            patch("footprinter.ingest.vector_ops.sqlite3") as mock_sqlite,
            patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
            patch("footprinter.source_registry.get_config", return_value={"indexing": {"max_file_size_mb": 10}}),
            patch("footprinter.paths.get_chroma_path", return_value=mock_chroma_path),
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=False),
            patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
            patch("pathlib.Path.exists", return_value=True),
            patch("footprinter.ingest.full_content_extractor.FullContentExtractor") as mock_ext,
        ):
            mock_sqlite.connect.return_value = mock_conn
            mock_ext_inst = MagicMock()
            mock_ext_inst.extract_with_chunking.return_value = [
                {"content": "chunk", "chunk_index": 0, "total_chunks": 1}
            ]
            mock_ext.from_config.return_value = mock_ext_inst

            from footprinter.ingest.vector_ops import _rebuild_vectors

            _rebuild_vectors(quiet=True, source="files", mode="incremental")

        assert mock_inst.upsert_file.called, "Modified file was not re-vectorized in incremental mode"

    def test_incremental_removes_vectors_for_removed_files(self):
        """Incremental mode should delete vectors for files with status='removed'."""
        files = [
            {
                "id": 1,
                "file_path": "/removed.txt",
                "status": "removed",
                "vectorized_at": "2026-03-01",
                "modified_at": "2026-03-01",
            },
        ]
        mock_inst, _, mock_cursor = _run_rebuild(
            mode="incremental",
            files=files,
            chat_vec=False,
        )
        mock_inst.delete_file.assert_called_with(1)
        # vectorized_at should be cleared in DB
        update_calls = [
            c for c in mock_cursor.execute.call_args_list if len(c[0]) > 0 and "vectorized_at = NULL" in str(c[0][0])
        ]
        assert len(update_calls) > 0, "vectorized_at was not cleared for removed file"

    def test_incremental_processes_new_messages(self):
        """Incremental mode should vectorize messages where vectorized_at IS NULL."""
        messages = [
            {
                "id": 1,
                "chat_id": 1,
                "role": "user",
                "content": "hello",
                "created_at": "",
                "title": "Test",
                "source": "claude",
                "vectorized_at": None,
            },
        ]
        mock_inst, _, mock_cursor = _run_rebuild(
            mode="incremental",
            messages=messages,
            file_vec=False,
        )
        # Check that vectorized_at was updated (meaning message was processed)
        update_calls = [
            c
            for c in mock_cursor.execute.call_args_list
            if len(c[0]) > 0 and "vectorized_at" in str(c[0][0]) and "UPDATE" in str(c[0][0])
        ]
        assert len(update_calls) > 0, "New message was not vectorized in incremental mode"

    def test_incremental_handles_chats_without_metadata_vectorized_at(self):
        """Incremental mode should vectorize chats where metadata_vectorized_at IS NULL."""
        chats = [
            {
                "id": 1,
                "title": "Test Chat",
                "source": "claude",
                "created_at": "",
                "message_count": 5,
                "metadata_vectorized_at": None,
            },
        ]
        mock_inst, _, mock_cursor = _run_rebuild(
            mode="incremental",
            chats=chats,
            file_vec=False,
        )
        update_calls = [
            c
            for c in mock_cursor.execute.call_args_list
            if len(c[0]) > 0 and "metadata_vectorized_at" in str(c[0][0]) and "UPDATE" in str(c[0][0])
        ]
        assert len(update_calls) > 0, "Chat without metadata_vectorized_at was not vectorized"

    def test_incremental_messages_uses_upsert(self):
        """Incremental mode should use upsert (not add) for messages to handle interrupted retries."""
        messages = [
            {
                "id": 1,
                "chat_id": 1,
                "role": "user",
                "content": "hello",
                "created_at": "",
                "title": "Test",
                "source": "claude",
                "vectorized_at": None,
            },
        ]
        mock_inst, _, _ = _run_rebuild(
            mode="incremental",
            messages=messages,
            file_vec=False,
        )
        assert mock_inst._chats.upsert.called, "_vectorize_messages should use upsert in incremental mode"
        assert not mock_inst._chats.add.called, "_vectorize_messages should not use add in incremental mode"

    def test_full_messages_uses_add(self):
        """Full mode should use add (not upsert) for messages since chroma is wiped first."""
        messages = [
            {
                "id": 1,
                "chat_id": 1,
                "role": "user",
                "content": "hello",
                "created_at": "",
                "title": "Test",
                "source": "claude",
                "vectorized_at": None,
            },
        ]
        with patch("shutil.rmtree"):
            mock_inst, _, _ = _run_rebuild(
                mode="full",
                messages=messages,
                file_vec=False,
                chroma_exists=True,
            )
        assert mock_inst._chats.add.called, "_vectorize_messages should use add in full mode"
        assert not mock_inst._chats.upsert.called, "_vectorize_messages should not use upsert in full mode"

    def test_full_mode_deletes_chroma(self):
        """Full mode should delete chroma and process all files."""
        mock_vs_cls, mock_inst = _mock_store()
        mock_conn, mock_cursor = _make_mock_conn(
            files=[{"id": 1, "file_path": "/f.txt", "vectorized_at": "2026-04-01", "modified_at": "2026-03-01"}],
        )
        mock_chroma_path = MagicMock()
        mock_chroma_path.exists.return_value = True

        with (
            patch("footprinter.ingest.vector_ops.sqlite3") as mock_sqlite,
            patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
            patch("footprinter.source_registry.get_config", return_value={"indexing": {"max_file_size_mb": 10}}),
            patch("footprinter.paths.get_chroma_path", return_value=mock_chroma_path),
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=False),
            patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
            patch("shutil.rmtree") as mock_rmtree,
        ):
            mock_sqlite.connect.return_value = mock_conn

            from footprinter.ingest.vector_ops import _rebuild_vectors

            _rebuild_vectors(quiet=True, mode="full")

        # Chroma MUST be deleted in full mode
        mock_rmtree.assert_called_once()
        mock_vs_cls.reset_instance.assert_called()

    def test_sync_mode_logs_discrepancies(self):
        """Sync mode should log when DB and chroma counts don't match."""
        mock_inst = MagicMock()
        mock_inst.ef.return_value = [[0.1] * 384]
        mock_inst.check_integrity.return_value = {"status": "ok", "files": 0, "chats": 0}
        mock_inst.get_file_stats.return_value = {"total_chunks": 50}
        mock_inst.get_chat_stats.return_value = {"total_documents": 10}

        mock_vs_cls = MagicMock()
        mock_vs_cls.get_instance.return_value = mock_inst

        mock_conn, mock_cursor = _make_mock_conn()
        mock_chroma_path = MagicMock()
        mock_chroma_path.exists.return_value = True

        with (
            patch("footprinter.ingest.vector_ops.sqlite3") as mock_sqlite,
            patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
            patch("footprinter.source_registry.get_config", return_value={"indexing": {"max_file_size_mb": 10}}),
            patch("footprinter.paths.get_chroma_path", return_value=mock_chroma_path),
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
            patch("rich.console.Console.print") as mock_print,
        ):
            mock_sqlite.connect.return_value = mock_conn

            from footprinter.ingest.vector_ops import _rebuild_vectors

            _rebuild_vectors(quiet=False, mode="sync")

        # Should have printed sync verification output
        printed = [str(c) for c in mock_print.call_args_list]
        sync_lines = [
            p
            for p in printed
            if "sync" in p.lower() or "chroma" in p.lower() or "discrepan" in p.lower() or "verif" in p.lower()
        ]
        assert len(sync_lines) > 0, f"Sync mode did not log verification results. Printed: {printed}"

    def test_default_mode_is_incremental(self):
        """_rebuild_vectors() with no mode should behave as incremental (no chroma delete)."""
        mock_vs_cls, mock_inst = _mock_store()
        mock_conn, _ = _make_mock_conn()
        mock_chroma_path = MagicMock()
        mock_chroma_path.exists.return_value = True

        with (
            patch("footprinter.ingest.vector_ops.sqlite3") as mock_sqlite,
            patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
            patch("footprinter.source_registry.get_config", return_value={"indexing": {"max_file_size_mb": 10}}),
            patch("footprinter.paths.get_chroma_path", return_value=mock_chroma_path),
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
            patch("shutil.rmtree") as mock_rmtree,
        ):
            mock_sqlite.connect.return_value = mock_conn

            from footprinter.ingest.vector_ops import _rebuild_vectors

            _rebuild_vectors(quiet=True)

        # Default mode should NOT delete chroma
        mock_rmtree.assert_not_called()
        mock_vs_cls.reset_instance.assert_not_called()

    def test_cli_argument_parsing(self):
        """CLI doctor semantic should parse mode values correctly."""
        with patch("footprinter.ingest.vector_ops._rebuild_vectors") as mock_rebuild:
            from conftest import run_fp

            # bare doctor semantic → incremental
            run_fp("doctor", "semantic")
            _, kwargs = mock_rebuild.call_args
            assert kwargs.get("mode") == "incremental", f"Expected mode='incremental', got {kwargs.get('mode')}"

            mock_rebuild.reset_mock()

            # doctor semantic full → full
            run_fp("doctor", "semantic", "full")
            _, kwargs = mock_rebuild.call_args
            assert kwargs.get("mode") == "full", f"Expected mode='full', got {kwargs.get('mode')}"

            mock_rebuild.reset_mock()

            # doctor semantic sync → sync
            run_fp("doctor", "semantic", "sync")
            _, kwargs = mock_rebuild.call_args
            assert kwargs.get("mode") == "sync", f"Expected mode='sync', got {kwargs.get('mode')}"

    def test_incremental_progress_shows_categories(self):
        """Incremental mode should show new/modified/removed counts in output."""
        mock_vs_cls, mock_inst = _mock_store()
        # Mix of new, modified, and removed files
        files = [
            {"id": 1, "file_path": "/new.txt", "vectorized_at": None, "modified_at": "2026-04-01", "status": "listed"},
            {
                "id": 2,
                "file_path": "/modified.txt",
                "vectorized_at": "2026-03-01",
                "modified_at": "2026-04-01",
                "status": "listed",
            },
            {
                "id": 3,
                "file_path": "/removed.txt",
                "vectorized_at": "2026-03-01",
                "modified_at": "2026-03-01",
                "status": "removed",
            },
        ]
        mock_conn, mock_cursor = _make_mock_conn(files=files)
        mock_chroma_path = MagicMock()
        mock_chroma_path.exists.return_value = True

        with (
            patch("footprinter.ingest.vector_ops.sqlite3") as mock_sqlite,
            patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
            patch("footprinter.source_registry.get_config", return_value={"indexing": {"max_file_size_mb": 10}}),
            patch("footprinter.paths.get_chroma_path", return_value=mock_chroma_path),
            patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=True),
            patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=False),
            patch("footprinter.semantic.vector_store.VectorStore", mock_vs_cls),
            patch("rich.console.Console.print") as mock_print,
        ):
            mock_sqlite.connect.return_value = mock_conn

            from footprinter.ingest.vector_ops import _rebuild_vectors

            _rebuild_vectors(quiet=False, mode="incremental")

        printed = [str(c) for c in mock_print.call_args_list]
        # Should show separate counts for categories (new, modified, removed)
        has_new = any("new" in p.lower() for p in printed)
        has_modified = any("modified" in p.lower() or "updated" in p.lower() for p in printed)
        has_removed = any("removed" in p.lower() or "cleaned" in p.lower() for p in printed)
        assert has_new or has_modified or has_removed, (
            f"Incremental progress didn't show category counts. Printed: {printed}"
        )


# ---------------------------------------------------------------------------
# Cleanup removed vectors — messages and chats
# ---------------------------------------------------------------------------


class TestCleanupRemovedVectors:
    """_cleanup_removed_vectors should clean files, messages, and chats."""

    def _make_cleanup_cursor(self, files=None, messages=None, chats=None):
        """Build a mock cursor that routes fetchall() by SQL table reference."""
        files = files or []
        messages = messages or []
        chats = chats or []
        mock_cursor = MagicMock()

        def _fetchall():
            sql = mock_cursor.execute.call_args[0][0] if mock_cursor.execute.call_args else ""
            if "FROM files" in sql:
                return [_row(f) for f in files]
            if "FROM messages" in sql:
                return [_row(m) for m in messages]
            if "FROM chats" in sql:
                return [_row(c) for c in chats]
            return []

        mock_cursor.fetchall.side_effect = _fetchall
        return mock_cursor

    def test_cleanup_removed_messages(self):
        """Removed messages should have Chroma vectors deleted and DB columns cleared."""
        mock_conn = MagicMock()
        mock_cursor = self._make_cleanup_cursor(
            messages=[{"id": 10}, {"id": 20}],
        )
        mock_store = MagicMock()

        from footprinter.ingest.vector_ops import _cleanup_removed_vectors

        result = _cleanup_removed_vectors(mock_conn, mock_cursor, mock_store)

        # store.delete_message called for each removed message
        assert mock_store.delete_message.call_count == 2
        mock_store.delete_message.assert_any_call(10)
        mock_store.delete_message.assert_any_call(20)

        # DB columns cleared via UPDATE
        update_calls = [
            c
            for c in mock_cursor.execute.call_args_list
            if len(c[0]) > 0 and "UPDATE messages" in str(c[0][0]) and "vectorized_at = NULL" in str(c[0][0])
        ]
        assert len(update_calls) == 2

        # Return dict includes message count
        assert result["removed_messages"] == 2
        mock_conn.commit.assert_called()

    def test_cleanup_removed_chats(self):
        """Removed chats should have Chroma vectors deleted and DB column cleared."""
        mock_conn = MagicMock()
        mock_cursor = self._make_cleanup_cursor(
            chats=[{"id": 5}],
        )
        mock_store = MagicMock()

        from footprinter.ingest.vector_ops import _cleanup_removed_vectors

        result = _cleanup_removed_vectors(mock_conn, mock_cursor, mock_store)

        # store.delete_chat called
        mock_store.delete_chat.assert_called_once_with(5)

        # Chat DB column cleared via UPDATE
        chat_updates = [
            c
            for c in mock_cursor.execute.call_args_list
            if len(c[0]) > 0 and "UPDATE chats" in str(c[0][0]) and "metadata_vectorized_at = NULL" in str(c[0][0])
        ]
        assert len(chat_updates) == 1

        # Child messages' vectorization state also cleared (delete_chat
        # removes message chunks from Chroma — DB must stay in sync)
        msg_updates = [
            c
            for c in mock_cursor.execute.call_args_list
            if len(c[0]) > 0 and "UPDATE messages" in str(c[0][0]) and "chat_id = ?" in str(c[0][0])
        ]
        assert len(msg_updates) == 1

        # Return dict includes chat count
        assert result["removed_chats"] == 1

    def test_cleanup_removed_all_types(self):
        """All three types should be cleaned in a single call."""
        mock_conn = MagicMock()
        mock_cursor = self._make_cleanup_cursor(
            files=[{"id": 1}],
            messages=[{"id": 2}, {"id": 3}],
            chats=[{"id": 4}],
        )
        mock_store = MagicMock()

        from footprinter.ingest.vector_ops import _cleanup_removed_vectors

        result = _cleanup_removed_vectors(mock_conn, mock_cursor, mock_store)

        assert result["removed"] == 1
        assert result["removed_messages"] == 2
        assert result["removed_chats"] == 1

        mock_store.delete_file.assert_called_once_with(1)
        assert mock_store.delete_message.call_count == 2
        mock_store.delete_chat.assert_called_once_with(4)

    def test_cleanup_message_error_continues(self):
        """Error on one message should not halt cleanup of remaining messages."""
        mock_conn = MagicMock()
        mock_cursor = self._make_cleanup_cursor(
            messages=[{"id": 10}, {"id": 20}],
        )
        mock_store = MagicMock()
        mock_store.delete_message.side_effect = [RuntimeError("chroma error"), None]

        from footprinter.ingest.vector_ops import _cleanup_removed_vectors

        result = _cleanup_removed_vectors(mock_conn, mock_cursor, mock_store)

        # Both attempts made
        assert mock_store.delete_message.call_count == 2
        # Only the successful one counted
        assert result["removed_messages"] == 1


# ---------------------------------------------------------------------------
# Sync verification chunk counting
# ---------------------------------------------------------------------------


class TestSyncVerifyChunkCounting:
    """Sync verification should compare chunk counts, not row counts."""

    def test_sync_verify_counts_message_chunks(self):
        """_sync_verify should use SUM(vectorized_chunks) for messages, not COUNT(*)."""
        from footprinter.ingest.vector_ops import _sync_verify

        mock_cursor = MagicMock()

        # 2 messages, each producing 3 chunks = 6 message chunks + 1 chat_info = 7 total
        def _fetchone():
            sql = mock_cursor.execute.call_args[0][0]
            if "SUM(vectorized_chunks)" in sql and "messages" in sql:
                return [6]
            if "vectorized_chunks = 0" in sql:
                return [0]  # no stale rows
            if "COUNT" in sql and "chats" in sql:
                return [1]
            # File queries (SUM for files)
            if "SUM(vectorized_chunks)" in sql and "files" in sql:
                return [10]
            return [0]

        mock_cursor.fetchone.side_effect = _fetchone

        mock_store = MagicMock()
        mock_store.get_file_stats.return_value = {"total_chunks": 10}
        mock_store.get_chat_stats.return_value = {"total_documents": 7}

        mock_console = MagicMock()
        _sync_verify(mock_cursor, mock_store, files_enabled=False, chats_enabled=True, console=mock_console)

        # Should print the green checkmark (no discrepancy)
        printed = [str(c) for c in mock_console.print.call_args_list]
        green_lines = [p for p in printed if "\u2713" in p]
        warning_lines = [p for p in printed if "\u26a0" in p]
        assert len(green_lines) > 0, f"Expected green checkmark for matching counts. Printed: {printed}"
        assert len(warning_lines) == 0, f"Got unexpected discrepancy warning. Printed: {printed}"

    def test_sync_verify_stale_chunks_shows_note(self):
        """_sync_verify should show guidance when chunk counts are stale (post-migration)."""
        from footprinter.ingest.vector_ops import _sync_verify

        mock_cursor = MagicMock()

        def _fetchone():
            sql = mock_cursor.execute.call_args[0][0]
            if "SUM(vectorized_chunks)" in sql and "messages" in sql:
                return [0]  # stale — all zeros
            if "vectorized_chunks = 0" in sql:
                return [5]  # 5 messages with stale chunk counts
            if "COUNT" in sql and "chats" in sql:
                return [2]
            return [0]

        mock_cursor.fetchone.side_effect = _fetchone

        mock_store = MagicMock()
        mock_store.get_chat_stats.return_value = {"total_documents": 20}

        mock_console = MagicMock()
        _sync_verify(mock_cursor, mock_store, files_enabled=False, chats_enabled=True, console=mock_console)

        # Should show "missing chunk counts" note, not a false discrepancy
        printed = [str(c) for c in mock_console.print.call_args_list]
        stale_lines = [p for p in printed if "missing chunk counts" in p]
        assert len(stale_lines) > 0, f"Expected stale chunk counts note. Printed: {printed}"

    def test_sync_verify_stale_excludes_removed_messages(self):
        """_sync_verify stale detection should exclude removed messages."""
        from footprinter.ingest.vector_ops import _sync_verify

        mock_cursor = MagicMock()

        def _fetchone():
            sql = mock_cursor.execute.call_args[0][0]
            if "SUM(vectorized_chunks)" in sql and "messages" in sql:
                return [6]  # healthy chunk sum
            if "vectorized_chunks = 0" in sql:
                # If the query filters removed messages, no stale rows.
                # If it doesn't filter, 3 removed messages look stale.
                if "status = 'listed'" in sql:
                    return [0]
                return [3]
            if "COUNT" in sql and "chats" in sql:
                return [1]
            return [0]

        mock_cursor.fetchone.side_effect = _fetchone

        mock_store = MagicMock()
        mock_store.get_chat_stats.return_value = {"total_documents": 7}

        mock_console = MagicMock()
        _sync_verify(mock_cursor, mock_store, files_enabled=False, chats_enabled=True, console=mock_console)

        printed = [str(c) for c in mock_console.print.call_args_list]
        # Should see green checkmark — no stale warning
        green_lines = [p for p in printed if "✓" in p]
        stale_lines = [p for p in printed if "missing chunk counts" in p]
        assert len(green_lines) > 0, f"Expected green checkmark (removed msgs excluded). Printed: {printed}"
        assert len(stale_lines) == 0, f"Removed messages inflated stale count. Printed: {printed}"

    def test_vectorize_messages_sets_vectorized_chunks(self):
        """_vectorize_messages should set vectorized_chunks per message."""
        from footprinter.ingest.vector_ops import _vectorize_messages

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # Return one message with content long enough for 3 chunks
        msg = MagicMock()
        msg.__getitem__ = lambda self, key: {
            "id": 1,
            "chat_id": 1,
            "role": "user",
            "content": "x" * 3000,  # long enough for multiple chunks
            "created_at": "",
            "title": "Test",
            "source": "claude",
        }[key]
        mock_cursor.fetchall.return_value = [msg]

        mock_store = MagicMock()
        mock_store.ef.return_value = [[0.1] * 384] * 10  # enough embeddings

        _vectorize_messages(mock_conn, mock_cursor, mock_store, console=None, mode="full")

        # Check UPDATE SQLs include vectorized_chunks
        update_calls = [
            c
            for c in mock_cursor.execute.call_args_list
            if len(c[0]) > 0 and "UPDATE" in str(c[0][0]) and "vectorized_chunks" in str(c[0][0])
        ]
        assert len(update_calls) > 0, "vectorized_chunks not set in UPDATE. Executed SQLs: " + str(
            [str(c) for c in mock_cursor.execute.call_args_list]
        )


# ---------------------------------------------------------------------------
# Preflight vectorize exclusion
# ---------------------------------------------------------------------------


def _make_preflight_db(files=None, messages=None, chats=None):
    """Create an in-memory SQLite DB with real tables for preflight testing.

    Uses real SQLite so json_extract/COALESCE filters execute against actual data.
    """
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE files ("
        "  id INTEGER PRIMARY KEY, source TEXT, status TEXT,"
        "  path TEXT, metadata TEXT, vectorized_at TEXT, modified_at TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE messages ("
        "  id INTEGER PRIMARY KEY, chat_id INTEGER, content TEXT,"
        "  status TEXT, metadata TEXT, vectorized_at TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE chats (  id INTEGER PRIMARY KEY, status TEXT, metadata TEXT,  metadata_vectorized_at TEXT)"
    )
    for f in files or []:
        conn.execute(
            "INSERT INTO files (id, source, status, path, metadata, vectorized_at, modified_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f["id"],
                f.get("source", "local"),
                f.get("status", "listed"),
                f.get("path", f"/tmp/f{f['id']}"),
                f.get("metadata"),
                f.get("vectorized_at"),
                f.get("modified_at"),
            ),
        )
    for m in messages or []:
        conn.execute(
            "INSERT INTO messages (id, chat_id, content, status, metadata, vectorized_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                m["id"],
                m.get("chat_id", 1),
                m.get("content", "hello"),
                m.get("status", "listed"),
                m.get("metadata"),
                m.get("vectorized_at"),
            ),
        )
    for c in chats or []:
        conn.execute(
            "INSERT INTO chats (id, status, metadata, metadata_vectorized_at) VALUES (?, ?, ?, ?)",
            (c["id"], c.get("status", "listed"), c.get("metadata"), c.get("metadata_vectorized_at")),
        )
    conn.commit()
    return conn


class TestPreflightVectorizeExclusion:
    """Preflight counts must respect metadata.vectorize exclusion flag."""

    def test_preflight_excludes_vectorize_flagged_files(self):
        """Files with metadata.vectorize=0 should not be counted in preflight."""
        conn = _make_preflight_db(
            files=[
                {"id": 1, "metadata": None},  # included (no flag)
                {"id": 2, "metadata": '{"vectorize": 1}'},  # included (explicit 1)
                {"id": 3, "metadata": '{"vectorize": 0}'},  # excluded
            ]
        )
        cursor = conn.cursor()
        from footprinter.ingest.vector_ops import _preflight_check

        counts = _preflight_check(conn, cursor, files_enabled=True, chats_enabled=False, console=None, mode="full")
        assert counts["files"] == 2, f"Expected 2 files, got {counts['files']}"

    def test_preflight_excludes_vectorize_flagged_messages(self):
        """Messages with metadata.vectorize=0 should not be counted in preflight."""
        conn = _make_preflight_db(
            messages=[
                {"id": 1, "content": "hi", "metadata": None},
                {"id": 2, "content": "hey", "metadata": '{"vectorize": 1}'},
                {"id": 3, "content": "yo", "metadata": '{"vectorize": 0}'},
                {"id": 4, "content": "sup", "metadata": None},
            ]
        )
        cursor = conn.cursor()
        from footprinter.ingest.vector_ops import _preflight_check

        counts = _preflight_check(conn, cursor, files_enabled=False, chats_enabled=True, console=None, mode="full")
        assert counts["messages"] == 3, f"Expected 3 messages, got {counts['messages']}"

    def test_preflight_excludes_vectorize_flagged_chats(self):
        """Chats with metadata.vectorize=0 should not be counted in preflight."""
        conn = _make_preflight_db(
            chats=[
                {"id": 1, "metadata": None},
                {"id": 2, "metadata": '{"vectorize": 0}'},
                {"id": 3, "metadata": '{"other": "field"}'},
            ]
        )
        cursor = conn.cursor()
        from footprinter.ingest.vector_ops import _preflight_check

        counts = _preflight_check(conn, cursor, files_enabled=False, chats_enabled=True, console=None, mode="full")
        assert counts["chats"] == 2, f"Expected 2 chats, got {counts['chats']}"

    def test_preflight_excludes_vectorize_flagged_incremental(self):
        """Incremental mode should also exclude vectorize-flagged files."""
        conn = _make_preflight_db(
            files=[
                {"id": 1, "metadata": None, "vectorized_at": None},
                {"id": 2, "metadata": '{"vectorize": 0}', "vectorized_at": None},
                {"id": 3, "metadata": None, "vectorized_at": None},
            ]
        )
        cursor = conn.cursor()
        from footprinter.ingest.vector_ops import _preflight_check

        counts = _preflight_check(
            conn, cursor, files_enabled=True, chats_enabled=False, console=None, mode="incremental"
        )
        assert counts["files"] == 2, f"Expected 2 files in incremental, got {counts['files']}"


def _run_rebuild_with_real_path(
    tmp_path,
    mode="full",
    phase=None,
    quiet=True,
    file_vec=True,
    chat_vec=True,
):
    """Run _rebuild_vectors using a real tmp_path for chroma so stamp files are observable."""
    _, mock_inst = _mock_store()
    mock_cls = MagicMock()
    mock_cls.get_instance.return_value = mock_inst
    mock_cls.reset_instance = MagicMock()
    mock_cls._REBUILD_STAMP = ".rebuild_stamp"

    mock_conn, mock_cursor = _make_mock_conn()

    chroma_path = tmp_path / "chroma"
    chroma_path.mkdir(exist_ok=True)

    with (
        patch("footprinter.ingest.vector_ops.sqlite3") as mock_sqlite,
        patch("footprinter.ingest.vector_ops.get_db_path", return_value="/tmp/test.db"),
        patch("footprinter.source_registry.get_config", return_value={"indexing": {"max_file_size_mb": 10}}),
        patch("footprinter.paths.get_chroma_path", return_value=chroma_path),
        patch("footprinter.semantic.vector_store._file_vectorization_enabled", return_value=file_vec),
        patch("footprinter.semantic.vector_store._chat_vectorization_enabled", return_value=chat_vec),
        patch("footprinter.semantic.vector_store.VectorStore", mock_cls),
        patch("shutil.rmtree"),
    ):
        mock_sqlite.connect.return_value = mock_conn

        from footprinter.ingest.vector_ops import _rebuild_vectors

        kwargs = dict(quiet=quiet, source="all", phase=phase, mode=mode)
        _rebuild_vectors(**kwargs)

    return chroma_path


class TestRebuildStampWrite:
    def test_full_rebuild_writes_stamp_file(self, tmp_path):
        chroma_path = _run_rebuild_with_real_path(tmp_path, mode="full")
        stamp = chroma_path / ".rebuild_stamp"
        assert stamp.exists(), "Full rebuild should write .rebuild_stamp"
        content = stamp.read_text().strip()
        assert len(content) == 32, f"Expected 32-char hex UUID, got {len(content)} chars: {content!r}"
        int(content, 16)  # must be valid hex

    def test_full_rebuild_stamp_is_fresh_each_time(self, tmp_path):
        (tmp_path / "run1").mkdir()
        (tmp_path / "run2").mkdir()
        chroma1 = _run_rebuild_with_real_path(tmp_path / "run1", mode="full")
        chroma2 = _run_rebuild_with_real_path(tmp_path / "run2", mode="full")
        stamp1 = (chroma1 / ".rebuild_stamp").read_text().strip()
        stamp2 = (chroma2 / ".rebuild_stamp").read_text().strip()
        assert stamp1 != stamp2, "Each full rebuild must produce a distinct stamp"

    def test_incremental_rebuild_does_not_write_stamp(self, tmp_path):
        chroma_path = _run_rebuild_with_real_path(tmp_path, mode="incremental")
        stamp = chroma_path / ".rebuild_stamp"
        assert not stamp.exists(), "Incremental rebuild must not write .rebuild_stamp"

    def test_sync_rebuild_does_not_write_stamp(self, tmp_path):
        chroma_path = _run_rebuild_with_real_path(tmp_path, mode="sync")
        stamp = chroma_path / ".rebuild_stamp"
        assert not stamp.exists(), "Sync rebuild must not write .rebuild_stamp"

    def test_single_phase_rebuild_does_not_write_stamp(self, tmp_path):
        chroma_path = _run_rebuild_with_real_path(tmp_path, mode="full", phase="files")
        stamp = chroma_path / ".rebuild_stamp"
        assert not stamp.exists(), "Single-phase rebuild must not write .rebuild_stamp"
