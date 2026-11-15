"""Wire tampering, UTF-8 boundaries and stateless context reconstruction."""

import gzip
import json

import pytest

from powercontext.builtin.runtime.context_pack import (
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)


@pytest.mark.parametrize("chunk_bytes", [1, 2, 3, 4, 7, 31, 512])
def test_chunking_is_bounded_deterministic_and_preserves_unicode(chunk_bytes):
    text = "alpha\n海洋🙂e\u0301\r\n" * 30
    if chunk_bytes < 4:
        with pytest.raises(ContextPackError):
            ContextPack.from_content(text, chunk_bytes=chunk_bytes)
        return
    pack = ContextPack.from_content(text, chunk_bytes=chunk_bytes)
    assert pack == ContextPack.from_content(text.encode(), chunk_bytes=chunk_bytes)
    assert b"".join(pack.items) == text.encode()
    assert all(0 < len(item) <= chunk_bytes and item.decode("utf-8") for item in pack.items)
    assert ContextPack.from_bytes(pack.to_bytes()) == pack


@pytest.mark.parametrize("chunk_bytes", [True, 0, -1, 1.5])
def test_invalid_chunk_limits_fail(chunk_bytes):
    with pytest.raises(ContextPackError):
        ContextPack.from_content("", chunk_bytes=chunk_bytes)


@pytest.mark.parametrize("raw", [b"\xff", b"{}", b"null", b"[]", b'{"schema":1,"schema":2}', b"{bad"])
def test_invalid_wire_fails(raw):
    for decoder in (ContextPack.from_bytes, ContextPackDelta.from_bytes):
        with pytest.raises(ContextPackError):
            decoder(raw)


@pytest.mark.parametrize("items", [["!!!!"], ["/w=="], [1], ["Zh=="], "YWJj"])
def test_bad_base64_or_utf8_fails(items):
    pack = ContextPack.build((b"abc",))
    payload = json.loads(pack.to_bytes())
    payload["items"] = items
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload).encode())


@pytest.mark.parametrize("prefix", [True, 0.0, -1, "0", None])
def test_delta_prefix_type_is_strict(prefix):
    empty = ContextPack.build(())
    with pytest.raises(ContextPackError):
        ContextPackDelta(empty.digest, empty.digest, prefix, ())


def test_wire_size_fixed_point_and_arbitrary_valid_prefix():
    for length in (0, 1, 500, 700, 7000, 75000):
        base = ContextPack.from_content("x" * length)
        assert base.byte_count == len(base.to_bytes())
        target = ContextPack.from_content("x" * length + "\n海洋")
        delta = ContextPackDelta(base.digest, target.digest, 0, target.items)
        assert (
            apply_context_pack_delta(
                ContextPack.from_bytes(base.to_bytes()), ContextPackDelta.from_bytes(delta.to_bytes())
            )
            == target
        )


def test_changed_chunk_boundaries_authenticate_as_well_as_content():
    base = ContextPack.build((b"ab", b"cd"))
    target = ContextPack.build((b"a", b"bcd"))
    assert base.digest != target.digest
    assert apply_context_pack_delta(base, ContextPackDelta.between(base, target)) == target
    tampered = ContextPackDelta(base.digest, target.digest, 2, ())
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, tampered)


def test_net_cost_includes_first_pack_and_all_wire_overhead():
    # Constructed protocol workload, independent of any meeting or turn number.
    base = None
    raw_bytes = incremental_bytes = full_bytes = gzip_bytes = 0
    contents = set()
    for round_number in range(40):
        text = "\n".join(
            f"Topic {index}: " + "Evidence remains intact. " * 20 for index in range(8 + round_number // 10)
        )
        contents.add(text)
        target = ContextPack.from_content(text)
        transmitted = target if base is None else ContextPackDelta.between(base, target)
        wire = transmitted.to_bytes()
        base = (
            ContextPack.from_bytes(wire)
            if base is None
            else apply_context_pack_delta(base, ContextPackDelta.from_bytes(wire))
        )
        assert b"".join(base.items) == text.encode()
        raw_bytes += len(text.encode())
        incremental_bytes += len(wire)
        full_bytes += len(target.to_bytes())
        gzip_bytes += len(gzip.compress(text.encode(), compresslevel=9, mtime=0))
    assert len(contents) == 4
    assert 1 - incremental_bytes / raw_bytes >= 0.45
    print({
        "raw_full_bytes": raw_bytes,
        "incremental_bytes": incremental_bytes,
        "full_pack_bytes": full_bytes,
        "gzip_bytes": gzip_bytes,
        "raw_saving": 1 - incremental_bytes / raw_bytes,
        "pack_saving": 1 - incremental_bytes / full_bytes,
        "gzip_saving": 1 - incremental_bytes / gzip_bytes,
    })


def test_runtime_forty_rounds_empty_query_switch_and_append(tmp_path):
    import asyncio

    from powercontext.builtin.artifacts.memory import MemoryEntryInput
    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, PrepareContextRequest, open_builtin_runtime
    from powercontext.builtin.scope import ScopeDraft

    class Pipeline:
        async def extract(self, request, /):
            return tuple(
                MemoryEntryInput(kind="turn", text=source.content, sources=(source,)) for source in request.sources
            )

    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'incremental.db'}"))
        async with open_builtin_runtime(config, candidate_pipeline=Pipeline()) as runtime:
            scope = await runtime.scopes.create(ScopeDraft(title="Test", summary="Test", idempotency_key="pack"))
            context = runtime.context.for_scope(scope.scope_id)
            request = PrepareContextRequest(query="alpha", max_bytes=8000)
            empty = await context.prepare_incremental(request)
            assert empty.items == ()
            sources = runtime.sources.for_scope(scope.scope_id)
            memory = runtime.memory.for_scope(scope.scope_id)
            for index in range(8):
                await sources.capture(
                    CaptureSource(
                        source_id=f"turn-{index}",
                        content=f"Participant {index}: alpha beta meeting discussion. "
                        + "The team discussed the project design and planned the next steps. " * 5,
                        metadata={},
                    )
                )
            while (await memory.flush()).processed:
                pass
            base = None
            raw_bytes = incremental_bytes = full_bytes = gzip_bytes = 0
            distinct = set()
            for index in range(40):
                if index in (10, 20, 30):
                    await sources.capture(
                        CaptureSource(
                            source_id=f"append-{index}",
                            content=f"New alpha beta meeting turn {index}. " + "Additional project evidence. " * 10,
                            metadata={},
                        )
                    )
                    while (await memory.flush()).processed:
                        pass
                request = PrepareContextRequest(query="alpha" if index < 20 else "beta", max_bytes=8000)
                expected = await context.prepare(request)
                wire = (await context.prepare_incremental(request, base_pack=base)).to_bytes()
                base = (
                    ContextPack.from_bytes(wire)
                    if base is None
                    else apply_context_pack_delta(base, ContextPackDelta.from_bytes(wire))
                )
                content = (expected.content or "").encode()
                assert content and b"".join(base.items) == content
                distinct.add(content)
                raw_bytes += len(content)
                incremental_bytes += len(wire)
                full_bytes += len(base.to_bytes())
                gzip_bytes += len(gzip.compress(content, compresslevel=9, mtime=0))
            assert len(distinct) >= 4
            assert 1 - incremental_bytes / raw_bytes >= 0.45
            print({
                "runtime_raw_bytes": raw_bytes,
                "runtime_incremental_bytes": incremental_bytes,
                "runtime_full_pack_bytes": full_bytes,
                "runtime_gzip_bytes": gzip_bytes,
                "raw_saving": 1 - incremental_bytes / raw_bytes,
                "pack_saving": 1 - incremental_bytes / full_bytes,
                "gzip_saving": 1 - incremental_bytes / gzip_bytes,
            })

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field,value", [("byte_count", True), ("byte_count", 0), ("digest", "sha256:" + "0" * 64), ("extra", 1)]
)
def test_pack_fields_and_self_inclusive_count_are_authenticated(field, value):
    payload = json.loads(ContextPack.from_content("abc").to_bytes())
    payload[field] = value
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def test_every_valid_prefix_reconstructs_the_exact_chunk_sequence():
    base = ContextPack.build((b"a", b"b", b"c", b"old"))
    target = ContextPack.build((b"a", b"b", b"c", b"new"))
    for prefix in range(4):
        delta = ContextPackDelta(base.digest, target.digest, prefix, target.items[prefix:])
        assert (
            apply_context_pack_delta(
                ContextPack.from_bytes(base.to_bytes()), ContextPackDelta.from_bytes(delta.to_bytes())
            )
            == target
        )
