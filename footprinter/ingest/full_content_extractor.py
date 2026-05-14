"""
Full content extraction with chunking for semantic search.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from .content_extractors import ContentExtractor

logger = logging.getLogger(__name__)

_pypdf_warned = False
_docx_warned = False


class FullContentExtractor(ContentExtractor):
    """Extract full content from files with intelligent chunking."""

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: float = 0.15,
        max_file_size_bytes: int = 50 * 1024 * 1024,
        max_vectorize_size_bytes: int = 100 * 1024 * 1024,
        file_types: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ):
        """
        Initialize full content extractor.

        Args:
            chunk_size: Size of each chunk in characters
            chunk_overlap: Fractional overlap (0.0–1.0) between consecutive chunks.
            max_file_size_bytes: Maximum file size to read (0 = no limit)
            max_vectorize_size_bytes: Always-on cap specific to vectorization.
                Applied even when ``max_file_size_bytes == 0``. 0 disables.
            file_types: Allowlist of file extensions (e.g. [".md", ".txt"]).
                        None means all supported types are extracted.
            exclude_patterns: fnmatch patterns for file paths to skip.
        """
        super().__init__(max_preview_length=1000)  # Keep small preview for DB
        self.chunk_size = chunk_size
        if isinstance(chunk_overlap, (int, float)) and chunk_overlap >= 1.0:
            import warnings

            warnings.warn(
                "Passing chunk_overlap as absolute characters is deprecated; "
                "use a fractional value in [0.0, 1.0) instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            chunk_overlap = chunk_overlap / chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_file_size_bytes = max_file_size_bytes
        self.max_vectorize_size_bytes = max_vectorize_size_bytes
        self.file_types = [t.lower() for t in file_types] if file_types is not None else None
        self.exclude_patterns = exclude_patterns

    @classmethod
    def from_config(cls, config: dict) -> "FullContentExtractor":
        """Build a FullContentExtractor from the application config dict.

        Reads ``config["indexing"]["max_file_size_mb"]`` (default 0 = no
        limit) and ``config["vectorization"]`` (chunk_size, chunk_overlap,
        file_types, exclude_patterns).  Missing vectorization keys fall back
        to constructor defaults.  Legacy integer ``chunk_overlap`` values
        (e.g. ``150``) are auto-converted to fractional by the constructor.
        """
        max_mb = config.get("indexing", {}).get("max_file_size_mb", 0)
        vec_config = config.get("vectorization", {})
        vec_kwargs: dict = {}
        if "chunk_size" in vec_config:
            vec_kwargs["chunk_size"] = vec_config["chunk_size"]
        if "chunk_overlap" in vec_config:
            vec_kwargs["chunk_overlap"] = vec_config["chunk_overlap"]
        if "file_types" in vec_config:
            vec_kwargs["file_types"] = vec_config["file_types"]
        if "exclude_patterns" in vec_config:
            vec_kwargs["exclude_patterns"] = vec_config["exclude_patterns"]
        max_vec_mb = vec_config.get("max_vectorize_size_mb", 100)
        return cls(
            max_file_size_bytes=int(max_mb * 1024 * 1024),
            max_vectorize_size_bytes=int(max_vec_mb * 1024 * 1024),
            **vec_kwargs,
        )

    def extract_with_chunking(self, file_path: Path) -> List[Dict[str, str]]:
        """
        Extract content and split into chunks if necessary.

        Returns:
            List of chunk dictionaries with 'content', 'chunk_index', 'total_chunks'
        """
        from footprinter.semantic.chunking import chunk_content

        full_content = self._extract_full_content(file_path)

        if not full_content or len(full_content) == 0:
            return []

        tuples = chunk_content(
            full_content, chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
        )
        chunks = [
            {"content": text, "chunk_index": idx, "total_chunks": total}
            for text, idx, total in tuples
        ]

        if len(chunks) > 1:
            logger.info(f"Split {file_path.name} into {len(chunks)} chunks")

        return chunks

    def _extract_full_content(self, file_path: Path) -> Optional[str]:
        """Extract full content from file.

        Gates checked in order: file type allowlist, exclude patterns,
        file size limit. Returns None if any gate rejects the file.
        """
        # File type allowlist gate (cheap — check before stat())
        file_type = file_path.suffix.lower()
        if self.file_types is not None and file_type not in self.file_types:
            logger.debug("Skipping %s: extension %s not in file_types allowlist", file_path.name, file_type)
            return None

        # Exclude patterns gate (fnmatch against full absolute path)
        if self.exclude_patterns:
            from fnmatch import fnmatch

            path_str = str(file_path)
            if any(fnmatch(path_str, pat) for pat in self.exclude_patterns):
                logger.debug("Skipping %s: matched exclude pattern", file_path.name)
                return None

        # File size guards — vectorize cap is always-on; max_file_size_bytes is broader (0=disabled).
        try:
            file_size = file_path.stat().st_size
        except OSError:
            file_size = None  # let the read attempt surface the error

        if file_size is not None:
            if self.max_file_size_bytes > 0 and file_size > self.max_file_size_bytes:
                logger.warning(
                    f"Skipping {file_path.name}: {file_size} bytes "
                    f"exceeds content extraction limit of {self.max_file_size_bytes} bytes"
                )
                return None
            if self.max_vectorize_size_bytes > 0 and file_size > self.max_vectorize_size_bytes:
                logger.info(
                    f"Skipping vectorization of {file_path.name}: {file_size} bytes "
                    f"exceeds vectorize cap of {self.max_vectorize_size_bytes} bytes"
                )
                return None

        try:
            # Text-based files
            if file_type in [
                ".txt",
                ".md",
                ".py",
                ".js",
                ".json",
                ".yaml",
                ".yml",
                ".html",
                ".css",
                ".jsx",
                ".tsx",
            ]:
                return self._extract_full_text(file_path)

            # Documents
            elif file_type == ".pdf":
                return self._extract_full_pdf(file_path)
            elif file_type in [".docx", ".doc"]:
                return self._extract_full_docx(file_path)

            # Data files
            elif file_type == ".csv":
                return self._extract_csv_full(file_path)

            # Other text-like formats
            elif file_type in [
                ".xml",
                ".svg",
                ".rst",
                ".toml",
                ".ini",
                ".cfg",
                ".conf",
                ".sh",
                ".bash",
                ".zsh",
                ".fish",
                ".sql",
                ".graphql",
                ".proto",
                ".ts",
                ".vue",
                ".svelte",
                ".astro",
                ".java",
                ".kt",
                ".scala",
                ".go",
                ".rs",
                ".rb",
                ".php",
                ".c",
                ".h",
                ".cpp",
                ".hpp",
                ".cs",
                ".swift",
                ".m",
                ".r",
                ".jl",
                ".lua",
                ".pl",
                ".pm",
                ".tf",
                ".hcl",
                ".dockerfile",
                ".log",
                ".env",
                ".gitignore",
                ".editorconfig",
                ".tex",
                ".bib",
                ".org",
            ]:
                return self._extract_full_text(file_path)

            else:
                # Skip binary/unknown files (images, video, audio, archives, etc.)
                return None

        except Exception as e:
            logger.debug(f"Could not extract content from {file_path}: {e}")
            return None

    def _extract_full_text(self, file_path: Path) -> Optional[str]:
        """Extract full text content."""
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            logger.debug(f"Error reading text file {file_path}: {e}")
            return None

    def _extract_full_pdf(self, file_path: Path) -> Optional[str]:
        """Extract full PDF content."""
        try:
            import pypdf

            with open(file_path, "rb") as f:
                reader = pypdf.PdfReader(f)

                text = ""
                for page in reader.pages:
                    text += page.extract_text() + "\n"

                return text

        except ImportError:
            global _pypdf_warned
            if not _pypdf_warned:
                logger.warning("pypdf not installed, skipping PDF extraction")
                _pypdf_warned = True
            return None
        except Exception as e:
            logger.debug(f"Error reading PDF {file_path}: {e}")
            return None

    def _extract_full_docx(self, file_path: Path) -> Optional[str]:
        """Extract full DOCX content."""
        try:
            import docx

            doc = docx.Document(file_path)

            text = ""
            for para in doc.paragraphs:
                text += para.text + "\n"

            return text

        except ImportError:
            global _docx_warned
            if not _docx_warned:
                logger.warning("python-docx not installed, skipping DOCX extraction")
                _docx_warned = True
            return None
        except Exception as e:
            logger.debug(f"Error reading DOCX {file_path}: {e}")
            return None

    def _extract_csv_full(self, file_path: Path) -> Optional[str]:
        """Extract CSV content (headers + sample rows)."""
        try:
            import csv

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.reader(f)

                lines = []
                for i, row in enumerate(reader):
                    lines.append(",".join(row))

                    # Limit to reasonable size
                    if i >= 1000:  # First 1000 rows
                        break

                return "\n".join(lines)

        except Exception as e:
            logger.debug(f"Error reading CSV {file_path}: {e}")
            return None
