"""Tests for the FileScanner preview/skip-hashing path (FPR-1723)."""

from pathlib import Path

from footprinter.ingest.file_scanner import FileScanner


def _config_for(tmp_path: Path) -> dict:
    return {
        "directories": [str(tmp_path)],
        "exclusions": {
            "always": [r"node_modules"],
            "sensitive": [],
        },
        "indexing": {},
    }


def _populate(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('hi')\n")
    (tmp_path / "b.txt").write_text("hello world\n")
    nested = tmp_path / "node_modules" / "pkg"
    nested.mkdir(parents=True)
    (nested / "index.js").write_text("module.exports = {};\n")


class TestSkipHashing:
    def test_scan_metadata_only_skips_hashing(self, tmp_path):
        _populate(tmp_path)
        scanner = FileScanner(_config_for(tmp_path))
        results = list(scanner.scan_all_directories(skip_hashing=True))

        assert results, "expected at least one file"
        for entry in results:
            assert entry["file_size"] >= 0
            assert entry["file_type"]
            assert entry.get("sha256_hash") is None
            assert entry.get("md5_hash") is None

    def test_scan_metadata_only_respects_exclusions(self, tmp_path):
        _populate(tmp_path)
        scanner = FileScanner(_config_for(tmp_path))
        results = list(scanner.scan_all_directories(skip_hashing=True))

        paths = {Path(e["file_path"]).name for e in results}
        assert "a.py" in paths
        assert "b.txt" in paths
        assert "index.js" not in paths, "node_modules must be excluded in preview too"

    def test_default_path_still_hashes(self, tmp_path):
        """Regression guard: existing callers (full ingest) keep getting hashes."""
        (tmp_path / "x.txt").write_text("data\n")
        scanner = FileScanner(_config_for(tmp_path))
        results = list(scanner.scan_all_directories())
        assert results
        assert results[0]["sha256_hash"]
        assert results[0]["md5_hash"]
