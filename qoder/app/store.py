"""本地 SQLite：只存身份到 Scope 的映射、会话与答题流水。

学习记忆（薄弱点、掌握变化、复习偏好、复习安排）一律存在 PowerContext，
本地库不承担记忆职责，仅用于可导出的过程记录与题号轮换计数。
另外记录每个会话的提交状态与已完成的记忆写入账本，用于重复提交保护与
部分写入后的补齐重试（两者都只保存回执，不保存学习结论）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS learners (
    learner_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    submit_state TEXT NOT NULL DEFAULT 'open',
    submitted_at TEXT,
    seq_base INTEGER,
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    chosen TEXT,
    answer TEXT NOT NULL,
    correct INTEGER NOT NULL,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (
    learner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value INTEGER NOT NULL,
    PRIMARY KEY (learner_id, name)
);
CREATE TABLE IF NOT EXISTS memory_writes (
    session_id TEXT NOT NULL,
    slot TEXT NOT NULL,
    kind TEXT NOT NULL,
    topic TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, slot)
);
CREATE INDEX IF NOT EXISTS idx_attempts_learner ON attempts(learner_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_session_question ON attempts(session_id, question_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SESSION_COLUMNS = {
    "submit_state": "TEXT NOT NULL DEFAULT 'open'",
    "submitted_at": "TEXT",
    "seq_base": "INTEGER",
    "result_json": "TEXT",
}


class LocalStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        with self._connect() as conn:
            self._migrate(conn)
            conn.executescript(SCHEMA)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """老库补齐提交状态列；同一会话的重复答题记录先去重，才能建唯一索引。"""
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "sessions" not in tables:
            return  # 全新库直接按 SCHEMA 建表
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        for name, ddl in SESSION_COLUMNS.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {ddl}")
        conn.execute(
            "DELETE FROM attempts WHERE id NOT IN (SELECT MAX(id) FROM attempts GROUP BY session_id, question_id)"
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ---------------------------------------------------------------- 身份
    def upsert_learner(self, learner_id: str, display_name: str, scope_id: str) -> dict[str, Any]:
        now = _now()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM learners WHERE learner_id = ?", (learner_id,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE learners SET display_name = ?, scope_id = ?, last_seen_at = ? WHERE learner_id = ?",
                    (display_name, scope_id, now, learner_id),
                )
                created = row["created_at"]
            else:
                conn.execute(
                    "INSERT INTO learners (learner_id, display_name, scope_id, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                    (learner_id, display_name, scope_id, now, now),
                )
                created = now
        return {"learner_id": learner_id, "display_name": display_name, "scope_id": scope_id, "created_at": created, "last_seen_at": now}

    def get_learner(self, learner_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM learners WHERE learner_id = ?", (learner_id,)).fetchone()
        return dict(row) if row else None

    def list_learners(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT learner_id, display_name, scope_id, created_at, last_seen_at FROM learners ORDER BY last_seen_at DESC").fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- 会话
    def create_session(self, learner_id: str, mode: str, plan: dict[str, Any]) -> str:
        session_id = f"ses_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}_{learner_id[:6]}"
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, learner_id, mode, created_at, plan_json) VALUES (?, ?, ?, ?, ?)",
                (session_id, learner_id, mode, _now(), json.dumps(plan, ensure_ascii=False)),
            )
        return session_id

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["plan"] = json.loads(data.pop("plan_json"))
        result = data.pop("result_json", None)
        data["result"] = json.loads(result) if result else None
        return data

    def begin_submit(self, learner_id: str, session_id: str) -> tuple[str, int]:
        """为一次提交固定写入时间与序号基数；并发或重试时以首次记录为准。

        记忆正文里的 at/seq 由这两个值决定，固定后重试得到的是同一条内容，
        配合 memory_writes 账本即可保证不会二次累计。
        """
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT submitted_at, seq_base FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if row and row["submitted_at"] is not None:
                return str(row["submitted_at"]), int(row["seq_base"])
        submit_at, seq_base = _now(), self.bump_counter(learner_id, "memory")
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET submitted_at = ?, seq_base = ? WHERE session_id = ? AND submitted_at IS NULL",
                (submit_at, seq_base, session_id),
            )
            row = conn.execute("SELECT submitted_at, seq_base FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return str(row["submitted_at"]), int(row["seq_base"])

    def mark_submit(self, session_id: str, result: dict[str, Any] | None) -> None:
        """result 非空表示已完成并可原样回放；None 表示批量写入只完成了一部分。"""
        state = "done" if result else "partial"
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET submit_state = ?, result_json = ? WHERE session_id = ?",
                (state, json.dumps(result, ensure_ascii=False) if result else None, session_id),
            )

    # ---------------------------------------------------------------- 记忆写入账本
    def memory_writes(self, session_id: str) -> dict[str, dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT slot, kind, topic, artifact_id, receipt_json, payload_json FROM memory_writes WHERE session_id = ? ORDER BY created_at, slot",
                (session_id,),
            ).fetchall()
        return {
            row["slot"]: {
                "kind": row["kind"],
                "topic": row["topic"],
                "artifact_id": row["artifact_id"],
                "receipt": json.loads(row["receipt_json"]),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        }

    def record_memory_write(
        self,
        session_id: str,
        slot: str,
        kind: str,
        topic: str,
        receipt: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """把确认写入的条目登记到账本：回执与正文一并留存，供重复提交时原样回放。"""
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO memory_writes (session_id, slot, kind, topic, artifact_id, receipt_json, payload_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(session_id, slot) DO UPDATE SET receipt_json = excluded.receipt_json, payload_json = excluded.payload_json",
                (
                    session_id,
                    slot,
                    kind,
                    topic,
                    receipt["artifact"]["artifact_id"],
                    json.dumps(receipt, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    _now(),
                ),
            )

    # ---------------------------------------------------------------- 流水
    def record_attempts(self, learner_id: str, session_id: str, rows: list[dict[str, Any]]) -> None:
        """按 (会话, 题目) 幂等写入：重复提交只更新选择结果，不增加条数。"""
        now = _now()
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO attempts (learner_id, session_id, question_id, topic, chosen, answer, correct, origin, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(session_id, question_id) DO UPDATE SET"
                " chosen = excluded.chosen, answer = excluded.answer, correct = excluded.correct,"
                " origin = excluded.origin, created_at = excluded.created_at",
                [
                    (
                        learner_id,
                        session_id,
                        r["question_id"],
                        r["topic"],
                        r.get("chosen"),
                        r["answer"],
                        1 if r["correct"] else 0,
                        r.get("origin", "unknown"),
                        now,
                    )
                    for r in rows
                ],
            )

    def attempts(self, learner_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id, question_id, topic, chosen, answer, correct, origin, created_at"
                " FROM attempts WHERE learner_id = ? ORDER BY id DESC LIMIT ?",
                (learner_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def asked_question_ids(self, learner_id: str, limit: int = 12) -> set[str]:
        return {row["question_id"] for row in self.attempts(learner_id, limit)}

    def bump_counter(self, learner_id: str, name: str) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM counters WHERE learner_id = ? AND name = ?", (learner_id, name)).fetchone()
            value = int(row["value"]) + 1 if row else 1
            if row:
                conn.execute("UPDATE counters SET value = ? WHERE learner_id = ? AND name = ?", (value, learner_id, name))
            else:
                conn.execute("INSERT INTO counters (learner_id, name, value) VALUES (?, ?, ?)", (learner_id, name, value))
        return value
