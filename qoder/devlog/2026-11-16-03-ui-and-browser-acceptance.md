# 开发记录 03 · 页面与真实浏览器验收

日期：2026-11-16　范围：`web/`（四个页面 + `assets/app.js`）、`scripts/verify_ui.py`

## 页面职责

| 页面 | 干什么 | 关键真实输出 |
| --- | --- | --- |
| `/` 概览 | 知识点图谱、服务能力、最近 8 条操作留痕 | `search_modes`、`memory_extraction=false`、留痕行含 `art_…@1`/耗时 |
| `/diagnose` | 建立身份 → 安排 → 作答 → 判分写回 → 再来一轮 | 每题下方「依据 + 记忆标识」；回执表含 `verified_by` |
| `/memory` | 记忆清单/筛选、检索、PreparedContext、偏好、遗忘、导出 | `Scope scp_…`、`mode=text`、`state_version` 递增 |
| `/verify` | 如实说明接口核实结果、能力边界、边界外不做的事 | 健康状态、留痕条数、题库口径 |

前端只与本项目服务端通信（`fetch` 同源接口），不直接持有 PowerContext token；
所有动态内容通过 `textContent` / `createTextNode` 写入 DOM，不用 `innerHTML` 拼接远端数据。

## 踩到并修掉的问题

| 问题 | 现象 | 修法 |
| --- | --- | --- |
| DOM 助手把节点当文本 | `el("td", null, pill(...))` 渲染成 `[object HTMLSpanElement]` | `el()` 判断入参是否为 Node：是则 `appendChild`，否则 `createTextNode` |
| 遗留占位代码 | `#stat-memory` 里插入空 span、`#backend-text` 绑定刷新点击 | 删除；概览改为把真实题量写进 `#stat-bank` 文案 |
| 状态筛选语义错位 | HTML 里「全部状态」的 `value=""` 与「默认 active」撞车 | 选项值改为 `all`，前端据此展开为 `active,forgotten,merged,retired` |
| 控制台 404 | 浏览器自动请求 `/favicon.ico`，验收断言「无 JS 错误」被资源错误干扰 | 新增 `web/assets/favicon.svg` 并在四个页面加 `<link rel="icon">` |
| Playwright 等待写错 | `wait_for_function("([t]) => …", arg=prev)`：字符串被解构成字符，`t` 只剩首字符，条件恒真 | 改为 `(t) => …`，并同时要求状态文案出现「本次安排已写入记忆」 |
| 粘性顶栏在长截图里重复 | `full_page=True` 截图时顶栏画在页面中部 | 截图前 `window.scrollTo(0,0)` 并等待 200ms（`shot()` 助手） |
| 复用浏览器版本不匹配 | pip 装的 playwright 1.55 期望 chromium-1187，本机缓存是 1194 | `browser_executable()` 优先使用本机已有 Chromium，支持 `RECALL_CHROMIUM` 覆盖 |

## 浏览器验收结果（真实 Chromium）

```
$ uv run python scripts/verify_ui.py --out-dir evidence/screenshots
使用浏览器：/home/xy/.cache/ms-playwright/chromium-1194/chrome-linux/chrome
…
通过 38 项断言，失败 0 项；截图与报告见 evidence/screenshots
```

覆盖：概览 8 个知识点与真实能力卡片；诊断首屏 8 题、依据表 8 行、混合作答后写入 14 条记忆、
回执含 `get_atomic_memory_state`；「再来一轮」切换为记忆驱动复习且 5 题给出记忆标识；
记忆页 `Scope scp_…`、16 条条目、`mode=text` 检索命中、PreparedContext 标记头、
偏好写入回执、按条遗忘 `state=forgotten/state_version=1`、遗忘后历史仍可见、
浏览器下载 Markdown 且含真实标识；验证页健康状态与 156 行留痕；
**全新浏览器上下文用同一身份进入即为复习模式**；页面未捕获 JS 错误 0 条。
