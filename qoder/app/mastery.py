"""掌握度模型：把 PowerContext 记忆条目折叠成可解释的学习状态。

纯函数模块，不触碰网络与磁盘，便于单元测试。所有输入都来自真实记忆读取结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

SCHEMA = "recall-coach.memory.v1"
ALPHA = 0.45
WEAK_THRESHOLD = 0.55
FLOOR = 0.02
CEIL = 0.98


@dataclass
class TopicState:
    topic: str
    name: str
    mastery: float = 0.5
    attempts: int = 0
    correct: int = 0
    wrong_streak: int = 0
    last_at: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def tested(self) -> bool:
        return self.attempts > 0

    @property
    def weak(self) -> bool:
        return self.mastery < WEAK_THRESHOLD or self.wrong_streak >= 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "name": self.name,
            "mastery": round(self.mastery, 3),
            "attempts": self.attempts,
            "correct": self.correct,
            "wrong_streak": self.wrong_streak,
            "last_at": self.last_at,
            "tested": self.tested,
            "weak": self.weak,
            "evidence_count": len(self.evidence),
            "evidence": self.evidence,
        }


@dataclass
class Preference:
    daily_questions: int = 5
    focus_topics: list[str] = field(default_factory=list)
    note: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "daily_questions": self.daily_questions,
            "focus_topics": list(self.focus_topics),
            "note": self.note,
            "evidence": self.evidence,
        }


@dataclass
class MemoryView:
    topics: dict[str, TopicState]
    preference: Preference
    last_plan: dict[str, Any] | None
    entries: list[dict[str, Any]]

    @property
    def has_memory(self) -> bool:
        return bool(self.topics)


def _payload(entry: dict[str, Any]) -> dict[str, Any] | None:
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None
    if payload.get("app") != "recall-coach" or payload.get("v") != 1:
        return None
    return payload


def _artifact(entry: dict[str, Any]) -> dict[str, Any]:
    ref = dict(entry.get("artifact") or {})
    ref["kind"] = entry.get("kind")
    ref["state_version"] = entry.get("state_version")
    return ref


def _sort_key(entry: dict[str, Any]) -> tuple[str, int, str]:
    payload = _payload(entry) or {}
    return (str(payload.get("at") or ""), int(payload.get("seq") or 0), str(entry.get("kind") or ""))


def fold(memories: Iterable[dict[str, Any]], module_names: dict[str, str]) -> MemoryView:
    entries = [m for m in memories if m.get("state") == "active"]
    entries.sort(key=_sort_key)
    topics: dict[str, TopicState] = {}
    preference = Preference()
    last_plan: dict[str, Any] | None = None
    for entry in entries:
        payload = _payload(entry)
        if payload is None:
            continue
        kind = entry.get("kind")
        if kind in ("weakness", "progress"):
            topic = str(payload.get("topic") or "")
            if not topic:
                continue
            state = topics.setdefault(topic, TopicState(topic=topic, name=module_names.get(topic, topic)))
            state.mastery = float(payload.get("mastery", state.mastery))
            state.attempts = int(payload.get("attempts", state.attempts))
            state.correct = int(payload.get("correct", state.correct))
            state.wrong_streak = int(payload.get("wrong_streak", state.wrong_streak))
            state.name = str(payload.get("topic_name") or state.name)
            state.last_at = str(payload.get("at") or state.last_at or "")
            state.evidence.append(_artifact(entry))
        elif kind == "preference":
            preference.daily_questions = int(payload.get("daily_questions", preference.daily_questions))
            preference.focus_topics = [str(t) for t in payload.get("focus_topics") or []]
            preference.note = str(payload.get("note") or "")
            preference.evidence.append(_artifact(entry))
        elif kind == "plan":
            last_plan = {
                "session_id": payload.get("session_id"),
                "topics": [str(t) for t in payload.get("topics") or []],
                "question_ids": [str(q) for q in payload.get("question_ids") or []],
                "at": payload.get("at"),
                "mode": payload.get("mode"),
                "artifact": _artifact(entry),
            }
    return MemoryView(topics=topics, preference=preference, last_plan=last_plan, entries=entries)


def apply_results(
    state: TopicState | None,
    topic: str,
    topic_name: str,
    results: list[bool],
) -> dict[str, Any]:
    """按答题顺序做指数滑动平均，返回可直接写入记忆的新状态。"""
    mastery = state.mastery if state else 0.5
    attempts = state.attempts if state else 0
    correct = state.correct if state else 0
    streak = state.wrong_streak if state else 0
    for ok in results:
        mastery = mastery + ALPHA * ((1.0 if ok else 0.0) - mastery)
        mastery = min(max(mastery, FLOOR), CEIL)
        attempts += 1
        if ok:
            correct += 1
            streak = 0
        else:
            streak += 1
    return {
        "topic": topic,
        "topic_name": topic_name,
        "mastery": round(mastery, 3),
        "mastery_before": round(state.mastery if state else 0.5, 3),
        "attempts": attempts,
        "correct": correct,
        "wrong_answered": len(results),
        "wrong_in_round": sum(1 for ok in results if not ok),
        "wrong_streak": streak,
        "weak": mastery < WEAK_THRESHOLD or streak >= 1,
    }


def prioritize(
    view: MemoryView,
    module_names: dict[str, str],
    *,
    asked_topics: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """给每个知识点打分并给出可展示的依据。"""
    asked = set(asked_topics)
    focus = set(view.preference.focus_topics)
    planned = set((view.last_plan or {}).get("topics") or [])
    rows: list[dict[str, Any]] = []
    for topic, name in module_names.items():
        state = view.topics.get(topic)
        mastery = state.mastery if state else 0.5
        streak = state.wrong_streak if state else 0
        factors: list[dict[str, Any]] = []

        gap = round(1 - mastery, 3)
        score = gap
        factors.append({"key": "掌握缺口", "value": gap, "weight": gap, "text": f"当前掌握度 {mastery:.2f}，缺口 {gap:.2f}"})

        if streak:
            weight = 0.10 * min(streak, 3)
            score += weight
            factors.append({"key": "连续答错", "value": streak, "weight": round(weight, 3), "text": f"连续答错 {streak} 次，加权 {weight:.2f}"})

        if topic in focus:
            score += 0.20
            factors.append({"key": "复习偏好", "value": "主动侧重", "weight": 0.2, "text": "记忆中的复习偏好把该知识点列为侧重"})

        if state is None:
            score += 0.15
            factors.append({"key": "尚无记忆", "value": "未测", "weight": 0.15, "text": "PowerContext 中没有该知识点的掌握记录，先补齐诊断"})

        if topic in planned and mastery >= 0.75:
            score -= 0.25
            factors.append({"key": "上次已复习且已掌握", "value": "抑制", "weight": -0.25, "text": "上次安排已覆盖且掌握度达标，本轮让位给更薄弱的知识点"})

        if topic in asked:
            score -= 0.05
            factors.append({"key": "本轮已作答", "value": "降权", "weight": -0.05, "text": "本会话已作答，避免重复占用题量"})

        rows.append(
            {
                "topic": topic,
                "name": name,
                "score": round(score, 3),
                "mastery": round(mastery, 3),
                "attempts": state.attempts if state else 0,
                "wrong_streak": streak,
                "weak": bool(state and state.weak),
                "tested": bool(state and state.tested),
                "factors": factors,
                "evidence": state.evidence if state else [],
            }
        )
    rows.sort(key=lambda r: (-r["score"], r["topic"]))
    return rows
