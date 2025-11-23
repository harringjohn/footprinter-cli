"""
Content extraction from various file types.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
logging.getLogger("pypdf").setLevel(logging.ERROR)

_pypdf_warned = False
_docx_warned = False


class ContentExtractor:
    """Extract text content from various file types."""

    def __init__(self, max_preview_length: int = 1000):
        self.max_preview_length = max_preview_length

    def extract(self, file_path: Path) -> Optional[str]:
        """
        Extract content from file based on type.

        Returns preview of file content (first N characters).
        """
        try:
            file_type = file_path.suffix.lower()

            if file_type in [".txt", ".md", ".py", ".js", ".json", ".yaml", ".yml"]:
                return self._extract_text(file_path)
            elif file_type == ".pdf":
                return self._extract_pdf(file_path)
            elif file_type == ".docx":
                return self._extract_docx(file_path)
            else:
                logger.debug(f"No extractor for {file_type}")
                return None

        except (
            Exception
        ) as e:  # Intentional broad catch: extraction is inherently brittle (encoding, corrupt files, library bugs)
            logger.error(f"Error extracting content from {file_path}: {e}")
            return None

    def _extract_text(self, file_path: Path) -> str:
        """Extract from plain text files."""
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(self.max_preview_length * 2)  # Read a bit more
                return content[: self.max_preview_length]
        except (
            Exception
        ) as e:  # Intentional broad catch: extraction is inherently brittle (encoding, corrupt files, library bugs)
            logger.error(f"Error reading text file {file_path}: {e}")
            return ""

    def _extract_pdf(self, file_path: Path) -> str:
        """Extract from PDF files."""
        try:
            import pypdf

            with open(file_path, "rb") as f:
                reader = pypdf.PdfReader(f)

                # Extract first few pages
                text = ""
                for page_num in range(min(3, len(reader.pages))):
                    page = reader.pages[page_num]
                    text += page.extract_text() + "\n"

                    if len(text) >= self.max_preview_length:
                        break

                return text[: self.max_preview_length]

        except ImportError:
            global _pypdf_warned
            if not _pypdf_warned:
                logger.warning("pypdf not installed, skipping PDF extraction")
                _pypdf_warned = True
            return None
        except (
            Exception
        ) as e:  # Intentional broad catch: extraction is inherently brittle (encoding, corrupt files, library bugs)
            logger.error(f"Error reading PDF {file_path}: {e}")
            return ""

    def _extract_docx(self, file_path: Path) -> str:
        """Extract from DOCX files."""
        try:
            import docx

            doc = docx.Document(file_path)

            # Extract paragraphs
            text = ""
            for para in doc.paragraphs:
                text += para.text + "\n"

                if len(text) >= self.max_preview_length:
                    break

            return text[: self.max_preview_length]

        except ImportError:
            global _docx_warned
            if not _docx_warned:
                logger.warning("python-docx not installed, skipping DOCX extraction")
                _docx_warned = True
            return None
        except (
            Exception
        ) as e:  # Intentional broad catch: extraction is inherently brittle (encoding, corrupt files, library bugs)
            logger.error(f"Error reading DOCX {file_path}: {e}")
            return ""
