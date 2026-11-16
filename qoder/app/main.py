"""拾忆 · Recall Coach 服务端。

职责：判分、编排 PowerContext 真实记忆读写、生成记忆驱动的复习安排。
学习记忆只存在 PowerContext；本地 SQLite 仅存身份映射与可导出的答题流水。
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.bank import QuestionBank
from app.config import PROJECT_ROOT, Settings, load_settings
from app.mastery import MemoryView, TopicState, apply_results, fold, prioritize
from app.planner import build_plan
from app.powercontext_service import MemoryBackendError, PowerContextMemory, accuracy_notice
from app.store import LocalStore

LEARNER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")
APP_VERSION = "1.0.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class LearnerCreate(BaseModel):
    learner_id: str = Field(min_length=2, max_length=31)
    display_name: str = Field(min_length=1, max_length=40)

    def normalized(self) -> tuple[str, str]:
        learner_id = self.learner_id.strip().lower()
        if not LEARNER_RE.match(learner_id):
            raise HTTPException(422, {"code": "invalid_learner_id", "message": "学习身份只能使用小写字母、数字与连字符，长度 2-31。"})
        display_name = re.sub(r"\s+", " ", self.display_name).strip()
        if not display_name:
            raise HTTPException(422, {"code": "invalid_display_name", "message": "请填写显示名称。"})
        return learner_id, display_name


class Answer(BaseModel):
    question_id: str = Field(min_length=3, max_length=64)
    chosen: Literal["A", "B", "C", "D"]


class Submit(BaseModel):
    answers: list[Answer] = Field(min_length=1, max_length=12)


class PreferenceUpdate(BaseModel):
    daily_questions: int = Field(default=5, ge=1, le=12)
    focus_topics: list[str] = Field(default_factory=list, max_length=8)
    note: str = Field(default="", max_length=80)


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=8, ge=1, le=20)


class RecallCoachApp(FastAPI):
    settings: Settings
    bank: QuestionBank
    store: LocalStore
    memory: PowerContextMemory


def create_app(settings: Settings | None = None) -> RecallCoachApp:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: RecallCoachApp):
        app.memory = PowerContextMemory(settings)
        yield
        await app.memory.aclose()

    app = RecallCoachApp(
        title="拾忆 · Recall Coach",
        description="基于 OceanBase PowerContext 的数据库基础复习工作台",
        version=APP_VERSION,
        lifespan=lifespan,
    )
    app.settings = settings
    app.bank = QuestionBank(settings.question_bank)
    app.store = LocalStore(settings.local_db)

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": f"http_{exc.status_code}", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_request", "message": "请求体不符合接口约束。", "details": exc.errors()[:5]}},
        )

    @app.exception_handler(MemoryBackendError)
    async def _memory_error(_: Request, exc: MemoryBackendError) -> JSONResponse:
        status = 503 if exc.code == "powercontext_unavailable" else (exc.status_code or 502)
        if status < 500 and status not in (409, 422):
            status = 502
        content: dict[str, Any] = {"error": exc.to_payload(), "notice": accuracy_notice(exc)}
        if exc.written_receipts:
            content["written_receipts"] = exc.written_receipts
        if exc.pending_slots:
            content["pending_slots"] = exc.pending_slots
        return JSONResponse(status_code=status, content=content)

    # ------------------------------------------------------------------ 公共信息
    @app.get("/api/context")
    async def context() -> dict[str, Any]:
        probe = await app.memory.probe()
        return {
            "app": {"name": "拾忆 · Recall Coach", "version": APP_VERSION},
            "bank": app.bank.catalogue(),
            "powercontext": probe,
            "learners": [
                {k: v for k, v in row.items() if k != "scope_id"} | {"scope_hint": row["scope_id"][:12] + "…"}
                for row in app.store.list_learners()
            ],
        }

    # ------------------------------------------------------------------ 身份
    @app.post("/api/learners")
    async def create_learner(payload: LearnerCreate) -> dict[str, Any]:
        learner_id, display_name = payload.normalized()
        # Scope 名称只用稳定 slug，显示名不写入 PowerContext，避免身份泄露与幂等冲突
        scope = await app.memory.ensure_scope(
            idempotency_key=f"recall-coach:v2:learner:{learner_id}",
            title=f"拾忆-{learner_id}",
            summary="拾忆 Recall Coach 学习身份记忆域",
        )
        learner = app.store.upsert_learner(learner_id, display_name, scope["scope_id"])
        return {"learner": learner, "scope": scope, "backend": app.memory.base_url}

    def _require(learner_id: str) -> dict[str, Any]:
        learner = app.store.get_learner(learner_id)
        if not learner:
            raise HTTPException(404, {"code": "learner_not_found", "message": "该学习身份尚未在本机建立，请先在页面上选择学习身份。"})
        return learner

    async def _view(scope_id: str):
        memories = await app.memory.list_memories(scope_id=scope_id, states=["active"])
        return fold(memories, app.bank.modules), memories

    @app.get("/api/learners/{learner_id}")
    async def get_learner(learner_id: str) -> dict[str, Any]:
        learner = _require(learner_id)
        view, memories = await _view(learner["scope_id"])
        return {
            "learner": learner,
            "memory_entry_count": len(memories),
            "topics": [state.as_dict() for state in sorted(view.topics.values(), key=lambda s: s.mastery)],
            "preference": view.preference.as_dict(),
            "last_plan": view.last_plan,
            "ranking": build_plan(app.bank, view)["ranking"],
        }

    # ------------------------------------------------------------------ 安排与提交
    @app.post("/api/learners/{learner_id}/sessions")
    async def create_session(learner_id: str) -> dict[str, Any]:
        learner = _require(learner_id)
        scope_id = learner["scope_id"]
        view, _ = await _view(scope_id)
        asked = app.store.asked_question_ids(learner_id, 12)
        rotation = app.store.bump_counter(learner_id, "rotation")
        plan = build_plan(app.bank, view, asked_question_ids=asked, rotation=rotation)
        session_id = app.store.create_session(learner_id, plan["mode"], plan)
        plan_payload = {
            "app": "recall-coach",
            "v": 1,
            "at": _now(),
            "seq": app.store.bump_counter(learner_id, "memory"),
            "session_id": session_id,
            "mode": plan["mode"],
            "topics": [item["topic"] for item in plan["items"]],
            "question_ids": [item["question"]["id"] for item in plan["items"]],
            "summary": f"本次{'基础诊断' if plan['mode'] == 'diagnosis' else '记忆驱动复习'}安排 {plan['question_count']} 题：" + "、".join(
                f"{item['topic_name']}(掌握 {item['mastery']:.2f})" for item in plan["items"]
            ),
        }
        receipt = await app.memory.remember(scope_id=scope_id, kind="plan", payload=plan_payload, reason="记录本次复习安排，供新会话续接")
        return {
            "session_id": session_id,
            "learner": learner,
            "mode": plan["mode"],
            "summary": plan["summary"],
            "question_count": plan["question_count"],
            "items": plan["items"],
            "ranking": plan["ranking"],
            "preference": plan["preference"],
            "memory_refs": [item["memory_refs"] for item in plan["items"]],
            "plan_receipt": receipt,
        }

    @app.get("/api/learners/{learner_id}/sessions/{session_id}")
    async def get_session(learner_id: str, session_id: str) -> dict[str, Any]:
        learner = _require(learner_id)
        session = app.store.get_session(session_id)
        if not session or session["learner_id"] != learner_id:
            raise HTTPException(404, {"code": "session_not_found", "message": "会话不存在或不属于该学习身份。"})
        return {
            "session_id": session_id,
            "mode": session["mode"],
            "created_at": session["created_at"],
            "submit_state": session["submit_state"],
            "submitted_at": session["submitted_at"],
            "plan": session["plan"],
        }

    @app.post("/api/learners/{learner_id}/sessions/{session_id}/submit")
    async def submit(learner_id: str, session_id: str, payload: Submit) -> dict[str, Any]:
        learner = _require(learner_id)
        scope_id = learner["scope_id"]
        session = app.store.get_session(session_id)
        if not session or session["learner_id"] != learner_id:
            raise HTTPException(404, {"code": "session_not_found", "message": "会话不存在或不属于该学习身份。"})
        if session["result"] is not None:
            # 服务端已持久记录本次提交：原样回放，既不新增答题流水，也不再次写记忆
            replayed = dict(session["result"])
            replayed["replay"] = {
                "repeated_submit": True,
                "recorded_at": session["submitted_at"],
                "new_memories_this_request": 0,
                "new_attempts_this_request": 0,
                "note": "本会话此前已完成判分与记忆写入，服务端直接返回当时留存的判分结果与真实回执；本次请求没有再次累计。",
            }
            return replayed
        plan = session["plan"]
        plan_items = {item["question"]["id"]: item for item in plan["items"]}
        answers = {a.question_id: a.chosen for a in payload.answers}
        unknown = sorted(set(answers) - set(plan_items))
        if unknown:
            raise HTTPException(422, {"code": "question_not_in_session", "message": f"题目不属于本次安排：{unknown}"})

        results: list[dict[str, Any]] = []
        grouped: dict[str, list[bool]] = {}
        rows: list[dict[str, Any]] = []
        for question_id, item in plan_items.items():
            question = app.bank.get(question_id)
            if question is None:  # 题库与历史会话不一致时明确报错，不猜答案
                raise HTTPException(500, {"code": "question_missing", "message": f"题库缺少题目 {question_id}"})
            chosen = answers.get(question_id)
            correct = chosen is not None and chosen == question.answer
            results.append(
                {
                    "question_id": question_id,
                    "topic": item["topic"],
                    "topic_name": item["topic_name"],
                    "stem": question.stem,
                    "chosen": chosen,
                    "answer": question.answer,
                    "correct": correct,
                    "explain": question.explain,
                    "plan_reasons": item["reasons"],
                }
            )
            grouped.setdefault(item["topic"], []).append(correct)
            rows.append(
                {
                    "question_id": question_id,
                    "topic": item["topic"],
                    "chosen": chosen,
                    "answer": question.answer,
                    "correct": correct,
                    "origin": plan["mode"],
                }
            )
        app.store.record_attempts(learner_id, session_id, rows)

        submit_at, seq_base = app.store.begin_submit(learner_id, session_id)
        ledger = app.store.memory_writes(session_id)
        written_ids = {row["artifact_id"] for row in ledger.values()}
        memories = await app.memory.list_memories(scope_id=scope_id, states=["active"])
        # 基线必须剔除本次提交已经写进去的条目，否则补齐重试会把同一轮变化叠加两次
        view = fold([m for m in memories if m["artifact"]["artifact_id"] not in written_ids], app.bank.modules)

        receipts: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        replayed_slots = 0

        # 先把每条要写的记忆正文全部算好：正文只由判分结果与本次提交固定的 at/seq 决定，
        # 因此中途失败后重试生成的内容与首次完全一致，补齐写入也不会改变已写内容。
        planned: list[tuple[str, str, dict[str, Any], str]] = []
        for index, (topic, outcomes) in enumerate(grouped.items()):
            state = view.topics.get(topic)
            new_state = apply_results(state, topic, app.bank.modules.get(topic, topic), outcomes)
            updates.append(new_state | {"session_id": session_id})
            base = {
                "app": "recall-coach",
                "v": 1,
                "at": submit_at,
                "seq": seq_base + index,
                "session_id": session_id,
                "source": "review" if plan["mode"] == "review" else "diagnosis",
                **new_state,
            }
            progress_text = (
                f"{new_state['topic_name']}：掌握度 {new_state['mastery_before']:.2f} → {new_state['mastery']:.2f}，"
                f"本轮 {new_state['wrong_answered']} 题错 {new_state['wrong_in_round']} 题，累计连错 {new_state['wrong_streak']} 次"
            )
            planned.append(("progress", topic, base | {"summary": progress_text}, "掌握变化写入"))
            if new_state["weak"]:
                weak_text = (
                    f"薄弱点：{new_state['topic_name']}（{topic}）掌握度 {new_state['mastery']:.2f}，"
                    f"连错 {new_state['wrong_streak']} 次，优先安排复习"
                )
                planned.append(
                    (
                        "weakness",
                        topic,
                        base | {"summary": weak_text, "note": "由答题结果判分产生，用于复习选题"},
                        "薄弱点写入",
                    )
                )

        current_slot: str | None = None
        try:
            for kind, topic, slot_payload, reason in planned:
                slot = f"{kind}:{topic}"
                current_slot = slot
                done = ledger.get(slot)
                if done:
                    receipts.append(done["receipt"])
                    replayed_slots += 1
                    continue
                receipt = await app.memory.remember(scope_id=scope_id, kind=kind, payload=slot_payload, reason=reason)
                app.store.record_memory_write(session_id, slot, kind, topic, receipt, slot_payload)
                ledger[slot] = {"artifact_id": receipt["artifact"]["artifact_id"], "receipt": receipt}
                receipts.append(receipt)
                current_slot = None
        except MemoryBackendError as exc:
            app.store.mark_submit(session_id, None)
            exc.written_receipts = [row["receipt"] for row in ledger.values()]
            exc.pending_slots = [f"{kind}:{topic}" for kind, topic, _, _ in planned if f"{kind}:{topic}" not in ledger]
            exc.notice = (
                f"本次批量写入只完成了一部分：{len(exc.written_receipts)} 条已真实写入 PowerContext，回执随本次响应返回；"
                f"失败发生在 {current_slot or '未知条目'}，还有 {len(exc.pending_slots)} 条本次没有写入。"
                "已写入的条目记录在服务端账本中，重试同一提交只补齐缺失条目，答题流水、掌握度与薄弱点都不会重复累计。"
            )
            raise

        refreshed = merge_view(view, updates)
        result = {
            "session_id": session_id,
            "mode": plan["mode"],
            "score": {"correct": sum(1 for r in results if r["correct"]), "total": len(results)},
            "results": results,
            "topic_updates": updates,
            "memory_receipts": receipts,
            "ranking_after": prioritize(refreshed, app.bank.modules),
            "next_plan_basis": "复习安排由上面这些真实记忆条目折叠得到，可在新会话或重启后继续读取。",
            "memory_writes": {
                "slots_planned": len(planned),
                "newly_written": len(planned) - replayed_slots,
                "reused_from_ledger": replayed_slots,
                "at": submit_at,
                "seq_base": seq_base,
            },
        }
        app.store.mark_submit(session_id, result)
        return result

    # ------------------------------------------------------------------ 记忆
    @app.get("/api/learners/{learner_id}/memories")
    async def memories(
        learner_id: str,
        kind: str | None = Query(default=None, max_length=40),
        states: list[str] = Query(default=["active"]),
    ) -> dict[str, Any]:
        learner = _require(learner_id)
        entries = await app.memory.list_memories(scope_id=learner["scope_id"], kind=kind, states=list(states))
        view = fold(entries, app.bank.modules)
        return {
            "scope_id": learner["scope_id"],
            "entries": entries,
            "topics": [s.as_dict() for s in sorted(view.topics.values(), key=lambda s: s.mastery)],
            "preference": view.preference.as_dict(),
            "last_plan": view.last_plan,
        }

    @app.post("/api/learners/{learner_id}/memories/search")
    async def search(learner_id: str, payload: SearchBody) -> dict[str, Any]:
        learner = _require(learner_id)
        return await app.memory.search(scope_id=learner["scope_id"], query=payload.query, limit=payload.limit)

    @app.post("/api/learners/{learner_id}/memories/prepare")
    async def prepare(learner_id: str, payload: SearchBody) -> dict[str, Any]:
        learner = _require(learner_id)
        return await app.memory.prepare(scope_id=learner["scope_id"], query=payload.query, max_bytes=4096)

    @app.put("/api/learners/{learner_id}/preference")
    async def save_preference(learner_id: str, payload: PreferenceUpdate) -> dict[str, Any]:
        learner = _require(learner_id)
        focus = [t for t in payload.focus_topics if t in app.bank.modules]
        text = (
            f"复习偏好：每次 {payload.daily_questions} 题"
            + (f"，侧重 {'、'.join(app.bank.modules[t] for t in focus)}" if focus else "，不指定侧重")
            + (f"；备注：{payload.note}" if payload.note else "")
        )
        receipt = await app.memory.remember(
            scope_id=learner["scope_id"],
            kind="preference",
            payload={
                "app": "recall-coach",
                "v": 1,
                "at": _now(),
                "seq": app.store.bump_counter(learner_id, "memory"),
                "daily_questions": payload.daily_questions,
                "focus_topics": focus,
                "note": payload.note,
                "summary": text,
            },
            reason="复习偏好写入",
        )
        return {"text": text, "receipt": receipt}

    @app.post("/api/learners/{learner_id}/memories/{artifact_id}/forget")
    async def forget(learner_id: str, artifact_id: str, revision: int = Query(ge=1), state_version: int = Query(ge=0)) -> dict[str, Any]:
        learner = _require(learner_id)
        if not re.fullmatch(r"art_[0-9a-f]{16,64}", artifact_id):
            raise HTTPException(422, {"code": "invalid_artifact_id", "message": "记忆标识格式不正确。"})
        return await app.memory.forget(
            scope_id=learner["scope_id"],
            artifact_id=artifact_id,
            family="atomic-memory",
            revision=revision,
            state_version=state_version,
        )

    @app.post("/api/learners/{learner_id}/memories/forget-all")
    async def forget_all(learner_id: str) -> dict[str, Any]:
        learner = _require(learner_id)
        entries = await app.memory.list_memories(scope_id=learner["scope_id"], states=["active"])
        receipts: list[dict[str, Any]] = []
        try:
            for entry in entries:
                receipts.append(
                    await app.memory.forget(
                        scope_id=learner["scope_id"],
                        artifact_id=entry["artifact"]["artifact_id"],
                        family=entry["artifact"]["family"],
                        revision=entry["artifact"]["revision"],
                        state_version=entry["state_version"],
                    )
                )
        except MemoryBackendError as exc:
            exc.pending_slots = [entry["artifact"]["artifact_id"] for entry in entries[len(receipts):]]
            exc.notice = (
                f"批量遗忘只完成了一部分：{len(receipts)} 条已确认退出召回，剩余 {len(exc.pending_slots)} 条本次没有变更。"
                "已完成的部分不会因重试被撤销，重试只处理仍然激活的条目。"
            )
            raise
        return {"forgotten": len(receipts), "receipts": receipts}

    # ------------------------------------------------------------------ 过程记录
    @app.get("/api/learners/{learner_id}/export")
    async def export(learner_id: str, fmt: Literal["json", "md"] = Query(default="json")) -> Any:
        learner = _require(learner_id)
        scope_id = learner["scope_id"]
        entries = await app.memory.list_memories(scope_id=scope_id, states=["active", "forgotten", "merged", "retired"])
        view = fold(entries, app.bank.modules)
        data = {
            "app": "recall-coach",
            "exported_at": _now(),
            "learner": {k: v for k, v in learner.items()},
            "powercontext": {"base_url": app.memory.base_url, "scope_id": scope_id, "memory_entry_count": len(entries)},
            "mastery": [s.as_dict() for s in sorted(view.topics.values(), key=lambda s: s.mastery)],
            "preference": view.preference.as_dict(),
            "last_plan": view.last_plan,
            "memories": entries,
            "attempts": app.store.attempts(learner_id),
        }
        if fmt == "json":
            return JSONResponse(data)
        return PlainTextResponse(_render_markdown(data), media_type="text/markdown; charset=utf-8")

    @app.get("/api/operations")
    async def operations(limit: int = Query(default=40, ge=1, le=400)) -> dict[str, Any]:
        path: Path = app.settings.operation_log
        if not path.exists():
            return {"entries": [], "path": str(path)}
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        entries = []
        for line in reversed(lines):
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
        return {"entries": entries, "path": str(path)}

    # ------------------------------------------------------------------ 页面
    web_dir = PROJECT_ROOT / "web"

    @app.get("/")
    async def page_home() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @app.get("/diagnose")
    async def page_diagnose() -> FileResponse:
        return FileResponse(web_dir / "diagnose.html")

    @app.get("/memory")
    async def page_memory() -> FileResponse:
        return FileResponse(web_dir / "memory.html")

    @app.get("/verify")
    async def page_verify() -> FileResponse:
        return FileResponse(web_dir / "verify.html")

    app.mount("/static", StaticFiles(directory=web_dir / "assets"), name="static")
    return app


def _render_markdown(data: dict[str, Any]) -> str:
    learner = data["learner"]
    backend = data["powercontext"]
    lines = [
        "# 拾忆 · Recall Coach 学习过程记录",
        "",
        f"- 学习身份：{learner['display_name']}（`{learner['learner_id']}`）",
        f"- PowerContext 服务：`{backend['base_url']}`",
        f"- 记忆域 Scope：`{backend['scope_id']}`",
        f"- 导出时间：{data['exported_at']}",
        f"- 记忆条目（含已遗忘）：{backend['memory_entry_count']}",
        "",
        "## 知识点掌握情况（由 PowerContext 记忆折叠得到）",
        "",
        "| 知识点 | 掌握度 | 作答 | 正确 | 连错 | 记忆条数 | 判定 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in data["mastery"]:
        lines.append(
            f"| {row['name']} | {row['mastery']:.2f} | {row['attempts']} | {row['correct']} | {row['wrong_streak']} | {row['evidence_count']} | "
            f"{'薄弱' if row['weak'] else '达标'} |"
        )
    preference = data["preference"]
    lines += [
        "",
        "## 复习偏好",
        "",
        f"- 每次题量：{preference['daily_questions']}",
        f"- 侧重知识点：{('、'.join(preference['focus_topics']) or '未指定')}",
        f"- 备注：{preference['note'] or '无'}",
    ]
    last_plan = data["last_plan"]
    if last_plan:
        lines += [
            "",
            "## 最近一次复习安排（记忆中的 plan 条目）",
            "",
            f"- 会话：`{last_plan['session_id']}`　模式：{last_plan['mode']}",
            f"- 知识点：{'、'.join(last_plan['topics']) or '无'}",
            f"- 题目：{'、'.join(f'`{q}`' for q in last_plan['question_ids']) or '无'}",
            f"- 记忆标识：`{last_plan['artifact']['family']}/{last_plan['artifact']['artifact_id']}@{last_plan['artifact']['revision']}`",
        ]
    lines += ["", "## 学习记忆（PowerContext 原子记忆条目）", ""]
    for entry in data["memories"]:
        artifact = entry["artifact"]
        payload = entry.get("payload") or {}
        summary = payload.get("summary") if isinstance(payload, dict) else None
        lines.append(
            f"- `{artifact['family']}/{artifact['artifact_id']}@{artifact['revision']}`　"
            f"kind={entry['kind']}　state={entry['state']}（state_version={entry['state_version']}）"
        )
        if summary:
            lines.append(f"  - {summary}")
    lines += ["", "## 答题流水（本地过程记录，仅用于导出）", ""]
    for row in data["attempts"]:
        lines.append(
            f"- {row['created_at']}　会话 `{row['session_id']}`　题目 `{row['question_id']}`（{row['topic']}）　"
            f"选择 {row['chosen'] or '未作答'} / 正确答案 {row['answer']}　{'✓' if row['correct'] else '✗'}"
        )
    lines += [
        "",
        "---",
        "",
        "说明：题目来自本项目内置原创题库（固定 24 题），系统不调用任何模型服务；"
        "掌握情况、薄弱点、复习偏好与复习安排均存放在 PowerContext，通过公开 Client SDK 读写。",
        "",
    ]
    return "\n".join(lines)


def merge_view(view: MemoryView, updates: list[dict[str, Any]]) -> MemoryView:
    """把本轮新写入记忆的掌握变化并入视图，用于返回最新复习优先级。"""
    topics: dict[str, TopicState] = {name: state for name, state in view.topics.items()}
    for update in updates:
        topic = update["topic"]
        state = topics.get(topic) or TopicState(topic=topic, name=update["topic_name"])
        state.mastery = update["mastery"]
        state.attempts = update["attempts"]
        state.correct = update["correct"]
        state.wrong_streak = update["wrong_streak"]
        state.name = update["topic_name"]
        topics[topic] = state
    return MemoryView(topics=topics, preference=view.preference, last_plan=view.last_plan, entries=list(view.entries))


app = create_app()
