#!/usr/bin/env python3
"""端到端闭环验证：选题由真实 PowerContext 记忆决定，判分结果实时写回记忆。

用法：
  uv run python scripts/e2e_loop.py --base-url http://127.0.0.1:8642 --learner e2e-demo
  uv run python scripts/e2e_loop.py --learner e2e-demo --existing-learner --expect-first-topic mvcc

题目答案只用于本脚本构造“故意答错/全部答对”两种输入，取自仓库内置题库文件；
服务端判分与页面作答仍然完全独立，脚本不调用任何模型服务。
任何一步与预期不符即以非零码退出，绝不用假数据补齐。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
PREPARED_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"

_state = {"step": 0}


def snap(record: dict[str, Any], label: str, **fields: Any) -> None:
    _state["step"] += 1
    print(f"[{_state['step']:02d}] {label}")
    for key, value in fields.items():
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
        print(f"       {key:14s}= {text}")
    record["steps"].append({"step": _state["step"], "label": label, **fields})


def check(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        raise SystemExit(1)


def ref(artifact_or_entry: Any) -> str:
    entry = artifact_or_entry.get("artifact", artifact_or_entry) if isinstance(artifact_or_entry, dict) else artifact_or_entry
    return f"{entry['family']}/{entry['artifact_id']}@{entry['revision']}"


def load_answers() -> dict[str, str]:
    raw = json.loads((ROOT / "data" / "questions.json").read_text(encoding="utf-8"))
    return {item["id"]: item["answer"] for item in raw["questions"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="拾忆 · Recall Coach 端到端闭环验证")
    parser.add_argument("--base-url", default="http://127.0.0.1:8642")
    parser.add_argument("--learner", default="e2e-demo")
    parser.add_argument("--display-name", default="端到端验证身份")
    parser.add_argument("--existing-learner", action="store_true", help="复用已有身份：用于服务重启后的续接验证")
    parser.add_argument("--expect-first-topic", default=None, help="续接时断言首题所属知识点")
    parser.add_argument("--json-out", default=None, help="把每步结果写入 JSON 文件留档")
    args = parser.parse_args()

    answers = load_answers()
    record: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "learner_id": args.learner,
        "steps": [],
    }

    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        context = client.get("/api/context").json()
        powercontext = context["powercontext"]
        snap(
            record,
            "读取应用与 PowerContext 能力",
            backend=powercontext["base_url"],
            health=powercontext["health"]["status"],
            search_modes=powercontext["search_modes"],
            memory_extraction=powercontext["memory_extraction"],
            bank=f"{context['bank']['question_total']} 题 / {len(context['bank']['modules'])} 个知识点",
        )
        check(powercontext["ready"], "PowerContext /health/ready 未就绪，拒绝继续（不回退到假记忆）")

        learner_id = args.learner
        if args.existing_learner:
            check(
                any(row["learner_id"] == learner_id for row in context["learners"]),
                f"本机记录中没有身份 {learner_id}，无法做续接验证",
            )
            snap(record, "复用已存在的身份（重启服务后）", learner=learner_id)
        else:
            response = client.post("/api/learners", json={"learner_id": learner_id, "display_name": args.display_name})
            check(response.status_code == 200, f"建立身份失败 HTTP {response.status_code}: {response.text[:300]}")
            created = response.json()
            learner_id = created["learner"]["learner_id"]
            snap(
                record,
                "建立学习身份（幂等 Scope）",
                scope=created["scope"]["scope_id"],
                title=created["scope"]["title"],
                scope_version=created["scope"]["version"],
            )
        api = f"/api/learners/{learner_id}"
        entries_before_round = client.get(api).json()["memory_entry_count"]

        # 第一轮：安排 + 故意答错两处
        session = client.post(f"{api}/sessions").json()
        snap(
            record,
            "第一轮复习安排",
            mode=session["mode"],
            count=session["question_count"],
            topics=[item["topic"] for item in session["items"]],
            plan_memory=ref(session["plan_receipt"]),
        )
        expected_mode = "review" if args.existing_learner else "diagnosis"
        check(session["mode"] == expected_mode, f"第一轮模式应为 {expected_mode}，实际 {session['mode']}")
        if args.expect_first_topic:
            check(
                session["items"][0]["topic"] == args.expect_first_topic,
                f"重启续接首题应为 {args.expect_first_topic}，实际 {session['items'][0]['topic']}",
            )
            snap(record, "重启后按记忆续接选题", first_topic=session["items"][0]["topic"], expected=args.expect_first_topic)

        targets = {item["question"]["id"] for item in session["items"][:2]}
        payload = [
            {
                "question_id": item["question"]["id"],
                "chosen": wrong_option(item, answers[item["question"]["id"]]) if item["question"]["id"] in targets else answers[item["question"]["id"]],
            }
            for item in session["items"]
        ]
        result = client.post(f"{api}/sessions/{session['session_id']}/submit", json={"answers": payload}).json()
        wrong_ids = sorted(row["question_id"] for row in result["results"] if not row["correct"])
        snap(
            record,
            "第一轮判分并写回真实记忆",
            score=f"{result['score']['correct']}/{result['score']['total']}",
            wrong=wrong_ids,
            receipts=[(row["kind"], ref(row), f"{row['state']}/v{row['state_version']}", f"{row['text_bytes']}B", row["verified_by"]) for row in result["memory_receipts"]],
            weak_after=[(row["topic"], row["mastery"], row["wrong_streak"]) for row in result["ranking_after"] if row["weak"]],
        )
        check(targets <= set(wrong_ids), f"故意答错的题目没有被判错：{sorted(targets)} vs {wrong_ids}")
        check(bool(result["memory_receipts"]), "判分后没有写入任何记忆条目")

        # 重复提交保护：换一份答卷再次提交同一会话，服务端必须回放而不是再次累计
        replay_payload = [dict(row) for row in payload]
        replay_payload[0]["chosen"] = flip(replay_payload[0]["chosen"])
        again = client.post(f"{api}/sessions/{session['session_id']}/submit", json={"answers": replay_payload}).json()
        entries_after_round = client.get(api).json()["memory_entry_count"]
        snap(
            record,
            "重复提交同一会话（持久幂等保护）",
            replayed=again.get("replay", {}).get("repeated_submit"),
            recorded_at=again.get("replay", {}).get("recorded_at"),
            kept_choice=again["results"][0]["chosen"],
            resent_choice=replay_payload[0]["chosen"],
            new_memories_this_request=again["replay"]["new_memories_this_request"],
            original_batch_written=again["memory_writes"]["newly_written"],
            memory_delta=entries_after_round - entries_before_round,
            receipts=len(result["memory_receipts"]),
        )
        check(again.get("replay", {}).get("repeated_submit") is True, "重复提交没有被识别为回放")
        check(again["results"][0]["chosen"] == payload[0]["chosen"], "回放必须保留当时的作答选择")
        check(again["score"] == result["score"], "重复提交不得按新答卷重新判分")
        check(again["replay"]["new_memories_this_request"] == 0, "重复提交又写入了新的记忆条目")
        check(again["replay"]["new_attempts_this_request"] == 0, "回放响应不应声称新增了答题记录")
        check(
            entries_after_round - entries_before_round == len(result["memory_receipts"]) + 1,
            "第一轮的记忆条数应等于 1 条安排 + 判分回执，重复提交不得叠加",
        )

        overview = client.get(api).json()
        weak_topics = [row["topic"] for row in overview["topics"] if row["weak"]]
        snap(
            record,
            "读回该身份记忆概览",
            entry_count=overview["memory_entry_count"],
            topics_covered=len(overview["topics"]),
            weak=weak_topics,
            last_plan=ref(overview["last_plan"]) if overview["last_plan"] else "无",
        )
        check(overview["memory_entry_count"] > 0, "记忆读回为空")
        check(bool(weak_topics), "第一轮未形成薄弱知识点")
        check(overview["memory_entry_count"] == entries_after_round, "读回条数与重复提交后一致")
        mastery_before = {row["topic"]: row["mastery"] for row in overview["topics"]}

        # 第二轮：复习安排必须由记忆驱动
        second = client.post(f"{api}/sessions").json()
        topics_second = [item["topic"] for item in second["items"]]
        refs_second = [entry for item in second["items"] for entry in item["memory_refs"]]
        weak_covered = [topic for topic in weak_topics if topic in topics_second]
        snap(
            record,
            "第二轮安排（记忆驱动选题）",
            mode=second["mode"],
            topics=topics_second,
            weak_covered=weak_covered,
            first_topic=second["items"][0]["topic"],
            first_reasons=second["items"][0]["reasons"],
            cited_memories=sorted({ref(entry) for entry in refs_second})[:6],
        )
        check(second["mode"] == "review", "已有记忆时应为复习模式")
        check(bool(weak_covered), "复习安排没有覆盖记忆中的薄弱知识点")
        check(bool(refs_second), "复习依据没有引用任何记忆标识")

        # 全部答对：薄弱点掌握度应上升
        payload_all = [{"question_id": item["question"]["id"], "chosen": answers[item["question"]["id"]]} for item in second["items"]]
        fixed = client.post(f"{api}/sessions/{second['session_id']}/submit", json={"answers": payload_all}).json()
        after = client.get(api).json()
        mastery_after = {row["topic"]: row["mastery"] for row in after["topics"]}
        improved = [topic for topic in weak_topics if mastery_after.get(topic, 0) > mastery_before.get(topic, 0)]
        snap(
            record,
            "全部答对后记忆更新",
            score=f"{fixed['score']['correct']}/{fixed['score']['total']}",
            receipt_count=len(fixed["memory_receipts"]),
            mastery_before={k: mastery_before[k] for k in weak_topics if k in mastery_before},
            mastery_after={k: mastery_after.get(k) for k in weak_topics},
            improved=improved,
        )
        check(fixed["score"]["correct"] == fixed["score"]["total"], "按题库答案作答仍有错题，说明判分或题库不一致")
        check(bool(improved), "答对后薄弱知识点掌握度没有上升")

        # 复习偏好：题量与侧重点应改变安排
        focus = weak_topics[0]
        preference = client.put(f"{api}/preference", json={"daily_questions": 3, "focus_topics": [focus], "note": "e2e：先补最薄弱的一块"}).json()
        third = client.post(f"{api}/sessions").json()
        snap(
            record,
            "写入复习偏好并再次安排",
            text=preference["text"],
            memory=ref(preference["receipt"]),
            third_count=third["question_count"],
            third_topics=[item["topic"] for item in third["items"]],
            focus_included=focus in [item["topic"] for item in third["items"]],
        )
        check(third["question_count"] == 3, f"题量应遵循偏好 3 题，实际 {third['question_count']}")

        # 检索 / 有界上下文
        search = client.post(f"{api}/memories/search", json={"query": "薄弱点", "limit": 5}).json()
        snap(
            record,
            "真实检索记忆（无嵌入模型时为全文模式）",
            mode=search["mode"],
            hits=[(hit["kind"], ref(hit), round(hit["score"], 5), hit["matched_by"]) for hit in search["hits"]],
        )
        check(bool(search["hits"]), "检索没有命中任何记忆")

        prepared = client.post(f"{api}/memories/prepare", json={"query": "薄弱知识点 复习"}).json()
        snap(
            record,
            "PreparedContext 有界上下文",
            schema=prepared["schema"],
            status=prepared["status"],
            content_bytes=prepared["content_bytes"],
            has_marker=PREPARED_MARKER in prepared["content"],
        )
        check(PREPARED_MARKER in prepared["content"], "PreparedContext 缺少真实标记头")

        # 导出
        export_json = client.get(f"{api}/export", params={"fmt": "json"}).json()
        export_md = client.get(f"{api}/export", params={"fmt": "md"}).text
        snap(
            record,
            "导出学习过程记录",
            memories=len(export_json["memories"]),
            attempts=len(export_json["attempts"]),
            md_sections=[line for line in export_md.splitlines() if line.startswith("## ")],
            first_cite=ref(export_json["memories"][0]),
        )
        check("## 学习记忆" in export_md, "Markdown 导出缺少记忆章节")
        check(export_json["memories"][0]["artifact"]["artifact_id"] in export_md, "Markdown 导出没有引用真实记忆标识")
        check(len(export_json["attempts"]) >= 8, "答题流水记录少于已作答题数")

        # 遗忘一条薄弱记忆
        weaknesses = client.get(f"{api}/memories", params={"kind": "weakness"}).json()["entries"]
        if weaknesses:
            target = weaknesses[0]
            receipt = client.post(
                f"{api}/memories/{target['artifact']['artifact_id']}/forget",
                params={"revision": target["artifact"]["revision"], "state_version": target["state_version"]},
            ).json()
            still = client.get(f"{api}/memories", params={"kind": "weakness"}).json()["entries"]
            snap(
                record,
                "按条遗忘薄弱记忆",
                forgotten=target["artifact"]["artifact_id"],
                state=receipt["state"],
                state_version=receipt["state_version"],
                active_left=len(still),
            )
            check(all(row["artifact"]["artifact_id"] != target["artifact"]["artifact_id"] for row in still), "遗忘后条目仍在激活列表内")
        else:
            snap(record, "薄弱记忆已随掌握度达标自然收敛", weakness_entries=0)

        history = client.get(f"{api}/memories", params={"states": ["active", "forgotten"]}).json()
        operations = client.get("/api/operations", params={"limit": 5}).json()
        snap(
            record,
            "历史可核验与操作留痕",
            active_and_forgotten=len(history["entries"]),
            forgotten_total=sum(1 for row in history["entries"] if row["state"] == "forgotten"),
            log_path=operations["path"],
            last_operations=[row["operation"] for row in operations["entries"]],
        )
        record["powercontext"] = {
            "base_url": powercontext["base_url"],
            "scope_id": history["scope_id"],
            "search_modes": powercontext["search_modes"],
            "memory_extraction": powercontext["memory_extraction"],
        }

    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    record["learner_id"] = learner_id
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {args.json_out}")
    print("\n端到端闭环通过：每次选题都读回真实记忆，每次判分都写回真实记忆，遗忘与导出均可核验。")
    return 0


def flip(chosen: str) -> str:
    return next(key for key in "ABCD" if key != chosen)


def wrong_option(item: dict[str, Any], answer: str) -> str:
    keys = [option["key"] for option in item["question"]["options"]]
    for key in keys:
        if key != answer:
            return key
    check(False, f"题目 {item['question']['id']} 选项不足，无法构造错误答案")
    return answer


if __name__ == "__main__":
    raise SystemExit(main())
