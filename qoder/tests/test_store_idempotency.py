"""离线回归：重复提交的持久幂等与批量写入账本（只动本地 SQLite，不访问网络）。

这些用例锁住独立审查发现的两个问题：
1 同一会话重复提交不得再次累计答题流水与记忆；
2 批量写入部分成功后，重试只补齐缺失条目，提示必须如实区分“已写入/未写入”。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.powercontext_service import MemoryBackendError, MemoryRejected, MemoryUnavailable, accuracy_notice
from app.store import LocalStore


def receipt(artifact_id: str) -> dict:
    return {
        "kind": "progress",
        "changed": True,
        "artifact": {"family": "atomic-memory", "artifact_id": artifact_id, "revision": 1},
        "state": "active",
        "state_version": 0,
        "text_bytes": 180,
        "verified_by": "get_atomic_memory_state",
        "duration_ms": 12.5,
    }


@pytest.fixture()
def store(tmp_path: Path) -> LocalStore:
    db = LocalStore(tmp_path / "recall-coach.db")
    db.upsert_learner("unit-idem", "单元-幂等", "scope_unit")
    return db


def rows(count: int) -> list[dict]:
    return [
        {
            "question_id": f"q-{index}",
            "topic": "mvcc",
            "chosen": "A",
            "answer": "A",
            "correct": True,
            "origin": "diagnosis",
        }
        for index in range(count)
    ]


def test_record_attempts_is_idempotent_per_question(store: LocalStore) -> None:
    session_id = store.create_session("unit-idem", "diagnosis", {"items": []})
    store.record_attempts("unit-idem", session_id, rows(4))
    store.record_attempts("unit-idem", session_id, rows(4))
    logged = store.attempts("unit-idem")
    assert len([r for r in logged if r["session_id"] == session_id]) == 4, "重复提交不得增加答题流水"
    flipped = rows(4)
    flipped[0]["chosen"] = "B"
    flipped[0]["correct"] = False
    store.record_attempts("unit-idem", session_id, flipped)
    again = [r for r in store.attempts("unit-idem") if r["session_id"] == session_id]
    assert len(again) == 4
    assert next(r for r in again if r["question_id"] == "q-0")["correct"] == 0


def test_begin_submit_fixes_time_and_sequence(store: LocalStore) -> None:
    session_id = store.create_session("unit-idem", "diagnosis", {"items": []})
    first = store.begin_submit("unit-idem", session_id)
    second = store.begin_submit("unit-idem", session_id)
    assert first == second, "同一次提交的时间与序号基数必须固定，重试才能生成完全相同的记忆正文"
    assert store.get_session(session_id)["seq_base"] == first[1]
    other = store.begin_submit("unit-idem", store.create_session("unit-idem", "review", {"items": []}))
    assert other != first, "不同会话不得共用同一个 at/seq"


def test_memory_write_ledger_survives_and_replays(store: LocalStore) -> None:
    session_id = store.create_session("unit-idem", "diagnosis", {"items": []})
    assert store.memory_writes(session_id) == {}
    store.record_memory_write(session_id, "progress:mvcc", "progress", "mvcc", receipt("art_" + "a" * 32), {"summary": "第一次"})
    store.record_memory_write(session_id, "progress:mvcc", "progress", "mvcc", receipt("art_" + "a" * 32), {"summary": "第一次"})
    ledger = store.memory_writes(session_id)
    assert set(ledger) == {"progress:mvcc"}
    assert ledger["progress:mvcc"]["artifact_id"] == "art_" + "a" * 32
    assert ledger["progress:mvcc"]["receipt"]["state_version"] == 0
    assert ledger["progress:mvcc"]["payload"]["summary"] == "第一次"


def test_submit_state_is_persisted(store: LocalStore) -> None:
    session_id = store.create_session("unit-idem", "diagnosis", {"items": []})
    assert store.get_session(session_id)["submit_state"] == "open"
    store.mark_submit(session_id, None)
    assert store.get_session(session_id)["submit_state"] == "partial"
    assert store.get_session(session_id)["result"] is None
    store.mark_submit(session_id, {"score": {"correct": 3, "total": 4}})
    reopened = store.get_session(session_id)
    assert reopened["submit_state"] == "done"
    assert reopened["result"]["score"]["correct"] == 3


def test_legacy_database_is_migrated(tmp_path: Path) -> None:
    """旧版库（无提交状态列、流水有重复行）必须能原地升级，不能要求用户删库。"""
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE sessions (session_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, mode TEXT NOT NULL,
                                   created_at TEXT NOT NULL, plan_json TEXT NOT NULL);
            CREATE TABLE attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, learner_id TEXT NOT NULL,
                                   session_id TEXT NOT NULL, question_id TEXT NOT NULL, topic TEXT NOT NULL,
                                   chosen TEXT, answer TEXT NOT NULL, correct INTEGER NOT NULL,
                                   origin TEXT NOT NULL, created_at TEXT NOT NULL);
            INSERT INTO sessions VALUES ('ses_old', 'unit-idem', 'diagnosis', '2026-01-01T00:00:00+00:00', '{"items": []}');
            INSERT INTO attempts (learner_id, session_id, question_id, topic, chosen, answer, correct, origin, created_at)
              VALUES ('unit-idem', 'ses_old', 'q-0', 'mvcc', 'A', 'A', 1, 'diagnosis', '2026-01-01T00:00:01+00:00');
            INSERT INTO attempts (learner_id, session_id, question_id, topic, chosen, answer, correct, origin, created_at)
              VALUES ('unit-idem', 'ses_old', 'q-0', 'mvcc', 'A', 'A', 1, 'diagnosis', '2026-01-01T00:00:02+00:00');
            """
        )
    store = LocalStore(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        duplicated = conn.execute("SELECT COUNT(*) AS c FROM attempts WHERE session_id = 'ses_old'").fetchone()["c"]
    assert duplicated == 1, "旧库里重复的答题记录要在建唯一索引前清掉"
    legacy = store.get_session("ses_old")
    assert legacy["submit_state"] == "open" and legacy["result"] is None
    store.record_attempts("unit-idem", "ses_old", rows(1))
    assert len([r for r in store.attempts("unit-idem") if r["session_id"] == "ses_old"]) == 1


def test_accuracy_notice_matches_the_real_failure() -> None:
    reading = MemoryBackendError(operation="list_atomic_memories", code="powercontext_unavailable", message="x")
    assert "没有读到" in accuracy_notice(reading) and "没有写入" not in accuracy_notice(reading)

    unreachable = MemoryUnavailable(operation="remember_memory", code="powercontext_unavailable", message="x")
    assert "没有产生任何记忆写入" in accuracy_notice(unreachable)

    rejected = MemoryRejected(operation="remember_memory", code="invalid_request", message="x", status_code=422)
    assert "拒绝了本次写入" in accuracy_notice(rejected)

    uncertain = MemoryBackendError(operation="remember_memory", code="readback_mismatch", message="x")
    assert "未获确认" in accuracy_notice(uncertain) and "不会显示假回执" in accuracy_notice(uncertain)

    partial = MemoryBackendError(operation="remember_memory", code="injected", message="x")
    partial.notice = "本次批量写入只完成了一部分"
    assert accuracy_notice(partial) == "本次批量写入只完成了一部分", "调用方给出的准确提示优先于通用兜底"
