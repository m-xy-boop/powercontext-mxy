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

"""Durable Source dependency graphs and native Memory eligibility propagation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime


class SourceLifecycleError(ValueError):
    """A lifecycle identity, dependency, event, or resolution is invalid."""


class SourceLifecycleEvent(BaseModel):
    """One strictly typed, replayable change to a registered Source."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    node_id: str
    revision: int = Field(gt=0)
    sequence: int = Field(gt=0)
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must contain non-whitespace text")  # noqa: TRY003
        return value


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    kind: Literal["source", "memory", "topic"]
    reference: SourceRef | MemoryCitation | ArtifactRef
    parents: tuple[str, ...] = ()
    policy: Literal["all", "any"] = "all"
    revision: int = 1
    available: bool = True
    event: SourceLifecycleEvent | None = None


def _node_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError("invalid-node-id")
    return value


def _propagate(nodes: dict[str, _Node]) -> dict[str, _Node]:
    """Evaluate the DAG without depending on lexical or database row order."""

    resolved: dict[str, _Node] = {}
    visiting: set[str] = set()

    def visit(node_id: str) -> _Node:
        if node_id in resolved:
            return resolved[node_id]
        if node_id in visiting or node_id not in nodes:
            raise SourceLifecycleError("invalid-dependency-graph")
        visiting.add(node_id)
        node = nodes[node_id]
        if node.kind != "source":
            support = tuple(visit(parent).available for parent in node.parents)
            node = node.model_copy(update={"available": all(support) if node.policy == "all" else any(support)})
        visiting.remove(node_id)
        resolved[node_id] = node
        return node

    for node_id in nodes:
        visit(node_id)
    return resolved


class ScopedSourceLifecycle:
    """Own one scope's immutable bindings and replayable eligibility state."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase, scope_id: str) -> None:
        self._runtime = runtime
        self._database = database
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int = 1) -> None:
        if not isinstance(source_ref, SourceRef) or type(revision) is not int or revision < 1:
            raise SourceLifecycleError("invalid-source-binding")
        await self._register(_Node(node_id=_node_id(node_id), kind="source", reference=source_ref, revision=revision))

    async def register_memory(
        self, node_id: str, citation: MemoryCitation, *, parents: tuple[str, ...], policy: str = "all"
    ) -> None:
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("invalid-memory-binding")
        await self._register(self._derived(node_id, "memory", citation, parents, policy))

    async def register_topic(
        self, node_id: str, artifact_ref: ArtifactRef, *, parents: tuple[str, ...], policy: str = "all"
    ) -> None:
        if not isinstance(artifact_ref, ArtifactRef) or artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("invalid-topic-binding")
        await self._register(self._derived(node_id, "topic", artifact_ref, parents, policy))

    @staticmethod
    def _derived(
        node_id: str,
        kind: Literal["memory", "topic"],
        reference: MemoryCitation | ArtifactRef,
        parents: tuple[str, ...],
        policy: str,
    ) -> _Node:
        node_id = _node_id(node_id)
        if not isinstance(policy, str) or policy not in {"all", "any"}:
            raise SourceLifecycleError("invalid-dependencies")
        if not isinstance(parents, (tuple, list)) or not parents:
            raise SourceLifecycleError("invalid-dependencies")
        checked = tuple(_node_id(parent) for parent in parents)
        if node_id in checked or len(set(checked)) != len(checked):
            raise SourceLifecycleError("invalid-dependencies")
        return _Node(node_id=node_id, kind=kind, reference=reference, parents=tuple(sorted(checked)), policy=policy)

    async def _register(self, node: _Node) -> None:
        async with self._runtime._context(self.scope_id), self._runtime._locked(self.scope_id):
            await self._validate_reference(node)
            previous = await self._load()
            existing = previous.get(node.node_id)
            if existing is not None:
                if existing.model_copy(update={"available": True, "event": None}) != node:
                    raise SourceLifecycleError("conflicting-binding")
                nodes = previous
            else:
                if any(parent not in previous for parent in node.parents):
                    raise SourceLifecycleError("missing-parent")
                nodes = _propagate({**previous, node.node_id: node})
                await self._save(previous, nodes)
            await self._project_memory(nodes)

    async def _validate_reference(self, node: _Node) -> None:
        try:
            ref = node.reference
            if isinstance(ref, SourceRef):
                await self._runtime.records.for_scope(self.scope_id).get_source(ref.source_type, ref.source_id)
            elif isinstance(ref, MemoryCitation):
                await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=ref))
            else:
                await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                    ref.family, ref.artifact_id, ref.revision
                )
        except Exception as error:
            raise SourceLifecycleError("unreadable-reference") from error

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("invalid-event")
        async with self._runtime._context(self.scope_id), self._runtime._locked(self.scope_id):
            previous = await self._load()
            source = previous.get(event.node_id)
            if source is None or source.kind != "source" or event.revision != source.revision:
                raise SourceLifecycleError("unknown-source-or-revision")
            if source.event is not None and event.sequence <= source.event.sequence:
                if event != source.event:
                    raise SourceLifecycleError("stale-or-conflicting-event")
                nodes = previous
            else:
                changed = source.model_copy(update={"available": event.available, "event": event})
                nodes = _propagate({**previous, event.node_id: changed})
                # Persist accepted work before projection. A crash or native write
                # failure leaves the same event replayable, never falsely finished.
                await self._save(previous, nodes)
            await self._project_memory(nodes)
            return sorted(node_id for node_id, node in nodes.items() if node.available)

    async def resolve(self, node_id: str, *, purpose: str = "generation") -> SourceRef | MemoryCitation | ArtifactRef:
        node_id = _node_id(node_id)
        if not isinstance(purpose, str) or purpose not in {"generation", "audit"}:
            raise SourceLifecycleError("unknown-purpose")
        async with self._runtime._context(self.scope_id):
            node = (await self._load()).get(node_id)
            if node is None or (purpose == "generation" and not node.available):
                raise SourceLifecycleError("unavailable-or-unknown-node")
            return node.reference

    async def _load(self) -> dict[str, _Node]:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        async with self._database.transaction() as connection:
            payloads = await connection.scalars(select(table.c.payload).where(table.c.scope_id == self.scope_id))
            nodes = [
                load_model(_Node, stored_bytes(payload, column="payload"), kind="source-lifecycle", name=self.scope_id)
                for payload in payloads
            ]
        return {node.node_id: node for node in nodes}

    async def _save(self, previous: dict[str, _Node], nodes: dict[str, _Node]) -> None:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        async with self._database.transaction() as connection:
            for node_id, node in nodes.items():
                if node == previous.get(node_id):
                    continue
                ref = node.reference
                values = {
                    "kind": node.kind,
                    "available": node.available,
                    "artifact_id": ref.artifact_id if isinstance(ref, ArtifactRef) else None,
                    "artifact_revision": ref.revision if isinstance(ref, ArtifactRef) else None,
                    "payload": dump_model(node, kind="source-lifecycle", name=node_id),
                }
                statement = (
                    update(table).where(table.c.scope_id == self.scope_id, table.c.node_id == node_id).values(**values)
                    if node_id in previous
                    else insert(table).values(scope_id=self.scope_id, node_id=node_id, **values)
                )
                await connection.execute(statement)

    async def _project_memory(self, nodes: dict[str, _Node]) -> None:
        bindings = [node for node in nodes.values() if node.kind == "memory"]
        if not bindings:
            return
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            current = await service.head(context.artifacts.memory_artifact_id)
            entries = {entry.entry_id: entry for entry in await service.entries(current)}
            states = {entry.entry_id: entry.state for entry in current.content.manifest.entries}
            targets: dict[str, bool] = {}
            for node in bindings:
                ref = node.reference
                if not isinstance(ref, MemoryCitation):
                    raise SourceLifecycleError("invalid-memory-binding")
                entry = entries.get(ref.entry_id)
                if entry is None or entry.entry_version_id != ref.entry_version_id:
                    raise SourceLifecycleError("bound-memory-version-changed")
                targets[ref.entry_id] = targets.get(ref.entry_id, True) and node.available
            inactive = tuple(entries[key] for key, active in targets.items() if not active and states[key] == "active")
            active = tuple(entries[key] for key, enabled in targets.items() if enabled and states[key] == "inactive")
            if inactive:
                current = await service.forget(current, entries=inactive, reason="source lifecycle")
            if active:
                await service.reactivate(current, entries=active, reason="source lifecycle")


class SourceLifecycleApplication:
    """Select lifecycle services backed by the Runtime's own database."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase) -> None:
        self._runtime = runtime
        self._database = database

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, self._database, scope_id)
