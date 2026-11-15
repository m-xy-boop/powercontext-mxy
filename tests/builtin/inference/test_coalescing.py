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
import gc

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel


def _vectors(texts):
    return tuple((float(sum(text.encode())),) for text in texts)


class _ControlledEmbedding:
    profile = EmbeddingProfile(profile_id="coalescing-test", model="offline", dimension=1)

    def __init__(self):
        self.calls = []
        self.started = asyncio.Queue()
        self.finished = asyncio.Queue()
        self.release = asyncio.Event()
        self.fail = False
        self.invalid = False

    async def embed(self, texts):
        self.calls.append(texts)
        self.started.put_nowait(texts)
        try:
            await self.release.wait()
            if self.fail and "bad" in texts:
                raise RuntimeError("controlled batch failure")  # noqa: TRY003
            return EmbeddingResult(
                vectors=() if self.invalid else _vectors(texts),
                usage=InferenceUsage(requests=2, input_tokens=len(texts) * 5, output_tokens=0),
            )
        finally:
            self.finished.put_nowait(texts)


def test_late_waiter_shares_running_batches_without_cache_and_counts_each_batch_once():
    async def scenario():
        base = _ControlledEmbedding()
        model = CoalescingEmbeddingModel(base, max_batch_size=2, coalesce_ms=0, cache_size=0)
        first = asyncio.create_task(model.embed(("a", "b", "c", "a")))
        await base.started.get()
        await base.started.get()
        second = asyncio.create_task(model.embed(("c", "b", "d", "c")))
        await base.started.get()
        base.release.set()
        left, right = await asyncio.gather(first, second)
        assert left.vectors == _vectors(("a", "b", "c", "a"))
        assert right.vectors == _vectors(("c", "b", "d", "c"))
        assert left.usage == InferenceUsage(requests=4, input_tokens=15, output_tokens=0)
        assert right.usage == InferenceUsage(requests=6, input_tokens=20, output_tokens=0)
        assert base.calls == [("a", "b"), ("c",), ("d",)]
        # With caching disabled a completed result is computed again.
        await model.embed(("a",))
        assert base.calls[-1] == ("a",) and len(base.calls) == 4

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_failed_batch_does_not_cancel_independent_batches_or_cache_failure():
    async def scenario():
        base = _ControlledEmbedding()
        base.fail = True
        model = CoalescingEmbeddingModel(base, max_batch_size=1, coalesce_ms=0)
        mixed = asyncio.create_task(model.embed(("bad", "other")))
        independent = asyncio.create_task(model.embed(("good",)))
        for _ in range(3):
            await base.started.get()
        base.release.set()
        with pytest.raises(RuntimeError, match="controlled"):
            await mixed
        assert (await independent).vectors == _vectors(("good",))
        for _ in range(3):
            await base.finished.get()
        cached = await model.embed(("other", "good"))
        assert cached.usage.requests == 0
        base.fail = False
        assert (await model.embed(("bad",))).vectors == _vectors(("bad",))
        assert base.calls.count(("bad",)) == 2

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


@pytest.mark.parametrize("before_dispatch", [False, True])
def test_cancelled_caller_cannot_cancel_late_survivor(before_dispatch):
    async def scenario():
        base = _ControlledEmbedding()
        model = CoalescingEmbeddingModel(base, coalesce_ms=10, cache_size=0)
        cancelled = asyncio.create_task(model.embed(("shared", "shared")))
        if before_dispatch:
            await asyncio.sleep(0)
        else:
            await base.started.get()
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        survivor = asyncio.create_task(model.embed(("shared",)))
        if before_dispatch:
            await base.started.get()
        base.release.set()
        assert (await survivor).vectors == _vectors(("shared",))
        assert base.calls == [("shared",)]

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_all_callers_cancelled_before_provider_failure_leave_retryable_work():
    async def scenario():
        loop = asyncio.get_running_loop()
        errors = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        try:
            base = _ControlledEmbedding()
            base.fail = True
            model = CoalescingEmbeddingModel(base, coalesce_ms=0)
            callers = [asyncio.create_task(model.embed(("bad",))) for _ in range(2)]
            await base.started.get()
            for caller in callers:
                caller.cancel()
            await asyncio.gather(*callers, return_exceptions=True)
            base.release.set()
            await base.finished.get()
            await asyncio.sleep(0)
            gc.collect()
            assert errors == []
            base.fail = False
            assert (await model.embed(("bad",))).vectors == _vectors(("bad",))
            assert len(base.calls) == 2
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_wrong_vector_count_never_partially_populates_cache():
    async def scenario():
        base = _ControlledEmbedding()
        base.invalid = True
        base.release.set()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0)
        with pytest.raises(ValueError):
            await model.embed(("a", "b"))
        base.invalid = False
        assert (await model.embed(("a", "b"))).vectors == _vectors(("a", "b"))
        assert base.calls == [("a", "b"), ("a", "b")]

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_results_survive_lru_eviction_and_duplicate_cache_access_refreshes_lru():
    async def scenario():
        base = _ControlledEmbedding()
        base.release.set()
        model = CoalescingEmbeddingModel(base, max_batch_size=2, coalesce_ms=0, cache_size=2)
        texts = ("a", "b", "c", "d", "a")
        result = await model.embed(texts)
        assert result.vectors == _vectors(texts)
        assert result.usage == InferenceUsage(requests=4, input_tokens=20, output_tokens=0)
        assert (await model.embed(("c", "d", "c"))).usage.requests == 0
        await model.embed(("e",))
        assert (await model.embed(("c",))).usage.requests == 0
        assert (await model.embed(("d",))).usage.requests == 2

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
