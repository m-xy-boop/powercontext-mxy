"""真实链路验收：全部通过真实 PowerContext Server（SQLite 持久化，无模型密钥）完成。

任一断言失败都代表真实行为不达标，测试不会用模拟数据兜底。
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from fastapi.testclient import TestClient

ARTIFACT_RE = re.compile(r"^art_[0-9a-f]{16,64}$")


def reset(client: TestClient, learner_id: str, display_name: str) -> tuple[str, str]:
    """建立（或复用）学习身份并清空激活记忆，返回 (learner_id, scope_id)。"""
    response = client.post("/api/learners", json={"learner_id": learner_id, "display_name": display_name})
    assert response.status_code == 200, response.text
    scope_id = response.json()["scope"]["scope_id"]
    client.post(f"/api/learners/{learner_id}/memories/forget-all")
    active = client.get(f"/api/learners/{learner_id}/memories", params=[("states", "active")])
    assert active.status_code == 200
    assert active.json()["entries"] == [], "重置后不应残留激活记忆"
    return learner_id, scope_id


def build_answers(session: dict, *, wrong_topics: set[str]) -> list[dict[str, str]]:
    from app.bank import QuestionBank
    from app.config import load_settings

    bank = QuestionBank(load_settings().question_bank)
    answers = []
    for item in session["items"]:
        question = bank.get(item["question"]["id"])
        assert question is not None
        if item["topic"] in wrong_topics:
            chosen = next(key for key in "ABCD" if key != question.answer)
        else:
            chosen = question.answer
        answers.append({"question_id": question.id, "chosen": chosen})
    return answers


def submit_plan(client: TestClient, learner_id: str, session: dict, *, wrong_topics: set[str]) -> dict:
    response = client.post(
        f"/api/learners/{learner_id}/sessions/{session['session_id']}/submit",
        json={"answers": build_answers(session, wrong_topics=wrong_topics)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def topic_table(client: TestClient, learner_id: str) -> dict[str, tuple]:
    """知识点学习状态的可比快照（不含时间戳与记忆标识）。"""
    rows = client.get(f"/api/learners/{learner_id}").json()["topics"]
    return {r["topic"]: (r["mastery"], r["attempts"], r["correct"], r["wrong_streak"]) for r in rows}


def session_memories(client: TestClient, learner_id: str, session_id: str) -> list[dict]:
    """本次提交批次写入的掌握/薄弱条目（安排条目单独计数，不参与断言）。"""
    entries = client.get(f"/api/learners/{learner_id}/memories", params=[("states", "active")]).json()["entries"]
    return [
        e
        for e in entries
        if (e.get("payload") or {}).get("session_id") == session_id and e["kind"] in ("progress", "weakness")
    ]


def session_attempts(client: TestClient, learner_id: str, session_id: str) -> list[dict]:
    rows = client.get(f"/api/learners/{learner_id}/export", params={"fmt": "json"}).json()["attempts"]
    return [r for r in rows if r["session_id"] == session_id]


def test_context_reports_real_backend(live_client, live_settings) -> None:
    payload = live_client.get("/api/context").json()
    backend = payload["powercontext"]
    assert backend["base_url"] == live_settings.powercontext_url
    assert backend["ready"] is True, backend
    assert backend["health"]["status"] == "ready"
    assert "fts" in backend["search_modes"]
    assert backend["memory_extraction"] is False, "未配置推理模型时必须如实上报"
    assert "content" in backend["source_types"]
    assert payload["bank"]["question_total"] == 24
    assert "无需任何模型服务" in payload["bank"]["note"] or "固定内置题库" in payload["bank"]["note"]


def test_diagnosis_then_memory_driven_review(live_client) -> None:
    learner, scope_id = reset(live_client, "it-loop", "验收-闭环")

    session = live_client.post(f"/api/learners/{learner}/sessions").json()
    assert session["mode"] == "diagnosis"
    assert session["question_count"] == 8
    assert len({item["topic"] for item in session["items"]}) == 8
    plan_receipt = session["plan_receipt"]
    assert ARTIFACT_RE.match(plan_receipt["artifact"]["artifact_id"]), plan_receipt
    assert plan_receipt["verified_by"] == "get_atomic_memory_state"

    graded = submit_plan(live_client, learner, session, wrong_topics={"tx-isolation", "mvcc"})
    assert graded["score"] == {"correct": 6, "total": 8}
    receipts = graded["memory_receipts"]
    kinds = [r["kind"] for r in receipts]
    assert "progress" in kinds and "weakness" in kinds
    weak_ids = {r["artifact"]["artifact_id"] for r in receipts if r["kind"] == "weakness"}
    assert weak_ids and all(ARTIFACT_RE.match(a) for a in weak_ids)
    updates = {u["topic"]: u for u in graded["topic_updates"]}
    assert updates["tx-isolation"]["mastery"] < updates["tx-isolation"]["mastery_before"]

    # 记忆页面读到的是真实写入的条目
    memories = live_client.get(f"/api/learners/{learner}/memories").json()
    assert memories["scope_id"] == scope_id
    assert any(e["artifact"]["artifact_id"] in weak_ids for e in memories["entries"])
    assert [t for t in memories["topics"] if not t["weak"]] != memories["topics"]

    # 新会话：复习安排必须由记忆驱动，且引用同一批记忆标识
    review = live_client.post(f"/api/learners/{learner}/sessions").json()
    assert review["mode"] == "review"
    topics_in_plan = [item["topic"] for item in review["items"]]
    assert set(["tx-isolation", "mvcc"]).issubset(set(topics_in_plan[:2])), topics_in_plan
    first = review["items"][0]
    assert first["memory_refs"], "选题依据必须包含真实记忆引用"
    assert {ref["artifact_id"] for ref in first["memory_refs"]} & (weak_ids | {r["artifact"]["artifact_id"] for r in receipts})
    assert any("掌握度" in reason for reason in first["reasons"])

    # 答对薄弱点后掌握度上升，且写入新的变化记忆
    fixed = submit_plan(live_client, learner, review, wrong_topics=set())
    fixed_updates = {u["topic"]: u for u in fixed["topic_updates"]}
    assert fixed_updates["tx-isolation"]["mastery"] > updates["tx-isolation"]["mastery"]
    assert fixed_updates["tx-isolation"]["wrong_streak"] == 0

    final = live_client.get(f"/api/learners/{learner}").json()
    tx = next(t for t in final["topics"] if t["topic"] == "tx-isolation")
    assert tx["wrong_streak"] == 0 and not tx["weak"]
    assert tx["evidence_count"] >= 2


def test_memory_survives_application_restart(live_settings, live_client) -> None:
    learner, _ = reset(live_client, "it-restart", "验收-重启")
    session = live_client.post(f"/api/learners/{learner}/sessions").json()
    graded = submit_plan(live_client, learner, session, wrong_topics={"lock"})
    written = {r["artifact"]["artifact_id"] for r in graded["memory_receipts"]}

    # 全新进程内的应用实例（等同重启服务）重新读取同一 Scope
    from app.main import create_app

    with TestClient(create_app(live_settings)) as second:
        memories = second.get(f"/api/learners/{learner}/memories").json()
        ids = {e["artifact"]["artifact_id"] for e in memories["entries"]}
        assert written.issubset(ids), (written - ids)
        review = second.post(f"/api/learners/{learner}/sessions").json()
        assert review["mode"] == "review"
        assert review["items"][0]["topic"] == "lock"
        assert {ref["artifact_id"] for ref in review["items"][0]["memory_refs"]} & ids


def test_identity_isolation(live_client) -> None:
    alice, alice_scope = reset(live_client, "it-alice", "验收-甲")
    bob, bob_scope = reset(live_client, "it-bob", "验收-乙")
    assert alice != bob and alice_scope != bob_scope

    session = live_client.post(f"/api/learners/{alice}/sessions").json()
    submit_plan(live_client, alice, session, wrong_topics={"recovery"})

    bob_memories = live_client.get(f"/api/learners/{bob}/memories").json()
    assert bob_memories["entries"] == []
    alice_ids = {e["artifact"]["artifact_id"] for e in live_client.get(f"/api/learners/{alice}/memories").json()["entries"]}
    assert alice_ids

    bob_plan = live_client.post(f"/api/learners/{bob}/sessions").json()
    assert bob_plan["mode"] == "diagnosis", "他人记忆不得影响本身份安排"
    recovery = next(r for r in bob_plan["ranking"] if r["topic"] == "recovery")
    assert recovery["mastery"] == 0.5 and recovery["attempts"] == 0

    canary = "孤岛校验码-7f3a"
    saved = live_client.put(
        f"/api/learners/{alice}/preference", json={"daily_questions": 5, "focus_topics": [], "note": canary}
    )
    assert saved.status_code == 200

    mine = live_client.post(f"/api/learners/{alice}/memories/search", json={"query": canary}).json()
    assert mine["hits"], "自己的记忆必须能被真实全文检索召回"
    theirs = live_client.post(f"/api/learners/{bob}/memories/search", json={"query": canary}).json()
    assert theirs["hits"] == [], "他人 Scope 的记忆不得被检索到"

    bob_ids = {e["artifact"]["artifact_id"] for e in live_client.get(f"/api/learners/{bob}/memories").json()["entries"]}
    assert bob_ids and not (bob_ids & alice_ids), "两个身份的记忆标识不得重叠"


def test_preference_changes_plan(live_client) -> None:
    learner, _ = reset(live_client, "it-pref", "验收-偏好")
    session = live_client.post(f"/api/learners/{learner}/sessions").json()
    submit_plan(live_client, learner, session, wrong_topics={"tx-acid"})

    saved = live_client.put(
        f"/api/learners/{learner}/preference",
        json={"daily_questions": 3, "focus_topics": ["storage", "不存在的知识点"], "note": "睡前 15 分钟"},
    )
    assert saved.status_code == 200
    assert saved.json()["receipt"]["kind"] == "preference"

    review = live_client.post(f"/api/learners/{learner}/sessions").json()
    assert review["question_count"] == 3
    assert review["preference"]["focus_topics"] == ["storage"]
    assert "storage" in [item["topic"] for item in review["items"]]
    assert "不存在的知识点" not in json.dumps(review, ensure_ascii=False)


def test_search_and_prepare_use_real_full_text_recall(live_client) -> None:
    learner, _ = reset(live_client, "it-recall", "验收-检索")
    session = live_client.post(f"/api/learners/{learner}/sessions").json()
    submit_plan(live_client, learner, session, wrong_topics={"idx-btree"})

    found = live_client.post(f"/api/learners/{learner}/memories/search", json={"query": "索引结构 薄弱点", "limit": 5}).json()
    assert found["mode"] == "text", found
    assert found["hits"], "写入的薄弱点记忆应能被真实全文检索命中"
    assert all(h["matched_by"] == ["text"] for h in found["hits"])

    prepared = live_client.post(f"/api/learners/{learner}/memories/prepare", json={"query": "索引结构"}).json()
    assert prepared["status"] == "ready"
    assert "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1" in prepared["content"]
    assert prepared["content_bytes"] > 0


def test_forget_all_clears_active_memory(live_client) -> None:
    learner, _ = reset(live_client, "it-forget", "验收-遗忘")
    live_client.put(f"/api/learners/{learner}/preference", json={"daily_questions": 4, "focus_topics": [], "note": "待清理"})
    active = live_client.get(f"/api/learners/{learner}/memories", params=[("states", "active")]).json()
    assert active["entries"]

    result = live_client.post(f"/api/learners/{learner}/memories/forget-all").json()
    assert result["forgotten"] == len(active["entries"])
    assert all(r["state"] == "forgotten" for r in result["receipts"])

    after = live_client.get(f"/api/learners/{learner}/memories", params=[("states", "active")]).json()
    assert after["entries"] == []
    history = live_client.get(f"/api/learners/{learner}/memories", params=[("states", "forgotten")]).json()
    assert history["entries"], "遗忘只退出召回，历史仍可核验"

    plan = live_client.post(f"/api/learners/{learner}/sessions").json()
    assert plan["mode"] == "diagnosis", "遗忘后应回到无记忆状态"


def test_export_contains_real_citations(live_client) -> None:
    learner, scope_id = reset(live_client, "it-export", "验收-导出")
    session = live_client.post(f"/api/learners/{learner}/sessions").json()
    graded = submit_plan(live_client, learner, session, wrong_topics={"storage"})
    written = {r["artifact"]["artifact_id"] for r in graded["memory_receipts"]}

    data = live_client.get(f"/api/learners/{learner}/export", params={"fmt": "json"}).json()
    assert data["powercontext"]["scope_id"] == scope_id
    assert data["learner"]["display_name"] == "验收-导出"
    assert {e["artifact"]["artifact_id"] for e in data["memories"]} >= written
    assert data["attempts"] and data["mastery"]

    markdown = live_client.get(f"/api/learners/{learner}/export", params={"fmt": "md"}).text
    assert "## 学习记忆" in markdown
    for artifact_id in written:
        assert artifact_id in markdown


def test_unavailable_backend_fails_loudly(live_settings, tmp_path) -> None:
    import copy

    from app.main import create_app

    settings = copy.copy(live_settings)
    object.__setattr__(settings, "powercontext_url", "http://127.0.0.1:60555")
    object.__setattr__(settings, "data_dir", tmp_path)
    object.__setattr__(settings, "operation_log", tmp_path / "ops.jsonl")

    with TestClient(create_app(settings)) as client:
        health = client.get("/api/context")
        assert health.status_code == 503, health.text
        body = health.json()
        assert body["error"]["code"] == "powercontext_unavailable"
        assert "无法连接 PowerContext Server" in body["error"]["message"]
        assert body["notice"]

        created = client.post("/api/learners", json={"learner_id": "it-offline", "display_name": "验收-离线"})
        assert created.status_code == 503
        assert created.json()["error"]["operation"] == "create_scope"
        # 服务不可达时绝不留下“已建立记忆”的假状态
        assert client.get("/api/learners/it-offline").status_code == 404
        assert not (tmp_path / "recall-coach.db").exists() or client.get("/api/learners/it-offline").json()["error"]["code"] == "learner_not_found"


def test_server_side_validation_is_surfaced(live_settings) -> None:
    from app.powercontext_service import MemoryRejected, PowerContextMemory

    async def run() -> None:
        memory = PowerContextMemory(live_settings)
        try:
            scope = await memory.ensure_scope(
                idempotency_key="recall-coach:v2:learner:it-validate",
                title="拾忆-it-validate",
                summary="拾忆 Recall Coach 学习身份记忆域",
            )
            with pytest.raises(MemoryRejected) as exc:
                await memory.remember(
                    scope_id=scope["scope_id"],
                    kind="weakness",
                    payload={"app": "recall-coach", "v": 1, "summary": "超长" * 6000},
                    reason="契约校验",
                )
            error = exc.value
            assert error.status_code == 422
            assert error.code == "invalid_request"
            assert error.request_id, "必须带回真实 request id 以便核验"
        finally:
            await memory.aclose()

    asyncio.run(run())


def test_repeat_submit_is_replayed_and_never_accumulates(live_settings, live_client) -> None:
    """独立审查问题 1：同一会话重复提交必须服务端持久幂等，不得二次累计。"""
    learner, _ = reset(live_client, "it-idempotent", "验收-重复提交")
    session = live_client.post(f"/api/learners/{learner}/sessions").json()
    sid = session["session_id"]
    url = f"/api/learners/{learner}/sessions/{sid}/submit"
    answers = build_answers(session, wrong_topics={"tx-isolation", "mvcc"})

    first = live_client.post(url, json={"answers": answers})
    assert first.status_code == 200, first.text
    graded = first.json()
    assert graded["score"] == {"correct": 6, "total": 8}
    assert graded["memory_writes"] == {
        "slots_planned": len(graded["memory_receipts"]),
        "newly_written": len(graded["memory_receipts"]),
        "reused_from_ledger": 0,
        "at": graded["memory_writes"]["at"],
        "seq_base": graded["memory_writes"]["seq_base"],
    }
    written = {r["artifact"]["artifact_id"] for r in graded["memory_receipts"]}
    entries_before = session_memories(live_client, learner, sid)
    attempts_before = session_attempts(live_client, learner, sid)
    table_before = topic_table(live_client, learner)
    assert len(entries_before) == len(written) and len(attempts_before) == session["question_count"]

    # 换一份答卷再提交同一会话：必须回放当时的判分，不重新判分、不再写记忆
    other = [dict(row) for row in answers]
    other[0]["chosen"] = next(key for key in "ABCD" if key != other[0]["chosen"])
    again = live_client.post(url, json={"answers": other})
    assert again.status_code == 200, again.text
    replayed = again.json()
    assert replayed["replay"]["repeated_submit"] is True
    assert replayed["score"] == graded["score"], "重复提交不得按新答卷重新判分"
    assert replayed["results"][0]["chosen"] == answers[0]["chosen"], "回放必须保留当时的作答选择"
    assert replayed["replay"]["new_memories_this_request"] == 0
    assert replayed["replay"]["new_attempts_this_request"] == 0
    # memory_writes 描述的是原始那一批写入（审计用），不是本次请求新增量
    assert replayed["memory_writes"]["newly_written"] == len(written)
    assert {r["artifact"]["artifact_id"] for r in replayed["memory_receipts"]} == written
    assert session_memories(live_client, learner, sid) == entries_before, "重复提交不得新增记忆条目"
    assert session_attempts(live_client, learner, sid) == attempts_before, "重复提交不得增加答题流水"
    assert topic_table(live_client, learner) == table_before, "重复提交不得叠加掌握度变化"

    # 新进程读取同一本地库：已完成状态是持久化的，不是内存标记
    from app.main import create_app

    with TestClient(create_app(live_settings)) as restarted:
        state = restarted.get(f"/api/learners/{learner}/sessions/{sid}").json()
        assert state["submit_state"] == "done"
        restarted_submit = restarted.post(url, json={"answers": other})
        assert restarted_submit.status_code == 200, restarted_submit.text
        assert restarted_submit.json()["replay"]["repeated_submit"] is True

    assert len(session_memories(live_client, learner, sid)) == len(written)
    assert len(session_attempts(live_client, learner, sid)) == session["question_count"]

    # 已完成保护不影响新一轮正常判分
    next_round = live_client.post(f"/api/learners/{learner}/sessions").json()
    fresh = submit_plan(live_client, learner, next_round, wrong_topics=set())
    assert "replay" not in fresh and fresh["memory_writes"]["reused_from_ledger"] == 0


def test_partial_batch_write_is_reported_and_completed_on_retry(live_app, live_client, monkeypatch) -> None:
    """独立审查问题 2：批量写入部分成功时必须如实提示、返回已完成回执，重试不重复累计。"""
    from app.powercontext_service import MemoryFailed

    learner, _ = reset(live_client, "it-partial", "验收-部分写入")
    control, _ = reset(live_client, "it-partial-control", "验收-无故障对照")
    session = live_client.post(f"/api/learners/{learner}/sessions").json()
    sid = session["session_id"]
    url = f"/api/learners/{learner}/sessions/{sid}/submit"
    answers = build_answers(session, wrong_topics={"tx-isolation", "mvcc"})

    original = live_app.memory.remember
    calls: list[str] = []

    async def flaky(*, scope_id: str, kind: str, payload: dict, reason: str) -> dict:
        calls.append(f"{kind}:{payload['topic']}")
        if len(calls) == 2:
            raise MemoryFailed(
                operation="remember_memory",
                code="injected_partial_write",
                message="验收注入：模拟批量写入第二条失败",
            )
        return await original(scope_id=scope_id, kind=kind, payload=payload, reason=reason)

    monkeypatch.setattr(live_app.memory, "remember", flaky)
    try:
        broken = live_client.post(url, json={"answers": answers})
    finally:
        monkeypatch.undo()

    assert broken.status_code == 502, broken.text
    body = broken.json()
    assert len(calls) == 2
    assert body["error"]["code"] == "injected_partial_write"
    assert len(body["written_receipts"]) == 1, "已写入的第一条必须把真实回执返回给页面"
    assert calls[1] in body["pending_slots"] and calls[0] not in body["pending_slots"], body["pending_slots"]
    assert "部分" in body["notice"] and "已真实写入" in body["notice"], body["notice"]
    assert "不会重复累计" in body["notice"]
    mid_entries = session_memories(live_client, learner, sid)
    assert len(mid_entries) == 1
    assert mid_entries[0]["artifact"]["artifact_id"] == body["written_receipts"][0]["artifact"]["artifact_id"]
    assert len(session_attempts(live_client, learner, sid)) == session["question_count"], "写入失败不得留下重复答题流水"

    retry = live_client.post(url, json={"answers": answers})
    assert retry.status_code == 200, retry.text
    done = retry.json()
    assert "replay" not in done, "未完成的提交应当补齐，而不是回放"
    assert done["memory_writes"]["reused_from_ledger"] == 1
    assert done["memory_writes"]["newly_written"] == len(done["memory_receipts"]) - 1
    final_entries = session_memories(live_client, learner, sid)
    assert {e["artifact"]["artifact_id"] for e in final_entries} == {r["artifact"]["artifact_id"] for r in done["memory_receipts"]}
    assert len(final_entries) == len(done["memory_receipts"]), "重试后同一会话每条变化只写一次"
    assert len(session_attempts(live_client, learner, sid)) == session["question_count"]

    # 对照：同样答卷、同样两步流程但没有注入故障的身份，学习状态必须逐项相等
    control_session = live_client.post(f"/api/learners/{control}/sessions").json()
    assert [item["question"]["id"] for item in control_session["items"]] == [item["question"]["id"] for item in session["items"]]
    submit_plan(live_client, control, control_session, wrong_topics={"tx-isolation", "mvcc"})
    assert topic_table(live_client, learner) == topic_table(live_client, control), "故障重试把同一轮变化叠加了"


def test_failure_notices_match_what_actually_happened(live_settings, tmp_path) -> None:
    """服务不可达时：读取类失败不得说成“写入失败”，提示必须与真实发生的事一致。"""
    import copy

    from app.main import create_app

    settings = copy.copy(live_settings)
    object.__setattr__(settings, "powercontext_url", "http://127.0.0.1:60556")
    object.__setattr__(settings, "data_dir", tmp_path)
    object.__setattr__(settings, "operation_log", tmp_path / "ops.jsonl")

    with TestClient(create_app(settings)) as client:
        reading = client.get("/api/context")
        assert reading.status_code == 503
        notice = reading.json()["notice"]
        assert "没有读到" in notice and "不会用模拟数据" in notice, notice
        assert "没有写入" not in notice

        writing = client.post("/api/learners", json={"learner_id": "it-notice", "display_name": "验收-提示"})
        assert writing.status_code == 503
        write_notice = writing.json()["notice"]
        assert "没有产生任何记忆写入" in write_notice, write_notice


def test_operation_log_records_real_calls(live_client, live_settings) -> None:
    learner = "it-loop"
    live_client.post(f"/api/learners/{learner}/sessions")
    entries = live_client.get("/api/operations", params={"limit": 20}).json()["entries"]
    assert entries, "每次真实调用都要留痕"
    assert {"create_scope", "remember_memory", "list_atomic_memories"} & {e["operation"] for e in entries}
    assert all(e["backend"] == live_settings.powercontext_url for e in entries)
    for entry in entries:
        if entry["operation"] == "remember_memory":
            assert ARTIFACT_RE.match(entry["artifact"]["artifact_id"]), entry
    source_check = asyncio.run(_source_capture(live_settings, learner))
    assert source_check["source_ref"].startswith("content/")


async def _source_capture(settings, learner: str) -> dict:
    from app.powercontext_service import PowerContextMemory

    memory = PowerContextMemory(settings)
    try:
        scope = await memory.ensure_scope(
            idempotency_key=f"recall-coach:v2:learner:{learner}",
            title=f"拾忆-{learner}",
            summary="拾忆 Recall Coach 学习身份记忆域",
        )
        return await memory.capture_source(
            scope_id=scope["scope_id"],
            source_id=f"attempt-{asyncio.get_running_loop().time()}",
            content="验收：一次答题流水作为证据留存",
            metadata={"app": "recall-coach", "kind": "attempt-log"},
        )
    finally:
        await memory.aclose()
