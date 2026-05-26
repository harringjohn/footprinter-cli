"""Aggregator for scanner output used by ``fp ingest --preview``.

Pure data layer: consumes ``FileScanner`` metadata dicts and produces
counts by extension, top-N largest files, top-N largest directories,
and outliers above a configurable size threshold.

Memory: a bounded min-heap of size ``top_n`` is used for the largest-files
view so memory stays O(top_n + unique_dirs + outliers) instead of growing
with the total file count. Outliers are bounded by the threshold (if set);
when no threshold is configured, no outliers are tracked.
"""

from __future__ import annotations

import heapq
import itertools
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class ScanSummary:
    top_n: int = 10
    outlier_threshold_bytes: int = 0  # 0 = do not collect outliers

    _ext_counts: Counter = field(default_factory=Counter)
    _dir_bytes: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _file_heap: List[Tuple[int, int, Dict]] = field(default_factory=list)
    _outliers: List[Dict] = field(default_factory=list)
    _total_files: int = 0
    _total_bytes: int = 0
    _seq: itertools.count = field(default_factory=itertools.count)

    def add(self, entry: Dict) -> None:
        """Record one scanner metadata dict in O(log top_n)."""
        size = int(entry.get("file_size") or 0)
        ext = entry.get("file_type") or "no_extension"

        self._total_files += 1
        self._total_bytes += size
        self._ext_counts[ext] += 1
        path = entry.get("file_path")
        if path:
            self._dir_bytes[os.path.dirname(path)] += size

        if self.top_n > 0:
            # Tie-break with a monotonic sequence so the heap never compares dicts.
            item = (size, next(self._seq), entry)
            if len(self._file_heap) < self.top_n:
                heapq.heappush(self._file_heap, item)
            elif size > self._file_heap[0][0]:
                heapq.heapreplace(self._file_heap, item)

        if self.outlier_threshold_bytes > 0 and size >= self.outlier_threshold_bytes:
            self._outliers.append(entry)

    @property
    def total_files(self) -> int:
        return self._total_files

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def by_extension(self) -> Dict[str, int]:
        return dict(self._ext_counts)

    def top_files(self) -> List[Dict]:
        return [entry for _, _, entry in sorted(self._file_heap, reverse=True)]

    def top_directories(self) -> List[Tuple[str, int]]:
        n = self.top_n if self.top_n > 0 else len(self._dir_bytes)
        return sorted(self._dir_bytes.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def outliers(self) -> List[Dict]:
        return list(self._outliers)
