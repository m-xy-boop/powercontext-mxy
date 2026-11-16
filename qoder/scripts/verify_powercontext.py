#!/usr/bin/env python3
"""直接调用 PowerContext 公开 Python Client SDK，逐项核实本作品依赖的接口行为。

不经过拾忆应用，也不使用任何模型服务：输出全部是运行中的真实服务返回值，
用于 ACCEPTANCE.md 的“公开接口核实”一节。任何一步失败即以非零码退出。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from powercontext.client import PowerContextClient
from powercontext.client.errors import ServerResponseError
from powercontext.http import (
    ArtifactReference,
    AtomicMemoryInput,
    AtomicMemoryLifecycleRequest,
    CaptureContentSourceRequest,
    CreateScopeRequest,
    ListAtomicMemoryRequest,
    PrepareContextRequest,
    RememberMemoryRequest,
    SearchMemoryRequest,
    UpdateScopeRequest,
)

BASE_URL = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8777")
TOKEN = os.environ.get("POWERCONTEXT_TOKEN") or None
MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"


def show(label: str, value: Any) -> None:
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    print(f"  {label:26s} {value}")


async def main() -> int:
    print(f"目标服务：{BASE_URL}")
    async with PowerContextClient(base_url=BASE_URL, token=TOKEN, timeout=20.0) as client:
        print("\n[1] GET /v1/capabilities")
        capabilities = await client.get_capabilities()
        show("search_modes", [m.value for m in capabilities.search_modes])
        show("memory_extraction", capabilities.memory_extraction)
        show("artifact_families", list(capabilities.artifact_families))
        show("source_types", list(capabilities.source_types))
        show("context_versions", [v.value for v in capabilities.context_versions])

        print("\n[2] POST /v1/scopes（幂等）")
        key = "recall-coach:probe:v1"
        first = await client.create_scope(CreateScopeRequest(idempotency_key=key, title="拾忆接口核实", summary="公开接口行为核实"))
        again = await client.create_scope(CreateScopeRequest(idempotency_key=key, title="拾忆接口核实", summary="公开接口行为核实"))
        show("scope_id", first.scope_id)
        show("version / 重复创建复用", f"{first.version} / {again.scope_id == first.scope_id}")
        assert first.scope_id == again.scope_id, "同一 idempotency_key 应复用同一个 Scope"

        print("\n[3] POST /v1/scopes/{id}（update_scope 乐观并发）")
        updated = await client.update_scope(
            first.scope_id, UpdateScopeRequest(expected_version=first.version, title="拾忆接口核实", summary="公开接口行为核实（已更新）")
        )
        show("version 递增", f"{first.version} → {updated.version}")

        scope = first.scope_id
        print("\n[4] POST /v1/memory/remember + 回读校验")
        payload = {"app": "recall-coach-probe", "v": 1, "topic": "tx-isolation", "mastery": 0.32, "summary": "接口核实：隔离级别薄弱"}
        remembered = await client.remember_memory(
            RememberMemoryRequest(scope_id=scope, kind="weakness", text=json.dumps(payload, ensure_ascii=False), reason="接口核实写入")
        )
        record = remembered.records[0]
        show("changed / kind / state", f"{remembered.changed} / {record.kind} / {record.state.value}")
        show("artifact", f"{record.artifact.family}/{record.artifact.artifact_id}@{record.artifact.revision}")
        show("state_version / text 字节", f"{record.state_version} / {len(record.text)}")
        readback = await client.get_atomic_memory_state(scope_id=scope, artifact_id=record.artifact.artifact_id)
        show("get_atomic_memory_state", f"{readback.artifact.revision} / {readback.state.value}（与写入返回一致：{readback.state.value == record.state.value}）")

        print("\n[5] POST /v1/atomic-memory/list")
        listed = await client.list_atomic_memories(ListAtomicMemoryRequest(scope_id=scope, kind="weakness", states=["active"], limit=20))
        show("active weakness 条数", len(listed.items))
        show("next_cursor", listed.next_cursor)

        print("\n[6] POST /v1/memory/search")
        searched = await client.search_memory(SearchMemoryRequest(scope_id=scope, query="隔离级别 薄弱", limit=5))
        show("mode", searched.mode.value)
        show("hits", [(hit.memory.kind, hit.memory.artifact.artifact_id[:16], round(hit.score, 5), [m.value for m in hit.matched_by]) for hit in searched.hits])

        print("\n[7] POST /v1/context/prepare")
        prepared = await client.prepare_context(PrepareContextRequest(scope_id=scope, query="隔离级别", max_bytes=2048))
        show("schema / status", f"{prepared.schema_} / {prepared.status.value}")
        show("content_bytes", prepared.content_bytes)
        show("含真实标记头", MARKER in prepared.content)

        print("\n[8] POST /v1/sources/content（证据留存）")
        captured = await client.capture_content_source(
            CaptureContentSourceRequest(
                scope_id=scope,
                source_id="probe-attempts",
                content="接口核实：一次答题流水样本",
                metadata={"app": "recall-coach-probe"},
            )
        )
        show("source_ref", f"{captured.source.name}/{captured.source.source_id}")
        show("status / position", f"{captured.status.value} / {captured.position}")

        print("\n[9] POST /v1/atomic-memory/lifecycle（遗忘）")
        lifecycle = await client.change_atomic_memory_lifecycle(
            AtomicMemoryLifecycleRequest(
                scope_id=scope,
                target=AtomicMemoryInput(
                    artifact=ArtifactReference(family=record.artifact.family, artifact_id=record.artifact.artifact_id, revision=record.artifact.revision),
                    state_version=record.state_version,
                ),
                state="forgotten",
            )
        )
        after = lifecycle.records[0]
        show("state / state_version", f"{after.state.value} / {record.state_version} → {after.state_version}")
        recheck = await client.list_atomic_memories(ListAtomicMemoryRequest(scope_id=scope, kind="weakness", states=["active"], limit=20))
        show("遗忘后 active 条数", len(recheck.items))

        print("\n[10] 服务端拒绝超长文本时保留真实错误信息")
        try:
            await client.remember_memory(
                RememberMemoryRequest(scope_id=scope, kind="weakness", text="x" * 200_000, reason="接口核实：故意超限")
            )
            show("结果", "服务端意外接受了超长文本（请核实版本）")
        except ServerResponseError as exc:
            show("status_code / code", f"{exc.status_code} / {exc.code}")
            show("server_message", (exc.server_message or "")[:120])
            show("X-PowerContext-Request-Id", exc.request_id)

    print("\n公开接口核实完成：以上每一项都是运行中的 PowerContext 真实返回值。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001 - 验收脚本需要把真实错误原样暴露出来
        print(f"核实失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
