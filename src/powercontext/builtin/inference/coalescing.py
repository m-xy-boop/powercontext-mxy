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

"""Share concurrent embedding work and retain successful vectors in an LRU."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from dataclasses import dataclass

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel


@dataclass(frozen=True, eq=False)
class _BatchUsage:
    # Identity distinguishes requests even if their usage values are identical.
    usage: InferenceUsage


_SharedResult = tuple[EmbeddingVector, _BatchUsage]


def _observe_failure(future: asyncio.Future[_SharedResult]) -> None:
    # All callers may cancel before a provider fails. Retrieve the exception
    # without changing what any surviving waiter receives.
    if not future.cancelled():
        future.exception()


class CoalescingEmbeddingModel:
    """Coordinate callers sharing one event loop and one embedding profile.

    Cancellation detaches a caller, leaving shared provider work running.
    Only successful vectors enter the bounded cache; in-flight sharing remains
    active even when ``cache_size=0``.
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a nonnegative integer")  # noqa: TRY003
        if (
            isinstance(coalesce_ms, bool)
            or not isinstance(coalesce_ms, (int, float))
            or not math.isfinite(coalesce_ms)
            or coalesce_ms < 0
        ):
            raise ValueError("coalesce_ms must be finite and nonnegative")  # noqa: TRY003
        self.profile = model.profile
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[_SharedResult]] = {}
        self._pending: list[str] = []
        self._dispatcher: asyncio.Task[None] | None = None
        self._batches: set[asyncio.Task[None]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return vectors in input order and usage for each awaited batch once."""

        vectors: dict[str, EmbeddingVector] = {}
        waiting: dict[str, asyncio.Future[_SharedResult]] = {}
        loop = asyncio.get_running_loop()
        # Registration has no suspension points, so each identity has exactly
        # one future before any other caller or dispatcher can run.
        for text in texts:
            if text in self._cache:
                vectors[text] = self._cache[text]
                self._cache.move_to_end(text)
            else:
                future = self._inflight.get(text)
                if future is None:
                    future = loop.create_future()
                    future.add_done_callback(_observe_failure)
                    self._inflight[text] = future
                    self._pending.append(text)
                waiting[text] = future
        if self._pending and self._dispatcher is None:
            self._dispatcher = loop.create_task(self._dispatch())
        results = await asyncio.gather(*(asyncio.shield(future) for future in waiting.values()))
        batches: set[_BatchUsage] = set()
        for text, (vector, batch) in zip(waiting, results, strict=True):
            vectors[text] = vector
            batches.add(batch)
        usages = tuple(batch.usage for batch in batches)
        return EmbeddingResult(
            vectors=tuple(vectors[text] for text in texts),
            usage=InferenceUsage(
                requests=sum(usage.requests for usage in usages),
                input_tokens=None
                if any(usage.input_tokens is None for usage in usages)
                else sum(usage.input_tokens or 0 for usage in usages),
                output_tokens=None
                if any(usage.output_tokens is None for usage in usages)
                else sum(usage.output_tokens or 0 for usage in usages),
            ),
        )

    async def _dispatch(self) -> None:
        # Even a zero window yields once so simultaneous callers can register.
        await asyncio.sleep(self._coalesce_seconds)
        pending, self._pending = self._pending, []
        self._dispatcher = None
        for start in range(0, len(pending), self._max_batch_size):
            texts = tuple(pending[start : start + self._max_batch_size])
            task = asyncio.create_task(self._embed_batch(texts))
            self._batches.add(task)
            task.add_done_callback(self._batches.discard)

    async def _embed_batch(self, texts: tuple[str, ...]) -> None:
        futures = tuple(self._inflight[text] for text in texts)
        try:
            result = await self._model.embed(texts)
            if len(result.vectors) != len(texts):
                raise ValueError("provider returned the wrong vector count")  # noqa: TRY003, TRY301
        except asyncio.CancelledError:
            for future in futures:
                future.cancel()
            raise
        except Exception as error:
            for future in futures:
                future.set_exception(error)
        else:
            batch = _BatchUsage(result.usage)
            self._cache_vectors(texts, result.vectors)
            for vector, future in zip(result.vectors, futures, strict=True):
                future.set_result((vector, batch))
        finally:
            for text in texts:
                del self._inflight[text]

    def _cache_vectors(self, texts: tuple[str, ...], vectors: tuple[EmbeddingVector, ...]) -> None:
        if not self._cache_size:
            return
        for text, vector in zip(texts, vectors, strict=True):
            self._cache[text] = vector
            self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
