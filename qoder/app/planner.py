"""记忆驱动的复习安排：读到的 PowerContext 记忆直接决定选题与顺序。"""

from __future__ import annotations

from typing import Any

from app.bank import Question, QuestionBank
from app.mastery import MemoryView, prioritize

MAX_QUESTIONS = 12


def _pick_question(bank: QuestionBank, topic: str, avoid: set[str], rotation: int) -> tuple[Question | None, bool]:
    pool = bank.by_module(topic)
    if not pool:
        return None, False
    fresh = [q for q in pool if q.id not in avoid]
    if fresh:
        return fresh[rotation % len(fresh)], False
    return pool[rotation % len(pool)], True


def build_plan(
    bank: QuestionBank,
    view: MemoryView,
    *,
    asked_question_ids: set[str] | None = None,
    count: int | None = None,
    rotation: int = 0,
) -> dict[str, Any]:
    """产出一次复习安排：mode、题量、每题依据与引用的记忆标识。"""
    asked_question_ids = asked_question_ids or set()
    preference = view.preference
    limit = count or preference.daily_questions or 5
    limit = max(1, min(int(limit), MAX_QUESTIONS))
    module_names = bank.modules
    ranked = prioritize(view, module_names)
    by_topic = {row["topic"]: row for row in ranked}

    if not view.topics:
        mode = "diagnosis"
        topics = [row["topic"] for row in ranked]
        # 首次诊断必须覆盖全部知识点
        limit = max(limit, len(topics))
        summary = (
            f"PowerContext 中没有该学习身份的掌握记忆，本次为基础诊断：覆盖 {len(topics)} 个知识点各 1 题，"
            "服务端判分后把薄弱点与掌握变化写回真实记忆。"
        )
    else:
        mode = "review"
        topics = [row["topic"] for row in ranked if row["weak"] or not row["tested"]]
        for focus in preference.focus_topics:
            if focus in module_names and focus not in topics:
                topics.append(focus)
        for row in ranked:
            if len(topics) >= limit:
                break
            if row["topic"] not in topics:
                topics.append(row["topic"])
        summary = (
            f"依据 PowerContext 已有的 {len(view.entries)} 条记忆安排复习：{len(topics)} 个候选知识点按掌握缺口、连续答错次数、"
            "复习偏好与上次安排记录排序，题量在候选之间轮转。"
        )

    avoid = set((view.last_plan or {}).get("question_ids") or []) | asked_question_ids
    per_topic = max(1, -(-limit // len(topics)))
    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    cursor = rotation

    def serve(topic: str, *, avoid_repeat: bool) -> None:
        nonlocal cursor
        row = by_topic[topic]
        question, repeated = _pick_question(
            bank, topic, avoid if avoid_repeat else asked_question_ids, counts.get(topic, 0) + cursor
        )
        cursor += 1
        if question is None:
            return
        counts[topic] = counts.get(topic, 0) + 1
        reasons = [factor["text"] for factor in row["factors"]]
        if repeated:
            reasons.append("该知识点的候选题已记录在上次安排的记忆里，本轮按难度轮换复用")
        items.append(
            {
                "order": len(items) + 1,
                "question": question.public(),
                "topic": topic,
                "topic_name": row["name"],
                "plan_score": row["score"],
                "mastery": row["mastery"],
                "wrong_streak": row["wrong_streak"],
                "reasons": reasons,
                "memory_refs": row["evidence"],
                "repeated": repeated,
            }
        )
        avoid.add(question.id)

    for topic in topics:
        while len(items) < limit and counts.get(topic, 0) < per_topic:
            serve(topic, avoid_repeat=True)
    guard = 0
    while len(items) < limit and guard < limit * 3:
        for topic in topics:
            if len(items) >= limit:
                break
            serve(topic, avoid_repeat=False)
        guard += 1

    return {
        "mode": mode,
        "summary": summary,
        "question_count": len(items),
        "items": items,
        "ranking": ranked,
        "candidate_topics": topics,
        "memory_entry_count": len(view.entries),
        "last_plan": view.last_plan,
        "preference": preference.as_dict(),
    }
