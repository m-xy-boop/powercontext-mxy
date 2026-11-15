"""Physical page preservation and retrieval through the real scoped Memory API."""

import asyncio
import hashlib
import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    parse_pdf_document,
    search_pdf_pages,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


def pdf_bytes(texts, password=None):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=500, height=800)
        if text is not None:
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
            })
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 10 700 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
    if password is not None:
        writer.encrypt(user_password=password, owner_password="owner")  # noqa: S106
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_physical_pages_table_numbers_and_full_document_digest():
    raw = pdf_bytes(["Revenue 2024 12345 expenses 10000", None, "Cash flows 2023 67890"])
    pages = parse_pdf_document(PDFDocumentInput(document_id=" report ", content=raw))
    assert [page.page_number for page in pages] == [1, 2, 3]
    assert pages[1].text == ""
    assert "12345" in pages[0].text and "67890" not in pages[0].text
    assert "67890" in pages[2].text
    assert all(page.document_sha256 == hashlib.sha256(raw).hexdigest() for page in pages)
    assert all(page.document_id == " report " for page in pages)


@pytest.mark.parametrize("raw", [b"", b"plain text", b"%PDF-1.7\ntruncated", pdf_bytes(["secret"], "password")])
def test_unreadable_pdfs_fail(raw):
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="report", content=raw))


@pytest.mark.parametrize("document_id", ["", " \t\n"])
def test_blank_document_identity_fails(document_id):
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id=document_id, content=pdf_bytes([None])))


def test_restarted_memory_returns_distinct_pages_and_exact_revisions(tmp_path):
    class Pipeline:
        async def extract(self, request, /):
            return tuple(
                MemoryEntryInput(
                    kind="page", text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"], sources=(source,)
                )
                for source in request.sources
            )

    async def scenario():
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pages.db'}"))
        pages = parse_pdf_document(
            PDFDocumentInput(
                document_id="issuer",
                content=pdf_bytes([
                    "Consolidated income revenue operating income 12345",
                    None,
                    "Consolidated cash flows depreciation 5678",
                ]),
            )
        )
        observations = [project_source_for_transport(registry, await registry.resolve(page)) for page in pages]
        async with open_builtin_runtime(config, candidate_pipeline=Pipeline()) as runtime:
            scope = await runtime.scopes.create(ScopeDraft(title="Reports", summary="Reports", idempotency_key="pdf"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for observation in observations:
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            assert await search_pdf_pages(memory, "income", limit=0) == ()
            assert await search_pdf_pages(memory, "   ") == ()
            citations = await search_pdf_pages(memory, "issuer operating income and cash flows", limit=2)
            assert 0 < len(citations) <= 2
            seen = set()
            for citation in citations:
                result = await memory.get(GetMemoryEntryRequest(citation=citation))
                assert len(result.entry.sources) == 1
                source_id = result.entry.sources[0].source_id
                assert source_id not in seen
                seen.add(source_id)
                original = next(value for value in observations if value.name == source_id)
                assert result.entry.text == original.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"].strip()
                restored = await registry.read(
                    registry.definition_for_name("pdf-page").source_class.model_validate(original.payload)
                )
                assert restored in pages

    asyncio.run(scenario())
