from sar_plimsoll.review.chunking import Chunk, chunk_source, pack_batches
from tests.helpers import VULNERABLE_PY

JS = """\
import fs from 'fs';
const LIMIT = 10;

function a() {
  return 1;
}

export class Thing {
  m() { return 2; }
}
"""


def _assert_covers(chunks, total):
    lines = [n for c in chunks for n in range(c.start_line, c.end_line + 1)]
    assert lines == list(range(1, total + 1))


def test_python_functions_become_chunks():
    chunks = chunk_source(VULNERABLE_PY, "python")
    kinds = [c.kind for c in chunks]
    assert kinds.count("function_definition") == 3
    _assert_covers(chunks, len(VULNERABLE_PY.splitlines()))


def test_javascript_chunks_cover_every_line():
    chunks = chunk_source(JS, "javascript")
    assert any(c.kind == "function_declaration" for c in chunks)
    _assert_covers(chunks, len(JS.splitlines()))


def test_small_file_packs_into_one_batch():
    lines = VULNERABLE_PY.splitlines()
    batches = pack_batches(chunk_source(VULNERABLE_PY, "python"), lines, max_chars=48_000)
    assert len(batches) == 1
    assert (batches[0].start_line, batches[0].end_line) == (1, len(lines))


def test_oversized_chunk_is_split_by_lines():
    lines = [f"x{i} = {'a' * 50}" for i in range(200)]
    batches = pack_batches([Chunk(1, 200, "module")], lines, max_chars=2_000)
    assert len(batches) > 1
    covered = [n for b in batches for n in range(b.start_line, b.end_line + 1)]
    assert covered == list(range(1, 201))


def test_every_supported_grammar_loads():
    from sar_plimsoll.review.chunking import parser_health

    assert set(parser_health().values()) == {"ok"}
