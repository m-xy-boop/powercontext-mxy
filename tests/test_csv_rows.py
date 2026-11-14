"""CSV contract boundaries beyond the published airport workload."""

import asyncio
import json

import pytest

from powercontext.sources import SourceDefinitionRegistry, SourceMaterialization, TextEvidence
from powercontext.sources.csv_rows import (
    CSV_ROW_SOURCE_DEFINITION,
    CSVDocumentInput,
    csv_row_source_id,
    parse_csv_document,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


def parse(content: bytes, *, primary_key: str = "id"):
    return parse_csv_document(CSVDocumentInput(document_id="机场:数据/一", primary_key=primary_key, content=content))


def test_unicode_spaces_and_string_fields_survive_source_json_round_trip():
    rows = parse('" id ",备注,零,空,真假\n" 键:一/二% ","中文\n第二行",000,,false\n'.encode(), primary_key=" id ")
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(rows[0]))
    restored = type(source).model_validate_json(source.model_dump_json())
    assert restored.materialization is SourceMaterialization.CAPTURED
    assert asyncio.run(registry.read(restored)) == rows[0]
    evidence = TextEvidence.model_validate(registry.project(restored, TEXT_EVIDENCE_PROJECTION_KEY))
    assert evidence.content == '{" id ":" 键:一/二% ","备注":"中文\\n第二行","零":"000","空":"","真假":"false"}'
    assert list(json.loads(evidence.content)) == list(rows[0].columns)
    assert evidence.metadata["row"] == json.loads(evidence.content)


@pytest.mark.parametrize(
    "content",
    [
        b"\n",
        b'""\n',
        b"\xef\xbb\xbf",
        b"id,name\nvalid,first\n ,bad\n",
        b"id,name\nvalid,first\nnext,too,many\n",
        b'id,name\nvalid,first\nnext,"bad"suffix\n',
        b"id,name\nvalid,first\nnext,\xc0\xaf\n",
        b'id\n""\n',
        b"id,name\nvalid,first\n,\n",
    ],
)
def test_invalid_document_never_exposes_earlier_valid_rows(content):
    with pytest.raises(ValueError):
        parse(content)


def test_only_empty_physical_records_are_skipped():
    rows = parse(b'id,name\r\n\r\n001,""\r\n\r\n002,"\r\n"\r\n')
    assert tuple(row.values for row in rows) == (("001", ""), ("002", "\r\n"))
    with pytest.raises(ValueError):
        parse(b"id,name\n \n")


def test_header_names_are_compared_without_trimming():
    rows = parse(b"id, id ,name\n001,002,test\n", primary_key=" id ")
    assert rows[0].primary_key_value == "002"
    with pytest.raises(ValueError):
        parse(b" id ,name\n001,test\n")


def test_identity_length_is_checked_after_percent_encoding():
    assert len(csv_row_source_id("d", "x" * 250)) == 256
    with pytest.raises(ValueError):
        csv_row_source_id("d", "x" * 251)
    with pytest.raises(ValueError):
        csv_row_source_id("d", "中" * 28)
    assert csv_row_source_id("a:b", "c") != csv_row_source_id("a", "b:c")
    assert csv_row_source_id("a%3Ab", "c") != csv_row_source_id("a:b", "c")
