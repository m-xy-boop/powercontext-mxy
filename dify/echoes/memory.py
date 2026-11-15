"""PowerContext's public SDK is the only durable game-state store."""

import json
from pathlib import Path

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.scope.models import ScopeDraft

from .game import initial_state


class Memory:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    async def __aenter__(self):
        config = BuiltinConfig(
            database=SQLiteConfig(
                url=f"sqlite+aiosqlite:///{self.root / 'powercontext.db'}"
            )
        )
        self.manager = open_builtin_runtime(
            config, scheduler_path=self.root / "scheduler.db"
        )
        self.runtime = await self.manager.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.manager.__aexit__(*args)

    async def records(self, player):
        scope = await self.runtime.scopes.create(
            ScopeDraft(
                title=f"回声当铺 / {player}",
                summary="跨会话交易、角色状态和剧情进展",
                idempotency_key=f"echo-pawnshop:v1:{player}",
            )
        )
        return scope.scope_id, self.runtime.records.for_scope(scope.scope_id)

    async def read(self, player):
        scope_id, records = await self.records(player)
        cursor, latest = None, None
        while True:
            page = await records.list_sources(limit=100, cursor=cursor)
            for record in page.items:
                if (
                    isinstance(record.content, dict)
                    and record.content.get("kind") == "echo-state"
                ):
                    if latest is None or record.position > latest.position:
                        latest = record
            cursor = page.next_cursor
            if not cursor:
                break
        state = latest.content["state"] if latest else initial_state()
        return state, {
            "scope_id": scope_id,
            "source_id": latest.source_id if latest else None,
            "position": latest.position if latest else None,
        }

    async def write(self, player, state):
        scope_id, records = await self.records(player)
        record = await records.create_source(
            "content", {"kind": "echo-state", "player": player, "state": state}
        )
        return {
            "scope_id": scope_id,
            "source_id": record.source_id,
            "position": record.position,
        }

    def audit(self, event, **data):
        # Local runtime logs; the submission evidence is exported separately.
        from datetime import datetime, timezone

        with (self.root / "events.jsonl").open("a", encoding="utf-8") as out:
            out.write(
                json.dumps(
                    {
                        "time": datetime.now(timezone.utc).isoformat(),
                        "event": event,
                        **data,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
