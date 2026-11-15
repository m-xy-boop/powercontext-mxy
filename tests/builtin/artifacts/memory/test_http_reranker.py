"""HTTP scoring protocol validation and global ranking across large pools."""

import asyncio
import json
import math

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.artifacts.memory.models import MemoryHit


def hits(count):
    return tuple(
        MemoryHit(
            memory_ref=ArtifactRef(family="memory", artifact_id="original", revision=3),
            entry_id=f"entry-{rank}",
            entry_version_id=f"version-{rank}",
            text=f"原始正文 {rank}",
            score=1 / rank,
            matched_by=("fts",),
        )
        for rank in range(1, count + 1)
    )


@pytest.mark.parametrize("batch_size", [1, 7, 32, 200])
def test_large_pool_all_batches_participate_and_client_remains_open(batch_size):
    candidates = hits(173)
    snapshot = tuple(hit.model_dump() for hit in candidates)
    seen = []
    scores = {rank: (rank % 11 - 5) / 7 for rank in range(1, 174)}

    def handler(request):
        body = json.loads(request.content)
        assert set(body) == {"query", "candidates"}
        assert body["query"] == "原始问题"
        assert 0 < len(body["candidates"]) <= batch_size
        for item in body["candidates"]:
            assert set(item) == {"rank", "text"}
            assert item["text"] == candidates[item["rank"] - 1].text
            seen.append(item["rank"])
        return httpx.Response(
            200,
            json={
                "scores": [
                    {"rank": item["rank"], "score": scores[item["rank"]]} for item in reversed(body["candidates"])
                ]
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker(
                "https://offline.invalid", client=client, batch_size=batch_size, policy_id="custom"
            )
            decision = await reranker.rerank("原始问题", candidates, 10)
            assert decision.selected_ranks == tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:10])
            assert decision.usage.requests == math.ceil(173 / batch_size)
            assert reranker.policy_id == "custom"
            assert not client.is_closed
            assert not decision.used_fallback and decision.discarded_rank_count == 0
        assert sorted(seen) == list(range(1, 174))
        assert tuple(hit.model_dump() for hit in candidates) == snapshot

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "raw",
    [
        b"null",
        b"[]",
        b"{}",
        b"{bad",
        b'{"scores":null}',
        b'{"scores":{}}',
        b'{"scores":[{"rank":true,"score":1}]}',
        b'{"scores":[{"rank":"1","score":1}]}',
        b'{"scores":[{"rank":1.0,"score":1}]}',
        b'{"scores":[{"rank":1,"score":true}]}',
        b'{"scores":[{"rank":1,"score":"1"}]}',
        b'{"scores":[{"rank":1,"score":null}]}',
        b'{"scores":[{"rank":1,"score":-Infinity}]}',
        b'{"scores":[{"rank":1,"score":1e999}]}',
        b'{"scores":[{"rank":1,"score":1,"score":2}]}',
    ],
)
def test_invalid_json_schema_and_number_types_are_rejected(raw):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw))
        ) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://offline.invalid", client=client).rerank("q", hits(1), 1)

    asyncio.run(scenario())


def test_response_must_cover_its_own_batch_not_another_batch():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={"scores": [{"rank": 1, "score": 0.5}]})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://offline.invalid", client=client, batch_size=1).rerank("q", hits(2), 1)
        assert len(calls) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [301, 400, 429, 500])
def test_http_status_errors_are_preserved(status):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status))) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await HTTPMemoryReranker("https://offline.invalid", client=client).rerank("q", hits(1), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_batch_size_rejected_before_request(batch_size):
    async def scenario():
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError):
                HTTPMemoryReranker("https://offline.invalid", client=client, batch_size=batch_size)

    asyncio.run(scenario())
