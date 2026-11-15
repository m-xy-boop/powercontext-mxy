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

"""Query-ranked literal sentence selection under a complete rendered budget."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_context import (
    MemoryEntryAddress,
    PreparedContextBuild,
    PreparedEvidenceFragment,
    PreparedMemoryCandidates,
)
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_BOUNDARY = re.compile(r"(?<=[.!?。！？])\s+|\n+")  # noqa: RUF001 - multilingual sentence punctuation
_STOP = frozenset([
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "in",
    "into",
    "is",
    "it",
    "its",
    "may",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "their",
    "these",
    "this",
    "those",
    "to",
    "was",
    "were",
    "which",
    "while",
    "with",
    "would",
    "we",
    "not",
    "no",
    "than",
    "then",
    "through",
    "during",
    "using",
    "used",
    "use",
    "also",
    "both",
    "each",
    "either",
    "such",
    "between",
    "within",
    "without",
])
_ABBREVIATIONS = frozenset({"al.", "e.g.", "i.e.", "vs.", "dr.", "mr.", "mrs.", "fig.", "et."})


def _stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3].rstrip("e")
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2].rstrip("e")
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "is", "us")):
        return word[:-1]
    return word


def _terms(text: str) -> Counter[str]:
    return Counter(_stem(word) for word in _WORDS.findall(text.casefold()) if word not in _STOP)


def _sentences(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        prefix = text[start : match.start()]
        last = prefix.split()[-1].casefold() if prefix.split() else ""
        if last in _ABBREVIATIONS:
            continue
        if prefix.strip():
            left = start + len(prefix) - len(prefix.lstrip())
            spans.append((left, match.start()))
        start = match.end()
    if text[start:].strip():
        spans.append((start + len(text[start:]) - len(text[start:].lstrip()), len(text.rstrip())))
    # Very long sentences may not fit even alone. Split at original whitespace;
    # adjacent selections are merged back with the exact intervening characters.
    bounded: list[tuple[int, int]] = []
    for start, end in spans:
        while end - start > 600:
            boundary = text.rfind(" ", start + 200, start + 400)
            if boundary < 0:
                boundary = start + 400
            bounded.append((start, boundary))
            start = boundary
            while start < end and text[start].isspace():
                start += 1
        if start < end:
            bounded.append((start, end))
    return tuple(bounded)


@dataclass(frozen=True)
class _Document:
    address: MemoryEntryAddress
    text: str
    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _Sentence:
    document: int
    ordinal: int
    terms: Counter[str]
    score: float = 0.0


def _documents(groups: Sequence[PreparedMemoryCandidates]) -> tuple[_Document, ...]:
    documents: list[_Document] = []
    seen: set[tuple[str, str, int, str, str]] = set()
    for group in groups:
        for hit in group.hits:
            if hit.memory_ref != group.memory_ref:
                raise PreparedContextInvariantError("memory-ref-mismatch")
            identity = (
                group.scope_id,
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            if identity in seen or not hit.text.strip():
                continue
            seen.add(identity)
            documents.append(
                _Document(
                    MemoryEntryAddress(
                        memory=ArtifactAddress(scope_id=group.scope_id, artifact=hit.memory_ref),
                        entry_id=hit.entry_id,
                        entry_version_id=hit.entry_version_id,
                    ),
                    hit.text,
                    _sentences(hit.text),
                )
            )
    return tuple(documents)


def _rank(query: str, documents: tuple[_Document, ...]) -> tuple[_Sentence, ...]:
    sentences = [
        _Sentence(document=index, ordinal=ordinal, terms=_terms(document.text[start:end]))
        for index, document in enumerate(documents)
        for ordinal, (start, end) in enumerate(document.spans)
    ]
    query_terms = _terms(query)
    frequency = Counter(term for sentence in sentences for term in sentence.terms)
    weights = {term: math.log1p((len(sentences) + 0.5) / (frequency[term] + 0.5)) for term in query_terms}
    average = sum(sum(sentence.terms.values()) for sentence in sentences) / max(1, len(sentences))
    ranked: list[_Sentence] = []
    for sentence in sentences:
        length = sum(sentence.terms.values())
        overlap = sentence.terms.keys() & query_terms.keys()
        score = sum(
            weights[term]
            * (sentence.terms[term] * 2.2)
            / (sentence.terms[term] + 1.2 * (0.4 + 0.6 * length / max(1, average)))
            for term in overlap
        )
        # Broad claim coverage is more useful than repeating a single query word.
        coverage = sum(weights[term] for term in overlap) / max(1e-9, sum(weights.values()))
        score *= 0.5 + coverage
        start, end = documents[sentence.document].spans[sentence.ordinal]
        text = documents[sentence.document].text[start:end].casefold()
        if re.search(r"\b(results?|conclu\w*|demonstrat\w*|found|show\w*|reveal\w*|suggest\w*)\b", text):
            score *= 1.12
        ranked.append(_Sentence(sentence.document, sentence.ordinal, sentence.terms, score))
    return tuple(sorted(ranked, key=lambda sentence: (-sentence.score, sentence.document, sentence.ordinal)))


def _fragments(
    documents: tuple[_Document, ...], sentences: Sequence[_Sentence]
) -> tuple[PreparedEvidenceFragment, ...]:
    selected: dict[int, list[int]] = {}
    for sentence in sentences:
        selected.setdefault(sentence.document, []).append(sentence.ordinal)
    fragments: list[PreparedEvidenceFragment] = []
    for index, ordinals in selected.items():
        document = documents[index]
        runs: list[tuple[int, int]] = []
        for ordinal in sorted(ordinals):
            if runs and ordinal == runs[-1][1] + 1:
                runs[-1] = (runs[-1][0], ordinal)
            else:
                runs.append((ordinal, ordinal))
        for first, last in runs:
            start, end = document.spans[first][0], document.spans[last][1]
            fragments.append(PreparedEvidenceFragment(document.address, start, end, document.text[start:end]))
    return tuple(fragments)


def _render(fragments: tuple[PreparedEvidenceFragment, ...]) -> str:
    items = [
        {
            "citation": {
                "scope_id": fragment.address.memory.scope_id,
                "memory_ref": fragment.address.memory.artifact.model_dump(mode="json"),
                "entry_id": fragment.address.entry_id,
                "entry_version_id": fragment.address.entry_version_id,
            },
            "content": fragment.text,
            "start": fragment.start,
            "end": fragment.end,
        }
        for fragment in fragments
    ]
    encoded = json.dumps({"trust": "untrusted_history", "items": items}, ensure_ascii=False, separators=(",", ":"))
    return f"{TRUST_POLICY}\n\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{encoded}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"


def select_evidence(request: PrepareContextRequest, groups: Sequence[PreparedMemoryCandidates]) -> PreparedContextBuild:
    """Select whole sentences, merging adjacent spans to share citation overhead."""

    documents = _documents(groups)
    ranked = _rank(request.query, documents)
    # A bounded beam considers alternate sentence combinations. Measuring each
    # rendered combination includes Unicode, escaping, offsets and exact IDs.
    # Short, relevant sentences can combine with neighbors without paying for
    # another citation, rather than losing to an early document's long prefix.
    states: list[tuple[float, tuple[_Sentence, ...], int]] = [(0.0, (), 0)]
    seen: set[tuple[int, str]] = set()
    for sentence in ranked[:96]:
        if sentence.score <= 0:
            continue
        document = documents[sentence.document]
        start, end = document.spans[sentence.ordinal]
        identity = (sentence.document, document.text[start:end])
        if identity in seen:
            continue
        seen.add(identity)
        additions: list[tuple[float, tuple[_Sentence, ...], int]] = []
        for score, selected, _size in states:
            candidate = (*selected, sentence)
            size = len(_render(_fragments(documents, candidate)).encode("utf-8"))
            if size <= request.max_bytes:
                additions.append((score + sentence.score, candidate, size))
        states = sorted((*states, *additions), key=lambda state: (-state[0], state[2]))[:96]
    chosen = states[0][1]
    fragments = _fragments(documents, chosen)
    if not fragments:
        return PreparedContextBuild(context=PreparedContext(status="empty", content=None, content_bytes=0), origins=())
    content = _render(fragments)
    return PreparedContextBuild(
        context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode("utf-8"))),
        origins=tuple(fragment.address for fragment in fragments),
        fragments=fragments,
    )
