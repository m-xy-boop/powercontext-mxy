#!/usr/bin/env python3
"""真实浏览器验收：用 Playwright 驱动 Chromium 走一遍页面，检查前端确实渲染了 PowerContext 的真实结果。

用法：uv run python scripts/verify_ui.py --base-url http://127.0.0.1:8642 --out-dir evidence/screenshots
断言失败或页面出现未捕获 JS 错误即以非零码退出。截图为真实浏览器渲染结果，不做任何后期处理。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

FAILURES: list[str] = []
PASSED: list[str] = []


def expect(condition: bool, message: str) -> None:
    if condition:
        PASSED.append(message)
        print(f"  PASS  {message}")
    else:
        FAILURES.append(message)
        print(f"  FAIL  {message}", file=sys.stderr)


def shot(page, path: Path) -> None:
    """整页截图前回到顶部，避免粘性顶栏在长截图中部重复出现。"""
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    page.screenshot(path=str(path), full_page=True)


def browser_executable() -> str | None:
    """优先使用本机已有的 Chromium（Playwright 缓存版本可能比 pip 包新）。"""
    import os
    import glob

    candidates = []
    env_path = os.environ.get("RECALL_CHROMIUM")
    if env_path:
        candidates.append(env_path)
    base = Path.home() / ".cache" / "ms-playwright"
    for pattern in ("chromium-*/chrome-linux/chrome", "chromium_headless_shell-*/chrome-linux/headless_shell"):
        candidates.extend(sorted(glob.glob(str(base / pattern)), reverse=True))
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="拾忆 · Recall Coach 页面真实浏览器验收")
    parser.add_argument("--base-url", default="http://127.0.0.1:8642")
    parser.add_argument("--out-dir", default="evidence/screenshots")
    parser.add_argument("--learner", default=None, help="学习身份标识，默认按时间戳新建，避免污染历史记忆")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    learner_id = args.learner or f"ui-{time.strftime('%H%M%S')}"
    console_errors: list[str] = []
    report: dict[str, object] = {"base_url": args.base_url, "learner_id": learner_id, "checks": PASSED, "screenshots": []}

    with sync_playwright() as playwright:
        executable = browser_executable()
        print(f"使用浏览器：{executable or 'Playwright 自带 Chromium'}")
        browser = playwright.chromium.launch(executable_path=executable) if executable else playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1100}, locale="zh-CN")
        page = context.new_page()
        page.on("pageerror", lambda exc: console_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: console_errors.append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)

        # ---------------- 概览页 ----------------
        print("\n[1/4] 概览页 /")
        page.goto(f"{args.base_url}/", wait_until="domcontentloaded")
        page.wait_for_selector("#modules .module", timeout=15000)
        modules = page.locator("#modules .module").count()
        expect(modules == 8, f"知识点图谱渲染 {modules} 个模块（应为 8）")
        pill_text = page.inner_text("#backend-text")
        expect("已就绪" in pill_text, f"顶栏状态显示真实连接结果：{pill_text}")
        detail = page.inner_text("#backend-detail")
        expect("http://127.0.0.1:8777" in detail, "运行状态卡片显示真实 PowerContext 地址")
        expect("fts" in detail, "运行状态卡片显示真实检索能力（fts）")
        expect("未配置" in detail, "如实标注未配置模型抽取")
        ops_rows = page.locator("#ops-body tr").count()
        expect(ops_rows >= 1, f"操作留痕表格渲染 {ops_rows} 行")
        shot(page, out_dir / "01-home.png")
        report["screenshots"].append("01-home.png")

        # ---------------- 诊断与复习 ----------------
        print("\n[2/4] 诊断与复习 /diagnose")
        page.goto(f"{args.base_url}/diagnose", wait_until="domcontentloaded")
        page.fill("#learner-id", learner_id)
        page.fill("#display-name", "页面验证身份")
        page.click("#enter")
        page.wait_for_selector("#quiz .question", timeout=20000)
        expect(page.inner_text("#plan-mode").startswith("基础诊断"), f"无记忆时首屏为诊断模式：{page.inner_text('#plan-mode')}")
        questions = page.locator("#quiz .question").count()
        expect(questions == 8, f"诊断覆盖 {questions} 个知识点（应为 8 题）")
        basis_rows = page.locator("#basis table tr").count() - 1
        expect(basis_rows == 8, f"选题依据表渲染 {basis_rows} 行候选知识点")
        expect(page.locator("#quiz .reasons li").count() >= 8, "每题都给出了选题依据")
        expect("本次安排已写入记忆" in page.inner_text("#learner-state"), "页面显示 plan 记忆写入的真实标识")

        # 混合作答：一半选第一项，一半选第三项，制造真实薄弱点
        for index in range(questions):
            option_index = 0 if index % 2 == 0 else 2
            page.locator("#quiz .question").nth(index).locator(".options label").nth(option_index).click()
        expect(page.locator("#quiz input:checked").count() == questions, f"已作答 {page.locator('#quiz input:checked').count()} 题")
        shot(page, out_dir / "02-diagnose-answered.png")
        report["screenshots"].append("02-diagnose-answered.png")

        page.click("#submit")
        page.wait_for_selector("#receipt-body tr", timeout=20000)
        receipts = page.locator("#receipt-body tr").count()
        expect(receipts >= 1, f"判分后写入 {receipts} 条记忆并展示回执")
        expect("回答错误" in page.inner_text("#result-list"), "结果区显示真实判分（含错题）")
        expect("get_atomic_memory_state" in page.inner_text("#receipt-body"), "回执显示写入后的回读校验方式")
        expect(page.locator("#ranking-after table tr").count() - 1 == 8, "展示更新后的 8 个知识点优先级")
        shot(page, out_dir / "03-diagnose-result.png")
        report["screenshots"].append("03-diagnose-result.png")

        # 重复提交保护：按钮锁定之外，服务端必须拒绝二次累计（直接绕过页面按钮提交）
        expect("已判分" in page.inner_text("#submit"), f"提交后按钮显示已完成状态：{page.inner_text('#submit')}")
        expect(page.locator("#submit").is_disabled(), "已完成判分的会话不允许再次点击提交")
        replay_probe = page.evaluate(
            """async () => {
              const learner = CTX.learner.learner_id;
              const session = CTX.session.session_id;
              const count = async () => (await (await fetch(`/api/learners/${learner}`)).json()).memory_entry_count;
              const before = await count();
              const response = await fetch(`/api/learners/${learner}/sessions/${session}/submit`, {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({ answers: [{ question_id: "probe-only", chosen: "A" }] }),
              });
              const data = await response.json();
              const after = await count();
              return {
                status: response.status,
                before: before,
                after: after,
                replay: !!(data.replay && data.replay.repeated_submit),
                newMemories: data.replay ? data.replay.new_memories_this_request : null,
                keptChoice: data.results ? data.results[0].chosen : null,
              };
            }"""
        )
        expect(replay_probe["status"] == 200 and replay_probe["replay"] is True,
               f"绕过按钮直接重复提交被服务端识别为回放（未二次判分）：{replay_probe}")
        expect(replay_probe["before"] == replay_probe["after"],
               f"重复提交没有改变记忆条数（前后一致）：{replay_probe}")
        expect(replay_probe["newMemories"] == 0,
               "重复提交本次新写入的记忆条数为 0（应恰好为 0）")
        expect(replay_probe["keptChoice"] is not None, "回放返回的是当时留存的作答与判分")

        # 再来一轮：应切换为记忆驱动复习（等待新会话安排真正替换页面内容）
        previous_title = page.inner_text("#plan-title")
        page.click("#next-round")
        page.wait_for_function(
            "(title) => document.querySelector('#plan-title').textContent !== title"
            " && document.querySelector('#learner-state').textContent.includes('本次安排已写入记忆')",
            arg=previous_title,
            timeout=30000,
        )
        mode = page.inner_text("#plan-mode")
        expect(mode.startswith("记忆驱动复习"), f"第二轮模式：{mode}")
        refs = page.locator("#quiz .refs").count()
        expect(refs >= 1, f"第二轮有 {refs} 题在页面上给出“依据记忆”标识")
        shot(page, out_dir / "04-diagnose-review.png")
        report["screenshots"].append("04-diagnose-review.png")

        # ---------------- 学习记忆 ----------------
        print("\n[3/4] 学习记忆 /memory")
        page.goto(f"{args.base_url}/memory", wait_until="domcontentloaded")
        page.wait_for_selector("#memory-body tr td .pill", timeout=20000)
        expect(page.inner_text("#scope-badge").startswith("Scope scp_"), f"页面显示真实 Scope：{page.inner_text('#scope-badge')}")
        rows = page.locator("#memory-body tr").count()
        expect(rows >= 3, f"记忆清单渲染 {rows} 条真实条目")
        expect("art_" in page.inner_text("#memory-body"), "清单展示 atomic-memory/art_… 标识")
        stats = page.inner_text("#memory-stats")
        expect("薄弱知识点" in stats and "/ 8" in stats, f"统计卡片来自记忆折叠：{' '.join(stats.split())[:70]}")

        page.fill("#search-query", "薄弱点")
        page.click("#run-search")
        page.wait_for_function("document.querySelector('#search-result').textContent.includes('mode=')", timeout=15000)
        search_text = page.inner_text("#search-result")
        expect("mode=text" in search_text, "检索结果为真实模式（未配置嵌入模型时为全文 text）")
        expect("命中 0 条" not in search_text, f"检索命中已有记忆：{search_text.splitlines()[0]}")

        page.fill("#prepare-query", "事务隔离")
        page.click("#run-prepare")
        page.wait_for_function("document.querySelector('#prepare-result').textContent.includes('BEGIN_POWERCONTEXT')", timeout=15000)
        expect("powercontext.prepared-context.v1" in page.inner_text("#prepare-result"), "PreparedContext 返回真实 schema 与有界内容")

        page.select_option("#filter-kind", "weakness")
        page.wait_for_timeout(400)
        weakness_rows = page.locator("#memory-body tr").count()
        expect(weakness_rows >= 1, f"按 kind=weakness 过滤后 {weakness_rows} 条")

        page.fill("#pref-count", "6")
        page.select_option("#pref-focus", "tx-isolation")
        page.fill("#pref-note", "页面验收：先补隔离级别")
        page.click("#save-preference")
        page.wait_for_function("document.querySelector('#pref-state').textContent.includes('art_')", timeout=15000)
        expect("复习偏好" in page.inner_text("#pref-state"), f"偏好写入回执：{' '.join(page.inner_text('#pref-state').split())[:90]}")

        page.select_option("#filter-kind", "")
        page.select_option("#filter-state", "active")
        page.wait_for_timeout(500)
        forget_buttons = page.locator("#memory-body button")
        expect(forget_buttons.count() >= 1, "激活记忆提供按条遗忘入口")
        before_total = page.locator("#memory-body tr").count()
        forget_buttons.first.click()
        page.wait_for_function("document.querySelector('#danger-state').textContent.includes('已遗忘')", timeout=15000)
        expect("forgotten" in page.inner_text("#danger-state"), f"遗忘回执显示真实状态：{' '.join(page.inner_text('#danger-state').split())[:90]}")
        page.select_option("#filter-state", "all")
        page.wait_for_timeout(600)
        after_total = page.locator("#memory-body tr").count()
        expect(after_total == before_total, f"遗忘后历史仍可见（{before_total} → {after_total} 条）")
        shot(page, out_dir / "05-memory.png")
        report["screenshots"].append("05-memory.png")

        # 导出：浏览器直接下载 JSON / Markdown
        with page.expect_download(timeout=20000) as download_info:
            page.click("#export-md")
        download = download_info.value
        md_path = out_dir / "export-process-record.md"
        download.save_as(str(md_path))
        md_text = md_path.read_text(encoding="utf-8")
        expect("## 学习记忆" in md_text, "页面导出的 Markdown 含真实记忆章节")
        expect("art_" in md_text, "导出内容引用真实记忆标识")
        report["screenshots"].append("export-process-record.md")

        # ---------------- 验证说明 ----------------
        print("\n[4/4] 验证说明 /verify")
        page.goto(f"{args.base_url}/verify", wait_until="domcontentloaded")
        page.wait_for_selector("#live-status div", timeout=15000)
        status = page.inner_text("#live-status")
        expect("ready" in status, "验证页读取真实健康状态")
        expect("未配置" in status, "验证页如实标注模型能力")
        expect(page.locator("#ops-body tr").count() >= 5, f"操作留痕渲染 {page.locator('#ops-body tr').count()} 行")
        shot(page, out_dir / "06-verify.png")
        report["screenshots"].append("06-verify.png")

        # ---------------- 跨会话续接：重新打开页面 ----------------
        print("\n[+] 重新打开页面（新浏览器上下文）验证记忆续接")
        second = browser.new_context(viewport={"width": 1440, "height": 1100}, locale="zh-CN")
        page2 = second.new_page()
        page2.on("pageerror", lambda exc: console_errors.append(f"pageerror(new context): {exc}"))
        page2.goto(f"{args.base_url}/diagnose", wait_until="domcontentloaded")
        page2.fill("#learner-id", learner_id)
        page2.fill("#display-name", "页面验证身份")
        page2.click("#enter")
        page2.wait_for_selector("#quiz .question", timeout=20000)
        mode2 = page2.inner_text("#plan-mode")
        expect(mode2.startswith("记忆驱动复习"), f"全新浏览器上下文用同一身份进入时为复习模式：{mode2}")
        expect(page2.locator("#quiz .refs").count() >= 1, "续接安排的依据指向已存在的记忆条目")
        shot(page2, out_dir / "07-cross-session.png")
        report["screenshots"].append("07-cross-session.png")

        second.close()
        context.close()
        browser.close()

    report["console_errors"] = console_errors
    expect(not console_errors, f"页面没有未捕获的 JS 错误（{len(console_errors)} 条）")
    if console_errors:
        for line in console_errors[:10]:
            print(f"        {line}", file=sys.stderr)

    (out_dir / "ui-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n通过 {len(PASSED)} 项断言，失败 {len(FAILURES)} 项；截图与报告见 {out_dir}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PlaywrightError as exc:
        print(f"浏览器验收无法启动：{exc}", file=sys.stderr)
        raise SystemExit(2)
