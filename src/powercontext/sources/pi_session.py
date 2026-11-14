"""Capture complete Pi v3 branch evidence with durable incremental progress."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Self, cast

from pydantic import BaseModel, Field, JsonValue, model_validator

from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PiSessionInput(BaseModel):
    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _identity(session_id: str, event_id: str) -> str:
    # A structured pair avoids delimiter collisions and bounds even long IDs.
    digest = hashlib.sha256(_canonical([session_id, event_id]).encode("utf-8")).hexdigest()
    return f"pi:{digest}"


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")  # noqa: TRY003


def _read_archive(content: bytes) -> tuple[str, dict[str, dict[str, JsonValue]]]:
    records = []
    for line in io.StringIO(content.decode("utf-8-sig", errors="strict"), newline=None):
        if not line.strip():
            continue
        record = json.loads(line, parse_constant=_reject_constant)
        if not isinstance(record, dict):
            raise ValueError("Pi JSONL records must be objects")  # noqa: TRY003, TRY004
        records.append(record)
    if not records:
        raise ValueError("Pi archive must begin with a session header")  # noqa: TRY003
    header = records[0]
    session_id = header.get("id")
    if (
        header.get("type") != "session"
        or type(header.get("version")) is not int
        or header["version"] != 3
        or not isinstance(session_id, str)
        or not session_id.strip()
    ):
        raise ValueError("Pi archive requires a version 3 session header with an ID")  # noqa: TRY003
    nodes = {}
    for record in records[1:]:
        event_id = record.get("id")
        parent = record.get("parentId")
        if not isinstance(event_id, str) or not event_id.strip() or event_id in nodes:
            raise ValueError("Pi event IDs must be non-empty and unique")  # noqa: TRY003
        if "parentId" not in record or (parent is not None and (not isinstance(parent, str) or parent not in nodes)):
            raise ValueError("Pi parent must be null or an earlier node")  # noqa: TRY003
        if not isinstance(record.get("type"), str) or not record["type"]:
            raise ValueError("Pi nodes must declare an event type")  # noqa: TRY003
        nodes[event_id] = record
    return session_id, nodes


def _visible_text(content: JsonValue, *, tools: bool = False) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if block.get("type") == "text" and isinstance(text, str):
                parts.append(text)
            elif tools and block.get("type") == "toolCall":
                parts.append(
                    f"[tool_call {block.get('name', '')} {block.get('id', '')}] "
                    f"{_canonical(block.get('arguments', {}))}"
                )
    return "\n".join(parts)


def _event(session_id: str, record: dict[str, JsonValue]) -> PiEventInput | None:
    event_type = record["type"]
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("role"), str):
            raise ValueError("Pi message requires a role")  # noqa: TRY003
        role = cast(str, message["role"])
        body = _visible_text(message.get("content"), tools=True)
        if role == "toolResult" and body:
            body = f"[tool_result {message.get('toolName', '')} {message.get('toolCallId', '')}] {body}"
        elif role == "bashExecution":
            command, output = message.get("command", ""), message.get("output", "")
            if not isinstance(command, str) or not isinstance(output, str):
                raise ValueError("Pi bash command and output must be strings")  # noqa: TRY003
            body = f"$ {command}\n{output}" if command or output else ""
    elif event_type in {"compaction", "branch_summary"}:
        role, body = event_type, record.get("summary", "")
        if not isinstance(body, str):
            raise ValueError("Pi summary must be a string")  # noqa: TRY003
    elif event_type == "custom_message":
        role, body = "custom", _visible_text(record.get("content"))
    else:
        return None
    if not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=cast(str, record["id"]),
        parent_id=cast(str | None, record["parentId"]),
        event_type=cast(str, event_type),
        role=cast(str, role),
        content=f"{role}: {body}",
        record=record,
    )


def _branch_events(
    session_id: str, nodes: dict[str, dict[str, JsonValue]], leaf_id: str | None
) -> tuple[PiEventInput, ...]:
    if leaf_id is None:
        leaf_id = next(reversed(nodes), None)
    elif leaf_id not in nodes:
        raise ValueError("Pi leaf does not exist")  # noqa: TRY003
    chain = []
    while leaf_id is not None:
        record = nodes[leaf_id]
        chain.append(record)
        leaf_id = cast(str | None, record["parentId"])
    return tuple(event for record in reversed(chain) if (event := _event(session_id, record)) is not None)


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    session_id, nodes = _read_archive(value.content)
    return _branch_events(session_id, nodes, value.leaf_id)


class PiEventSource(Source, PiEventInput):
    materialization: SourceMaterialization = SourceMaterialization.CAPTURED

    @model_validator(mode="after")
    def validate_capture(self) -> Self:
        if self.materialization is not SourceMaterialization.CAPTURED or self.name != _identity(
            self.session_id, self.event_id
        ):
            raise ValueError("Pi Source must capture the exact event identity")  # noqa: TRY003
        return self


class _PiEventProjection:
    name: str = TEXT_EVIDENCE_PROJECTION_KEY.name
    version: str = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[PiEventSource] = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


class _PiEventDefinition:
    name: str = "pi-event"
    version: str = "1"
    input_class: type[PiEventInput] = PiEventInput
    source_class: type[PiEventSource] = PiEventSource
    projections: tuple[SourceProjection[PiEventSource], ...] = (_PiEventProjection(),)

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        return PiEventSource(name=_identity(value.session_id, value.event_id), **value.model_dump())

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput.model_validate(source.model_dump(include=set(PiEventInput.model_fields)))


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = _PiEventDefinition()


class _Progress(BaseModel):
    # Retain separate sessions if a binding's file is rotated to another session.
    sessions: dict[str, dict[str, str]] = Field(default_factory=dict)


class PiSessionConnector:
    name: str = "pi-session-history"
    version: str = "1"
    source_definitions: frozenset[str] = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        session_id, nodes = _read_archive(self.path.read_bytes())
        events = _branch_events(session_id, nodes, self.leaf_id)
        progress = _Progress.model_validate(session.checkpoint) if session.checkpoint is not None else _Progress()
        previous = progress.sessions.get(session_id, {})
        # Check every previously imported event, even on an unselected branch,
        # before submitting anything or proposing a checkpoint.
        for event_id, fingerprint in previous.items():
            if event_id not in nodes or _fingerprint(nodes[event_id]) != fingerprint:
                raise ValueError("Previously imported Pi event was changed or removed")  # noqa: TRY003
        imported = dict(previous)
        for event in events:
            if event.event_id not in previous:
                await session.submit(_identity(session_id, event.event_id), "pi-event", event)
                imported[event.event_id] = _fingerprint(event.record)
        progress.sessions[session_id] = imported
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=progress.model_dump(mode="json"))


def _fingerprint(record: dict[str, JsonValue]) -> str:
    return hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
]
