"""Split a file into function/class-level chunks with tree-sitter, then pack chunks into batches.

Chunks drive rule retrieval (one embedding per function). Batches drive model calls: packing
chunks into as few calls as fit keeps the fixed prompt overhead (system prompt + rules) from being
paid once per function.
"""

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

_DEFINITION = re.compile(
    r"^(decorated_definition|export_statement|"
    r".*(function|method|class|struct|interface|enum|trait|impl|module|object|namespace|record)"
    r".*(definition|declaration|item|specifier)?)$"
)


@dataclass(frozen=True)
class Chunk:
    start_line: int  # 1-based, inclusive
    end_line: int
    kind: str

    def text(self, lines: list[str]) -> str:
        return "\n".join(lines[self.start_line - 1 : self.end_line])


@dataclass(frozen=True)
class Batch:
    start_line: int
    end_line: int
    chunks: tuple[Chunk, ...]


def chunk_source(text: str, language: str) -> list[Chunk]:
    lines = text.splitlines()
    if not lines:
        return []
    try:
        import tree_sitter_language_pack as tslp  # native grammars: loaded only by the worker

        tree = tslp.get_parser(language).parse(text.encode("utf-8"))
    except Exception as exc:
        log.warning(
            "tree-sitter parse failed; using whole-file chunk",
            extra={"lang": language, "error": f"{type(exc).__name__}: {str(exc)[:300]}"},
        )
        return [Chunk(1, len(lines), "file")]

    chunks: list[Chunk] = []
    gap_start: int | None = None
    for node in tree.root_node.children:
        start, end = node.start_point[0] + 1, node.end_point[0] + 1
        if _DEFINITION.match(node.type):
            if gap_start is not None and gap_start < start:
                chunks.append(Chunk(gap_start, start - 1, "module"))
            gap_start = None
            chunks.append(Chunk(start, end, node.type))
        elif gap_start is None:
            gap_start = start
    if gap_start is not None:
        chunks.append(Chunk(gap_start, len(lines), "module"))

    return _cover(chunks, len(lines))


def _cover(chunks: list[Chunk], total_lines: int) -> list[Chunk]:
    """Guarantee chunks are ordered, non-overlapping and cover every line exactly once."""
    covered: list[Chunk] = []
    cursor = 1
    for c in sorted(chunks, key=lambda c: c.start_line):
        start = max(c.start_line, cursor)
        if start > c.end_line:
            continue
        if start > cursor:
            covered.append(Chunk(cursor, start - 1, "module"))
        covered.append(Chunk(start, c.end_line, c.kind))
        cursor = c.end_line + 1
    if cursor <= total_lines:
        covered.append(Chunk(cursor, total_lines, "module"))
    return covered


def parser_health() -> dict[str, str]:
    """Parse a one-line snippet with every supported grammar; used by the worker's readiness check."""
    from sar_plimsoll.review.languages import SUPPORTED_LANGUAGES

    results: dict[str, str] = {}
    for language in sorted(SUPPORTED_LANGUAGES):
        try:
            import tree_sitter_language_pack as tslp

            tslp.get_parser(language).parse(b"x")
            results[language] = "ok"
        except Exception as exc:
            results[language] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return results


def pack_batches(chunks: list[Chunk], lines: list[str], max_chars: int) -> list[Batch]:
    batches: list[Batch] = []
    current: list[Chunk] = []
    size = 0
    for chunk in _split_oversized(chunks, lines, max_chars):
        chunk_size = len(chunk.text(lines)) + (chunk.end_line - chunk.start_line + 1) * 8
        if current and size + chunk_size > max_chars:
            batches.append(Batch(current[0].start_line, current[-1].end_line, tuple(current)))
            current, size = [], 0
        current.append(chunk)
        size += chunk_size
    if current:
        batches.append(Batch(current[0].start_line, current[-1].end_line, tuple(current)))
    return batches


def _split_oversized(chunks: list[Chunk], lines: list[str], max_chars: int) -> list[Chunk]:
    out: list[Chunk] = []
    for chunk in chunks:
        start, size = chunk.start_line, 0
        for n in range(chunk.start_line, chunk.end_line + 1):
            size += len(lines[n - 1]) + 9
            if size > max_chars and n > start:
                out.append(Chunk(start, n - 1, chunk.kind))
                start, size = n, len(lines[n - 1]) + 9
        out.append(Chunk(start, chunk.end_line, chunk.kind))
    return out
