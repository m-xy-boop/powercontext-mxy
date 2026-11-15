"""Generate the reviewable DSL; all business state travels in signed JSON tickets."""

import os
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
nodes, edges = [], []


def node(ident, title, kind, x, y, **data):
    nodes.append(
        {
            "id": ident,
            "type": "custom",
            "position": {"x": x, "y": y},
            "width": 244,
            "height": 98,
            "sourcePosition": "right",
            "targetPosition": "left",
            "data": {
                "title": title,
                "type": kind,
                "desc": "",
                "selected": False,
                **data,
            },
        }
    )


def edge(source, target, handle="source"):
    edges.append(
        {
            "id": source + "-" + handle + "-" + target,
            "source": source,
            "target": target,
            "sourceHandle": handle,
            "targetHandle": "target",
            "type": "custom",
            "data": {
                "sourceType": next(
                    n["data"]["type"] for n in nodes if n["id"] == source
                ),
                "targetType": next(
                    n["data"]["type"] for n in nodes if n["id"] == target
                ),
                "isInIteration": False,
                "isInLoop": False,
            },
        }
    )


def http(ident, title, path, x, y, body, form=False):
    node(
        ident,
        title,
        "http-request",
        x,
        y,
        method="post",
        url="{{#env.ECHO_BASE_URL#}}" + path,
        authorization={"type": "no-auth"},
        headers="X-Echo-Key:{{#env.ECHO_TOOL_KEY#}}\n"
        + ("" if form else "Content-Type:application/json"),
        params="",
        ssl_verify=True,
        timeout={"connect": 10, "read": 180, "write": 30},
        body={"type": "x-www-form-urlencoded" if form else "json", "data": body},
        error_strategy="fail-branch",
        retry_config={"retry_enabled": False, "max_retries": 0, "retry_interval": 1000},
    )


variables = []
for key, label, default, limit in [
    ("player_id", "旅人编号（相同编号续玩）", "traveler-demo", 48),
    ("session_id", "本次会话编号（换会话仍可找回记忆）", "visit-1", 64),
    ("request_id", "唯一行动编号（重试保持相同）", "turn-1", 80),
    ("action", "行动", "enter", 20),
    ("memory", "记忆", "umbrella", 20),
    ("words", "对店主说的话", "", 400),
]:
    entry = {
        "variable": key,
        "label": label,
        "type": "text-input",
        "required": key != "words",
        "max_length": limit,
        "default": default,
    }
    if key in {"action", "memory"}:
        entry["type"] = "select"
        entry["options"] = (
            ["enter", "pawn", "ask", "redeem", "finish"]
            if key == "action"
            else ["umbrella", "name", "song"]
        )
    variables.append(entry)
node("start", "旅人行动", "start", 0, 100, variables=variables)
http(
    "recall",
    "PowerContext 读账本与行动预检",
    "/tools/context",
    300,
    100,
    [
        {
            "key": v["variable"],
            "type": "text",
            "value": "{{#start." + v["variable"] + "#}}",
        }
        for v in variables
    ],
    True,
)
http(
    "route",
    "提取回执状态",
    "/tools/route",
    600,
    100,
    [{"key": "", "type": "text", "value": "{{#recall.body#}}"}],
)
node(
    "branch",
    "是否需要生成新剧情",
    "if-else",
    900,
    100,
    cases=[
        {
            "case_id": "true",
            "logical_operator": "and",
            "conditions": [
                {
                    "id": "needs-model",
                    "variable_selector": ["route", "body"],
                    "varType": "string",
                    "comparison_operator": "is",
                    "value": "narrate",
                }
            ],
        }
    ],
)
http(
    "narrate",
    "本地 Qwen 根据记忆生成对白",
    "/tools/narrate",
    1200,
    0,
    [{"key": "", "type": "text", "value": "{{#recall.body#}}"}],
)
http(
    "commit",
    "PowerContext 写交易与剧情",
    "/tools/commit",
    1500,
    0,
    [{"key": "", "type": "text", "value": "{{#narrate.body#}}"}],
)
http(
    "replay",
    "从 PowerContext 返回已有回执",
    "/tools/commit",
    1200,
    260,
    [{"key": "", "type": "text", "value": "{{#recall.body#}}"}],
)
node(
    "end",
    "返回剧情与账本",
    "end",
    1800,
    0,
    outputs=[
        {
            "variable": "story",
            "value_selector": ["commit", "body"],
            "value_type": "string",
        }
    ],
)
node(
    "cached_end",
    "返回原始剧情（不重复交易）",
    "end",
    1500,
    260,
    outputs=[
        {
            "variable": "story",
            "value_selector": ["replay", "body"],
            "value_type": "string",
        }
    ],
)
for a, b in [
    ("start", "recall"),
    ("recall", "route"),
    ("route", "branch"),
    ("narrate", "commit"),
    ("commit", "end"),
    ("replay", "cached_end"),
]:
    edge(a, b)
edge("branch", "narrate", "true")
edge("branch", "replay", "false")
for index, ident in enumerate(["recall", "route", "narrate", "commit", "replay"]):
    error_id = ident + "_error"
    node(
        error_id,
        "行动中止：返回错误信息",
        "end",
        300 * index,
        560,
        outputs=[
            {
                "variable": "error",
                "value_selector": [ident, "body"],
                "value_type": "string",
            },
            {
                "variable": "status_code",
                "value_selector": [ident, "status_code"],
                "value_type": "number",
            },
        ],
    )
    edge(ident, error_id, "fail-branch")
dsl = {
    "version": "0.4.0",
    "kind": "app",
    "dependencies": [],
    "app": {
        "name": "回声当铺｜记忆有价，后果自负",
        "mode": "workflow",
        "icon": "☂️",
        "icon_type": "emoji",
        "icon_background": "#E6D9C6",
        "use_icon_as_answer_icon": False,
        "description": "典当记忆寻找妹妹。跨会话保留交易，四种结局；Dify 编排 + PowerContext 持久化 + 本地 Qwen。",
    },
    "workflow": {
        "conversation_variables": [],
        "environment_variables": [
            {
                "id": "echo-url",
                "name": "ECHO_BASE_URL",
                "value_type": "string",
                "selector": ["env", "ECHO_BASE_URL"],
                "value": os.getenv("DSL_ECHO_URL", "http://host.docker.internal:18080"),
            },
            {
                "id": "echo-key",
                "name": "ECHO_TOOL_KEY",
                "value_type": "secret",
                "selector": ["env", "ECHO_TOOL_KEY"],
                "value": "REPLACE_WITH_ECHO_TOOL_KEY",
            },
        ],
        "features": {
            "file_upload": {"enabled": False},
            "opening_statement": "",
            "retriever_resource": {"enabled": False},
            "speech_to_text": {"enabled": False},
            "suggested_questions": [],
            "suggested_questions_after_answer": {"enabled": False},
            "text_to_speech": {"enabled": False},
            "sensitive_word_avoidance": {"enabled": False},
        },
        "graph": {
            "nodes": nodes,
            "edges": edges,
            "viewport": {"x": 0, "y": 0, "zoom": 0.65},
        },
    },
}
destination = ROOT / "dsl" / "echo-pawnshop.yml"
destination.parent.mkdir(exist_ok=True)
destination.write_text(
    yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False), encoding="utf-8"
)
print(destination)
