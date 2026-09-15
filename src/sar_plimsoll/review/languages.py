"""Submission validation. Runs before any storage write or model call."""

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

# Extension → tree-sitter grammar name. These grammars are prefetched into the container image.
EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
}
SUPPORTED_LANGUAGES: frozenset[str] = frozenset(EXTENSIONS.values())


class SubmissionError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class ValidatedSource:
    filename: str
    language: str
    text: str
    sha256: str
    line_count: int
    size_bytes: int


def validate_submission(
    *,
    filename: str,
    content: bytes,
    declared_language: str | None,
    max_bytes: int,
    max_lines: int,
) -> ValidatedSource:
    name = PurePosixPath(filename.replace("\\", "/")).name
    if not name or len(name) > 255:
        raise SubmissionError("invalid_filename", "filename is required (max 255 characters)")
    if not content.strip():
        raise SubmissionError("empty_file", "file is empty")
    if len(content) > max_bytes:
        raise SubmissionError(
            "file_too_large", f"file is {len(content)} bytes; limit is {max_bytes}", status=413
        )
    if b"\x00" in content:
        raise SubmissionError("binary_file", "binary files are not supported")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SubmissionError("not_utf8", "file must be UTF-8 encoded") from None

    language = _resolve_language(name, declared_language)
    lines = text.splitlines()
    if len(lines) > max_lines:
        raise SubmissionError(
            "too_many_lines", f"file has {len(lines)} lines; limit is {max_lines}", status=413
        )
    return ValidatedSource(
        filename=name,
        language=language,
        text=text,
        sha256=hashlib.sha256(content).hexdigest(),
        line_count=len(lines),
        size_bytes=len(content),
    )


def _resolve_language(filename: str, declared: str | None) -> str:
    if declared:
        lang = declared.strip().lower()
        if lang not in SUPPORTED_LANGUAGES:
            raise SubmissionError(
                "unsupported_language",
                f"language {declared!r} is not supported; use one of {sorted(SUPPORTED_LANGUAGES)}",
            )
        return lang
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in EXTENSIONS:
        raise SubmissionError(
            "unsupported_language",
            f"cannot infer language from {suffix or 'no extension'}; pass `language` explicitly",
        )
    return EXTENSIONS[suffix]
