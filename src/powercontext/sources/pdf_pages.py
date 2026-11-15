"""Complete physical PDF pages and offline retrieval through scoped Memory."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from collections import Counter, defaultdict
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator
from pypdf import PdfReader

from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.artifacts.search import analyze_fts_query
from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest
from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PDFDocumentInput(BaseModel):
    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("PDF document_id must not be blank")  # noqa: TRY003
        return value


class PDFPageInput(BaseModel):
    document_id: str
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(ge=1)
    text: str

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("PDF document_id must not be blank")  # noqa: TRY003
        return value


def _identity(page: PDFPageInput) -> str:
    encoded = json.dumps(
        [page.document_id, page.document_sha256, page.page_number], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return "pdf:" + hashlib.sha256(encoded).hexdigest()


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page atomically, preserving its original text."""

    digest = hashlib.sha256(value.content).hexdigest()
    if not value.content.startswith(b"%PDF-"):
        raise ValueError("Input must be a complete PDF")  # noqa: TRY003
    try:
        reader = PdfReader(io.BytesIO(value.content), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a non-empty password")  # noqa: TRY003, TRY301
        return tuple(
            PDFPageInput(
                document_id=value.document_id, document_sha256=digest, page_number=index, text=page.extract_text() or ""
            )
            for index, page in enumerate(reader.pages, 1)
        )
    except Exception as error:
        # pypdf raises several exception families for corrupt object streams,
        # encryption and page trees; none should expose partially extracted pages.
        raise ValueError("PDF could not be completely parsed") from error  # noqa: TRY003


class PDFPageSource(Source, PDFPageInput):
    materialization: SourceMaterialization = SourceMaterialization.CAPTURED

    @model_validator(mode="after")
    def validate_capture(self) -> Self:
        if self.materialization is not SourceMaterialization.CAPTURED or self.name != _identity(self):
            raise ValueError("PDF source must capture the exact page and revision")  # noqa: TRY003
        return self


class _PDFPageProjection:
    name: str = TEXT_EVIDENCE_PROJECTION_KEY.name
    version: str = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[PDFPageSource] = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


class _PDFPageDefinition:
    name: str = "pdf-page"
    version: str = "1"
    input_class: type[PDFPageInput] = PDFPageInput
    source_class: type[PDFPageSource] = PDFPageSource
    projections: tuple[SourceProjection[PDFPageSource], ...] = (_PDFPageProjection(),)

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        return PDFPageSource(name=_identity(value), **value.model_dump())

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput.model_validate(source.model_dump(include=set(PDFPageInput.model_fields)))


PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = _PDFPageDefinition()

# General financial terminology, independent of any company, year or benchmark.
_FINANCIAL_TERMS = (
    (
        r"geograph|operat.*(?:region|countr)|primarily operat",
        "geographic countries regions segments international operations",
    ),
    (r"legal|litigat|lawsuit|battles", "legal proceedings litigation contingencies claims lawsuits"),
    (
        r"income statement|p&l|ebitda|profit|margin",
        "consolidated statements income earnings operations revenue operating",
    ),
    (
        r"cash flow|capex|capital expend|ebitda|d&a",
        "consolidated statements cash flows depreciation amortization capital spending expenditures",
    ),
    (r"restructur", "restructuring impairment charges costs expenses"),
    (r"balance sheet|assets|liabilities|working capital", "consolidated balance sheets assets liabilities current"),
    (r"revenue|sales|growth", "net revenue revenues sales segment organic growth"),
    (r"dividend|repurchase|buyback", "dividends repurchases common shares stock shareholders"),
    (r"debt|borrow|interest", "debt borrowings interest maturities obligations financing"),
)


def _terms(text: str) -> list[str]:
    words = re.findall(r"[a-z]+|\d+", text.casefold())
    return [
        (word[:-3] + "y")
        if len(word) > 6 and word.endswith("ies")
        else word[:-1]
        if len(word) > 4 and word.endswith("s")
        else word
        for word in words
    ]


def _rank_candidates(
    candidates: dict[tuple[str, str, int, str, str], MemoryHit],
    fusion: dict[tuple[str, str, int, str, str], float],
    core: str,
    expansions: list[str],
) -> list[MemoryHit]:
    """Rank retrieved page text by length-normalized lexical relevance."""

    documents = {key: Counter(_terms(hit.text)) for key, hit in candidates.items()}
    count = len(documents)
    frequencies: Counter[str] = Counter(term for terms in documents.values() for term in terms)
    average = sum(sum(terms.values()) for terms in documents.values()) / count or 1
    original = set(_terms(core))
    expanded = set(_terms(" ".join(expansions))) - original

    def score(key):
        terms = documents[key]
        length = sum(terms.values())
        total = 0.0
        for term in original | expanded:
            tf = terms[term]
            if tf:
                idf = math.log(1 + (count - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
                total += (
                    (1 if term in original else 0.35) * idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / average))
                )
        # Company/year tokens in the retained document ID disambiguate reports
        # when the caller's scope contains multiple document revisions or issuers.
        heading = set(_terms(candidates[key].text.split("\n", 1)[0]))
        return total + 2 * len(original & heading) + fusion[key] * 10

    return [candidates[key] for key in sorted(candidates, key=score, reverse=True)]


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Retrieve real Memory candidates, rerank locally, and deduplicate page Sources."""

    if limit <= 0 or not question.strip():
        return ()
    core = analyze_fts_query(question)
    expansions = [terms for pattern, terms in _FINANCIAL_TERMS if re.search(pattern, question.casefold())]
    queries = list(dict.fromkeys([core, *expansions]))
    candidates: dict[tuple[str, str, int, str, str], MemoryHit] = {}
    fusion: dict[tuple[str, str, int, str, str], float] = defaultdict(float)
    # Each query uses the same supplied scope. No file access, model or private
    # backend access is involved; every returned reference is verified via get.
    for query in queries:
        result = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(100, limit * 5)))
        for rank, hit in enumerate(result.hits):
            key = (
                hit.memory_ref.family,
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            candidates[key] = hit
            fusion[key] += 1 / (40 + rank)
    if not candidates:
        return ()
    citations = []
    seen = set()
    for hit in _rank_candidates(candidates, fusion, core, expansions):
        citation = MemoryCitation(
            memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
        )
        record = await memory.get(GetMemoryEntryRequest(citation=citation))
        pages = {source.source_id for source in record.entry.sources if source.source_type == "pdf-page"}
        if len(pages) != 1 or pages & seen:
            continue
        seen.update(pages)
        citations.append(citation)
        if len(citations) >= limit:
            break
    return tuple(citations)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "search_pdf_pages",
]
