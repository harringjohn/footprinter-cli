"""Tests for footprinter.ingest.scan_summary.ScanSummary (FPR-1723).

Aggregates scanner output into a preview summary: counts by extension,
top-N largest files, top-N largest directories, and outliers above a
size threshold. Pure data — no I/O. Memory is bounded via a heap.
"""

from footprinter.ingest.scan_summary import ScanSummary


def _entry(path: str, size: int, ext: str | None = None) -> dict:
    """Build a minimal scanner-shaped metadata dict."""
    if ext is None:
        suffix = path.rsplit(".", 1)
        ext = "." + suffix[1] if len(suffix) == 2 else "no_extension"
    return {"file_path": path, "file_type": ext, "file_size": size}


class TestAggregation:
    def test_aggregates_counts_by_extension(self):
        s = ScanSummary()
        for p in ("a.py", "b.py", "c.py", "x.log", "y.log", "z.txt"):
            s.add(_entry(f"/r/{p}", 10))
        assert s.by_extension() == {".py": 3, ".log": 2, ".txt": 1}

    def test_no_extension_bucket(self):
        s = ScanSummary()
        s.add({"file_path": "/r/README", "file_type": "no_extension", "file_size": 5})
        assert s.by_extension() == {"no_extension": 1}

    def test_totals(self):
        s = ScanSummary()
        s.add(_entry("/r/a.py", 100))
        s.add(_entry("/r/b.py", 250))
        s.add(_entry("/r/c.log", 50))
        assert s.total_files == 3
        assert s.total_bytes == 400


class TestTopFiles:
    def test_top_n_largest_files(self):
        s = ScanSummary(top_n=5)
        for i in range(20):
            s.add(_entry(f"/r/f{i}.bin", size=(i + 1) * 1000))
        top5 = s.top_files()
        assert len(top5) == 5
        sizes = [e["file_size"] for e in top5]
        assert sizes == sorted(sizes, reverse=True)
        assert sizes[0] == 20_000
        assert sizes[-1] == 16_000

    def test_top_files_fewer_than_n(self):
        s = ScanSummary(top_n=10)
        s.add(_entry("/r/a.bin", 10))
        s.add(_entry("/r/b.bin", 20))
        assert len(s.top_files()) == 2

    def test_top_files_zero_disables_tracking(self):
        s = ScanSummary(top_n=0)
        s.add(_entry("/r/a.bin", 10))
        assert s.top_files() == []
        # Aggregate counters still work
        assert s.total_files == 1


class TestTopDirectories:
    def test_top_n_largest_directories(self):
        s = ScanSummary(top_n=3)
        # /a: 100, /b: 50, /c: 300, /d: 1
        s.add(_entry("/a/one.txt", 60))
        s.add(_entry("/a/two.txt", 40))
        s.add(_entry("/b/one.txt", 50))
        s.add(_entry("/c/big.bin", 300))
        s.add(_entry("/d/x.txt", 1))
        top3 = s.top_directories()
        assert [d for d, _ in top3] == ["/c", "/a", "/b"]
        assert dict(top3) == {"/c": 300, "/a": 100, "/b": 50}


class TestOutliers:
    def test_outliers_above_size_threshold(self):
        threshold = 10 * 1024 * 1024  # 10 MB
        s = ScanSummary(outlier_threshold_bytes=threshold)
        s.add(_entry("/r/small.txt", threshold - 1))
        s.add(_entry("/r/equal.bin", threshold))
        s.add(_entry("/r/big.bin", threshold * 2))
        outliers = s.outliers()
        paths = {e["file_path"] for e in outliers}
        assert paths == {"/r/equal.bin", "/r/big.bin"}

    def test_no_threshold_collects_no_outliers(self):
        s = ScanSummary()  # outlier_threshold_bytes defaults to 0
        s.add(_entry("/r/big.bin", 10**9))
        assert s.outliers() == []


class TestBoundedMemory:
    def test_heap_does_not_grow_with_entries(self):
        """Memory invariant: file heap is capped at top_n regardless of input."""
        s = ScanSummary(top_n=5)
        for i in range(10_000):
            s.add(_entry(f"/r/{i}.bin", size=i))
        assert len(s._file_heap) == 5
        assert s.total_files == 10_000
