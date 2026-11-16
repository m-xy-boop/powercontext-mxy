"""原创内置题库：固定 JSON，无需任何模型服务。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Question:
    id: str
    module: str
    module_name: str
    difficulty: int
    stem: str
    options: tuple[dict[str, str], ...]
    answer: str
    explain: str

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module": self.module,
            "module_name": self.module_name,
            "difficulty": self.difficulty,
            "stem": self.stem,
            "options": [dict(o) for o in self.options],
        }


class QuestionBank:
    def __init__(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") != "recall-coach.bank.v1":
            raise ValueError(f"题库 schema 不受支持：{raw.get('schema')}")
        self.modules: dict[str, str] = {m["id"]: m["name"] for m in raw["modules"]}
        self.module_summaries: dict[str, str] = {m["id"]: m["summary"] for m in raw["modules"]}
        questions: list[Question] = []
        for item in raw["questions"]:
            questions.append(
                Question(
                    id=item["id"],
                    module=item["module"],
                    module_name=self.modules[item["module"]],
                    difficulty=int(item["difficulty"]),
                    stem=item["stem"],
                    options=tuple(item["options"]),
                    answer=item["answer"],
                    explain=item["explain"],
                )
            )
        self.questions: dict[str, Question] = {q.id: q for q in questions}
        self._by_module: dict[str, list[Question]] = {}
        for question in questions:
            self._by_module.setdefault(question.module, []).append(question)
        for bucket in self._by_module.values():
            bucket.sort(key=lambda q: (q.difficulty, q.id))

    def get(self, question_id: str) -> Question | None:
        return self.questions.get(question_id)

    def by_module(self, module: str) -> list[Question]:
        return list(self._by_module.get(module, []))

    def module_ids(self) -> list[str]:
        return list(self.modules)

    def catalogue(self) -> dict[str, Any]:
        return {
            "schema": "recall-coach.bank.v1",
            "note": "固定内置题库，不声称动态 AI 出题。",
            "modules": [
                {"id": mid, "name": name, "summary": self.module_summaries[mid], "question_count": len(self._by_module.get(mid, []))}
                for mid, name in self.modules.items()
            ],
            "question_total": len(self.questions),
        }
