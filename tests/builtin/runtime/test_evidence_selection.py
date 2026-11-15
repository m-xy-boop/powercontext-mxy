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

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder, PreparedMemoryCandidates
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY


def _group(texts, scope="scope", revision=3):
    ref = ArtifactRef(family="memory", artifact_id="memory", revision=revision)
    return PreparedMemoryCandidates(
        scope_id=scope,
        memory_ref=ref,
        hits=tuple(
            MemoryHit(
                memory_ref=ref,
                entry_id=f"entry-{index}",
                entry_version_id=f"version-{index}",
                text=text,
                score=1.0,
                matched_by=("fts",),
            )
            for index, text in enumerate(texts)
        ),
    )


def _build(query, groups, budget=1800, **settings):
    return PreparedContextBuilder().build_scopes_result(
        request=PrepareContextRequest(query=query, max_bytes=budget, evidence_selection=True, **settings),
        current_scope_id="scope",
        memory_candidates=groups,
    )


def _validate(build, groups, budget):
    content = build.context.content or ""
    assert build.context.content_bytes == len(content.encode()) <= budget
    assert len(build.fragments) == len(build.origins)
    if not content:
        assert build.fragments == build.origins == ()
        return
    assert content.startswith(TRUST_POLICY + "\n\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n")
    assert content.endswith("\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1")
    envelope = json.loads(content.splitlines()[-2])
    assert envelope["trust"] == "untrusted_history"
    assert len(envelope["items"]) == len(build.fragments)
    for fragment, origin, item in zip(build.fragments, build.origins, envelope["items"], strict=True):
        address = fragment.address
        group = next(group for group in groups if group.scope_id == address.memory.scope_id)
        hit = next(hit for hit in group.hits if hit.entry_id == address.entry_id)
        assert address.memory.artifact == hit.memory_ref
        assert address.entry_version_id == hit.entry_version_id
        assert type(fragment.start) is type(fragment.end) is int
        assert 0 <= fragment.start < fragment.end <= len(hit.text)
        assert fragment.text == hit.text[fragment.start : fragment.end]
        assert origin == address
        assert item == {
            "citation": {
                "scope_id": group.scope_id,
                "memory_ref": hit.memory_ref.model_dump(mode="json"),
                "entry_id": hit.entry_id,
                "entry_version_id": hit.entry_version_id,
            },
            "content": fragment.text,
            "start": fragment.start,
            "end": fragment.end,
        }


def test_queries_select_different_late_disjoint_original_fragments():
    first = "Cobalt sensors detect a signal at 37 degrees."
    second = "Cobalt sensors remain accurate after 90 cycles."
    unrelated = "Routine administration and scheduling notes. " * 30
    text = unrelated + first + " " + unrelated + second + " " + unrelated + "Amber valves open at 19 degrees."
    groups = (_group((text,)),)
    build = _build("cobalt sensors", groups, 1400)
    _validate(build, groups, 1400)
    assert len(build.fragments) == 2
    assert {fragment.text for fragment in build.fragments} == {first, second}
    amber = _build("amber valves", groups, 1400)
    _validate(amber, groups, 1400)
    assert "19 degrees" in (amber.context.content or "")
    assert amber.fragments != build.fragments


def test_adjacent_evidence_merges_and_identical_text_in_other_scopes_keeps_its_own_address():
    text = "Trial samples confirm cobalt activity. Cobalt activity increases at higher concentrations."
    groups = (_group((text,), "scope", 3), _group((text,), "other", 7))
    build = _build("cobalt activity concentrations", groups, 3000)
    _validate(build, groups, 3000)
    assert {fragment.address.memory.scope_id for fragment in build.fragments} == {"scope", "other"}
    assert len(build.fragments) == 2
    assert all(fragment.text == text for fragment in build.fragments)


def test_unicode_escaped_text_and_exact_budget_boundary():
    groups = (_group(('海洋观测 station says "cool" \\ temperature.\nStation 温度 rises by 3°C.',)),)
    large = _build("station temperature", groups)
    _validate(large, groups, 1800)
    exact = _build("station temperature", groups, large.context.content_bytes)
    assert exact == large
    smaller = _build("station temperature", groups, large.context.content_bytes - 1)
    _validate(smaller, groups, large.context.content_bytes - 1)
    assert smaller.context != large.context


def test_tiny_budget_and_unrelated_candidates_can_return_empty_without_losing_accounting():
    groups = (_group(("Cobalt changes the measured response.",)),)
    for query, budget in (("cobalt", 512), ("unrelated octopus", 1800)):
        build = _build(query, groups, budget)
        _validate(build, groups, budget)
        assert build.context.status == "empty"


def test_repeated_candidates_deduplicate_by_identity_and_long_sentence_keeps_late_evidence():
    text = "Administrative context " * 100 + "and the cobalt sensor remains precise at 37 degrees."
    group = _group((text,))
    duplicated = replace(group, hits=group.hits * 2)
    build = _build("cobalt sensor precise", (duplicated,), 1200)
    _validate(build, (group,), 1200)
    assert len(build.fragments) == 1
    assert "37 degrees" in build.fragments[0].text
    assert build.fragments[0].start > 0


def test_default_prepare_and_budget_probe_remain_consistent():
    groups = (_group(("Earlier background. Cobalt sensor remains precise.",)),)
    builder = PreparedContextBuilder()
    request = PrepareContextRequest(query="cobalt", max_bytes=1400)
    ordinary = builder.build_scopes_result(request=request, current_scope_id="scope", memory_candidates=groups)
    explicit = builder.build_scopes_result(
        request=request.model_copy(update={"evidence_selection": False}),
        current_scope_id="scope",
        memory_candidates=groups,
    )
    assert ordinary == explicit and ordinary.fragments == ()
    enabled = request.model_copy(update={"evidence_selection": True})
    selected = builder.build_scopes_result(request=enabled, current_scope_id="scope", memory_candidates=groups)
    probe = builder.probe_budget(request=enabled, current_scope_id="scope", memory_candidates=groups)
    assert probe.delivered_items == len(selected.fragments)
    assert probe.unused_bytes == 1400 - selected.context.content_bytes
    with pytest.raises(ValidationError):
        PrepareContextRequest.model_validate({"query": "cobalt", "evidence_selection": "true"})
    mismatched = replace(groups[0], memory_ref=groups[0].memory_ref.model_copy(update={"revision": 999}))
    with pytest.raises(PreparedContextInvariantError):
        _build("cobalt", (mismatched,))
