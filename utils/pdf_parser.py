"""Extract text from PDF file-like objects."""

from typing import BinaryIO

import pymupdf as fitz


class PDFParserError(Exception):
    """Raised when a PDF file-like object cannot be parsed."""


def read_pdf(file: BinaryIO) -> str:
    """Return text extracted from a readable PDF file-like object."""
    try:
        content = file.read()
    except OSError as exc:
        raise PDFParserError(f"Could not read PDF upload: {exc}") from exc
    if not isinstance(content, bytes):
        raise PDFParserError("PDF upload must provide bytes.")
    if not content:
        raise PDFParserError("PDF upload is empty.")

    try:
        with fitz.open(stream=content, filetype="pdf") as pdf:
            return "\n".join(page.get_text() for page in pdf).strip()
    except (fitz.FileDataError, RuntimeError, ValueError) as exc:
        raise PDFParserError(f"Could not extract text from PDF upload: {exc}") from exc
