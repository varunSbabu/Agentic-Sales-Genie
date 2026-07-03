"""Text extraction from uploaded KB documents.

Supports PDF (PyMuPDF/fitz), DOCX (python-docx), and TXT. Files are validated
for type + size before extraction. Returns clean unicode text — chunking and
embedding are downstream concerns.
"""

import io
from pathlib import Path
from typing import Literal

import fitz  # PyMuPDF
from docx import Document

from backend.utils.logging import logger

FileKind = Literal["pdf", "docx", "txt"]

ALLOWED_EXTENSIONS: dict[str, FileKind] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "txt",
}

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


class IngestionError(Exception):
    """Raised when a file cannot be ingested."""


def detect_file_kind(filename: str) -> FileKind:
    """Return the file kind from its extension, or raise IngestionError."""
    ext = Path(filename).suffix.lower()
    kind = ALLOWED_EXTENSIONS.get(ext)
    if kind is None:
        raise IngestionError(
            f"unsupported file type {ext!r}; accepted: {sorted(ALLOWED_EXTENSIONS)}"
        )
    return kind


def validate_size(content: bytes) -> None:
    size = len(content)
    if size == 0:
        raise IngestionError("empty file")
    if size > MAX_FILE_SIZE_BYTES:
        raise IngestionError(
            f"file too large ({size} bytes); max {MAX_FILE_SIZE_BYTES} bytes"
        )


def _extract_pdf(content: bytes) -> str:
    try:
        with fitz.open(stream=content, filetype="pdf") as doc:
            pages = [page.get_text("text") for page in doc]
    except Exception as exc:
        raise IngestionError(f"failed to parse PDF: {exc}") from exc
    return "\n\n".join(p for p in pages if p.strip())


def _extract_docx(content: bytes) -> str:
    try:
        doc = Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs]
    except Exception as exc:
        raise IngestionError(f"failed to parse DOCX: {exc}") from exc
    return "\n".join(p for p in paragraphs if p.strip())


def _extract_txt(content: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise IngestionError("could not decode text file in any common encoding")


def extract_text(filename: str, content: bytes) -> tuple[str, FileKind]:
    """Validate + extract clean text from an uploaded file.

    Returns (text, file_kind). Raises IngestionError on any failure.
    """
    validate_size(content)
    kind = detect_file_kind(filename)
    logger.info("extracting text from {} ({} bytes, kind={})", filename, len(content), kind)

    if kind == "pdf":
        text = _extract_pdf(content)
    elif kind == "docx":
        text = _extract_docx(content)
    elif kind == "txt":
        text = _extract_txt(content)
    else:  # pragma: no cover — exhaustive
        raise IngestionError(f"unhandled file kind: {kind}")

    text = text.strip()
    if not text:
        raise IngestionError("extracted text was empty — file may be scanned or corrupt")

    logger.info("extracted {} chars from {}", len(text), filename)
    return text, kind
