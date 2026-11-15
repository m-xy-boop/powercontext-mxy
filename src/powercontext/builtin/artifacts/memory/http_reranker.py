"""Batch HTTP scores while preserving the original Memory candidate ranks."""

from __future__ import annotations

import json
import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("HTTP rerank response contains a duplicate JSON key")  # noqa: TRY003
        result[key] = value
    return result


def _batch_scores(content: bytes, expected: set[int]) -> dict[int, int | float]:
    payload = json.loads(content, object_pairs_hook=_unique_object)
    if not isinstance(payload, dict) or set(payload) != {"scores"} or not isinstance(payload["scores"], list):
        raise ValueError("HTTP rerank response must contain only a scores array")  # noqa: TRY003
    scores: dict[int, int | float] = {}
    for item in payload["scores"]:
        if not isinstance(item, dict) or set(item) != {"rank", "score"}:
            raise ValueError("HTTP rerank score records must contain only rank and score")  # noqa: TRY003
        rank, score = item["rank"], item["score"]
        if type(rank) is not int or rank not in expected or rank in scores:
            raise ValueError("HTTP rerank ranks must cover each requested global rank exactly once")  # noqa: TRY003
        if type(score) not in (int, float) or (isinstance(score, float) and not math.isfinite(score)):
            raise ValueError("HTTP rerank scores must be finite JSON numbers")  # noqa: TRY003
        scores[rank] = score
    if set(scores) != expected:
        raise ValueError("HTTP rerank response is missing requested ranks")  # noqa: TRY003
    return scores


class HTTPMemoryReranker:
    """Select globally by descending service score, breaking ties by original rank.

    The caller owns the client and its transport, authentication and timeout
    configuration. This adapter neither closes it nor retries requests.
    """

    policy_id: str

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("HTTP rerank batch_size must be a positive integer")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(self, query: str, candidates: tuple[MemoryHit, ...], limit: int, /) -> MemoryRerankDecision:
        if not candidates or type(limit) is not int or not 1 <= limit <= len(candidates):
            raise ValueError("HTTP rerank requires candidates and a valid integer limit")  # noqa: TRY003
        scores: dict[int, int | float] = {}
        requests = 0
        for offset in range(0, len(candidates), self._batch_size):
            batch = candidates[offset : offset + self._batch_size]
            items = [{"rank": offset + index, "text": candidate.text} for index, candidate in enumerate(batch, 1)]
            requests += 1
            response = await self._client.post(self._endpoint, json={"query": query, "candidates": items})
            response.raise_for_status()
            scores.update(_batch_scores(response.content, set(range(offset + 1, offset + len(batch) + 1))))
        return MemoryRerankDecision(
            selected_ranks=tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit]),
            usage=InferenceUsage(requests=requests),
        )


__all__ = ["HTTPMemoryReranker"]
