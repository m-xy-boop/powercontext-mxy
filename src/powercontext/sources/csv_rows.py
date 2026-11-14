# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Offline CSV records captured with stable document and primary-key identities."""

from __future__ import annotations

import csv
import io
import json
from typing import Self
from urllib.parse import quote

from pydantic import BaseModel, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class CSVDocumentInput(BaseModel):
    document_id: str
    primary_key: str
    content: bytes


def _validate_document_id(document_id: str) -> None:
    if not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be non-empty and have no surrounding whitespace")  # noqa: TRY003


def _validate_columns(columns: tuple[str, ...], primary_key: str) -> None:
    if not columns or any(not column.strip() for column in columns):
        raise ValueError("CSV headers must be non-empty and not whitespace-only")  # noqa: TRY003
    if len(set(columns)) != len(columns):
        raise ValueError("CSV headers must be unique")  # noqa: TRY003
    if not primary_key or primary_key not in columns:
        raise ValueError("primary_key must exactly match a CSV header")  # noqa: TRY003


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Encode the original key without conflating whitespace or delimiters."""

    _validate_document_id(document_id)
    if not primary_key_value.strip():
        raise ValueError("primary_key_value must not be empty or whitespace-only")  # noqa: TRY003
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("CSV source identity must not exceed 256 characters")  # noqa: TRY003
    return identity


class CSVRowInput(BaseModel):
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_row(self) -> Self:
        _validate_columns(self.columns, self.primary_key)
        csv_row_source_id(self.document_id, self.primary_key_value)
        if len(self.values) != len(self.columns):
            raise ValueError("CSV record width must equal the header width")  # noqa: TRY003
        if self.values[self.columns.index(self.primary_key)] != self.primary_key_value:
            raise ValueError("primary_key_value must match the original row field")  # noqa: TRY003
        return self


class CSVRowSource(Source, CSVRowInput):
    """A self-contained row; reading never reopens the original document."""

    materialization: SourceMaterialization = SourceMaterialization.CAPTURED
    source_id: str

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.materialization is not SourceMaterialization.CAPTURED:
            raise ValueError("CSV rows must retain their captured content")  # noqa: TRY003
        identity = csv_row_source_id(self.document_id, self.primary_key_value)
        if self.source_id != identity or self.name != identity:
            raise ValueError("CSV source_id and name must match the row identity")  # noqa: TRY003
        return self


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Validate the entire document before exposing any rows to ingestion."""

    _validate_document_id(value.document_id)
    # newline="" preserves CR/LF inside quoted fields; utf-8-sig only strips
    # a BOM at the very beginning and otherwise decodes strict UTF-8.
    text = value.content.decode("utf-8-sig", errors="strict")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=",", quotechar='"', doublequote=True, strict=True)
    try:
        columns = tuple(next(reader, ()))
        _validate_columns(columns, value.primary_key)
        key_index = columns.index(value.primary_key)
        rows: list[CSVRowInput] = []
        seen: set[str] = set()
        for record in reader:
            # Only an empty physical record is skipped, not quoted empty fields.
            if not record:
                continue
            if len(record) != len(columns):
                raise ValueError("CSV record width must equal the header width")  # noqa: TRY003
            key = record[key_index]
            if key in seen:
                raise ValueError("CSV primary key values must be unique within a document")  # noqa: TRY003
            row = CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=key,
                columns=columns,
                values=tuple(record),
            )
            seen.add(key)
            rows.append(row)
    except csv.Error as error:
        raise ValueError(f"Invalid CSV: {error}") from error  # noqa: TRY003
    return tuple(rows)


class _CSVRowTextProjection:
    name: str = TEXT_EVIDENCE_PROJECTION_KEY.name
    version: str = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[CSVRowSource] = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type="csv-row",
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


class _CSVRowSourceDefinition:
    name: str = "csv-row"
    version: str = "1"
    input_class: type[CSVRowInput] = CSVRowInput
    source_class: type[CSVRowSource] = CSVRowSource
    projections: tuple[SourceProjection[CSVRowSource], ...] = (_CSVRowTextProjection(),)

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(name=identity, source_id=identity, **value.model_dump())

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = _CSVRowSourceDefinition()

__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
