"""Tests for file scanning and folder scanning.

Merged from test_file_scanner.py and test_folder_indexer_config.py.
File analyzer tests removed when file_analyzer.py was archived.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import yaml

if TYPE_CHECKING:
    from footprinter.ingest.file_scanner import FileScanner

# ═══════════════════════════════════════════════════════════════════════
# §1 — File scanner (from test_file_scanner.py)
# ═══════════════════════════════════════════════════════════════════════


class TestExclusionPatterns:
    """Test file exclusion pattern matching."""

    def test_trash_excluded(self, mock_config):
        """Test that .Trash directories are excluded."""
        patterns = mock_config["exclusions"]["always"]
        path = "/Users/test/.Trash/deleted_file.txt"

        excluded = any(re.match(p, path) for p in patterns)
        assert excluded, ".Trash should be excluded"

    def test_git_excluded(self, mock_config):
        """Test that .git directories are excluded."""
        patterns = mock_config["exclusions"]["always"]
        path = "/Users/test/project/.git/objects/pack"

        excluded = any(re.match(p, path) for p in patterns)
        assert excluded, ".git should be excluded"

    def test_node_modules_excluded(self, mock_config):
        """Test that node_modules directories are excluded."""
        patterns = mock_config["exclusions"]["always"]
        path = "/Users/test/project/node_modules/lodash/index.js"

        excluded = any(re.match(p, path) for p in patterns)
        assert excluded, "node_modules should be excluded"

    def test_pycache_excluded(self, mock_config):
        """Test that __pycache__ directories are excluded."""
        patterns = mock_config["exclusions"]["always"]
        path = "/Users/test/project/__pycache__/module.cpython-311.pyc"

        excluded = any(re.match(p, path) for p in patterns)
        assert excluded, "__pycache__ should be excluded"

    def test_normal_file_not_excluded(self, mock_config):
        """Test that normal files are not excluded."""
        patterns = mock_config["exclusions"]["always"]
        path = "/Users/test/project/src/main.py"

        excluded = any(re.match(p, path) for p in patterns)
        assert not excluded, "Normal files should not be excluded"

    def test_home_dir_exclusion_macos_path(self):
        """^~/Downloads/.* excludes files under macOS home."""
        from footprinter.ingest.file_scanner import FileScanner

        config = {
            "directories": ["/tmp/test"],
            "exclusions": {"always": [r"^~/Downloads/.*"]},
            "indexing": {"supported_extensions": [], "max_file_size_mb": 0},
        }

        with patch("os.path.expanduser", side_effect=lambda p: p.replace("~", "/Users/testuser", 1)):
            scanner = FileScanner(config=config)

        assert scanner.should_exclude("/Users/testuser/Downloads/file.txt") is True

    def test_home_dir_exclusion_linux_path(self):
        """^~/Downloads/.* excludes files under Linux home."""
        from footprinter.ingest.file_scanner import FileScanner

        config = {
            "directories": ["/tmp/test"],
            "exclusions": {"always": [r"^~/Downloads/.*"]},
            "indexing": {"supported_extensions": [], "max_file_size_mb": 0},
        }

        with patch("os.path.expanduser", side_effect=lambda p: p.replace("~", "/home/testuser", 1)):
            scanner = FileScanner(config=config)

        assert scanner.should_exclude("/home/testuser/Downloads/file.txt") is True

    def test_home_dir_exclusion_does_not_match_other_users(self):
        """^~/.ssh/.* only excludes current user's home, not other users."""
        from footprinter.ingest.file_scanner import FileScanner

        config = {
            "directories": ["/tmp/test"],
            "exclusions": {"always": [r"^~/\.ssh/.*"]},
            "indexing": {"supported_extensions": [], "max_file_size_mb": 0},
        }

        with patch("os.path.expanduser", side_effect=lambda p: p.replace("~", "/home/alice", 1)):
            scanner = FileScanner(config=config)

        assert scanner.should_exclude("/home/bob/.ssh/id_rsa") is False

    @pytest.mark.parametrize(
        "sensitive_dir,subpath",
        [
            (".ssh", "id_rsa"),
            (".claude", "settings.json"),
            ("Downloads", "report.pdf"),
            (".local", "share/data"),
        ],
    )
    @pytest.mark.parametrize("home_prefix", ["/Users/testuser", "/home/testuser"])
    def test_sensitive_dirs_excluded_cross_platform(self, sensitive_dir, subpath, home_prefix):
        """Home-anchored patterns exclude sensitive dirs on any platform."""
        from footprinter.ingest.file_scanner import FileScanner

        config = {
            "directories": ["/tmp/test"],
            "exclusions": {
                "always": [
                    r"^~/\.ssh/.*",
                    r"^~/\.claude/.*",
                    r"^~/Downloads/.*",
                    r"^~/\.local/.*",
                ],
            },
            "indexing": {"supported_extensions": [], "max_file_size_mb": 0},
        }

        with patch("os.path.expanduser", side_effect=lambda p: p.replace("~", home_prefix, 1)):
            scanner = FileScanner(config=config)

        file_path = f"{home_prefix}/{sensitive_dir}/{subpath}"
        assert scanner.should_exclude(file_path) is True

    def test_client_hidden_config_key_is_ignored(self):
        """client_hidden patterns in config must not cause exclusions (v3 regression guard)."""
        from footprinter.ingest.file_scanner import FileScanner

        config = {
            "directories": ["/tmp/test"],
            "exclusions": {
                "always": [],
                "client_hidden": [
                    r".*/\.[^/]+/.*",  # Hidden directories
                    r".*/\.[^/]+$",  # Hidden files
                ],
            },
            "indexing": {"supported_extensions": [], "max_file_size_mb": 0},
        }
        scanner = FileScanner(config=config)

        # Hidden file must NOT be excluded
        assert scanner.should_exclude("/Users/test/Work/clients/.env") is False
        # Hidden directory must NOT be excluded
        assert scanner.should_exclude("/Users/test/Work/clients/.husky/pre-commit") is False
        # Scanner must not have the dead attribute
        assert not hasattr(scanner, "client_hidden_exclusions")

    def test_scanner_has_no_classification_attributes(self):
        """FileScanner must not expose folder classification attributes or methods."""
        from footprinter.ingest.file_scanner import FileScanner

        config = {
            "directories": ["/tmp/test"],
            "exclusions": {"always": []},
            "indexing": {"supported_extensions": [], "max_file_size_mb": 0},
        }
        scanner = FileScanner(config=config)

        assert not hasattr(scanner, "folder_classifications")
        assert not hasattr(scanner, "get_folder_classification")

    def test_non_home_patterns_unaffected(self):
        """Patterns without ~ still work unchanged."""
        from footprinter.ingest.file_scanner import FileScanner

        config = {
            "directories": ["/tmp/test"],
            "exclusions": {
                "always": [r".*/node_modules/.*", r"^~/Downloads/.*"],
            },
            "indexing": {"supported_extensions": [], "max_file_size_mb": 0},
        }

        with patch("os.path.expanduser", side_effect=lambda p: p.replace("~", "/home/testuser", 1)):
            scanner = FileScanner(config=config)

        assert scanner.should_exclude("/any/path/node_modules/lodash/index.js") is True


class TestDotFolderExclusionPatterns:
    """Dot-folders that should never enter the DB (FPR-1797).

    Tests load the real bundled config to verify shipped patterns.
    """

    @pytest.fixture(params=["/Users/testuser", "/home/testuser"])
    def bundled_scanner(self, request) -> "FileScanner":
        from footprinter.ingest.file_scanner import FileScanner

        bundled_config = Path(__file__).parent.parent.parent / "footprinter" / "bundled" / "config.example.yaml"
        config = yaml.safe_load(bundled_config.read_text())

        with patch("os.path.expanduser", side_effect=lambda p: p.replace("~", request.param, 1)):
            scanner = FileScanner(config=config)
            scanner._test_home = request.param
            yield scanner

    @pytest.mark.parametrize(
        "dot_folder,subpath",
        [
            (".next", "server/pages/index.js"),
            (".vscode", "settings.json"),
            (".vscode", "extensions/ms-python/package.json"),
            (".husky", "pre-commit"),
            (".astro", "content-cache.json"),
            (".githooks", "pre-push"),
            (".aesthetic", "cache.json"),
            (".users", "admin.json"),
        ],
    )
    def test_dot_folder_excluded_by_bundled_config(self, bundled_scanner, dot_folder, subpath):
        """Files under noise dot-folders are rejected by should_exclude()."""
        path = f"{bundled_scanner._test_home}/Work/project/{dot_folder}/{subpath}"
        assert bundled_scanner.should_exclude(path) is True

    def test_claude_dir_not_excluded_by_scanner(self, bundled_scanner):
        """Project-level .claude/ must NOT be excluded (gets listed status instead)."""
        path = f"{bundled_scanner._test_home}/Work/project/.claude/CLAUDE.md"
        assert bundled_scanner.should_exclude(path) is False

    def test_normal_files_unaffected(self, bundled_scanner):
        """Regular project files must not be caught by dot-folder patterns."""
        path = f"{bundled_scanner._test_home}/Work/project/src/main.py"
        assert bundled_scanner.should_exclude(path) is False


class TestConfiguredDirectoryOverridesExclusions:
    """Explicitly configured directories override default `always` exclusion patterns.

    When the user explicitly configures a directory, exclusion patterns whose
    only purpose was to skip that area at home-scan time should deactivate for
    that scan. Sensitive patterns must continue to apply unconditionally.
    """

    @staticmethod
    def _build_scanner(home, always=None, sensitive=None):
        from footprinter.ingest.file_scanner import FileScanner

        config = {
            "directories": [],
            "exclusions": {
                "always": list(always or []),
                "sensitive": list(sensitive or []),
            },
            "indexing": {"supported_extensions": [], "max_file_size_mb": 0},
        }
        with patch("os.path.expanduser", side_effect=lambda p: p.replace("~", str(home), 1)):
            return FileScanner(config=config)

    def test_configured_root_under_downloads_indexes_files(self, tmp_path):
        """Files under a configured ~/Downloads/<dir> are yielded, not excluded."""
        sample_root = tmp_path / "Downloads" / "sample"
        work_dir = sample_root / "work"
        work_dir.mkdir(parents=True)
        target = work_dir / "a.txt"
        target.write_text("hello")

        scanner = self._build_scanner(tmp_path, always=[r"^~/Downloads/.*"])

        results = list(scanner.scan_directory(str(sample_root)))

        assert len(results) == 1
        assert results[0]["file_name"] == "a.txt"

    def test_unconfigured_downloads_path_still_excluded(self, tmp_path):
        """Paths under ~/Downloads/ that aren't the configured root remain excluded."""
        scanner = self._build_scanner(tmp_path, always=[r"^~/Downloads/.*"])

        unconfigured = str(tmp_path / "Downloads" / "other" / "file.txt")
        assert scanner.should_exclude(unconfigured) is True

    def test_other_always_patterns_still_apply_within_configured_root(self, tmp_path):
        """Patterns that don't match the root (e.g. __pycache__) still exclude inside it."""
        sample_root = tmp_path / "Downloads" / "sample"
        (sample_root / "work").mkdir(parents=True)
        (sample_root / "__pycache__").mkdir()
        keep = sample_root / "work" / "keep.txt"
        keep.write_text("keep")
        skip = sample_root / "__pycache__" / "x.pyc"
        skip.write_text("compiled")

        scanner = self._build_scanner(
            tmp_path,
            always=[r"^~/Downloads/.*", r".*/__pycache__/.*"],
        )

        names = sorted(r["file_name"] for r in scanner.scan_directory(str(sample_root)))
        assert names == ["keep.txt"]

    def test_sensitive_exclusions_still_apply_within_configured_root(self, tmp_path):
        """Sensitive patterns are unconditional — credentials never get indexed."""
        sample_root = tmp_path / "Downloads" / "sample"
        sample_root.mkdir(parents=True)
        (sample_root / ".ssh").mkdir()
        notes = sample_root / "notes.txt"
        notes.write_text("notes")
        secret = sample_root / ".ssh" / "id_rsa"
        secret.write_text("PRIVATE")

        scanner = self._build_scanner(
            tmp_path,
            always=[r"^~/Downloads/.*"],
            sensitive=[r".*/\.ssh/.*"],
        )

        names = sorted(r["file_name"] for r in scanner.scan_directory(str(sample_root)))
        assert names == ["notes.txt"]

    def test_should_exclude_default_behavior_unchanged(self, tmp_path):
        """Direct should_exclude(path) calls (no override) preserve current semantics."""
        scanner = self._build_scanner(tmp_path, always=[r"^~/Downloads/.*"])

        downloads_path = str(tmp_path / "Downloads" / "foo.txt")
        assert scanner.should_exclude(downloads_path) is True

    def test_symlink_target_outside_root_uses_full_exclusion_set(self, tmp_path):
        """A symlink in the configured root pointing to an excluded sibling stays excluded.

        The relaxed exclusion list applies to *content under* the configured
        root, not to wherever symlinks happen to resolve. Otherwise a symlink
        could escape the always-exclusion set.
        """
        sample_root = tmp_path / "Downloads" / "sample"
        sample_root.mkdir(parents=True)
        sibling = tmp_path / "Downloads" / "other"
        sibling.mkdir()
        target = sibling / "secret.txt"
        target.write_text("should not be indexed")

        link = sample_root / "link.txt"
        link.symlink_to(target)

        scanner = self._build_scanner(tmp_path, always=[r"^~/Downloads/.*"])

        results = list(scanner.scan_directory(str(sample_root)))
        names = [r["file_name"] for r in results]
        assert "link.txt" not in names
        assert "secret.txt" not in names

    def test_pattern_substring_match_does_not_deactivate(self, tmp_path):
        """Patterns that share a substring with the root but don't exclude its contents stay active.

        Probe-based check: a pattern is deactivated only if it would exclude
        arbitrary descendants of the scan root, not merely match the root path
        as a regex string.
        """
        sample_root = tmp_path / "Downloads" / "sample"
        sample_root.mkdir(parents=True)
        cache_dir = sample_root / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "x.pyc").write_text("compiled")
        (sample_root / "keep.txt").write_text("keep")

        # `__pycache__` shares no substring with the root, but stays active.
        # `^~/Downloads/.*` matches all descendants — gets deactivated.
        scanner = self._build_scanner(
            tmp_path,
            always=[r"^~/Downloads/.*", r".*/__pycache__/.*"],
        )

        names = sorted(r["file_name"] for r in scanner.scan_directory(str(sample_root)))
        assert names == ["keep.txt"]


class TestFileScannerIntegration:
    """Integration tests for file scanner."""

    def test_scanner_initialization(self, mock_config, sample_files):
        """Test that scanner can be initialized with config."""
        from footprinter.ingest.file_scanner import FileScanner

        # Update config to use sample_files directory
        mock_config["directories"] = [str(sample_files)]

        scanner = FileScanner(config=mock_config)

        assert scanner is not None

    def test_scanner_should_exclude(self, mock_config, sample_files):
        """Test the should_exclude method."""
        from footprinter.ingest.file_scanner import FileScanner

        mock_config["directories"] = [str(sample_files)]
        scanner = FileScanner(config=mock_config)

        # Test exclusion
        assert scanner.should_exclude("/test/.git/config") is True
        assert scanner.should_exclude("/test/node_modules/pkg/index.js") is True
        assert scanner.should_exclude("/test/src/main.py") is False


class TestConfigFiltering:
    """Tests proving supported_extensions and max_file_size_mb config options work."""

    def test_is_supported_file_filters_by_extension(self, mock_config, sample_files):
        """When supported_extensions is non-empty, only listed extensions pass."""
        from footprinter.ingest.file_scanner import FileScanner

        mock_config["indexing"]["supported_extensions"] = [".py", ".txt"]
        mock_config["directories"] = [str(sample_files)]
        scanner = FileScanner(config=mock_config)

        assert scanner.is_supported_file(Path("foo.py")) is True
        assert scanner.is_supported_file(Path("readme.txt")) is True
        assert scanner.is_supported_file(Path("photo.jpg")) is False
        assert scanner.is_supported_file(Path("data.csv")) is False

    def test_is_supported_file_allows_all_when_empty(self, mock_config, sample_files):
        """When supported_extensions is empty, all file types are allowed."""
        from footprinter.ingest.file_scanner import FileScanner

        mock_config["indexing"]["supported_extensions"] = []
        mock_config["directories"] = [str(sample_files)]
        scanner = FileScanner(config=mock_config)

        assert scanner.is_supported_file(Path("anything.xyz")) is True
        assert scanner.is_supported_file(Path("photo.jpg")) is True
        assert scanner.is_supported_file(Path("script.py")) is True

    def test_max_file_size_skips_large_files(self, mock_config, tmp_path):
        """When max_file_size_mb > 0, files exceeding the limit return None."""
        from footprinter.ingest.file_scanner import FileScanner

        mock_config["indexing"]["max_file_size_mb"] = 1  # 1 MB limit
        mock_config["directories"] = [str(tmp_path)]
        scanner = FileScanner(config=mock_config)

        # Create a file larger than 1 MB
        large_file = tmp_path / "large.bin"
        large_file.write_bytes(b"\0" * (1024 * 1024 + 1))

        # Create a small file
        small_file = tmp_path / "small.txt"
        small_file.write_text("hello")

        assert scanner.get_file_metadata(large_file) is None
        assert scanner.get_file_metadata(small_file) is not None

    def test_max_file_size_defaults_to_no_limit_when_key_missing(self):
        """When max_file_size_mb is absent from config, default is 0 (no limit)."""
        from footprinter.ingest.file_scanner import FileScanner

        config = {
            "directories": ["/tmp/test"],
            "exclusions": {"always": []},
            "indexing": {"supported_extensions": []},
        }
        scanner = FileScanner(config=config)
        assert scanner.max_file_size == 0

    def test_max_file_size_zero_means_no_limit(self, mock_config, tmp_path):
        """When max_file_size_mb is 0, no size limit is applied."""
        from footprinter.ingest.file_scanner import FileScanner

        mock_config["indexing"]["max_file_size_mb"] = 0
        mock_config["directories"] = [str(tmp_path)]
        scanner = FileScanner(config=mock_config)

        # Create a file larger than 1 MB
        large_file = tmp_path / "large.bin"
        large_file.write_bytes(b"\0" * (1024 * 1024 + 1))

        metadata = scanner.get_file_metadata(large_file)
        assert metadata is not None
        assert metadata["file_name"] == "large.bin"


class TestSymlinkExclusion:
    """Test symlink resolution in scan_directory()."""

    def test_symlink_target_excluded_when_target_matches(self, tmp_path):
        """Symlink to a file in an excluded dir is not yielded."""
        from footprinter.ingest.file_scanner import FileScanner

        # Structure: safe_dir/link.txt → excluded_dir/.secret_file
        excluded_dir = tmp_path / "excluded_dir"
        excluded_dir.mkdir()
        secret = excluded_dir / ".secret_file"
        secret.write_text("secret")

        safe_dir = tmp_path / "safe_dir"
        safe_dir.mkdir()
        (safe_dir / "link.txt").symlink_to(secret)

        config = {
            "directories": [str(safe_dir)],
            "exclusions": {"always": [r".*/excluded_dir/.*"]},
            "indexing": {"supported_extensions": [], "max_file_size_mb": 0},
        }
        scanner = FileScanner(config=config)
        results = list(scanner.scan_directory(str(safe_dir)))

        assert len(results) == 0, f"Symlink to excluded target should not be yielded, got {results}"

    def test_symlink_to_non_excluded_target_still_indexed(self, tmp_path):
        """Symlink to a normal (non-excluded) file is yielded."""
        from footprinter.ingest.file_scanner import FileScanner

        dir_a = tmp_path / "dir_a"
        dir_a.mkdir()
        real = dir_a / "real.txt"
        real.write_text("normal file")

        dir_b = tmp_path / "dir_b"
        dir_b.mkdir()
        (dir_b / "link.txt").symlink_to(real)

        config = {
            "directories": [str(dir_b)],
            "exclusions": {"always": [r".*/excluded_dir/.*"]},
            "indexing": {"supported_extensions": [], "max_file_size_mb": 0},
        }
        scanner = FileScanner(config=config)
        results = list(scanner.scan_directory(str(dir_b)))

        assert len(results) == 1

    def test_symlink_loop_detected_and_skipped(self, tmp_path):
        """Directory symlink creating a cycle doesn't hang the scan."""
        from footprinter.ingest.file_scanner import FileScanner

        dir_a = tmp_path / "dir_a"
        dir_a.mkdir()
        (dir_a / "file.txt").write_text("content")
        (dir_a / "loop").symlink_to(dir_a)  # directory cycle

        config = {
            "directories": [str(dir_a)],
            "exclusions": {},
            "indexing": {"supported_extensions": [], "max_file_size_mb": 0},
        }
        scanner = FileScanner(config=config)
        results = list(scanner.scan_directory(str(dir_a)))

        file_names = [r["file_name"] for r in results]
        assert file_names.count("file.txt") == 1

    def test_broken_symlink_skipped(self, tmp_path):
        """Broken symlinks are skipped without error."""
        from footprinter.ingest.file_scanner import FileScanner

        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        (scan_dir / "broken.txt").symlink_to(tmp_path / "nonexistent")
        (scan_dir / "real.txt").write_text("real content")

        config = {
            "directories": [str(scan_dir)],
            "exclusions": {},
            "indexing": {"supported_extensions": [], "max_file_size_mb": 0},
        }
        scanner = FileScanner(config=config)
        results = list(scanner.scan_directory(str(scan_dir)))

        file_names = [r["file_name"] for r in results]
        assert "real.txt" in file_names
        assert "broken.txt" not in file_names

    def test_file_symlink_dedup(self, tmp_path):
        """Two symlinks to the same real file yield only one result."""
        from footprinter.ingest.file_scanner import FileScanner

        scan_dir = tmp_path / "scan"
        scan_dir.mkdir()
        real = tmp_path / "real.txt"
        real.write_text("content")
        (scan_dir / "link_a.txt").symlink_to(real)
        (scan_dir / "link_b.txt").symlink_to(real)

        config = {
            "directories": [str(scan_dir)],
            "exclusions": {},
            "indexing": {"supported_extensions": [], "max_file_size_mb": 0},
        }
        scanner = FileScanner(config=config)
        results = list(scanner.scan_directory(str(scan_dir)))

        assert len(results) == 1


class TestCreatedAtLogic:
    """Test platform-aware created_at extraction in get_file_metadata()."""

    def test_created_at_uses_birthtime_when_available(self, mock_config, tmp_path):
        """created_at should use st_birthtime when the platform provides it (macOS)."""
        from footprinter.ingest.file_scanner import FileScanner

        mock_config["directories"] = [str(tmp_path)]
        scanner = FileScanner(config=mock_config)

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        # Mock stat with distinct timestamps so we can tell which one is used
        real_stat = test_file.stat()
        mock_stat = MagicMock()
        mock_stat.st_size = real_stat.st_size
        mock_stat.st_mode = real_stat.st_mode
        mock_stat.st_birthtime = 1000000000.0  # 2001-09-09 — the one we want
        mock_stat.st_ctime = 1100000000.0  # different — should NOT be used
        mock_stat.st_mtime = 1200000000.0
        mock_stat.st_atime = 1300000000.0

        with patch.object(Path, "stat", return_value=mock_stat):
            metadata = scanner.get_file_metadata(test_file)

        expected = datetime.fromtimestamp(1000000000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        assert metadata["created_at"] == expected

    def test_created_at_falls_back_to_mtime_without_birthtime(self, mock_config, tmp_path):
        """Without st_birthtime (Linux), created_at should fall back to st_mtime."""
        from footprinter.ingest.file_scanner import FileScanner

        mock_config["directories"] = [str(tmp_path)]
        scanner = FileScanner(config=mock_config)

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        # Mock stat WITHOUT st_birthtime (simulating Linux)
        real_stat = test_file.stat()
        mock_stat = MagicMock(spec=["st_size", "st_mode", "st_ctime", "st_mtime", "st_atime"])
        mock_stat.st_size = real_stat.st_size
        mock_stat.st_mode = real_stat.st_mode
        mock_stat.st_ctime = 1100000000.0  # should NOT be used
        mock_stat.st_mtime = 1200000000.0  # fallback — the one we want
        mock_stat.st_atime = 1300000000.0

        with patch.object(Path, "stat", return_value=mock_stat):
            metadata = scanner.get_file_metadata(test_file)

        expected = datetime.fromtimestamp(1200000000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        assert metadata["created_at"] == expected

    def test_created_at_never_uses_raw_ctime(self, mock_config, tmp_path):
        """created_at must never equal st_ctime when it differs from birthtime and mtime."""
        from footprinter.ingest.file_scanner import FileScanner

        mock_config["directories"] = [str(tmp_path)]
        scanner = FileScanner(config=mock_config)

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        # All three timestamps are distinct — ctime must not appear in created_at
        real_stat = test_file.stat()
        mock_stat = MagicMock()
        mock_stat.st_size = real_stat.st_size
        mock_stat.st_mode = real_stat.st_mode
        mock_stat.st_birthtime = 1000000000.0
        mock_stat.st_ctime = 1100000000.0  # unique value — must NOT be used
        mock_stat.st_mtime = 1200000000.0
        mock_stat.st_atime = 1300000000.0

        with patch.object(Path, "stat", return_value=mock_stat):
            metadata = scanner.get_file_metadata(test_file)

        ctime_iso = datetime.fromtimestamp(1100000000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        assert metadata["created_at"] != ctime_iso

    def test_created_at_falls_back_when_birthtime_is_zero(self, mock_config, tmp_path):
        """st_birthtime=0 (unsupported filesystem on Linux 3.12+) should fall back to st_mtime."""
        from footprinter.ingest.file_scanner import FileScanner

        mock_config["directories"] = [str(tmp_path)]
        scanner = FileScanner(config=mock_config)

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        # Linux 3.12+ exposes st_birthtime via statx, but unsupported
        # filesystems report 0 — treat as absent
        real_stat = test_file.stat()
        mock_stat = MagicMock()
        mock_stat.st_size = real_stat.st_size
        mock_stat.st_mode = real_stat.st_mode
        mock_stat.st_birthtime = 0.0  # unsupported filesystem
        mock_stat.st_ctime = 1100000000.0
        mock_stat.st_mtime = 1200000000.0
        mock_stat.st_atime = 1300000000.0

        with patch.object(Path, "stat", return_value=mock_stat):
            metadata = scanner.get_file_metadata(test_file)

        expected = datetime.fromtimestamp(1200000000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        assert metadata["created_at"] == expected


# ═══════════════════════════════════════════════════════════════════════
# §2 — Folder indexer config (from test_folder_indexer_config.py)
# ═══════════════════════════════════════════════════════════════════════


def test_main_reads_directories_from_config(tmp_path):
    """main() should read directories from config, not use hardcoded paths."""
    config = {
        "directories": ["/tmp/test-work", "/tmp/test-personal"],
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))

    db_path = tmp_path / "test.db"
    db_path.touch()

    mock_scanner = MagicMock()
    mock_scanner.scan_folders.return_value = []
    mock_scanner.save_folders.return_value = (0, 0, 0)
    mock_scanner.get_folder_stats.return_value = {
        "total_folders": 0,
    }

    with (
        patch("footprinter.ingest.folder_indexer.FolderIndexer", return_value=mock_scanner),
        patch("footprinter.source_registry.get_config_path", return_value=config_path),
        patch("footprinter.paths.get_db_path", return_value=db_path),
    ):
        from footprinter.ingest.folder_indexer import main

        main()

    mock_scanner.scan_folders.assert_called_once()
    call_args = mock_scanner.scan_folders.call_args[0][0]
    assert call_args == ["/tmp/test-work", "/tmp/test-personal"]


def test_main_raises_on_empty_directories(tmp_path):
    """main() should raise ValueError when no directories are configured."""
    config = {
        "directories": [],
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))

    db_path = tmp_path / "test.db"
    db_path.touch()

    with (
        patch("footprinter.source_registry.get_config_path", return_value=config_path),
        patch("footprinter.paths.get_db_path", return_value=db_path),
    ):
        from footprinter.ingest.folder_indexer import main

        with pytest.raises(ValueError, match="No directories configured"):
            main()
