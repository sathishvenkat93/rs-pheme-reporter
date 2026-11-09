from __future__ import annotations

import pytest

from app.ingestion.detect import HEAD_BYTES, Format, detect

from .helpers import FIXTURES, MANIFEST


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_detector_picks_the_right_format(name: str) -> None:
    expected = MANIFEST[name]["format"]
    d = detect(name, (FIXTURES / name).read_bytes()[:HEAD_BYTES])
    assert d.format.value == expected
    if expected != "unknown":
        assert d.confidence >= 0.9, d


@pytest.mark.parametrize(
    ("name", "head", "fmt"),
    [
        ("x.tsv", b"a\tb\tc\n1\t2\t3\n4\t5\t6\n", Format.TSV),
        ("x.csv", b"a;b;c\n1;2;3\n4;5;6\n", Format.CSV),
        ("x.json", b'{"records": [{"a": 1}]}', Format.JSON),
        ("x.jsonl", b'{"a": 1}\n{"a": 2}\n', Format.NDJSON),
        (
            "x.xml",
            b'<?xml version="1.0"?>\n<!-- c -->\n<testsuite name="s"></testsuite>',
            Format.JUNIT_XML,
        ),
        ("x.xml", b"<rss></rss>", Format.UNKNOWN),
        ("x.bin", b"", Format.UNKNOWN),
    ],
)
def test_detector_edge_cases(name: str, head: bytes, fmt: Format) -> None:
    assert detect(name, head).format is fmt
