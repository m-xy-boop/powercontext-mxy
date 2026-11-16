"""PowerContext 真实读写封装。

只使用 powercontext 1.5.1 公开 Python Client SDK（powercontext.client.PowerContextClient
与 powercontext.http 请求模型），对应公开 HTTP 契约见 reference/ 下留存的 openapi.json。
任何失败都抛出异常并向上传递真实错误码与 request id，绝不返回模拟数据。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from powercontext.client import PowerContextClient
from powercontext.client.errors import ClientError, ServerResponseError, TransportError
from powercontext.http import (
    ArtifactReference,
    UpdateScopeRequest,
    AtomicMemoryInput,
    AtomicMemoryLifecycleRequest,
    CaptureContentSourceRequest,
    CreateScopeRequest,
    ListAtomicMemoryRequest,
    PrepareContextRequest,
    RememberMemoryRequest,
    SearchMemoryRequest,
)
import httpx

from app.config import Settings

MEMORY_FAMILIES = ("weakness", "progress", "preference", "plan")
ALL_STATES = ["active", "forgotten", "merged", "retired"]


class MemoryBackendError(RuntimeError):
    """PowerContext 操作失败的统一表示，携带真实诊断信息。

    notice / written_receipts / pending_slots 由调用方按操作语义填写：
    批量写入可能只成功了一部分，提示必须如实说明哪些已经落库，不能一概说“没有写入”。
    """

    def __init__(
        self,
        *,
        operation: str,
        code: str,
        message: str,
        status_code: int | None = None,
        request_id: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(f"[{operation}] {code}: {message}")
        self.operation = operation
        self.code = code
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.details = details
        self.notice: str | None = None
        self.written_receipts: list[dict[str, Any]] = []
        self.pending_slots: list[str] = []

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "operation": self.operation,
            "status_code": self.status_code,
            "request_id": self.request_id,
            "details": self.details,
        }


# 只读操作：失败时页面只是没读到内容，不涉及记忆增删
READ_ONLY_OPERATIONS = {
    "get_capabilities",
    "health_ready",
    "list_atomic_memories",
    "search_memory",
    "prepare_context",
    "get_atomic_memory_state",
}


def accuracy_notice(exc: MemoryBackendError) -> str:
    """按操作与失败阶段给出如实提示，绝不把部分成功说成完全没写。"""
    if exc.notice:
        return exc.notice
    if exc.operation in READ_ONLY_OPERATIONS:
        return "本次没有读到 PowerContext 记忆，页面对应区域显示读取失败，不会用模拟数据填充。"
    if exc.code == "powercontext_unavailable":
        return "请求未能送达 PowerContext Server，本次没有产生任何记忆写入；服务恢复后重试即可。"
    if exc.status_code is not None and 400 <= exc.status_code < 500:
        return "PowerContext Server 拒绝了本次写入（请求不符合公开契约），已有记忆没有被改变。"
    if exc.operation == "remember_memory":
        return "本次记忆写入未获确认（服务错误或写入后回读不一致），页面不会显示假回执；重试同一操作不会重复累计。"
    return "本次操作未确认完成，请以“操作留痕”中的真实记录为准；重试同一操作不会重复累计。"


class MemoryUnavailable(MemoryBackendError):
    """服务不可达：调用方必须报错，不得降级为假记忆。"""


class MemoryRejected(MemoryBackendError):
    """服务明确拒绝（4xx）：契约或权限问题。"""


class MemoryFailed(MemoryBackendError):
    """服务内部错误（5xx）或响应不符合公开契约。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _val(value: Any) -> Any:
    return getattr(value, "value", value)


class PowerContextMemory:
    def __init__(self, settings: Settings, client: PowerContextClient | None = None) -> None:
        self._settings = settings
        self._client = client or PowerContextClient(
            base_url=settings.powercontext_url,
            token=settings.powercontext_token,
            timeout=settings.request_timeout,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def base_url(self) -> str:
        return self._settings.powercontext_url

    # ------------------------------------------------------------------ 诊断
    async def probe(self) -> dict[str, Any]:
        """读取真实健康状态与能力，用于页面展示与验收记录。"""
        capabilities = await self._call("get_capabilities", lambda: self._client.get_capabilities())
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=5.0) as health_client:
                response = await health_client.get("/health/ready")
                health = response.json() if response.status_code == 200 else {"status": f"http_{response.status_code}"}
        except httpx.HTTPError as exc:
            raise MemoryUnavailable(
                operation="health_ready",
                code="powercontext_unavailable",
                message=f"无法连接 PowerContext Server（{self.base_url}），请确认服务已启动。",
                details=str(exc),
            ) from exc
        return {
            "base_url": self.base_url,
            "health": health,
            "ready": health.get("status") == "ready",
            "search_modes": [_val(m) for m in capabilities.search_modes],
            "memory_extraction": capabilities.memory_extraction,
            "artifact_families": list(capabilities.artifact_families),
            "source_types": list(capabilities.source_types),
            "context_versions": [_val(v) for v in capabilities.context_versions],
        }

    # ------------------------------------------------------------------ 身份
    async def ensure_scope(self, *, idempotency_key: str, title: str, summary: str) -> dict[str, Any]:
        started = time.perf_counter()
        scope = await self._call(
            "create_scope",
            lambda: self._client.create_scope(
                CreateScopeRequest(idempotency_key=idempotency_key, title=title, summary=summary)
            ),
        )
        action = "create_scope(idempotent)"
        if scope.title != title or scope.summary != summary:
            # 同一 idempotency_key 复用同一个 Scope；命名漂移时用公开接口 update_scope 对齐
            scope = await self._call(
                "update_scope",
                lambda: self._client.update_scope(
                    scope.scope_id,
                    UpdateScopeRequest(expected_version=scope.version, title=title, summary=summary),
                ),
            )
            action = "update_scope"
        self._log(
            "ensure_scope",
            scope_id=scope.scope_id,
            idempotency_key=idempotency_key,
            action=action,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return {"scope_id": scope.scope_id, "title": scope.title, "version": scope.version}

    # ------------------------------------------------------------------ 写入
    async def remember(self, *, scope_id: str, kind: str, payload: dict[str, Any], reason: str) -> dict[str, Any]:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        started = time.perf_counter()
        response = await self._call(
            "remember_memory",
            lambda: self._client.remember_memory(
                RememberMemoryRequest(scope_id=scope_id, kind=kind, text=text, reason=reason)
            ),
        )
        if not response.records:
            raise MemoryFailed(
                operation="remember_memory",
                code="empty_mutation_response",
                message="PowerContext 返回成功但没有记忆记录，无法确认写入。",
            )
        record = response.records[0]
        ref = record.artifact
        readback = await self._call(
            "get_atomic_memory_state",
            lambda: self._client.get_atomic_memory_state(scope_id=scope_id, artifact_id=ref.artifact_id),
        )
        if readback.artifact.revision != ref.revision or readback.state.value != record.state.value:
            raise MemoryFailed(
                operation="remember_memory",
                code="readback_mismatch",
                message="写入后回读状态与返回结果不一致。",
                details={"expected": record.model_dump(mode="json"), "actual": readback.model_dump(mode="json")},
            )
        receipt = {
            "kind": kind,
            "changed": response.changed,
            "artifact": {
                "family": ref.family,
                "artifact_id": ref.artifact_id,
                "revision": ref.revision,
            },
            "state": record.state.value,
            "state_version": record.state_version,
            "text_bytes": len(text.encode("utf-8")),
            "verified_by": "get_atomic_memory_state",
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        self._log("remember_memory", scope_id=scope_id, **receipt)
        return receipt

    async def capture_source(self, *, scope_id: str, source_id: str, content: str, metadata: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        response = await self._call(
            "capture_content_source",
            lambda: self._client.capture_content_source(
                CaptureContentSourceRequest(scope_id=scope_id, source_id=source_id, content=content, metadata=metadata)
            ),
        )
        source = response.source
        receipt = {
            "status": _val(response.status),
            "source_ref": f"{source.name}/{source.source_id}",
            "position": response.position,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        self._log("capture_content_source", scope_id=scope_id, **receipt)
        return receipt

    # ------------------------------------------------------------------ 读取
    async def list_memories(self, *, scope_id: str, kind: str | None = None, states: list[str] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            request = ListAtomicMemoryRequest(
                scope_id=scope_id,
                kind=kind,
                states=states or ["active"],
                limit=100,
                cursor=cursor,
            )
            response = await self._call("list_atomic_memories", lambda: self._client.list_atomic_memories(request))
            for record in response.items:
                items.append(self._record_to_dict(record))
            cursor = response.next_cursor
            if not cursor:
                break
        return items

    async def search(self, *, scope_id: str, query: str, limit: int = 8) -> dict[str, Any]:
        started = time.perf_counter()
        response = await self._call(
            "search_memory",
            lambda: self._client.search_memory(SearchMemoryRequest(scope_id=scope_id, query=query, limit=limit)),
        )
        hits = []
        for hit in response.hits:
            item = self._record_to_dict(hit.memory)
            item["score"] = hit.score
            item["matched_by"] = [_val(m) for m in hit.matched_by]
            hits.append(item)
        result = {
            "mode": response.mode.value,
            "query": query,
            "hits": hits,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        self._log("search_memory", scope_id=scope_id, mode=result["mode"], query=query, hits=len(hits))
        return result

    async def prepare(self, *, scope_id: str, query: str, max_bytes: int = 4096) -> dict[str, Any]:
        started = time.perf_counter()
        prepared = await self._call(
            "prepare_context",
            lambda: self._client.prepare_context(
                PrepareContextRequest(scope_id=scope_id, query=query, max_bytes=max_bytes)
            ),
        )
        result = {
            "schema": prepared.schema_,
            "status": prepared.status.value,
            "content": prepared.content,
            "content_bytes": prepared.content_bytes,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        self._log("prepare_context", scope_id=scope_id, status=result["status"], content_bytes=result["content_bytes"])
        return result

    # ------------------------------------------------------------------ 遗忘
    async def forget(self, *, scope_id: str, artifact_id: str, family: str, revision: int, state_version: int) -> dict[str, Any]:
        started = time.perf_counter()
        target = AtomicMemoryInput(
            artifact=ArtifactReference(family=family, artifact_id=artifact_id, revision=revision),
            state_version=state_version,
        )
        response = await self._call(
            "change_atomic_memory_lifecycle",
            lambda: self._client.change_atomic_memory_lifecycle(
                AtomicMemoryLifecycleRequest(scope_id=scope_id, target=target, state="forgotten")
            ),
        )
        record = response.records[0] if response.records else None
        receipt = {
            "artifact_id": artifact_id,
            "changed": response.changed,
            "state": record.state.value if record else None,
            "state_version": record.state_version if record else None,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        self._log("forget_atomic_memory", scope_id=scope_id, **receipt)
        return receipt

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _record_to_dict(record: Any) -> dict[str, Any]:
        payload: Any = None
        parse_error: str | None = None
        try:
            payload = json.loads(record.text)
        except (ValueError, TypeError) as exc:  # 允许历史/手工条目不是 JSON
            parse_error = str(exc)
        ref = record.artifact
        return {
            "kind": record.kind,
            "text": record.text,
            "payload": payload,
            "parse_error": parse_error,
            "state": record.state.value,
            "state_version": record.state_version,
            "artifact": {
                "family": ref.family,
                "artifact_id": ref.artifact_id,
                "revision": ref.revision,
            },
        }

    async def _call(self, operation: str, func: Any) -> Any:
        try:
            return await func()
        except TransportError as exc:
            raise MemoryUnavailable(
                operation=operation,
                code="powercontext_unavailable",
                message=f"无法连接 PowerContext Server（{self.base_url}），请确认服务已启动。",
                details=str(exc),
            ) from exc
        except ServerResponseError as exc:
            status = exc.status_code
            klass = MemoryRejected if 400 <= status < 500 else MemoryFailed
            raise klass(
                operation=operation,
                code=exc.code or f"http_{status}",
                message=exc.server_message or "PowerContext Server 拒绝了本次请求。",
                status_code=status,
                request_id=exc.request_id,
                details=exc.details,
            ) from exc
        except ClientError as exc:
            raise MemoryFailed(
                operation=operation,
                code="powercontext_client_error",
                message=str(exc),
            ) from exc

    def _log(self, operation: str, **fields: Any) -> None:
        entry = {"at": _now(), "operation": operation, "backend": self.base_url, **fields}
        try:
            with self._settings.operation_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # 运行日志失败不应伪装成记忆写入失败
