# SUBMISSION · 提交说明

作品：**拾忆 · Recall Coach**（2026 OceanBase PowerContext 生态赛）
一句话：换一次会话、重启一次服务，不用重新交代你的薄弱点——学习记忆放在真实 PowerContext 里，选题依据可逐条核验。

---

## 1. 账号与发布信息

| 项目 | 状态 |
| --- | --- |
| Qoder 账号 UID | `1684174493638547`（提交人提供；CLI 用户名与该数字一致） |
| Qoder Sites 站点名称 / URL | **已发布**：展示名 `拾忆 · Recall Coach`，slug `recall-coach`，地址 <https://recall-coach-f3jx2506222.qoder.zone/>（受众：任何人可访问）。2026-11-16 19:03–19:05 真实执行 prepare → publish → 受众变更 → 公开可达性复核，逐项留痕在 `evidence/publish-20261116/` 与 `evidence/site-public/`。发布内容只有 `site/dist/` 静态文件，不含后端与学习数据 |
| 代码仓库远端 / 分支 | `https://github.com/m-xy-boop/powercontext-mxy` / `qoder-recall-coach`；最终 commit 见 `git rev-parse HEAD` |
| 演示截图 | 已提供，来自真实 Chromium 渲染：`evidence/screenshots/*.png`（非设计稿、非后期合成）；同一批截图已随展示站公开发布 |
| 开发会话记录 | 已提供：`evidence/qoder-development*.stream.jsonl`（真实 Qoder 会话留档） |

## 2. 交付物清单

```
README.md            作品说明：解决什么、怎么跑、用了哪些公开接口、边界在哪
RUNBOOK.md           9 步操作手册：启动 → 手工闭环 → 跨重启验证 → 隔离 → 验收 → 部署 → 排错
ACCEPTANCE.md        验收结论：逐项写明「怎么验的 + 真实结果 + 留档文件」
SUBMISSION.md        本文件
app/                 FastAPI 服务端（记忆读写封装、掌握度折叠、记忆驱动选题、判分编排）
web/                 四个中文页面 + 样式 + 前端逻辑（无构建步骤）
site/                静态展示站（中文产品介绍 + 真实截图 + 真实验收数字，不调用本机 API），已公开发布；
                     `site/dist/` 是实际发布的输出目录，`.拾忆 · Recall Coach.qoder.site` 是 prepare_site 生成的描述文件
data/questions.json  内置原创题库：8 知识点 × 3 题 = 24 题
tests/               29 项自动化测试（15 离线单元 + 14 真实记忆流程）
scripts/             启停、接口核实、端到端闭环、浏览器验收、展示站页面检查、总验收
reference/           powercontext-1.5.1-openapi.json（由运行中的服务下载的真实契约）
evidence/            验收输出、指纹 JSON、真实截图、会话记录、展示站检查（site-draft / site-dist）、发布留痕（publish-20261116 / site-public）
devlog/              4 篇开发记录（真实命令、踩坑与修法）
pyproject.toml / uv.lock / .python-version  Python 3.12 + 依赖锁定
```

## 3. 三分钟演示脚本

0. 先看公开展示站 <https://recall-coach-f3jx2506222.qoder.zone/>（静态介绍 + 真实截图 + 真实验收数字，
   **不提供在线作答**；下面的步骤 1–7 必须在本机运行完整项目）。
1. `/diagnose` 输入身份 `demo-teacher` → 首屏 **基础诊断**，8 个知识点各 1 题，
   每题写着「PowerContext 中没有该知识点的掌握记录，先补齐诊断」。
2. 故意答错两题 → 提交 → 回执表逐条列出 `atomic-memory/art_…@1`、字节数、
   校验方式 `get_atomic_memory_state`（写入后回读，不是「假装成功」）。
3. 点「按新记忆再来一轮」→ 模式变 **记忆驱动复习**，答错的知识点排到前面，
   题目下方出现「依据记忆：atomic-memory/art_…@1」，优先级表给出加权理由。
4. `/memory` → 检索「薄弱点」显示真实 `mode=text` 与命中分数；生成 PreparedContext；
   把题量改成 3、勾选侧重知识点并保存 → 新记忆标识出现，再来一轮立刻按新偏好出题。
5. **终止并重启 PowerContext**（`scripts/stop_powercontext.sh && scripts/start_powercontext.sh`），
   重新打开页面用同一身份进入 → 仍是复习模式，薄弱点顺序不变；`/verify` 页面显示同一 `scope_id` 的记忆条数。
6. 导出 Markdown → 每条记忆都带 `family/artifact_id@revision`，可直接核对。
7. 回到刚提交过的那一轮：按钮已是「本轮已判分并写入记忆」且禁用；`/memory` 记下当前条数，
   再对同一会话提交一次（页面上点不动，可用 `curl` 直接打 `POST .../submit`）→ 返回当时留存的判分结果、
   标注 `repeated_submit: true`，条数与导出流水行数都不变——重复提交被服务端持久幂等挡住。

## 4. Qoder Sites 展示站：已发布（2026-11-16 19:03–19:05）

**线上地址：<https://recall-coach-f3jx2506222.qoder.zone/>**（受众：任何人可访问；提交人明确授权公开发布）。
发布内容 = `site/dist/`（`site/index.html` + `site/assets/` 的逐文件复制，共 10 个文件 4.7 MiB）。
工具侧回执、受众变更与公开可达性复核逐条留痕在 `evidence/publish-20261116/`，浏览器层复核在 `evidence/site-public/`。

1. 站点身份：`displayName` 拾忆 · Recall Coach、`slug` `recall-coach`，实际分配的主机名 `recall-coach-f3jx2506222.qoder.zone`
   （工具在 slug 后追加账号固定后缀，最终地址以工具返回值为准，不预测）。
2. 描述文件 `.拾忆 · Recall Coach.qoder.site` 由 `prepare_site` 生成在 `site/` 下，**不是手写、也未手改**；
   它记录 `deploymentId`、`artifactSha256`、`indexSha256`，指向那次已冻结的准备结果。
3. 准备输入刻意不含 `functionDirectory` / `databaseAccess` / `secretNames`：即**没有申请函数、数据库与 Secret**，
   与「只发布静态展示站、不发布本机后端与学习数据库」的授权范围一致。
4. 这个草稿是**展示站**，刻意不包含在线演示：页面只有静态资源，没有任何接口调用，
   因此静态托管路径就能如实承载它——不会出现「看起来能用其实连不上记忆服务」的误导。
   线上页面同样没有 `127.0.0.1` 的可点击链接、没有任何 `scp_*`/`art_*` 真实记忆标识（浏览器断言已核）。
5. 发布后复核（独立于发布回执）：`curl` 无 Cookie 取首页 `HTTP/2 200`；正文 = 本机 `site/dist/index.html`
   + 平台注入的 `data-qoder-watermark` 脚本（55 行，本身不发起网络请求），其余逐行相同；
   CSS/JS/favicon/6 张截图的 SHA-256 与本机完全一致；不存在的资源返回 404。
6. 平台会在页面注入「Made with Qoder」水印脚本（宿主行为，不在本仓库源码里，也不受本地预览影响）；
   除此之外线上正文与本机输出逐字节一致。
7. 若以后要发布**可交互**的版本（在线作答），必须另外满足：站点具备后端/函数能力以承载 `app/`（ASGI 或 Edge Function），
   且 `POWERCONTEXT_URL` 指向公网可达并置于 TLS 之后的 PowerContext 后端（见第 5 节），
   同时先补认证与按认证主体派生 Scope 幂等键——当前形态不具备公开多用户安全条件。
   **本次发布的静态站不提供任何在线作答与记忆读写**，页面里也这样写着。
8. 发布内容复核：包内没有 `POWERCONTEXT_TOKEN` 或任何密钥（`.env` 根本不在包内），
   没有把演示记忆写进任何共享 Scope（站点不连后端，也就无从写入），
   截图里出现的 `ui-182107`/`acceptance-*` 均为自动化验收用的合成身份，不含个人信息。
9. 更新已发布站点的路径（供以后改版用）：改 `site/` 源码 → 重新复制到 `site/dist/` →
   `verify_site.py` 本地复核 → 对**同一 `projectId`** 用**新的 `actionId`** 再 `prepare_site` → `publish_site`；
   受众保持不变。发布失败时保留线上既有版本，不声称已更新。

## 5. 独立后端的部署要求

| 项 | 要求 |
| --- | --- |
| 版本 | PowerContext 1.5.1（`uv tool install "powercontext[cli,server]==1.5.1"`），仅用公开 HTTP 契约 |
| 持久化 | `POWERCONTEXT_HOME` 指向持久卷；目录含 `powercontext.db`(+`-wal`/`-shm`)、`*.cursor-key`、迁移状态目录。备份该目录即备份全部记忆 |
| 监听 | `powercontext server run --host <地址> --port <端口>`；跨主机必须置于 TLS 反向代理之后，禁止公网明文 HTTP |
| 鉴权 | 启用 token 时应用侧设 `POWERCONTEXT_TOKEN`；封装层会把 401/403 的真实 `code`/`request_id` 显示到页面 |
| 认证与信任边界 | 应用自身**没有登录/授权**，`learner_id` 非凭证：只监听 `127.0.0.1` 或受信任内网。公开多用户需：反代认证 → 服务端由认证主体派生 `idempotency_key`（不接受客户端自由填写）→ 本地库按认证主体分区 → 记忆服务侧启用 token 分域。详见 RUNBOOK 第 8 步第 7 项 |
| 能力 | 只需默认能力：原子记忆、全文检索、PreparedContext、lifecycle、content source。**不需要嵌入/推理模型** |
| 应用侧 | `uv run uvicorn app.main:app --host 0.0.0.0 --port 8642` + 进程守护；`data/runtime/` 可重建（只存映射与流水） |
| 可观测 | `var/log/powercontext-operations.jsonl` 每次真实调用落一行（操作、记忆标识、状态、字节、耗时），便于线上排查 |
| 故障口径 | 记忆服务不可用时接口返回 `503 powercontext_unavailable`，页面不显示任何成功状态、不写假记忆 |

## 6. 已知限制（如实说明）

- 题库为固定 24 题原创内容，不做动态出题；题目覆盖数据库基础 8 个知识点，不是完整课程。
- 未配置嵌入模型，检索为全文模式（`mode=text`），中文近义改写召回不敏感；语义检索需另行配置推理服务。
- 掌握度模型为 EWMA + 连错计数，属于可解释的简单模型，未做知识追踪（DKT 等）。
- 页面为单人本机演示形态：**无登录体系，身份标识（`learner_id`）只是选择 Scope 的短名，不是凭证**。
  能访问该端口的人输入同一标识即进入同一份记忆，因此只应在本机或受信任内网运行；
  文档与页面均不声称「公开多用户安全」，第 6 节身份隔离证明的是 Scope **数据隔离**，不是权限隔离。
  面向公众的多用户形态需要先加认证、由服务端从认证主体派生 Scope 幂等键（见第 5 节「认证与信任边界」与 RUNBOOK 第 8 步）。
- 展示站**已公开发布**（静态前端，见第 4 节）；它不提供在线作答，也不承载任何学习记录，
  完整交互项目仍需按运行说明在本机运行。**代码推送与 PR 本次未执行**，远端地址字段仍为待补充。

## 7. 合规声明

- 未使用任何模型 API 密钥，核心闭环完全离线可复现（判分、选题、记忆读写）。
- 未虚构 Qoder 记录、性能数字、用户数量或他人反馈；文档中每个数字都能在 `evidence/` 找到对应输出。
- 未读写其他赛题目录；所有新增文件位于本 `qoder/` 目录内。
- 未提交任何密钥；`.env.example` 中 token 为空占位。发布的静态包内不含 `.env`、密钥、源码映射与任何数据库文件（逐文件清单见 `evidence/publish-20261116/00-publication-commands.md` 第 0 节）。
- **未推送**：`git status` 中的新增文件等待本人确认后自行提交，本次没有 `git add`/`commit`/`push`。
- **已发布（经明确授权）**：仅 `site/dist/` 静态展示站，见第 4 节；未发布本机后端、未发布学习数据库、
  未公开任何真实学习记录。

## 8. 独立审查修正记录（2026-11-16 第二轮）

| 审查发现 | 修正 | 证据 |
| --- | --- | --- |
| 1. 同一 session 重复提交会再次累计答题流水与记忆 | 服务端持久幂等：`sessions.submit_state/submitted_at/seq_base/result_json` + `memory_writes` 账本；已完成会话直接回放，`attempts` 改 `(session_id, question_id)` 幂等 upsert，记忆正文的 `at/seq` 按提交固定 | `tests/test_store_idempotency.py`（6 项，含旧库原地迁移）、`tests/test_live_flows.py::test_repeat_submit_is_replayed_and_never_accumulates`（含新进程回放）、`e2e_loop.py` 第 06 步、`verify_ui.py` 绕过按钮的直接重放探测 |
| 2. `MemoryBackendError` 一律说「本次没有写入」，而批量写入可能部分成功 | 提示按真实阶段分别生成（`accuracy_notice`）；批量写入改为「先算好全部正文再逐条写」，失败时 `mark_submit(partial)` 并返回 `written_receipts` + `pending_slots`；重试从账本续写缺失条目，基线折叠时剔除本次已写条目，掌握度不叠加两次 | `tests/test_live_flows.py::test_partial_batch_write_is_reported_and_completed_on_retry`（注入第二条失败，与无故障对照身份逐项比对掌握度相等）、`test_failure_notices_match_what_actually_happened`、`test_accuracy_notice_matches_the_real_failure` |
| 3. 无登录的 `learner_id` 只是本机隔离，不得声称公开多用户安全 | README §8/§9、RUNBOOK 第 6 步与第 8 步第 7 项、SUBMISSION §5/§6、页面文案（`/`、`/diagnose`、`/verify`）统一改为「Scope 数据隔离 ≠ 权限隔离」，并写明公开多用户部署的前置条件 | 本轮文档改动；页面文案见 `web/index.html`、`web/diagnose.html`、`web/verify.html` |
| 4.（复验时发现）判分成功后提交按钮反而可再点：`web/assets/app.js` 里写成 `disabled = !committed`，语义反了 | 改为 `disabled = committed`：已判分的会话按钮锁定并显示「本轮已判分并写入记忆」，未完成的提交保持可点以便重试补缺失条目。幂等仍以服务端持久保护为准，前端状态只是提示 | `verify_ui.py` 的「已完成判分的会话不允许再次点击提交」断言（首轮失败、修正后通过），见 `evidence/rerun-20261116-fix3/08-ui-verify.log` 与 `evidence/rerun-20261116-fix4/08-ui-verify.log` |

修正后的完整验收输出另存于 `evidence/rerun-20261116-fix4/`（2026-11-16 18:21 全量重跑，退出码 0；
`fix1`/`fix2`/`fix3` 三目录是同一轮修正过程中的真实中间结果，保留以便追溯问题是如何被发现的；原始 `evidence/` 下的日志与截图保持不动）。
逐项结论见 `ACCEPTANCE.md` 第 9 节。展示站在该轮（18:21）**只到本机静态预览，未 prepare、未发布**；
随后于 19:03–19:05 经提交人明确授权发布，见第 10 节。

## 10. 最终发布状态（2026-11-16 19:03–19:08，本机真实执行）

| 项 | 真实结果 | 留痕 |
| --- | --- | --- |
| 发布对象 | `site/dist/`（10 个文件、4.7 MiB 纯静态），不含 Function / 数据库 / Storage / Secret 申请 | `evidence/publish-20261116/00-publication-commands.md` 第 0–1 节 |
| 站点身份 | 展示名 `拾忆 · Recall Coach`，slug `recall-coach`，`site_id 01a1e406-a4f4-70b6-8743-fbba65098e5a`，`lifecycle active`、`governance_status normal` | 同上第 1–5、7 节 |
| 发布结果 | `published: true`、发布 Operation `state succeeded` / `committed true`、`readiness publication_completed` | 同上第 5 节 |
| 受众 | `public`（任何人可访问，`confirmPublic=true`，`rule_count` 仍为 0：未邀请任何具体访客） | 同上第 6–7 节 |
| 线上地址 | <https://recall-coach-f3jx2506222.qoder.zone/> | `10-public-http.txt` |
| 公开可达性（匿名） | `HTTP/2 200`、0 重定向、TLS 校验通过；正文与本机输出仅差平台注入的 55 行水印脚本；CSS/JS/favicon/6 张截图 SHA-256 全部一致；不存在的路径 404 | `10-public-http.txt`、`11-public-assets.txt` |
| 线上浏览器复核 | 真实 Chromium 对线上地址跑 `verify_site.py`：**30 项断言全部通过**（含窄屏 390px 横向溢出 0px、6 张截图真实解码、无本站之外请求、源码无接口调用、无真实记忆标识） | `evidence/site-public/site-report.json` + `site-desktop.png`、`site-mobile.png` |
| 未执行 | `git add`/`commit`/`push`；后端发布；记忆数据公开 | 第 7 节、`00-publication-commands.md` 第 10 节 |
| 无法核实 | Qoder 账号 UID（工具面不返回，见第 1 节说明） | 同上第 8 节 |

## 交付补充

提交人已提供 Qoder UID `1684174493638547`。代码由外层完成最终 Git 整理，早前日志中的“未提交/未推送”描述仅记录当时操作范围。完整代码位于团队仓库的 `qoder-recall-coach` 分支、`qoder/` 目录；最终提交以 `git rev-parse HEAD` 为准。
