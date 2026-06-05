"""
Tests for chat upload: database path resolution, subdirectory detection,
and zip security validation.

Covers:
- No bare Database() calls in dashboard upload endpoint or chat_indexer.py CLI
- _detect_source() finds conversations.json in root or one-level subdirectory
- _detect_source() raises ValueError when conversations.json is missing
- Zip path traversal rejection
- Zip bomb protection (entry count, decompressed size, compression ratio)
"""

import ast
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from footprinter.ingest.chat_indexer import ChatIndexer


class TestDatabasePathResolution:
    """Source inspection: no bare Database() calls in upload paths."""

    def _get_bare_database_calls(self, filepath: str) -> list:
        """Find bare Database() calls (no args) in a Python file via AST."""
        source = Path(filepath).read_text()
        tree = ast.parse(source)
        bare_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Match Database() with no positional or keyword args
                func = node.func
                is_database = (isinstance(func, ast.Name) and func.id == "Database") or (
                    isinstance(func, ast.Attribute) and func.attr == "Database"
                )
                if is_database and not node.args and not node.keywords:
                    bare_calls.append(node.lineno)
        return bare_calls

    def test_no_bare_database_in_dashboard(self):
        """Dashboard blueprints should not have any bare Database() calls."""
        blueprints_dir = Path(__file__).parent.parent.parent / "footprinter" / "dashboard" / "blueprints"
        all_bare = {}
        for bp_file in sorted(blueprints_dir.glob("*.py")):
            if bp_file.name == "__init__.py":
                continue
            bare = self._get_bare_database_calls(str(bp_file))
            if bare:
                all_bare[bp_file.name] = bare
        assert all_bare == {}, f"Bare Database() calls in blueprints: {all_bare}"

    def test_no_bare_database_in_chat_indexer(self):
        """chat_indexer.py should not have any bare Database() calls."""
        filepath = Path(__file__).parent.parent.parent / "footprinter" / "ingest" / "chat_indexer.py"
        bare = self._get_bare_database_calls(str(filepath))
        assert bare == [], f"Bare Database() calls at lines: {bare}"

    def test_no_dashboard_imports_in_chat_indexer(self):
        """chat_indexer.py must not import from the dashboard package (tool → app boundary)."""
        filepath = Path(__file__).parent.parent.parent / "footprinter" / "ingest" / "chat_indexer.py"
        source = filepath.read_text()
        tree = ast.parse(source)
        dashboard_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "dashboard" in node.module:
                dashboard_imports.append((node.lineno, node.module))
        assert dashboard_imports == [], f"Reverse imports from dashboard in chat_indexer.py: {dashboard_imports}"


class TestSubdirectoryDetection:
    """_detect_source() should find conversations.json in subdirectories."""

    def _make_claude_conversations(self, path: Path):
        """Write a minimal Claude-format conversations.json."""
        data = [
            {
                "uuid": "test-uuid-1",
                "name": "Test Conversation",
                "chat_messages": [{"uuid": "msg-1", "text": "hello"}],
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-01T00:00:00Z",
            }
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def test_root_conversations_detected(self, tmp_path):
        """conversations.json at root is detected correctly."""
        self._make_claude_conversations(tmp_path / "conversations.json")
        db = MagicMock()
        manager = ChatIndexer(db)
        source, resolved_dir = manager._detect_source(tmp_path)
        assert source == "claude"
        assert resolved_dir == tmp_path

    def test_subdirectory_conversations_detected(self, tmp_path):
        """conversations.json in a subdirectory is found and resolved dir is returned."""
        subdir = tmp_path / "claude-export-2025"
        self._make_claude_conversations(subdir / "conversations.json")
        db = MagicMock()
        manager = ChatIndexer(db)
        source, resolved_dir = manager._detect_source(tmp_path)
        assert source == "claude"
        assert resolved_dir == subdir

    def test_missing_conversations_raises(self, tmp_path):
        """Missing conversations.json raises ValueError."""
        db = MagicMock()
        manager = ChatIndexer(db)
        with pytest.raises(ValueError, match="conversations.json not found"):
            manager._detect_source(tmp_path)


class TestZipValidation:
    """_validate_zip() should reject malicious or dangerous zip files."""

    def test_path_traversal_rejected(self, tmp_path):
        """Zip with path traversal entries should be rejected."""
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../../etc/passwd", "root:x:0:0")

        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        manager = ChatIndexer(MagicMock())
        with zipfile.ZipFile(zip_path, "r") as zf:
            with pytest.raises(ValueError, match="path traversal"):
                manager._validate_zip(zf, extract_dir)

    def test_absolute_path_rejected(self, tmp_path):
        """Zip with absolute path entries should be rejected."""
        zip_path = tmp_path / "absolute.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("/tmp/evil.txt", "content")

        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        manager = ChatIndexer(MagicMock())
        with zipfile.ZipFile(zip_path, "r") as zf:
            with pytest.raises(ValueError, match="absolute path"):
                manager._validate_zip(zf, extract_dir)

    def test_too_many_entries_rejected(self, tmp_path):
        """Zip with too many entries should be rejected."""
        zip_path = tmp_path / "many.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for i in range(101):
                zf.writestr(f"file_{i}.txt", "x")

        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        config = {"limits": {"zip": {"max_entries": 100}}}
        manager = ChatIndexer(MagicMock())
        with zipfile.ZipFile(zip_path, "r") as zf:
            with patch("footprinter.source_registry.get_config", return_value=config):
                with pytest.raises(ValueError, match="entries"):
                    manager._validate_zip(zf, extract_dir)

    def test_decompressed_size_exceeded(self, tmp_path):
        """Zip claiming huge decompressed size should be rejected."""
        zip_path = tmp_path / "bomb.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("large.txt", "x" * 1000)

        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        config = {"limits": {"zip": {"max_decompressed_size_mb": 0}}}
        manager = ChatIndexer(MagicMock())
        with zipfile.ZipFile(zip_path, "r") as zf:
            with patch("footprinter.source_registry.get_config", return_value=config):
                with pytest.raises(ValueError, match="decompressed size"):
                    manager._validate_zip(zf, extract_dir)

    def test_compression_ratio_exceeded(self, tmp_path):
        """Zip with suspiciously high compression ratio should be rejected."""
        zip_path = tmp_path / "ratio.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("repeat.txt", "A" * 100_000)

        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        config = {"limits": {"zip": {"max_compression_ratio": 2}}}
        manager = ChatIndexer(MagicMock())
        with zipfile.ZipFile(zip_path, "r") as zf:
            with patch("footprinter.source_registry.get_config", return_value=config):
                with pytest.raises(ValueError, match="compression ratio"):
                    manager._validate_zip(zf, extract_dir)

    def test_valid_zip_passes(self, tmp_path):
        """A normal zip should pass all validation checks."""
        zip_path = tmp_path / "good.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("conversations.json", '[{"uuid":"1","chat_messages":[]}]')
            zf.writestr("subdir/file.txt", "content")

        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        manager = ChatIndexer(MagicMock())
        with zipfile.ZipFile(zip_path, "r") as zf:
            manager._validate_zip(zf, extract_dir)  # Should not raise


class TestZipLimitsConfig:
    """Zip security limits should be loaded from config with fallback defaults."""

    def test_zip_limits_from_config(self, tmp_path):
        """Config override for max_entries should be respected by _validate_zip."""
        zip_path = tmp_path / "many.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for i in range(60):
                zf.writestr(f"file_{i}.txt", "x")

        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        config = {"limits": {"zip": {"max_entries": 50}}}
        manager = ChatIndexer(MagicMock())
        with zipfile.ZipFile(zip_path, "r") as zf:
            with patch("footprinter.source_registry.get_config", return_value=config):
                with pytest.raises(ValueError, match="entries"):
                    manager._validate_zip(zf, extract_dir)

    def test_zip_limits_fallback_on_missing_config(self, tmp_path):
        """When config is unavailable, default limits apply and small zips pass."""
        from footprinter.source_registry import ConfigError

        zip_path = tmp_path / "small.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for i in range(5):
                zf.writestr(f"file_{i}.txt", "content")

        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        manager = ChatIndexer(MagicMock())
        with zipfile.ZipFile(zip_path, "r") as zf:
            with patch("footprinter.source_registry.get_config", side_effect=ConfigError("no config")):
                manager._validate_zip(zf, extract_dir)  # Should not raise

    def test_default_decompressed_size_is_2gb(self):
        """Default max decompressed size should be 2 GB when config has no limits section."""
        from footprinter.ingest.chat_indexer import _get_zip_limits

        config = {"directories": ["~/Work"]}
        with patch("footprinter.source_registry.get_config", return_value=config):
            max_size, _, _ = _get_zip_limits()
        assert max_size == 2 * 1024 * 1024 * 1024, f"Expected 2 GB, got {max_size}"

    def test_chatgpt_export_size_passes(self, tmp_path):
        """A ~1.04 GB zip (ChatGPT export size) should pass with 2 GB default."""
        zip_path = tmp_path / "chatgpt.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            info = zipfile.ZipInfo("conversations.json")
            info.file_size = 1_092_000_000  # ~1.04 GB
            info.compress_size = 200_000_000
            zf.writestr(info, "x" * 100)

        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        config = {}  # No limits section — uses defaults
        manager = ChatIndexer(MagicMock())
        with zipfile.ZipFile(zip_path, "r") as zf:
            with patch("footprinter.source_registry.get_config", return_value=config):
                manager._validate_zip(zf, extract_dir)  # Should not raise


class TestDatabaseDefaultPath:
    """Database() with no args resolves via footprinter.paths.get_db_path()."""

    def test_database_default_uses_get_db_path(self, monkeypatch):
        """Database() with no args should resolve to an absolute path,
        regardless of the current working directory."""
        monkeypatch.delenv("FOOTPRINTER_DB_PATH", raising=False)

        from footprinter.ingest.database import Database
        from footprinter.paths import get_db_path

        db = Database()
        try:
            db_path = Path(db.db_path)
            expected = get_db_path()
            assert db_path == expected, f"Database() resolved to {db_path}, expected {expected}"
            assert db_path.is_absolute(), f"Database path should be absolute: {db_path}"
        finally:
            db.close()

    def test_database_default_independent_of_cwd(self, tmp_path, monkeypatch):
        """Database() should resolve the same path even when CWD is /tmp."""
        monkeypatch.delenv("FOOTPRINTER_DB_PATH", raising=False)
        monkeypatch.chdir(tmp_path)

        from footprinter.ingest.database import Database
        from footprinter.paths import get_db_path

        db = Database()
        try:
            db_path = Path(db.db_path)
            expected = get_db_path()
            assert db_path == expected, f"From CWD={tmp_path}, Database() resolved to {db_path}, expected {expected}"
        finally:
            db.close()


class TestGetDbPath:
    """get_db_path() in footprinter.ingest.database resolves the database path."""

    def test_get_db_path_importable(self):
        """get_db_path can be imported from footprinter.ingest.database."""
        from footprinter.ingest.database import get_db_path

        assert callable(get_db_path)

    def test_get_db_path_returns_path(self):
        """get_db_path() returns a Path object."""
        from footprinter.ingest.database import get_db_path

        result = get_db_path()
        assert isinstance(result, Path)

    def test_get_db_path_respects_env_var(self, tmp_path, monkeypatch):
        """FOOTPRINTER_DB_PATH env var overrides the default path."""
        from footprinter.ingest.database import get_db_path

        custom = tmp_path / "custom" / "test.db"
        monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(custom))
        result = get_db_path()
        assert result == custom

    def test_get_db_path_default_fallback(self, monkeypatch):
        """Without env var, resolves to ~/.footprinter/footprinter.db."""
        from footprinter.ingest.database import get_db_path

        monkeypatch.delenv("FOOTPRINTER_DB_PATH", raising=False)
        monkeypatch.delenv("FOOTPRINTER_HOME", raising=False)
        result = get_db_path()
        assert result.name == "footprinter.db"
        assert result.parent.name == ".footprinter"

    def test_get_db_path_creates_parent_dir(self, tmp_path, monkeypatch):
        """get_db_path() creates the parent directory if it doesn't exist."""
        from footprinter.ingest.database import get_db_path

        custom = tmp_path / "nonexistent" / "subdir" / "test.db"
        monkeypatch.setenv("FOOTPRINTER_DB_PATH", str(custom))
        result = get_db_path()
        assert result.parent.exists()


class TestImportCounterAccuracy:
    """Import counters must reflect actual insertions, not attempts."""

    @patch("footprinter.ingest.chat_indexer.chats_db")
    def test_counter_excludes_failed_inserts(self, mock_chats_db, tmp_path):
        """chats_added counts only successful inserts, not attempts."""
        db = MagicMock()
        mock_chats_db.get_chat_id_by_uuid.return_value = None  # new chat each time
        call_count = 0

        def insert_chat_side_effect(conn, data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated DB error")
            return call_count

        mock_chats_db.insert_chat.side_effect = insert_chat_side_effect
        mock_chats_db.insert_message.return_value = 1
        db.conn = MagicMock()

        # Write a minimal ChatGPT conversations.json with 2 chats
        conv_data = [
            {
                "title": "Chat 1",
                "create_time": 1704067200,
                "update_time": 1704067200,
                "mapping": {
                    "msg1": {
                        "message": {
                            "id": "msg1",
                            "author": {"role": "user"},
                            "content": {"parts": ["hello"]},
                            "create_time": 1704067200,
                        }
                    }
                },
                "id": "uuid-1",
            },
            {
                "title": "Chat 2",
                "create_time": 1704153600,
                "update_time": 1704153600,
                "mapping": {
                    "msg2": {
                        "message": {
                            "id": "msg2",
                            "author": {"role": "user"},
                            "content": {"parts": ["world"]},
                            "create_time": 1704153600,
                        }
                    }
                },
                "id": "uuid-2",
            },
        ]
        conv_file = tmp_path / "conversations.json"
        conv_file.write_text(json.dumps(conv_data))

        indexer = ChatIndexer(db)
        result = indexer._import_with_dedup(tmp_path, "chatgpt")

        assert result["chats_added"] == 1, f"Expected 1 successful add, got {result['chats_added']}"
        assert result["errors"] == 1, f"Expected 1 error, got {result['errors']}"
