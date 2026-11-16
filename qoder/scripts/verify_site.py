#!/usr/bin/env python3
"""展示站草稿的本机验收：真实 Chromium 打开静态页，检查脚本报错、图片加载、锚点、响应式与「不含本机接口依赖」。

用法：
  python3 -m http.server 4173 --bind 127.0.0.1 --directory site   # 另开一个终端
  uv run python scripts/verify_site.py --url http://127.0.0.1:4173/ --out-dir evidence/site-draft

只做展示层检查：不调用主应用后端，也不替代 scripts/verify_ui.py 的功能验收。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from verify_ui import FAILURES, PASSED, browser_executable, expect  # 复用同一套真实浏览器定位与断言记录方式
from playwright.sync_api import sync_playwright

REQUIRED_IMAGES = [
    "assets/screenshots/01-home.png",
    "assets/screenshots/02-diagnose-answered.png",
    "assets/screenshots/03-diagnose-result.png",
    "assets/screenshots/04-diagnose-review.png",
    "assets/screenshots/05-memory.png",
    "assets/screenshots/07-cross-session.png",
]
SECTIONS = ["problem", "loop", "memory", "evidence", "run", "limits"]

# 逐屏滚到页面底部再回顶：懒加载图片必须先经过视口才会解码；顺带关掉 CSS 平滑滚动，避免滚动被动画拖慢
SCROLL_THROUGH = """async () => {
    const root = document.documentElement;
    const keep = getComputedStyle(root).scrollBehavior;
    root.style.scrollBehavior = 'auto';
    const step = Math.max(360, window.innerHeight - 60);
    const bottom = Math.max(document.body.scrollHeight, root.scrollHeight);
    for (let y = 0; y <= bottom; y += step) {
        window.scrollTo(0, y);
        await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    }
    await Promise.all(Array.from(document.images).filter((img) => !img.complete)
        .map((img) => new Promise((r) => { img.onload = img.onerror = r; })));
    window.scrollTo(0, 0);
    root.style.scrollBehavior = keep;
}"""


def main() -> int:
    parser = argparse.ArgumentParser(description="拾忆展示站草稿页面验收")
    parser.add_argument("--url", default="http://127.0.0.1:4173/")
    parser.add_argument("--out-dir", default="evidence/site-draft")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    requests: list[str] = []

    with sync_playwright() as playwright:
        executable = browser_executable()
        browser = playwright.chromium.launch(executable_path=executable) if executable else playwright.chromium.launch()

        # ---------- 桌面宽度 ----------
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        page = context.new_page()
        page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)
        page.on("request", lambda req: requests.append(req.url))
        page.goto(args.url, wait_until="load")

        title = page.title()
        expect("拾忆" in title and "PowerContext" in title, f"页面标题如实描述作品：{title}")
        expect(page.inner_text("h1").strip() != "", "主标题有内容")

        # 懒加载图片要先滚到才会解码：完整走一遍页面后再判断
        page.evaluate(SCROLL_THROUGH)
        page.wait_for_timeout(300)

        images = page.evaluate(
            """() => Array.from(document.images).filter((img) => !img.closest('.lightbox')).map((img) => ({
                src: img.currentSrc || img.src, ok: img.complete && img.naturalWidth > 0, w: img.naturalWidth, h: img.naturalHeight }))"""
        )
        broken = [row for row in images if not row["ok"]]
        expect(not broken, f"{len(images)} 张截图全部真实解码（损坏清单为空）：{broken[:3]}")
        loaded = {row["src"] for row in images}
        missing = [path for path in REQUIRED_IMAGES if not any(path in src for src in loaded)]
        expect(not missing, f"六张约定截图齐备（缺失清单为空）：{missing}")

        for section in SECTIONS:
            info = page.evaluate(
                """(id) => {
                    const link = document.querySelector('a[href="#' + id + '"]');
                    const target = document.getElementById(id);
                    return {link: !!link, target: !!target, chars: target ? target.innerText.trim().length : 0};
                }""",
                section,
            )
            expect(info["link"] and info["target"] and info["chars"] > 40, f"内容区块 #{section} 有导航入口且非空：{info}")

        dangling = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href^="#"]'))
                  .map((a) => (a.getAttribute('href') || '').slice(1))
                  .filter((id) => id && !document.getElementById(id))"""
        )
        expect(not dangling, f"全部锚点都指向真实存在的区块（空白目标清单为空）：{dangling}")

        # 展示站不得依赖本机 API：真实网络请求只允许来自本站静态资源
        outside = sorted({u for u in requests if not u.startswith(args.url.rstrip("/") + "/") and u != args.url})
        expect(not outside, f"页面未发起本站之外的请求（外部请求清单为空）：{outside}")
        api_calls = page.evaluate("() => document.documentElement.innerHTML.match(/fetch\\(|XMLHttpRequest|WebSocket/g) || []")
        expect(not api_calls, f"展示页源码不含任何接口调用（fetch/XHR/WebSocket 命中数为 0）：{sorted(set(api_calls))}")

        links = page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map((a) => a.getAttribute('href'))")
        local_links = [u for u in links if re.match(r"^https?://(127\.0\.0\.1|localhost)", u or "")]
        expect(not local_links, f"没有把本机地址做成可点击链接（命中数为 0）：{local_links}")
        source_links = [u for u in links if u and "github.com" in u]
        expect(
            bool(source_links)
            and all(u == "https://github.com/m-xy-boop/powercontext-mxy/tree/qoder-recall-coach/qoder" for u in source_links),
            f"源码链接指向本仓库 qoder 目录：{sorted(set(source_links))}",
        )

        body = page.inner_text("body")
        leaks = re.findall(r"scp_[a-z0-9]{10,}|art_[a-f0-9]{20,}", body)
        expect(not leaks, f"页面文字未内嵌真实记忆标识（命中数为 0）：{sorted(set(leaks))[:5]}")
        for phrase in ("不是凭证", "本机", "数据隔离"):
            expect(phrase in body, f"页面如实说明边界（含「{phrase}」）")

        metrics = page.inner_text("#evidence")
        expect("自动化测试" in metrics, f"验收指标区块展示真实结果：{metrics[:80].replace(chr(10), ' / ')}")

        # ---------- 交互 ----------
        page.locator(".shot img").first.click()
        page.wait_for_selector("#lightbox:not([hidden])", timeout=5000)
        expect(bool(page.locator("#lb-img").get_attribute("src")), "点击截图弹出灯箱并显示同一张图")
        viewer = page.evaluate(
            """() => { const b = document.getElementById('lightbox'); const i = document.getElementById('lb-img');
                       return {scrollable: b.scrollHeight > b.clientHeight, focus: document.activeElement.className,
                               width: Math.round(i.getBoundingClientRect().width), natural: i.naturalWidth}; }"""
        )
        expect(viewer["scrollable"], "完整页面截图在灯箱里可以上下滚动查看")
        expect(viewer["focus"] == "lb-close", f"打开灯箱后焦点落在关闭按钮：{viewer}")
        expect(viewer["width"] > 900, f"灯箱按接近原始宽度显示（可读），实测 {viewer}")
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.getElementById('lightbox').hidden === true", timeout=5000)
        expect(True, "灯箱可用 Esc 关闭")

        page.locator("#run").scroll_into_view_if_needed()
        page.evaluate("() => { navigator.clipboard = { writeText: () => Promise.resolve() }; }")
        page.click(".copy")
        page.wait_for_function("() => document.querySelector('.copy').textContent.trim().length > 2", timeout=5000)
        label = page.inner_text(".copy").strip()
        expect(label in {"已复制", "请手动复制"}, f"命令复制按钮给出反馈：{label}")

        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(200)
        page.screenshot(path=str(out_dir / "site-desktop.png"), full_page=True)

        # ---------- 窄屏 ----------
        small = browser.new_context(viewport={"width": 390, "height": 780}, locale="zh-CN", is_mobile=True, has_touch=True)
        mpage = small.new_page()
        mpage.on("pageerror", lambda exc: errors.append(f"mobile pageerror: {exc}"))
        mpage.goto(args.url, wait_until="load")
        mpage.evaluate(SCROLL_THROUGH)
        doc_overflow = mpage.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        expect(doc_overflow <= 1, f"窄屏 390px 整页无横向滚动（实测溢出 {doc_overflow}px）")
        unscrolled = mpage.evaluate(
            """() => Array.from(document.querySelectorAll('body *')).filter((n) => {
                if (n.getBoundingClientRect().right <= window.innerWidth + 1) return false;
                for (let a = n.parentElement; a; a = a.parentElement) {
                    const s = getComputedStyle(a);
                    if ((s.overflowX === 'auto' || s.overflowX === 'scroll') && a.scrollWidth > a.clientWidth + 1) return false;
                }
                return true;
            }).slice(0, 5).map((n) => n.tagName + '.' + (n.className || '') + ':' + Math.round(n.getBoundingClientRect().right))"""
        )
        expect(not unscrolled, f"窄屏存在既不换行也不可滑动的溢出元素（清单应为空）：{unscrolled}")
        table_scrollable = mpage.evaluate(
            """() => { const w = document.querySelector('.table-wrap');
                       return !!w && getComputedStyle(w).overflowX === 'auto' && w.scrollWidth > w.clientWidth; }"""
        )
        expect(table_scrollable, "窄屏下公开接口表是可横向滑动的容器（而不是撑破页面）")
        hint = mpage.evaluate("() => { const n = document.querySelector('.narrow-only'); return !!n && getComputedStyle(n).display !== 'none'; }")
        expect(hint, "窄屏下显示表格可横向滑动的提示（默认隐藏、断点内可见）")
        mpage.evaluate(SCROLL_THROUGH)
        mpage.evaluate("() => window.scrollTo(0, 0)")
        mpage.wait_for_timeout(200)
        mpage.screenshot(path=str(out_dir / "site-mobile.png"), full_page=True)

        context.close()
        small.close()
        browser.close()

    if errors:
        print("\n浏览器报告了脚本错误：", file=sys.stderr)
        for line in errors:
            print("  -", line, file=sys.stderr)
        raise SystemExit(1)
    if FAILURES:
        for line in FAILURES:
            print("FAIL:", line, file=sys.stderr)
        raise SystemExit(1)
    report = {
        "url": args.url,
        "checks": len(PASSED),
        "passed": list(PASSED),
        "requests": sorted(set(requests)),
        "artifacts": sorted(p.name for p in out_dir.glob("site-*.png")),
    }
    (out_dir / "site-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n展示站草稿页面验收通过：{len(PASSED)} 项断言，截图与报告在 {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
