#!/usr/bin/env bash
# 真实验收：公开接口核实 → 自动化测试 → 端到端闭环 → 停止并重启 PowerContext 后继续 → 真实浏览器页面验收。
# 全过程不调用任何模型服务。输出留档在 $EVIDENCE_DIR（默认 evidence/）；任何一步失败即整体失败。
set -euo pipefail
cd "$(dirname "$0")/.."

UV="${UV:-$HOME/.local/bin/uv}"
command -v "$UV" >/dev/null 2>&1 || UV="$(command -v uv)"
HOST="${APP_HOST:-127.0.0.1}"
PORT="${APP_PORT:-8642}"
BASE="http://$HOST:$PORT"
LEARNER="${ACCEPTANCE_LEARNER:-acceptance-$(date +%H%M%S)}"   # 每次运行用新身份，保证首屏是基础诊断
# 留档目录：默认 evidence/。修正后重跑请指向新目录（例如 EVIDENCE_DIR=evidence/rerun-01），
# 这样原始验收日志保持不动，两轮结果都在仓库里可查。
EVIDENCE="${EVIDENCE_DIR:-evidence}"
mkdir -p "$EVIDENCE" "$EVIDENCE/screenshots" var/log var/run

log() { printf '\n=== %s ===\n' "$*"; }

log "0. 从当前代码重启真实服务（先停旧进程，验收必须跑在待验代码上）"
scripts/stop_app.sh 2>&1 | tee "$EVIDENCE/00-restart.log" || true
scripts/stop_powercontext.sh 2>&1 | tee -a "$EVIDENCE/00-restart.log" || true
scripts/start_powercontext.sh 2>&1 | tee -a "$EVIDENCE/00-restart.log"
scripts/start_app.sh 2>&1 | tee -a "$EVIDENCE/00-restart.log"

log "1. 公开接口核实（直接调用 PowerContext Client SDK）"
"$UV" run python scripts/verify_powercontext.py 2>&1 | tee "$EVIDENCE/01-public-api-probe.log"

log "2. 自动化测试（离线单元 + 真实记忆流程）"
"$UV" run pytest -q 2>&1 | tee "$EVIDENCE/02-pytest.log"

log "3. 端到端闭环（诊断→写记忆→复习→纠正→偏好→检索→导出→遗忘）"
"$UV" run python scripts/e2e_loop.py --base-url "$BASE" --learner "$LEARNER" \
  --display-name "验收闭环身份" --json-out "$EVIDENCE/03-e2e-loop.json" 2>&1 | tee "$EVIDENCE/03-e2e-loop.log"

log "4. 记录重启前的记忆指纹"
"$UV" run python - "$BASE" "$LEARNER" "$EVIDENCE/04-before-restart.json" <<'PY' 2>&1 | tee "$EVIDENCE/04-before-restart.log"
import json, sys
import httpx

base, learner, out = sys.argv[1], sys.argv[2], sys.argv[3]
client = httpx.Client(base_url=base, timeout=30.0)
overview = client.get(f"/api/learners/{learner}").json()
scope_id = overview["learner"]["scope_id"]
memories = client.get(f"/api/learners/{learner}/memories", params={"states": ["active", "forgotten"]}).json()
fingerprint = {
    "scope_id": scope_id,
    "active_entries": overview["memory_entry_count"],
    "all_entries": len(memories["entries"]),
    "artifact_ids": sorted(row["artifact"]["artifact_id"] for row in memories["entries"]),
    "last_plan": overview["last_plan"],
    "ranking_head": [(row["topic"], row["mastery"], row["weak"]) for row in overview["ranking"][:3]],
    "preference": overview["preference"],
}
json.dump(fingerprint, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"Scope {scope_id}")
print(f"记忆条目：激活 {fingerprint['active_entries']} / 全部 {fingerprint['all_entries']}")
print(f"最近安排：{(fingerprint['last_plan'] or {}).get('artifact', {}).get('artifact_id')}")
print(f"优先级前三：{fingerprint['ranking_head']}")
print(f"偏好：{fingerprint['preference']['daily_questions']} 题，侧重 {fingerprint['preference']['focus_topics']}")
PY

log "5. 停止并重启 PowerContext（真实重启，验证记忆跨重启存活）"
scripts/stop_app.sh 2>&1 | tee "$EVIDENCE/05-restart.log"
scripts/stop_powercontext.sh 2>&1 | tee -a "$EVIDENCE/05-restart.log"
scripts/start_powercontext.sh 2>&1 | tee -a "$EVIDENCE/05-restart.log"
scripts/start_app.sh 2>&1 | tee -a "$EVIDENCE/05-restart.log"

log "6. 重启后读回同一 Scope 并逐条比对指纹"
"$UV" run python - "$BASE" "$LEARNER" "$EVIDENCE/04-before-restart.json" "$EVIDENCE/06-after-restart.json" <<'PY' 2>&1 | tee "$EVIDENCE/06-after-restart.log"
import json, sys
import httpx

base, learner, before_path, after_path = sys.argv[1:5]
before = json.load(open(before_path, encoding="utf-8"))
client = httpx.Client(base_url=base, timeout=30.0)
overview = client.get(f"/api/learners/{learner}").json()
memories = client.get(f"/api/learners/{learner}/memories", params={"states": ["active", "forgotten"]}).json()
after = {
    "scope_id": overview["learner"]["scope_id"],
    "active_entries": overview["memory_entry_count"],
    "all_entries": len(memories["entries"]),
    "artifact_ids": sorted(row["artifact"]["artifact_id"] for row in memories["entries"]),
    "last_plan": overview["last_plan"],
    "ranking_head": [(row["topic"], row["mastery"], row["weak"]) for row in overview["ranking"][:3]],
    "preference": overview["preference"],
}
json.dump(after, open(after_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
after = json.loads(json.dumps(after))  # 元组统一成列表后再比对，避免容器类型造成假差异
mismatches = [key for key in before if before[key] != after[key]]
for key in before:
    same = "一致" if before[key] == after[key] else "不一致"
    print(f"  {key:14s} {same}")
if mismatches:
    print(f"FAIL: 重启后记忆指纹不一致：{mismatches}", file=sys.stderr)
    raise SystemExit(1)
print(f"重启后同一 Scope（{after['scope_id']}）的 {after['all_entries']} 条记忆逐条指纹一致")
PY

log "7. 重启后继续闭环（全新应用实例 + 已重启的记忆服务）"
"$UV" run python scripts/e2e_loop.py --base-url "$BASE" --learner "$LEARNER" \
  --existing-learner --json-out "$EVIDENCE/07-e2e-after-restart.json" 2>&1 | tee "$EVIDENCE/07-e2e-after-restart.log"

log "8. 真实浏览器页面验收"
"$UV" run python scripts/verify_ui.py --base-url "$BASE" --out-dir "$EVIDENCE/screenshots" 2>&1 | tee "$EVIDENCE/08-ui-verify.log"

log "9. 汇总"
"$UV" run python - "$EVIDENCE" <<'PY'
import datetime, json, pathlib, re, subprocess, sys

evidence = pathlib.Path(sys.argv[1])
powercontext_version = subprocess.run(["bash", "-lc", "powercontext --version 2>/dev/null | head -1"], capture_output=True, text=True).stdout.strip()
python_version = subprocess.run([sys.executable, "--version"], capture_output=True, text=True).stdout.strip()
pytest_text = (evidence / "02-pytest.log").read_text(encoding="utf-8", errors="replace")
match = re.search(r"\d+ (passed|failed)[^\n]*", pytest_text)
pytest_log = match.group(0) if match else "（未在日志中找到统计行）"
ui_report = json.loads((evidence / "screenshots" / "ui-report.json").read_text(encoding="utf-8"))
loop = json.loads((evidence / "03-e2e-loop.json").read_text(encoding="utf-8"))
after_restart = json.loads((evidence / "07-e2e-after-restart.json").read_text(encoding="utf-8"))
before = json.loads((evidence / "04-before-restart.json").read_text(encoding="utf-8"))

lines = [
    "# 验收运行记录（本次真实执行）",
    "",
    f"- 执行时间：{datetime.datetime.now().astimezone().isoformat(timespec='seconds')}",
    f"- PowerContext：{powercontext_version or '（版本命令不可用）'}，服务地址 http://127.0.0.1:8777，数据目录 .powercontext/",
    f"- Python：{python_version}；依赖锁定：uv.lock",
    f"- pytest：{pytest_log}",
    f"- 页面断言：{len(ui_report['checks'])} 项通过，未捕获 JS 错误 {len(ui_report['console_errors'])} 条",
    f"- 端到端闭环：{len(loop['steps'])} 个步骤全部通过；重启后续接 {len(after_restart['steps'])} 个步骤全部通过",
    f"- 跨重启记忆：Scope `{before['scope_id']}`，{before['all_entries']} 条条目（含 {before['active_entries']} 条激活）逐条指纹一致",
    f"- 模型 API 密钥使用：无（判分与选题在本地服务端完成，检索为 PowerContext 全文模式）",
    "",
    "## 留档文件",
    "",
]
for path in sorted(evidence.glob("*")):
    if path.is_file():
        lines.append(f"- `{path.name}`（{path.stat().st_size} 字节）")
for path in sorted((evidence / "screenshots").glob("*")):
    lines.append(f"- `screenshots/{path.name}`（{path.stat().st_size} 字节）")
lines += [
    "",
    "复现命令：`scripts/acceptance.sh`（会启动真实服务、跑全部校验并重启 PowerContext 验证跨重启续接）。",
    "",
]
(evidence / "acceptance-run.md").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines[:12]))
print(f"汇总写入 {evidence}/acceptance-run.md")
PY

echo
echo "验收完成：全部步骤通过，留档见 $EVIDENCE/"
