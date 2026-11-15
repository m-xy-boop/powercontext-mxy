"""Acceptance via the actual browser gateway -> Dify API -> tools -> PowerContext.

Uses fresh player IDs, never deletes an existing player's story.
"""

import argparse
import asyncio
import json
import os
import platform
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


async def verify():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--output", default="evidence/acceptance.json")
    args = parser.parse_args()
    run_id = "accept-" + uuid.uuid4().hex[:10]
    steps = []
    async with httpx.AsyncClient(
        base_url=args.url, timeout=240, trust_env=False
    ) as client:
        health = (await client.get("/health")).json()
        assert health["workflow_configured"] and health["narrator"] == "llm", health

        async def turn(
            label,
            action,
            memory="umbrella",
            session="visit-1",
            player=None,
            words="",
            request_id=None,
        ):
            payload = {
                "player_id": player or run_id,
                "session_id": session,
                "request_id": request_id or uuid.uuid4().hex,
                "action": action,
                "memory": memory,
                "words": words,
            }
            before = time.perf_counter()
            response = await client.post("/api/play", json=payload)
            response.raise_for_status()
            result = response.json()
            assert result["workflow_run_id"]
            steps.append(
                {
                    "label": label,
                    "input": payload,
                    "result": result,
                    "elapsed_seconds": round(time.perf_counter() - before, 3),
                }
            )
            print(label, result["workflow_run_id"], flush=True)
            return result, payload

        await turn(
            "01 初次进店",
            "enter",
            words='我想找妹妹。她说过："别忘了带伞。"\n你见过她吗？',
        )
        pawn, original = await turn("02 典当红伞", "pawn")
        assert (
            "回声币：**3**" in pawn["story"] and "柜中记忆：**红伞**" in pawn["story"]
        )
        replay, _ = await turn(
            "03 同一请求安全重试", "pawn", request_id=original["request_id"]
        )
        assert replay["story"] == pawn["story"]
        await turn("04 拒绝重复典当", "pawn")
        resumed, _ = await turn("05 新会话记住旧交易", "enter", session="visit-2")
        assert "第 2 次来访" in resumed["story"] and "红伞" in resumed["story"]
        isolated, _ = await turn("06 玩家隔离", "enter", player=run_id + "-other")
        assert (
            "回声币：**0**" in isolated["story"]
            and "柜中记忆：**无**" in isolated["story"]
        )
        clue, _ = await turn("07 买线索并承受遗忘", "ask", session="visit-2")
        assert "回声币：**1**" in clue["story"] and "需要赎回" in clue["story"]
        poor, _ = await turn("08 余额不足不能赎回", "redeem", session="visit-2")
        assert "余额不足" in poor["story"] and "回声币：**1**" in poor["story"]
        await turn("09 典当摇篮曲筹钱", "pawn", "song", "visit-2")
        redeemed, _ = await turn("10 赎回红伞", "redeem", session="visit-2")
        assert (
            "柜中记忆：**摇篮曲**" in redeemed["story"]
            and "回声币：**0**" in redeemed["story"]
        )
        ending, _ = await turn("11 重逢结局", "finish", session="visit-2")
        assert "【带伞的人】" in ending["story"]
        other = run_id + "-lost"
        await turn("12 对照故事进店", "enter", player=other)
        await turn("13 对照故事典当红伞", "pawn", player=other)
        await turn("14 对照故事买线索", "ask", player=other)
        lost, _ = await turn("15 未赎回导致擦肩结局", "finish", player=other)
        assert "【擦肩】" in lost["story"]
        conflict = dict(original, action="finish")
        response = await client.post("/api/play", json=conflict)
        assert response.status_code == 502, response.text
        steps.append(
            {
                "label": "16 请求编号冲突不会交易成功",
                "input": conflict,
                "http_status": response.status_code,
                "result": response.json(),
            }
        )
        invalid = dict(original, player_id="../bad")
        response = await client.post("/api/play", json=invalid)
        assert response.status_code == 422
        steps.append(
            {"label": "17 拒绝非法玩家编号", "http_status": response.status_code}
        )

    # Export real SDK events, scoped to this verification run, with no tool credentials or signed tickets.
    events = []
    runtime = Path(os.getenv("ECHO_RUNTIME", str(ROOT / ".runtime")))
    request_ids = {step["input"]["request_id"] for step in steps if "input" in step}
    for line in (runtime / "events.jsonl").read_text().splitlines():
        event = json.loads(line)
        if (
            event.get("player", "").startswith(run_id)
            or event.get("request_id") in request_ids
        ):
            events.append(event)
    writes = [event for event in events if event["event"] == "powercontext.write"]
    reads = [event for event in events if event["event"] == "powercontext.read"]
    replays = [event for event in events if event["event"] == "powercontext.replay"]
    assert writes and reads and replays
    assert sum(e.get("request_id") == original["request_id"] for e in writes) == 1
    report = {
        "status": "passed",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "dify": "1.9.2",
            "powercontext": "1.5.0",
            "model": "Qwen2.5-1.5B-Instruct Q4_K_M",
            "model_runtime": "llama.cpp b7132 CPU",
        },
        "assertion_groups": len(steps),
        "steps": steps,
        "powercontext": {
            "writes": len(writes),
            "reads": len(reads),
            "replays": len(replays),
        },
    }
    destination = ROOT / args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    events_file = destination.with_name(destination.stem + "-events.jsonl")
    events_file.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
    )
    print(
        json.dumps(
            {"status": "passed", "steps": len(steps), "report": str(destination)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(verify())
