"""Aggregator for scanner output used by ``fp ingest --preview`` (FPR-1723).

Pure data layer: consumes ``FileScanner`` metadata dicts and produces
counts by extension, top-N largest files, top-N largest directories,
and outliers above a configurable size threshold. No I/O, no logging.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple


@dataclass
class ScanSummary:
    entries: List[Dict] = field(default_factory=list)
    _ext_counts: Counter = field(default_factory=Counter)
    _dir_bytes: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _total_bytes: int = 0

    def add(self, entry: Dict) -> None:
        """Record one scanner metadata dict."""
        self.entries.append(entry)
        ext = entry.get("file_type") or "no_extension"
        self._ext_counts[ext] += 1
        size = int(entry.get("file_size") or 0)
        self._total_bytes += size
        path = entry.get("file_path")
        if path:
            self._dir_bytes[os.path.dirname(path)] += size

    def extend(self, entries: Iterable[Dict]) -> None:
        for e in entries:
            self.add(e)

    @property
    def total_files(self) -> int:
        return len(self.entries)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def by_extension(self) -> Dict[str, int]:
        return dict(self._ext_counts)

    def top_files(self, n: int) -> List[Dict]:
        return sorted(self.entries, key=lambda e: int(e.get("file_size") or 0), reverse=True)[:n]

    def top_directories(self, n: int) -> List[Tuple[str, int]]:
        return sorted(self._dir_bytes.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def outliers(self, threshold_bytes: int) -> List[Dict]:
        return [e for e in self.entries if int(e.get("file_size") or 0) >= threshold_bytes]
