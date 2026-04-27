"""Shared MIME type utilities for file serving and upload."""

import mimetypes
from pathlib import Path

MIME_MAP: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".json": "application/json",
    ".xml": "text/xml",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
}

_INLINE_PREFIXES = ("image/", "text/", "application/pdf")


def guess_mime_type(filename: str) -> str:
    """Return MIME type for *filename*: MIME_MAP → mimetypes stdlib → octet-stream."""
    ext = Path(filename).suffix.lower()
    if ext in MIME_MAP:
        return MIME_MAP[ext]

    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed

    return "application/octet-stream"


def is_inline_displayable(mime_type: str) -> bool:
    """Return True if browsers can render this MIME type inline (image/*, text/*, PDF)."""
    return mime_type.startswith(_INLINE_PREFIXES)
