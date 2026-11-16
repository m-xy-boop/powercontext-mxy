"""离线单元测试：题库、判分模型与选题逻辑（不访问网络）。"""

from __future__ import annotations

import copy
import pytest

from app.bank import QuestionBank
from app.config import PROJECT_ROOT
from app.mastery import MemoryView, Preference, TopicState, apply_results, fold, prioritize
from app.planner import build_plan


@pytest.fixture(scope="module")
def bank() -> QuestionBank:
    return QuestionBank(PROJECT_ROOT / "data" / "questions.json")


def test_bank_shape(bank: QuestionBank) -> None:
    assert len(bank.modules) == 8
    assert len(bank.questions) == 24
    for question in bank.questions.values():
        assert len(question.options) == 4
        assert question.answer in {o["key"] for o in question.options}
        assert 1 <= question.difficulty <= 3
        assert question.explain.strip()
        assert set(question.stem) != set()
    # 每个知识点都有题可出
    for module in bank.modules:
        assert bank.by_module(module)


def test_bank_public_hides_answer(bank: QuestionBank) -> None:
    payload = bank.by_module("tx-isolation")[0].public()
    assert "answer" not in payload and "explain" not in payload


def test_fold_empty_memory_is_untested() -> None:
    view = fold([], {"tx-acid": "事务与ACID"})
    assert view.topics == {} and not view.has_memory


def test_apply_results_moves_mastery() -> None:
    wrong = apply_results(None, "mvcc", "多版本并发控制", [False])
    assert wrong["mastery"] < 0.5 and wrong["wrong_streak"] == 1 and wrong["weak"]
    recovered = apply_results(
        TopicState(topic="mvcc", name="多版本并发控制", mastery=wrong["mastery"], attempts=1, wrong_streak=1),
        "mvcc",
        "多版本并发控制",
        [True, True],
    )
    assert recovered["mastery"] > wrong["mastery"] and recovered["wrong_streak"] == 0


def test_fold_reads_real_shaped_entries() -> None:
    entries = [
        {
            "kind": "progress",
            "state": "active",
            "state_version": 0,
            "artifact": {"family": "atomic-memory", "artifact_id": "art_" + "a" * 32, "revision": 1},
            "payload": {
                "app": "recall-coach",
                "v": 1,
                "at": "2026-11-16T01:00:00+00:00",
                "seq": 1,
                "topic": "tx-isolation",
                "topic_name": "隔离级别与并发异常",
                "mastery": 0.2,
                "attempts": 3,
                "correct": 1,
                "wrong_streak": 2,
            },
        },
        {
            "kind": "progress",
            "state": "forgotten",
            "state_version": 1,
            "artifact": {"family": "atomic-memory", "artifact_id": "art_" + "b" * 32, "revision": 1},
            "payload": {
                "app": "recall-coach",
                "v": 1,
                "at": "2026-11-16T02:00:00+00:00",
                "seq": 2,
                "topic": "tx-isolation",
                "topic_name": "隔离级别与并发异常",
                "mastery": 0.9,
                "attempts": 4,
                "correct": 4,
                "wrong_streak": 0,
            },
        },
        {
            "kind": "preference",
            "state": "active",
            "state_version": 0,
            "artifact": {"family": "atomic-memory", "artifact_id": "art_" + "c" * 32, "revision": 1},
            "payload": {
                "app": "recall-coach",
                "v": 1,
                "at": "2026-11-16T02:30:00+00:00",
                "seq": 3,
                "daily_questions": 4,
                "focus_topics": ["lock"],
                "note": "晚上复习",
            },
        },
    ]
    view = fold(copy.deepcopy(entries), {"tx-isolation": "隔离级别与并发异常", "lock": "锁与死锁"})
    state = view.topics["tx-isolation"]
    assert state.mastery == 0.2, "已遗忘的记忆不得参与掌握度计算"
    assert state.evidence[0]["artifact_id"].startswith("art_")
    assert view.preference.daily_questions == 4 and view.preference.focus_topics == ["lock"]


def test_priority_uses_memory_preferences_and_gaps() -> None:
    modules = {"tx-isolation": "隔离级别与并发异常", "lock": "锁与死锁", "storage": "存储引擎与分布式"}
    view = MemoryView(
        topics={
            "tx-isolation": TopicState(topic="tx-isolation", name="隔离级别与并发异常", mastery=0.2, attempts=3, correct=1, wrong_streak=2),
            "lock": TopicState(topic="lock", name="锁与死锁", mastery=0.9, attempts=3, correct=3),
        },
        preference=Preference(daily_questions=3, focus_topics=["lock"]),
        last_plan=None,
        entries=[],
    )
    ranked = prioritize(view, modules)
    # 薄弱优先于未测，未测优先于已掌握
    assert ranked[0]["topic"] == "tx-isolation"
    assert ranked[1]["topic"] == "storage"
    keys = [f["key"] for f in ranked[0]["factors"]]
    assert "掌握缺口" in keys and "连续答错" in keys
    assert "复习偏好" in [f["key"] for f in ranked[2]["factors"]]


def test_plan_switches_from_diagnosis_to_review(bank: QuestionBank) -> None:
    empty = build_plan(bank, fold([], bank.modules))
    assert empty["mode"] == "diagnosis"
    assert empty["question_count"] == 8
    assert len({item["topic"] for item in empty["items"]}) == 8

    entries = [
        {
            "kind": "weakness",
            "state": "active",
            "state_version": 0,
            "artifact": {"family": "atomic-memory", "artifact_id": "art_" + "d" * 32, "revision": 1},
            "payload": {
                "app": "recall-coach",
                "v": 1,
                "at": "2026-11-16T03:00:00+00:00",
                "seq": 1,
                "topic": "recovery",
                "topic_name": "日志与崩溃恢复",
                "mastery": 0.15,
                "attempts": 2,
                "correct": 0,
                "wrong_streak": 2,
            },
        }
    ]
    view = fold(entries, bank.modules)
    plan = build_plan(bank, view, count=3)
    assert plan["mode"] == "review"
    assert plan["question_count"] == 3
    assert plan["items"][0]["topic"] == "recovery"
    assert plan["items"][0]["memory_refs"], "复习选题必须携带真实记忆引用"
    assert any("掌握度" in text for text in plan["items"][0]["reasons"])


def test_plan_avoids_repeating_last_served_questions(bank: QuestionBank) -> None:
    entries = [
        {
            "kind": "plan",
            "state": "active",
            "state_version": 0,
            "artifact": {"family": "atomic-memory", "artifact_id": "art_" + "e" * 32, "revision": 1},
            "payload": {
                "app": "recall-coach",
                "v": 1,
                "at": "2026-11-16T04:00:00+00:00",
                "seq": 9,
                "session_id": "ses_old",
                "mode": "review",
                "topics": ["recovery"],
                "question_ids": ["recovery-1"],
            },
        },
        {
            "kind": "progress",
            "state": "active",
            "state_version": 0,
            "artifact": {"family": "atomic-memory", "artifact_id": "art_" + "f" * 32, "revision": 1},
            "payload": {
                "app": "recall-coach",
                "v": 1,
                "at": "2026-11-16T04:00:01+00:00",
                "seq": 10,
                "topic": "recovery",
                "topic_name": "日志与崩溃恢复",
                "mastery": 0.2,
                "attempts": 2,
                "correct": 0,
                "wrong_streak": 2,
            },
        },
    ]
    plan = build_plan(bank, fold(entries, bank.modules), count=2)
    served = {item["question"]["id"] for item in plan["items"]}
    assert "recovery-1" not in served, "上次已安排进记忆的题目应轮换"
    assert plan["last_plan"]["session_id"] == "ses_old"


def test_bank_module_order(bank: QuestionBank) -> None:
    assert bank.module_ids()[0] == "tx-acid"
