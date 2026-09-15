import pytest

from sar_plimsoll.review.languages import SubmissionError, validate_submission


def check(filename="a.py", content=b"x = 1\n", language=None, max_bytes=1000, max_lines=100):
    return validate_submission(
        filename=filename,
        content=content,
        declared_language=language,
        max_bytes=max_bytes,
        max_lines=max_lines,
    )


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("a.py", "python"), ("dir/b.TSX", "tsx"), ("c.rs", "rust"), ("d.kt", "kotlin")],
)
def test_language_from_extension(filename, expected):
    assert check(filename=filename).language == expected


@pytest.mark.parametrize(
    ("kwargs", "code", "status"),
    [
        ({"content": b"   \n"}, "empty_file", 422),
        ({"content": b"x" * 1001}, "file_too_large", 413),
        ({"content": b"a\x00b"}, "binary_file", 422),
        ({"content": b"\xff\xfe\xfa"}, "not_utf8", 422),
        ({"filename": "notes.txt"}, "unsupported_language", 422),
        ({"language": "cobol"}, "unsupported_language", 422),
        ({"content": b"x=1\n" * 101}, "too_many_lines", 413),
    ],
)
def test_rejections(kwargs, code, status):
    with pytest.raises(SubmissionError) as exc:
        check(**kwargs)
    assert (exc.value.code, exc.value.status) == (code, status)


def test_declared_language_overrides_extension():
    assert check(filename="script", language="Go").language == "go"


def test_path_components_are_stripped():
    assert check(filename="../../etc/evil.py").filename == "evil.py"
