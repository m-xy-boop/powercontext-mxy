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

# Generic accounting concepts and alternate report headings. These rules do not
# depend on a document, issuer, question ID or annotated evidence location.
_FINANCIAL_TERMS = (
    (
        r"geograph|primarily operat|operat.*(?:region|countr)",
        ("geographic", "reportable segments", "international operations", "countries regions"),
        ("geographic", "our divisions", "reportable segments", "business segments"),
    ),
    (
        r"legal|litigat|lawsuit|battles",
        ("legal proceedings", "litigation", "contingencies", "lawsuits"),
        ("legal proceedings", "litigation", "contingencies"),
    ),
    (
        r"income statement|p&l|ebitda|profit|margin|operating income",
        ("statement income", "statements operations", "statements earnings", "operating profit"),
        (
            "statement of income",
            "statements of income",
            "statements of operations",
            "statement of operations",
            "statements of earnings",
            "income statements",
        ),
    ),
    (
        r"cash flow|capex|capital expend|ebitda|d&a",
        ("statement cash flows", "depreciation amortization", "capital expenditures", "capital spending"),
        ("statement of cash flows", "statements of cash flows", "cash flow statements"),
    ),
    (r"restructur", ("restructuring", "impairment charges"), ("restructuring",)),
    (
        r"balance sheet|assets|liabilities|working capital|current ratio",
        ("balance sheets", "current assets", "current liabilities"),
        ("balance sheet", "balance sheets", "financial position"),
    ),
    (
        r"revenue|sales|growth",
        ("net revenue", "net sales", "organic growth"),
        ("net revenue", "net sales", "segment results"),
    ),
    (
        r"dividend|repurchase|buyback",
        ("dividends", "share repurchases", "stock repurchases"),
        ("dividends", "repurchases"),
    ),
    (r"debt|borrow|interest", ("debt", "borrowings", "interest expense"), ("debt", "borrowings")),
)
_QUERY_NOISE = frozenset([
    "has",
    "have",
    "had",
    "any",
    "all",
    "also",
    "primarily",
    "reported",
    "report",
    "annual",
    "fiscal",
    "fy",
    "important",
    "materially",
    "ongoing",
    "quantity",
    "directly",
    "outlined",
    "explicitly",
    "state",
    "then",
    "if",
    "not",
    "define",
    "defined",
    "assuming",
    "perspective",
    "investment",
    "analyst",
    "use",
    "only",
    "details",
    "shown",
    "within",
    "answer",
    "respond",
    "response",
    "give",
    "question",
    "calculate",
    "calculated",
    "using",
    "based",
    "relying",
    "less",
    "unadjusted",
    "adjusted",
    "usd",
    "dollar",
    "dollars",
    "million",
    "millions",
    "billion",
    "billions",
    "year",
    "years",
    "end",
    "ended",
    "much",
    "many",
    "total",
    "company",
    "inc",
    "corporation",
    "corp",
    "financial",
    "statements",
    "statement",
])


def _query_plan(question: str) -> tuple[str, list[tuple[str, ...]], list[str]]:
    normalized = re.sub(r"\bfy\s*(\d{4})\b", r"\1", question.casefold())
    core = " ".join(word for word in analyze_fts_query(normalized).split() if word not in _QUERY_NOISE)
    groups: list[tuple[str, ...]] = [
        queries for pattern, queries, _headings in _FINANCIAL_TERMS if re.search(pattern, normalized)
    ]
    headings = [
        heading
        for pattern, _queries, headings in _FINANCIAL_TERMS
        if re.search(pattern, normalized)
        for heading in headings
    ]
    return core, groups, headings


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
    headings: list[str],
) -> list[MemoryHit]:
    """Combine lexical relevance with report titles, without using page positions."""

    documents = {key: Counter(_terms(hit.text.split("\n", 1)[-1])) for key, hit in candidates.items()}
    count = len(documents)
    frequencies: Counter[str] = Counter(term for terms in documents.values() for term in terms)
    average = sum(sum(terms.values()) for terms in documents.values()) / count or 1
    original = set(_terms(core))
    expanded = set(_terms(" ".join(headings))) - original

    def score(key):
        terms = documents[key]
        length = sum(terms.values())
        total = 0.0
        for term in original | expanded:
            tf = terms[term]
            if tf:
                idf = math.log(1 + (count - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
                total += (
                    (1 if term in original else 0.4) * idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / average))
                )
        document_id, _, body = candidates[key].text.partition("\n")
        heading = set(_terms(document_id))
        # Issuer/year constrain a question; body term frequency must not swamp
        # them when the scope contains reports from multiple companies.
        document_match = 5 * len(original & heading)
        # A report can contain several sections on one physical page. Match
        # short heading lines anywhere, rather than just the first characters.
        title_lines = [" ".join(line.casefold().split()) for line in body.splitlines() if len(line.strip()) <= 100]
        title_match = max((12.0 for phrase in headings if any(phrase in line for line in title_lines)), default=0.0)
        # Contents pages mention every financial topic without providing its
        # evidence. Recognize the list structure, not the recurring page banner.
        contents_penalty = 12 if len(re.findall(r"\bItem\s+\d+[A-Z]?\.", body)) >= 5 else 0
        return total + document_match + title_match + fusion[key] * 5 - contents_penalty

    return [candidates[key] for key in sorted(candidates, key=score, reverse=True)]


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Retrieve real Memory candidates, rerank locally, and deduplicate page Sources."""

    if limit <= 0 or not question.strip():
        return ()
    core, groups, headings = _query_plan(question)
    # Short queries avoid excluding a relevant statement merely because it
    # lacks the many explanatory or instructional words in a long question.
    queries = list(dict.fromkeys([core, *(query for group in groups for query in group)]))
    if not groups:
        words = core.split()
        queries.extend(" ".join(words[index : index + 2]) for index in range(0, len(words), 2))
    candidates: dict[tuple[str, str, int, str, str], MemoryHit] = {}
    fusion: dict[tuple[str, str, int, str, str], float] = defaultdict(float)
    # Each query uses the same supplied scope. No file access, model or private
    # backend access is involved; every returned reference is verified via get.
    for query in queries:
        if not query.strip():
            continue
        result = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(150, limit * 8)))
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
    for hit in _rank_candidates(candidates, fusion, core, headings):
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
