"""Original Pi evidence and transactional connector progress."""

import asyncio
import json

import pytest

from powercontext.sources import ConnectorBinding, SourceDefinitionRegistry
from powercontext.sources.connectors import ConnectorLifecycle
from powercontext.sources.models import SourceRef
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiSessionConnector,
    PiSessionInput,
    parse_pi_session,
)


def archive(records, session_id="session"):
    return "\n".join(
        json.dumps(record, ensure_ascii=False)
        for record in [{"type": "session", "version": 3, "id": session_id}, *records]
    ).encode()


def message(event_id, parent, text):
    return {"type": "message", "id": event_id, "parentId": parent, "message": {"role": "user", "content": text}}


@pytest.mark.parametrize(
    "record",
    [
        {"id": "a", "type": "model_change"},
        {"id": "a", "type": "model_change", "parentId": "later"},
        {"id": "a", "type": "model_change", "parentId": []},
        {"id": "", "type": "model_change", "parentId": None},
    ],
)
def test_invalid_off_branch_nodes_are_validated(record):
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=archive([message("root", None, "text"), record]), leaf_id="root"))


def test_large_unicode_event_and_full_record_round_trip():
    text = "工具结果\u2028保持原样\u0085" * 5000
    record = message("id:/%", None, text)
    record["extra"] = {"preserved": [False, None, 123]}
    event = parse_pi_session(PiSessionInput(content=archive([record], "会话")))[0]
    assert event.content == f"user: {text}"
    assert event.record == record

    async def scenario():
        registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
        source = await registry.resolve(event)
        restored = type(source).model_validate_json(source.model_dump_json())
        assert await registry.read(restored) == event
        assert len(source.name) <= 256

    asyncio.run(scenario())


def test_tool_arguments_are_sorted_and_control_events_preserve_ancestry():
    records = [
        message("root", None, "before"),
        {"type": "model_change", "id": "control", "parentId": "root"},
        {
            "type": "message",
            "id": "tool",
            "parentId": "control",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "private"},
                    {"type": "toolCall", "name": "read", "id": "call", "arguments": {"z": "中文", "a": 1}},
                    {"type": "text", "text": "after"},
                ],
            },
        },
    ]
    events = parse_pi_session(PiSessionInput(content=archive(records)))
    assert [event.event_id for event in events] == ["root", "tool"]
    assert events[1].parent_id == "control"
    assert events[1].content == 'assistant: [tool_call read call] {"a":1,"z":"中文"}\nafter'


def test_failed_run_preserves_progress_and_retry_then_detects_off_branch_edits(tmp_path):
    class Checkpoints:
        checkpoint = None

        async def load(self, binding):
            return self.checkpoint

        async def save(self, binding, checkpoint, *, expected):
            assert self.checkpoint == expected
            self.checkpoint = checkpoint

    class Sink:
        fail_on = "second"

        def __init__(self):
            self.stored = {}

        async def submit(self, binding, item_id, definition_name, value):
            if value.event_id == self.fail_on:
                raise RuntimeError("simulated write failure")  # noqa: TRY003
            if item_id in self.stored:
                assert self.stored[item_id] == value
            self.stored[item_id] = value
            return SourceRef(source_type=definition_name, source_id=item_id)

    async def scenario():
        path = tmp_path / "session.jsonl"
        records = [message("root", None, "original"), message("second", "root", "left")]
        path.write_bytes(archive(records))
        checkpoints, sink = Checkpoints(), Sink()
        binding = ConnectorBinding(
            scope_id="scope", binding_id="pi", connector_name="pi-session-history", connector_version="1"
        )
        lifecycle = ConnectorLifecycle(sink=sink, checkpoints=checkpoints)
        with pytest.raises(RuntimeError, match="write failure"):
            await lifecycle.run(PiSessionConnector(path), binding)
        assert checkpoints.checkpoint is None
        sink.fail_on = None
        first = await lifecycle.run(PiSessionConnector(path), binding)
        assert len(sink.stored) == 2
        records.append(message("right", "root", "right branch"))
        path.write_bytes(archive(records))
        second = await lifecycle.run(PiSessionConnector(path), binding)
        assert len(second.items) == 1
        saved = second.committed_checkpoint
        records[1]["message"]["content"] = "changed unselected branch"
        path.write_bytes(archive(records))
        with pytest.raises(ValueError, match="changed"):
            await lifecycle.run(PiSessionConnector(path), binding)
        assert checkpoints.checkpoint == saved
        assert first.committed_checkpoint != saved

    asyncio.run(scenario())
