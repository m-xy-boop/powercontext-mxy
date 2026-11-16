# ACCEPTANCE · 验收记录

作品：拾忆 · Recall Coach　验收时间：2026-11-16 14:01–14:03（本机真实执行）
执行命令：`bash scripts/acceptance.sh`　退出码：`0`　全部输出留档在 `evidence/`

> 第 1–8 节记录的是**首轮**验收（原始留档 `evidence/` 保持不动）。独立审查修改代码后，
> 同一套脚本在 18:21 重跑了一遍完整验收，结果写在**第 9 节**，留档在 `evidence/rerun-20261116-fix4/`。
> 之后经提交人明确授权，把静态展示站发布到 Qoder Sites 并做了线上复核，结果写在**第 10 节**，
> 留档在 `evidence/publish-20261116/` 与 `evidence/site-public/`（同样不改动任何历史日志）。

> 本文只写「怎么验的 + 真实结果」。所有数字来自本次执行，可在 `evidence/` 逐条核对；
> 未执行或未验证的事项一律写在第 7 节并标注待补充，不做任何推测性描述。

---

## 1. 结论摘要

| 验收项 | 结果 | 留档 |
| --- | --- | --- |
| 公开接口核实（不猜 API） | 10 项逐项核实通过 | `evidence/01-public-api-probe.log` |
| 自动化测试 | **20 passed**, 1 warning in 11.50s | `evidence/02-pytest.log` |
| 端到端闭环（诊断→写→复习→纠正→偏好→检索→导出→遗忘） | **13 步全部通过** | `evidence/03-e2e-loop.{log,json}` |
| 真实重启 PowerContext 后记忆存活 | 同一 `scp_6yh4ywxypgyzjtj1atk2p953cc`，**19 条条目（18 条激活）七项指纹逐条一致** | `evidence/04/05/06-*` |
| 重启后继续闭环（全新应用实例） | 13 步全部通过 | `evidence/07-e2e-after-restart.{log,json}` |
| 真实浏览器页面验收 | **38 项断言通过，未捕获 JS 错误 0 条**，7 张截图 + 1 份导出 | `evidence/08-ui-verify.log`、`evidence/screenshots/` |
| 模型 API 密钥 | **未使用任何密钥**（判分与选题本地完成，检索为全文模式） | 第 5 节 |

环境：`powercontext 1.5.1`（`~/.local/bin/powercontext`）、Python 3.12.16、`uv` 锁定依赖（`uv.lock`）、
WSL2 Linux、服务监听 `127.0.0.1:8777`（数据目录 `.powercontext/`）、应用监听 `127.0.0.1:8642`。

## 2. 公开接口核实（先核实，后实现）

`scripts/verify_powercontext.py` 直接调用公开 Python Client SDK，输出真实返回值：

| 接口 | 真实结果 |
| --- | --- |
| `GET /v1/capabilities` | `search_modes=["auto","fts"]`、`memory_extraction=false`、`source_types=["content"]`、`context_versions=["powercontext.prepared-context.v1"]` |
| `POST /v1/scopes` | 同一 `idempotency_key` 两次调用返回同一 `scp_2rj0eb9cya2kda908b87dy9bef` |
| `PUT /v1/scopes/{id}` | 带 `expected_version` 更新，`version 1 → 2` |
| `POST /v1/memory/remember` | `atomic-memory/art_57e43088b0db41728ba0fb0929f4a982@1`、`changed=True`、`state=active`、`state_version=0`、正文 105 字节 |
| `GET …/artifacts/atomic-memory/{id}/state` | 回读 `revision=1 / active`，与写入返回一致 |
| `POST /v1/atomic-memory/list` | `kind=weakness, states=["active"]` → 1 条，`next_cursor=None` |
| `POST /v1/memory/search` | `mode=text`，命中 1 条，`score=0.01639`，`matched_by=["text"]` |
| `POST /v1/context/prepare` | `schema=powercontext.prepared-context.v1`、`status=ready`、`content_bytes=684`、含真实标记头 |
| `POST /v1/sources/content` | `content/probe-attempts`、`status=accepted`、`position=2` |
| `POST /v1/atomic-memory/lifecycle` | `active/v0 → forgotten/v1`，随后 `list(active)` 归零 |
| 超长文本写入 | `422 invalid_request`，`X-PowerContext-Request-Id=a304d1125aaeba24` |

真实契约留档：`reference/powercontext-1.5.1-openapi.json`（由运行中的服务 `GET /openapi.json` 下载）。

## 3. 端到端闭环的真实证据（`evidence/03-e2e-loop.json`）

| 步骤 | 真实输出 |
| --- | --- |
| 建立身份 | `scp_6yh4ywxypgyzjtj1atk2p953cc`，标题 `拾忆-acceptance-140157`，`scope_version=1` |
| 第一轮安排 | `mode=diagnosis`，8 题覆盖 8 个知识点；`plan` 记忆 `art_bb604be7a2d54243bc51195a71e8d015@1` |
| 判分写回 | 故意答错 `idx-btree-2`、`idx-perf-3` → 得分 6/8；写入 10 条回执（8 `progress` + 2 `weakness`），每条标注 `verified_by=get_atomic_memory_state` |
| 薄弱形成 | `idx-btree` 掌握度 0.275、连错 1；`idx-perf` 同 |
| 第二轮安排 | `mode=review`，候选 `["idx-btree","idx-perf","lock","mvcc","recovery"]`，**两个薄弱点均被覆盖**；首题依据：「当前掌握度 0.28，缺口 0.72」「连续答错 1 次，加权 0.10」；引用 6 条真实记忆标识 |
| 全部答对 | 5/5；`idx-btree`/`idx-perf` 掌握度 0.275 → **0.601**（离开薄弱阈值） |
| 偏好生效 | 写入 `每次 3 题，侧重 索引结构`（`art_a79dae79e1ee41548ead2737f44660d4@1`）→ 第三次安排 `question_count=3` 且含侧重知识点 |
| 检索 | `mode=text`，命中 2 条 `weakness`，`score=0.01639 / 0.01613` |
| PreparedContext | `status=ready`、`content_bytes=836`、含 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` |
| 导出 | 19 条记忆、13 条答题流水；Markdown 含 5 个章节并引用真实标识 |
| 遗忘 | `art_3805376d365b408b9b585d6a3301d802` → `state=forgotten`、`state_version=1`，激活 `weakness` 由 2 条降为 1 条 |

## 4. 跨重启与跨会话

**真实重启过程**（`evidence/05-restart.log`）：

```
已停止拾忆应用（PID 63422）
已停止 PowerContext Server（PID 62998）
已启动 PowerContext Server，PID 65245，日志 var/log/powercontext-server.log
就绪：http://127.0.0.1:8777（1.5.1）
拾忆 · Recall Coach 已就绪：http://127.0.0.1:8642（PID 65688，后端 http://127.0.0.1:8777）
```

重启前后指纹比对（`evidence/04-before-restart.json` vs `evidence/06-after-restart.json`，七项全部一致）：

| 指纹项 | 值 |
| --- | --- |
| `scope_id` | `scp_6yh4ywxypgyzjtj1atk2p953cc` |
| 激活条数 / 全部条数 | 18 / 19 |
| `artifact_id` 集合 | 19 个标识排序后完全相同 |
| 最近一次 `plan` 记忆 | `art_82abd606a58e4ebc8440979aa82f4d29` |
| 优先级前三 | `idx-btree 0.601`、`idx-perf 0.601`、`storage 0.725` |
| 复习偏好 | 每次 3 题，侧重 `["idx-btree"]` |

重启后的全新应用实例继续跑完整闭环（`evidence/07-e2e-after-restart.log`）：首屏即为 `review`，
薄弱点仍被优先安排，依据仍指向重启前写入的记忆标识。

另外两条独立证据：
- `tests/test_live_flows.py::test_memory_survives_application_restart`：只重建应用（`TestClient(create_app(...))`），记忆照样读回。
- 浏览器层：`verify_ui.py` 用**全新浏览器上下文**（无 localStorage、无 Cookie）以同一身份进入，
  断言结果「全新浏览器上下文用同一身份进入时为复习模式：记忆驱动复习」且「续接安排的依据指向已存在的记忆条目」。

## 5. 「核心闭环无需模型密钥」的验证方式

- 本次执行环境未设置任何模型服务密钥；`grep -riE "api_key|openai|anthropic|model_secret|DASHSCOPE" app web scripts tests conftest.py pyproject.toml .env.example | wc -l` → **0**。
- 应用侧只有两个网络出口，且都指向同一个地址：`PowerContextClient(base_url=settings.powercontext_url)`
  与 `/health/ready` 探测（`httpx.AsyncClient(base_url=self.base_url)`）。因此默认配置下全流程只访问 `127.0.0.1:8777`，
  外部网络不可达时闭环照常工作。
- 服务端能力如实显示 `memory_extraction=false`，页面与验收文档均不声称自动抽取。
- 检索真实返回 `mode=text`（无嵌入模型时的全文模式），未伪装语义检索。
- 判分在 `app/main.py` 内完成，题库为 `data/questions.json` 固定 24 题原创内容。

## 6. 页面验收（真实 Chromium，38 项断言）

分组结果（完整清单见 `evidence/08-ui-verify.log`）：

| 页面 | 关键断言 |
| --- | --- |
| `/` 概览 | 8 个知识点渲染；顶栏「已就绪 · http://127.0.0.1:8777 · 检索模式 auto/fts」；如实标注未配置模型抽取；留痕表格 8 行 |
| `/diagnose` | 首屏诊断 8 题、依据表 8 行、每题有依据；混合作答 8 题后写入 14 条记忆；回执显示 `get_atomic_memory_state`；优先级表 8 行；「再来一轮」变复习且 5 题给出记忆标识 |
| `/memory` | `Scope scp_…`；16 条条目与 `art_…` 标识；统计卡片来自记忆折叠；`mode=text` 命中 6 条；PreparedContext schema 正确；`kind=weakness` 过滤 6 条；偏好写入回执；按条遗忘 `state=forgotten/state_version=1`；切「全部状态」历史仍可见（17 → 17）；浏览器下载 Markdown 含真实标识 |
| `/verify` | 真实健康状态、能力边界、301 行操作留痕 |
| 全局 | 未捕获 JS 错误 0 条；新浏览器上下文续接为复习模式 |

截图（真实渲染，未做后期）：`evidence/screenshots/01-home.png` … `07-cross-session.png`，
另有页面下载得到的 `evidence/screenshots/export-process-record.md`。

## 7. 待补充 / 未验证事项

| 项目 | 状态 |
| --- | --- |
| Qoder 账号 UID | `1684174493638547`（提交人提供；CLI 用户名与该数字一致） |
| Qoder Sites 站点 URL 与在线可达性验证 | **已完成**（2026-11-16 19:03–19:08，经提交人明确授权公开发布）：<https://recall-coach-f3jx2506222.qoder.zone/>，`published=true` + 受众 `public` + 匿名 `HTTP/2 200` + 线上真实 Chromium 30 项断言通过，见**第 10 节** |
| 代码推送 / PR | **未执行**（按要求不自行推送；文件均为本地未跟踪状态，待本人确认后提交） |
| 多人并发与鉴权场景 | 未验证：本作品为单人本机形态，无登录体系；跨主机部署需 TLS 与 token（见 RUNBOOK 第 8 步） |
| 语义向量检索、自动记忆抽取 | 未启用：本机服务未配置嵌入/推理模型，能力如实显示 `memory_extraction=false` |
| 真实用户反馈 / 使用统计 | 无：不虚构任何用户数、提效百分比或反馈 |

## 8. 复现命令

```bash
scripts/start_powercontext.sh && scripts/start_app.sh
uv run python scripts/verify_powercontext.py
uv run pytest -q
uv run python scripts/e2e_loop.py --learner e2e-demo --json-out evidence/e2e-demo.json
uv run python scripts/verify_ui.py --out-dir evidence/screenshots
bash scripts/acceptance.sh          # 上述全部 + 真实重启 + 指纹比对 + 汇总
```

展示站：本机预览 + 发布产物 + 线上复核（三种口径的 `verify_site.py` 都是同一套 30 项断言）：

```bash
python3 -m http.server 4173 --bind 127.0.0.1 --directory site
uv run python scripts/verify_site.py --url http://127.0.0.1:4173/ --out-dir evidence/site-draft
uv run python scripts/verify_site.py --url http://127.0.0.1:4173/dist/ --out-dir evidence/site-dist
uv run python scripts/verify_site.py --url https://recall-coach-f3jx2506222.qoder.zone/ --out-dir evidence/site-public
```

## 9. 独立审查后的复验（2026-11-16 18:21，本机真实执行）

审查发现三项问题（重复提交可二次累计、部分写入提示不准确、不得声称公开多用户安全），修改代码后
用**同一套验收脚本**重跑了一遍完整验收。原始留档 `evidence/` 保持不动，本轮真实输出另存在
`evidence/rerun-20261116-fix4/`（`EVIDENCE_DIR=evidence/rerun-20261116-fix4 bash scripts/acceptance.sh`，退出码 `0`）。

| 验收项 | 本轮真实结果 | 留档 |
| --- | --- | --- |
| 公开接口核实 | 10 项逐项核实通过（与首轮一致） | `evidence/rerun-20261116-fix4/01-public-api-probe.log` |
| 自动化测试 | **29 passed**, 1 warning in 14.21s（新增 6 项离线幂等 + 3 项真实流程回归） | `…/02-pytest.log` |
| 端到端闭环 | **14 步全部通过**（新增「重复提交同一会话（持久幂等保护）」一步） | `…/03-e2e-loop.{log,json}` |
| 真实重启后记忆存活 | 同一 `scp_19ftvqw81ry1zx8nx32vhc1wk5`，**19 条条目（18 条激活）七项指纹逐条一致** | `…/04/05/06-*` |
| 重启后继续闭环 | 14 步全部通过 | `…/07-e2e-after-restart.{log,json}` |
| 真实浏览器页面验收 | **44 项断言通过，未捕获 JS 错误 0 条**，7 张截图 + 1 份导出 | `…/08-ui-verify.log`、`…/screenshots/` |
| 模型 API 密钥 | 未使用任何密钥 | 第 5 节 |

本轮新增的关键断言（全部为真实执行结果，非声称）：

- 页面：提交后按钮文案为「本轮已判分并写入记忆」且为**禁用态**；绕过按钮用 `fetch` 直接重放同一提交，
  返回 `200` 且 `replay.repeated_submit=true`、`new_memories_this_request=0`，记忆条数前后均为 15（未增加）。
- 端到端：重复提交后 `attempts` 流水条数、记忆条数、导出流水行数均不变。
- 真实流程测试：注入「批量写入第二条失败」，响应为 `502` 且带 `written_receipts`/`pending_slots` 与按阶段生成的提示；
  重试后 `reused_from_ledger=1`，与无故障对照身份的掌握度表逐项相等（不叠加两次）。
- 只读失败与写入失败的提示文案分别断言，避免出现「没读到」被说成「没写入」。

复验过程中发现并修掉的一个新问题：**页面提交按钮的启用条件写反**（`disabled = !committed`），
判分成功后按钮反而可以再点。它由第 8 步的浏览器断言抓到（`已完成判分的会话不允许再次点击提交`），
改为 `disabled = committed` 后重跑全量验收通过。这条也说明为什么幂等必须同时有服务端持久保护：
前端按钮状态本身不是可靠约束。

展示站草稿的页面层检查：`scripts/verify_site.py` 用真实 Chromium 在 1440 与 390 两种宽度下跑 **30 项断言**，
包括 6 张截图真实解码、锚点均有目标、无本站之外请求、源码无 `fetch`/`XHR`/`WebSocket`、
无本机地址链接、无真实记忆标识内嵌、灯箱可滚动查看完整截图、窄屏无横向溢出；
留档 `evidence/site-draft/site-report.json`（含整页桌面与窄屏截图 `site-desktop.png`、`site-mobile.png`）。

## 10. 公开展示站的发布与线上复核（2026-11-16 19:03–19:08，本机真实执行）

授权口径：提交人明确回复「授权公开发布」，范围只到 `qoder/site/` 静态展示站，
**不含本机后端（`app/`+`web/`）与学习数据库（`.powercontext/`）**。全部命令与真实返回逐条写在
`evidence/publish-20261116/00-publication-commands.md`；下面只记结论与核验方式。

| 验收项 | 真实结果 | 核验方式 / 留档 |
| --- | --- | --- |
| 发布产物是专用输出目录而非源码根 | `site/dist/`（`index.html` + `assets/`，10 个文件、4.7 MiB）；包内无 `.env`/密钥/源码映射/数据库/符号链接 | 逐文件清单见 `…/00-publication-commands.md` 第 0 节 |
| 未申请任何后端能力 | `prepare_site` 输入不含 `functionDirectory`/`databaseAccess`/`secretNames`；站点 `auth_integration_status: not_required` | 同上第 1、7 节 |
| 草稿就绪后才发布 | `artifactReady=true`、`dependenciesReady=true`、`blockers=[]`、`preparation.state=ready`、`canPublish=true` | `get_publish_status` 返回，第 2 节 |
| 发布成立 | `published=true`、发布 Operation `succeeded` 且 `committed=true`、`readiness=publication_completed`；该 action 只调用了一次 `publish_site` | 同上第 4–5 节 |
| 受众改为任何人可访问 | `update_access_policy mode=public confirmPublic=true` → `policy.mode=public`，`rule_count` 仍为 0（未邀请具体访客） | 同上第 6–7 节 |
| 线上地址 | `https://recall-coach-f3jx2506222.qoder.zone/`（工具在 slug 后追加账号后缀，地址取自返回值而非预测） | `get_publish_status.site.url` |
| 匿名可达性 | `curl`（无 Cookie、无自定义头）→ `HTTP/2 200`、0 次重定向、`ssl_verify_result=0`、平台 `x-request-id` 存在；两次抓取正文逐字节相同 | `…/10-public-http.txt`、`…/site-public-headers.txt` |
| 线上正文 = 本机产物 | 差异只有一处 hunk：平台注入的 `data-qoder-watermark` 脚本 55 行（脚本内无 `fetch`/`XHR`/`WebSocket`/`sendBeacon`）；CSS/JS/favicon/6 张截图的 SHA-256 与 `site/dist/` 全部一致；不存在的路径 404 | `…/10-public-http.txt`、`…/11-public-assets.txt` |
| 线上页面行为 | 真实 Chromium 对**线上地址**跑同一套 30 项断言：全部通过，其中窄屏 390px 横向溢出 0px、6 张截图真实解码、无本站之外请求、页面无接口调用、无 `scp_*`/`art_*` 标识、灯箱与复制按钮可用 | `evidence/site-public/site-report.json` + `site-desktop.png`、`site-mobile.png` |
| 发布前的产物级检查 | 先把 `site/dist/` 用 `python3 -m http.server` 起本机预览，跑同一套 30 项断言通过（30/30） | `evidence/site-dist/site-report.json` |
| 未执行 | `git add`/`commit`/`push`（远端字段仍待补充）；后端上线；任何学习记忆公开 | `…/00-publication-commands.md` 第 10 节 |

一条如实说明的负面结论：**Qoder 账号 UID 无法由本次可用工具面核实**。`get_context`、`get_site`、
`list_sites` 都不返回 owner/UID，32 个 Sites 操作里没有读取本人 UID 的入口，本机非凭据状态里只有
`lastLoginMethod: job_token`（设备标识 `installation_id`/`umid` 不是账号 UID）。本次工具检查时第 7 节该行标为
待补充，由提交人自行填写；可核实的归属证据是 `list_sites(scope=owned)` 返回的唯一站点即本次发布站点。
平台在页面注入水印属宿主行为，不在本仓库源码内；除注入部分外线上正文与本机产物一致。


## 交付补充

提交人已提供 Qoder UID `1684174493638547`。代码由外层完成最终 Git 整理，早前日志中的“未提交/未推送”描述仅记录当时操作范围。完整代码位于团队仓库的 `qoder-recall-coach` 分支、`qoder/` 目录；最终提交以 `git rev-parse HEAD` 为准。
