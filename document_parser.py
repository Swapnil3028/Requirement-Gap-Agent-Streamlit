"""Extract clean text from supported requirement document formats."""

import re
import unicodedata
from pathlib import Path


class DocumentParserError(Exception):
    """Raised when a document cannot be parsed or uses an unsupported format."""


def _clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[^\S\n]+", " ", line).strip()
        if line and lines and lines[-1].endswith("-") and line[0].islower():
            lines[-1] = lines[-1][:-1] + line
        elif line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip()


def extract_text(file_path):
    """Return normalized text from a PDF, DOCX, or UTF-8 text document."""
    source = Path(file_path)
    if not source.exists():
        raise DocumentParserError(f"Document does not exist: {source}")
    if not source.is_file():
        raise DocumentParserError(f"Document path is not a file: {source}")
    if source.stat().st_size == 0:
        raise DocumentParserError(f"Document is empty: {source}")

    suffix = source.suffix.lower()
    if suffix in {".md", ".txt"}:
        try:
            return _clean_text(source.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError) as exc:
            raise DocumentParserError(f"Could not read text document '{source}': {exc}") from exc

    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError as exc:
            raise DocumentParserError(
                "DOCX support is missing. Install dependencies with: "
                "python -m pip install -r requirements.txt"
            ) from exc
        try:
            document = Document(source)
        except Exception as exc:
            raise DocumentParserError(f"Could not read DOCX document '{source}': {exc}") from exc

        blocks = [
            paragraph.text
            for paragraph in document.paragraphs
            if paragraph.text.strip()
        ]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    blocks.append(" | ".join(cells))
        return _clean_text("\n\n".join(blocks))

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise DocumentParserError(
                "PDF support is missing. Install dependencies with: "
                "python -m pip install -r requirements.txt"
            ) from exc
        try:
            reader = PdfReader(source)
            if reader.is_encrypted:
                raise DocumentParserError(
                    f"PDF document is encrypted and cannot be read: {source}"
                )
            page_text = [page.extract_text() or "" for page in reader.pages]
        except DocumentParserError:
            raise
        except Exception as exc:
            raise DocumentParserError(f"Could not read PDF document '{source}': {exc}") from exc
        return _clean_text("\n\n".join(page_text))

    raise DocumentParserError(
        f"Unsupported document format '{suffix}' for '{source}'. "
        "Supported formats are .pdf, .docx, and .txt."
    )
