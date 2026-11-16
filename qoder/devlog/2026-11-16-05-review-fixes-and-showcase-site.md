# 开发记录 05 · 独立审查修正与展示站草稿（2026-11-16 下午到傍晚）

范围：三项审查发现的修复与复验，以及 `site/` 静态展示站草稿的构建与页面层检查。
本文只写真实执行过的命令与真实输出；未执行的（prepare、发布、推送）明确写在最后。

---

## 1. 幂等：把「已完成」写进数据库，而不是写在内存里

首轮实现只在内存里记 `session.done`，进程一重启就忘。这轮改成服务端持久状态：

```sql
-- app/store.py SCHEMA 新增列（旧库用 ALTER 原地迁移）
sessions.submit_state   TEXT NOT NULL DEFAULT 'open'   -- open | partial | done
sessions.submitted_at   TEXT
sessions.seq_base       INTEGER
sessions.result_json    TEXT                            -- 首次判分的完整结果，回放时原样返回
CREATE TABLE memory_writes (session_id, slot, kind, topic, artifact_id, receipt_json, payload_json,
                            created_at, PRIMARY KEY (session_id, slot));
CREATE UNIQUE INDEX idx_attempts_session_question ON attempts(session_id, question_id);
```

三个决定性的细节：

1. **正文必须可重放**。`begin_submit()` 在写入前就把 `at`（时间戳）与 `seq_base`（流水基线）定下来，
   记忆正文里嵌的 `recall-coach.memory.v1` 载荷只依赖这两个值 + 判分结果，所以重试生成的字节与首次完全一致。
2. **基线折叠要剔除本次已写的条目**。否则重试时「已写入的 progress」会被当成历史记忆再折叠一次，
   EWMA 等于把同一次答题算了两遍。`app/main.py` 里：
   `fold([m for m in memories if m["artifact"]["artifact_id"] not in written_ids], ...)`。
3. **`attempts` 改成 `(session_id, question_id)` 幂等 upsert**，旧库里已有的重复行在迁移时按
   `MAX(id)` 分组删掉（`_migrate`）。

踩到的坑（真实输出）：`_migrate` 一开始无条件 `PRAGMA table_info(sessions)`，全新库里表还不存在时
两个测试报 `sqlite3.OperationalError: no such table: sessions` → 先查 `sqlite_master`，表不存在直接返回，
让 `SCHEMA` 自己建表。

## 2. 失败提示：按「实际发生了什么」生成

`MemoryBackendError` 以前一律说「本次没有写入」。批量写入可能是部分成功，这句话在那种情况下是错的。
新增 `accuracy_notice()`（`app/powercontext_service.py`）按阶段分支，并把批量写入改成
「先把全部正文算好，再逐条写」，中断时把已完成回执与缺失槽位挂在异常上：

```python
exc.written_receipts = [row["receipt"] for row in ledger.values()]
exc.pending_slots = [f"{kind}:{topic}" for kind, topic, _, _ in planned if f"{kind}:{topic}" not in ledger]
exc.notice = (f"本次批量写入只完成了一部分：{len(exc.written_receipts)} 条已真实写入 PowerContext，回执随本次响应返回；"
              f"失败发生在 {current_slot or '未知条目'}，还有 {len(exc.pending_slots)} 条本次没有写入。…")
```

故障注入测试（`tests/test_live_flows.py`）用 `monkeypatch` 替换 `live_app.memory.remember`，让第 2 次调用抛
`powercontext_unavailable`，断言：响应 502、`written_receipts` 1 条、`pending_slots` 含 `weakness:<topic>`；
重试后 `reused_from_ledger == 1`；并且**与一个无故障对照身份的掌握度表逐项相等**。
为了让 session 级 app 实例可被 monkeypatch，把 `conftest.py` 的 fixture 拆成 `live_app` + `live_client`。

## 3. 复验踩到的两个真问题

**① 验收跑在旧代码上。** 第一次重跑（`evidence/rerun-20261116-fix1`）在 e2e 第 06 步崩了：
`KeyError: 'memory_writes'`。原因不是代码错，而是 `acceptance.sh` 第 0 步只启动服务、没有先停旧进程，
跑的是修正前已经加载进内存的模块。修法：第 0 步先 `stop_app.sh` + `stop_powercontext.sh` 再启动，
输出留档到 `00-restart.log`。**验收脚本必须保证跑在待验代码上**，否则日志会误导人。

**② 回放的「新写入条数」语义混淆。** 第二次重跑（`fix2`）里，重复提交返回的是首次留存的判分结果，
其中 `memory_writes.newly_written = 10` 描述的是**原始批次**，而 `replay.repeated_submit = true`，
我的断言写成 `newly_written == 0` 就必然失败。这不是断言太严，而是字段本身会诱导误读。
改法：在 `replay` 里显式给出本次请求的真实增量 `new_memories_this_request` / `new_attempts_this_request`（都是 0），
`memory_writes` 保持描述原始批次；e2e、`verify_ui.py`、pytest 三处断言都改用前者。

**③ 前端按钮的启用条件写反。** `fix3` 那轮验收在第 8 步失败：

```
FAIL  已完成判分的会话不允许再次点击提交
通过 43 项断言，失败 1 项；截图与报告见 evidence/rerun-20261116-fix3/screenshots
```

`web/assets/app.js` 里写的是 `$("#submit").disabled = !committed;`——判分成功后按钮反而可以再点，
失败时反而锁死。改成 `disabled = committed`（未完成才允许重试补缺失条目），重跑全量通过。
这条也印证了幂等为什么必须在服务端持久：前端状态不是约束，只是提示。

## 4. 修正后的全量验收（真实输出）

```bash
EVIDENCE_DIR=evidence/rerun-20261116-fix4 bash scripts/acceptance.sh   # 退出码 0
```

```
- pytest：29 passed, 1 warning in 14.21s
- 页面断言：44 项通过，未捕获 JS 错误 0 条
- 端到端闭环：14 个步骤全部通过；重启后续接 14 个步骤全部通过
- 跨重启记忆：Scope scp_19ftvqw81ry1zx8nx32vhc1wk5，19 条条目（含 18 条激活）逐条指纹一致
- 模型 API 密钥使用：无
```

原始 `evidence/`（14:01 那轮）一个字节都没改；`fix1`/`fix2`/`fix3` 保留为过程证据。

> 读 `evidence/rerun-20261116-fix4/08-ui-verify.log` 时注意：其中两行 PASS 的旧文案容易被误读成
> 「重复提交改变了记忆条数」「重复提交又写入了记忆」。它们当时是**否定式断言**（断言条数不变、新写入为 0），
> 括号里的 `{'before': 15, 'after': 15, 'newMemories': 0}` 才是实际结果。这轮之后把提示语改成了
> 通过/失败都能读通的写法（只改字符串，不改断言逻辑），所以后续运行的文案与此份留档不同。

## 5. 展示站草稿：只做展示层，不假装能演示

`site/` 是纯静态（`index.html` + `assets/site.css` + `assets/site.js` + `assets/screenshots/*.png`），
无构建步骤、无外部字体、无第三方脚本。内容口径：

- 截图全部 `cp` 自 `evidence/rerun-20261116-fix4/screenshots/`，`md5sum` 与源文件一致，不做任何合成或修图。
- 验收数字（29 / 14 / 7 / 44 / 0）逐项抄自该轮的 `acceptance-run.md` 与 `ui-report.json`，不写推测性提效百分比。
- 明确写了「完整交互项目需按运行说明在本机运行」，页面里没有任何 `fetch`/`XHR`/`WebSocket`，
  也没有把 `127.0.0.1` 做成可点击链接。
- 边界与隐私一节沿用审查结论：`learner_id` 不是凭证，Scope 是数据隔离不是权限隔离；
  截图来自 `ui-182107` 这类自动化验收合成身份。

新写了 `scripts/verify_site.py`（复用 `verify_ui.py` 的真实 Chromium 定位与断言记录），30 项断言。
它抓到的四个问题都是我自己写出来的：

| 现象 | 根因 | 修法 |
| --- | --- | --- |
| 首屏只有图、标题正文跑到 1300px 以下 | `<img>` 的 `width/height` 属性把高度钉成 1100px，`aspect-ratio` 因两个维度都确定而被忽略；整行被撑高，`align-items:center` 又把文字推到中间 | CSS 补 `height:auto`，图片框 `aspect-ratio:16/10` + `object-fit:cover` + `object-position:top`，属性值改成真实像素尺寸（1440×7651 等） |
| 三分钟步骤里 `code` 各占一行、行间大片空隙 | `.minsteps li` 是 `display:grid` 两列，但文本节点与 `<code>` 都成了匿名网格项，每个掉进新行 | 文案包进 `<span>`，保证 li 恰好两个子元素 |
| 窄屏接口表把行撑得很高、第三列被挤没 | 第二列 `code{white-space:nowrap}` 的长路径吃掉绝大部分宽度 | 固定三列宽度 33/38/29，长路径允许 `overflow-wrap:anywhere`，表体 `min-width:560px` 交给 `.table-wrap` 横向滚动，并在 caption 里提示可滑动 |
| 灯箱里 7000px 高的整页截图被压成一条看不清的细条 | `max-height:82vh` + 居中缩放 | 灯箱改为可上下滚动的原尺寸查看器（宽度 `min(1400px, 100vw-40px)`），caption 固定底部，打开时焦点移到关闭按钮、Esc 关闭后焦点归还 |

另外两处是检查脚本自己的错，不是站点问题：懒加载图片没滚过就断言「未解码」；
`a.href` 会把 `#run` 解析成 `http://127.0.0.1:4173/#run`，于是「不得链接本机地址」误报。
分别改成先整页滚一遍再测、以及读 `getAttribute('href')` 原始值。

页面高度从 13184px 降到 6937px；390px 窄屏 `documentElement.scrollWidth - innerWidth = 0`。
留档：`evidence/site-draft/site-report.json`（30 项）+ 整页桌面/窄屏截图各一张。

## 6. 本轮没有做的事

- **未执行 `prepare_site` / `publish_site`**：按要求停在可预览草稿，因此没有 `.站点名称.qoder.site` 描述文件，也没有站点地址。
- **未 `git add` / commit / push**：新增与修改的文件都留在工作区等本人确认。
- 未使用任何模型 API 密钥；未读写其他赛题目录。
- 展示站的在线可达性、Qoder 账号 UID、站点 URL 在 `SUBMISSION.md` 第 1 节仍标 **待补充**。
