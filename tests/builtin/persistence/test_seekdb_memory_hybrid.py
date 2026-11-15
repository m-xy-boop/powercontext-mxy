# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Real seekdb channel equivalence across database-side candidate boundaries."""

from __future__ import annotations

import hashlib
import math
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from powercontext.builtin.artifacts.memory import EmbeddingProfile, Memory, MemoryEntryInput, MemorySearchRequest
from powercontext.builtin.artifacts.memory.fusion import fuse_rankings
from powercontext.builtin.artifacts.memory.service import MemoryService
from powercontext.builtin.artifacts.search import analyze_fts_query
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex
from powercontext.builtin.persistence.oceanbase.memory_index import OceanBaseMemoryFTSIndex, OceanBaseMemoryVectorIndex
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.seekdb.memory_index import SeekDBMemoryHybridIndex
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.tags import MemoryEntryTagTarget, TagFilter

pytest.importorskip("pylibseekdb")


@contextmanager
def _candidate_statements(connection):
    statements = []

    def record(_connection, _cursor, statement, parameters, _context, _executemany):
        if "WITH lexical" in statement:
            statements.append((statement, parameters))

    event.listen(connection.sync_connection, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", record)


class _Embedding:
    profile = EmbeddingProfile(
        profile_id="hybrid-channel-equivalence",
        model="deterministic",
        dimension=384,
        distance="l2",
        normalization="unit",
    )

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        vectors = []
        for value in texts:
            components = tuple(float(byte) - 127.5 for byte in hashlib.shake_256(value.encode()).digest(384))
            norm = math.sqrt(sum(component * component for component in components))
            vectors.append(tuple(component / norm for component in components))
        return EmbeddingResult(vectors=tuple(vectors))


@pytest.mark.asyncio
async def test_native_hybrid_preserves_channels_with_limits_artifacts_and_tags(tmp_path):
    embedding = _Embedding()
    async with open_builtin_contexts(
        BuiltinConfig(database=SeekDBConfig(path=tmp_path / "db")), embedding_model=embedding
    ) as contexts:
        memory = (await contexts.get("project")).artifacts.memory
        head = await memory.remember(
            memory=None,
            entries=tuple(
                MemoryEntryInput(kind="fact", text=f"Storage retrieval record {number}. " + "index " * (number % 5 + 1))
                for number in range(64)
            ),
            mode="append",
        )
        assert head is not None
        # Both an excluded artifact in this scope and another scope share terms.
        for scope in ("project", "other"):
            await contexts.get(scope)
            decoy = MemoryService(
                backend=RelationalMemoryBackend(
                    database=contexts.database,
                    scope_id=scope,
                    artifacts=ArtifactRepository((Memory,)),
                    index=SeekDBMemoryHybridIndex(
                        OceanBaseMemoryFTSIndex(), OceanBaseMemoryVectorIndex(embedding.profile)
                    ),
                ),
                embedding_model=embedding,
            )
            await decoy.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="Storage index retrieval."),),
                mode="append",
            )
        tagged_ids = set()
        both_ids = set()
        for number, entry in enumerate(head.content.manifest.entries[:12]):
            target = MemoryEntryTagTarget(artifact_id=head.artifact_id, entry_id=entry.entry_id)
            empty = await contexts.records.get_tags("project", target)
            tags = ("red", "blue") if number % 2 else ("red",)
            await contexts.records.replace_tags("project", target, tags, expected_etag=empty.etag)
            tagged_ids.add(entry.entry_id)
            if number % 2:
                both_ids.add(entry.entry_id)

        native = SeekDBMemoryHybridIndex(OceanBaseMemoryFTSIndex(), OceanBaseMemoryVectorIndex(embedding.profile))
        frozen = CompositeMemoryIndex(OceanBaseMemoryFTSIndex(), OceanBaseMemoryVectorIndex(embedding.profile))
        for query in ("storage index", "retrieval", "missingterm"):
            vector = (await embedding.embed((query,))).vectors[0]
            for tags, allowed in (
                (None, {entry.entry_id for entry in head.content.manifest.entries}),
                (TagFilter(tags=("red", "blue"), match="any"), tagged_ids),
                (TagFilter(tags=("red", "blue"), match="all"), both_ids),
                (TagFilter(tags=("absent",)), set()),
            ):
                for limit in (1, 7, 32):
                    request = MemorySearchRequest(
                        query=query,
                        analyzed_query=analyze_fts_query(query),
                        memories=(head.as_ref(),),
                        candidate_limit=limit,
                        mode="hybrid",
                        query_vector=vector,
                        embedding_profile=embedding.profile,
                        tag_filter=tags,
                    )
                    async with contexts.database.transaction() as connection:
                        expected = await frozen.search(connection, "project", request)
                        with _candidate_statements(connection) as statements:
                            actual = await native.search(connection, "project", request)
                        assert len(statements) == 1
                        if query == "storage index" and limit == 7 and allowed:
                            sql, parameters = statements[0]
                            plan = "\n".join(
                                str(row[0])
                                for row in (await connection.exec_driver_sql(f"EXPLAIN {sql}", parameters)).all()
                            )
                            assert "TEXT RETRIEVAL SCAN" in plan
                            assert "VECTOR INDEX" in plan
                        repeated = await native.search(connection, "project", request)
                    assert actual == repeated
                    assert actual == expected
                    assert fuse_rankings(fts=actual.fts, vector=actual.vector, limit=10) == fuse_rankings(
                        fts=expected.fts, vector=expected.vector, limit=10
                    )
                    for channel in (actual.fts, actual.vector):
                        assert len(channel) <= limit
                        assert all(hit.memory_ref == head.as_ref() and hit.entry_id in allowed for hit in channel)
