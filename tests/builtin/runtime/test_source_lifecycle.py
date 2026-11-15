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

import asyncio
import sqlite3

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    GetMemoryEntryRequest,
    GetTopicMemoryRequest,
    RememberMemoryRequest,
    SearchMemoryRequest,
    SearchTopicMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceRef


def _event(node="root-a", sequence=1, available=False):
    return SourceLifecycleEvent(
        node_id=node, revision=3, sequence=sequence, available=available, reason="test withdrawal"
    )


async def _seed(runtime, key="test"):
    sid = (await runtime.scopes.create(ScopeDraft(title=key, summary="Lifecycle", idempotency_key=key))).scope_id
    lifecycle = runtime.lifecycle.for_scope(sid)
    citations = {}
    for suffix in ("a", "b"):
        root = f"root-{suffix}"
        receipt = await runtime.sources.for_scope(sid).capture(CaptureSource(source_id=root, content=root, metadata={}))
        await lifecycle.register_source(root, receipt.source_ref, revision=3)
        entry = await runtime.memory.for_scope(sid).remember(
            RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text=f"fact{suffix} supported evidence"),))
        )
        citations[suffix] = entry.entry.citation
        await lifecycle.register_memory(f"memory-{suffix}", citations[suffix], parents=(root,))
    record = await runtime.records.for_scope(sid).create_artifact(
        "topic-memory",
        ArtifactWrite(
            content={"title": "Joined topic", "summary": "Joined supported evidence", "detail": "Both facts"}
        ),
    )
    topic = ArtifactRef(family="topic-memory", artifact_id=record.artifact_id, revision=record.revision)
    await lifecycle.register_topic("topic", topic, parents=("memory-a", "memory-b"), policy="all")
    return sid, lifecycle, citations, topic


def test_real_native_write_failure_recovers_same_event_after_restart_and_replay_is_quiet(tmp_path):
    async def scenario():
        database = tmp_path / "retry.db"
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
        async with open_builtin_runtime(config) as runtime:
            sid, lifecycle, citations, topic = await _seed(runtime)
            before = (await runtime.memory.for_scope(sid).list()).memory_ref
            assert before is not None
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "CREATE TRIGGER interrupt_lifecycle BEFORE INSERT ON pc_artifacts "
                    "WHEN NEW.family = 'memory' BEGIN SELECT RAISE(ABORT, 'controlled native failure'); END"
                )
            with pytest.raises(IntegrityError, match="controlled native failure"):
                await lifecycle.apply(_event())
            assert (await runtime.memory.for_scope(sid).list()).memory_ref == before
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TRIGGER interrupt_lifecycle")
        async with open_builtin_runtime(config) as runtime:
            lifecycle = runtime.lifecycle.for_scope(sid)
            assert await lifecycle.apply(_event()) == ["memory-b", "root-b"]
            memory = runtime.memory.for_scope(sid)
            after = (await memory.list(include_inactive=True)).memory_ref
            assert after is not None
            assert after.revision == before.revision + 1
            assert not (await memory.search(SearchMemoryRequest(query="facta", mode="fts"))).hits
            assert (await memory.get(GetMemoryEntryRequest(citation=citations["a"]))).entry.text.startswith("facta")
            assert not (await runtime.topic_memory.for_scope(sid).search(SearchTopicMemoryRequest(query="Joined"))).hits
            # Exact Topic reads remain valid while generation and browse exclude it.
            assert (
                await runtime.topic_memory.for_scope(sid).get(GetTopicMemoryRequest(artifact=topic))
            ).topic.as_ref() == topic
            assert not await runtime.topic_memory.for_scope(sid).browse(limit=10)
            await lifecycle.apply(_event())
            assert (await memory.list()).memory_ref == after
            await lifecycle.register_memory("memory-a", citations["a"], parents=("root-a",))
            assert (await memory.list()).memory_ref == after
            assert await lifecycle.resolve("memory-a", purpose="audit") == citations["a"]
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("memory-a", purpose="generation")
            await lifecycle.apply(_event(sequence=2, available=True))
            assert await lifecycle.resolve("memory-a") == citations["a"]
            restored = (await memory.list()).memory_ref
            assert restored is not None and restored.revision == after.revision + 1
            assert (await runtime.topic_memory.for_scope(sid).browse(limit=10))[0].artifact_ref == topic

    asyncio.run(scenario())


def test_scope_isolation_strict_bindings_and_invalid_events(tmp_path):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'isolation.db'}"))
        async with open_builtin_runtime(config) as runtime:
            left, service, citations, topic = await _seed(runtime, "left")
            right, peer, other, _ = await _seed(runtime, "right")
            await service.apply(_event(sequence=4))
            assert await peer.resolve("memory-a") == other["a"]
            assert (await runtime.memory.for_scope(right).search(SearchMemoryRequest(query="facta", mode="fts"))).hits
            for kwargs in (
                {"parents": ()},
                {"parents": ("root-a", "root-a")},
                {"parents": ("missing",)},
                {"parents": ("new",)},
                {"parents": ("root-a",), "policy": "invalid"},
            ):
                with pytest.raises(SourceLifecycleError):
                    await service.register_memory("new", citations["a"], **kwargs)
            with pytest.raises(SourceLifecycleError):
                await service.register_memory("memory-a", citations["a"], parents=("root-b",))
            with pytest.raises(SourceLifecycleError):
                await service.register_source("new", SourceRef(source_type="content", source_id="missing"), revision=3)
            with pytest.raises(SourceLifecycleError):
                await service.register_memory("new", other["a"], parents=("root-b",))
            with pytest.raises(SourceLifecycleError):
                await service.register_topic("new", topic.model_copy(update={"revision": 999}), parents=("root-b",))
            for invalid in (
                _event(sequence=3),
                _event(sequence=4, available=True),
                _event(sequence=4).model_copy(update={"reason": "conflict"}),
                _event("memory-a"),
                _event("missing"),
                _event(sequence=5).model_copy(update={"revision": 4}),
            ):
                with pytest.raises(SourceLifecycleError):
                    await service.apply(invalid)
            for node, purpose in (("missing", "audit"), ("memory-b", "unknown")):
                with pytest.raises(SourceLifecycleError):
                    await service.resolve(node, purpose=purpose)
        async with open_builtin_runtime(config) as runtime:
            with pytest.raises(SourceLifecycleError):
                await runtime.lifecycle.for_scope(left).resolve("memory-a")
            assert await runtime.lifecycle.for_scope(right).resolve("memory-a") == other["a"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("node_id", ""),
        ("node_id", " \n"),
        ("node_id", 1),
        ("reason", ""),
        ("reason", "\t"),
        ("reason", False),
        ("revision", 0),
        ("revision", -1),
        ("revision", True),
        ("revision", 3.0),
        ("revision", "3"),
        ("sequence", 0),
        ("sequence", -1),
        ("sequence", False),
        ("sequence", 1.0),
        ("sequence", "1"),
        ("available", 0),
        ("available", "false"),
        ("available", None),
        ("extra", "field"),
    ],
)
def test_event_fields_are_strict_and_extra_fields_are_rejected(field, value):
    fields = _event().model_dump()
    fields[field] = value
    with pytest.raises(ValidationError):
        SourceLifecycleEvent(**fields)


def test_withdrawn_topics_are_excluded_from_vector_search_and_exact_reads_survive():
    class Embedding:
        profile = EmbeddingProfile(profile_id="lifecycle", model="offline", dimension=2)

        async def embed(self, texts):
            return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

    async def scenario():
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig()), embedding_model=Embedding()) as runtime:
            sid, lifecycle, _, topic = await _seed(runtime)
            app = runtime.topic_memory.for_scope(sid)
            assert (await app.search(SearchTopicMemoryRequest(query="Joined"))).hits
            await lifecycle.apply(_event())
            assert not (await app.search(SearchTopicMemoryRequest(query="Joined"))).hits
            assert (await app.get(GetTopicMemoryRequest(artifact=topic))).topic.as_ref() == topic
            await lifecycle.apply(_event(sequence=2, available=True))
            assert (await app.search(SearchTopicMemoryRequest(query="Joined"))).hits

    asyncio.run(scenario())
