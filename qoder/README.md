# 拾忆 · Recall Coach

基于 **OceanBase PowerContext** 的数据库基础复习工作台。把「哪里不会、掌握度怎么变、想怎么复习」写进真实记忆，
换一次会话、重启一次服务，都不用重新交代薄弱点：系统先读记忆，再按记忆安排题目，并给出可核验的依据与记忆标识。

> 2026 OceanBase PowerContext 生态赛参赛作品。核心闭环**不需要任何模型 API 密钥**，题目与判分全部本地完成。
> 静态展示站（产品介绍 + 真实验收截图，**不提供在线作答**）：<https://recall-coach-f3jx2506222.qoder.zone/>

---

## 1. 它解决什么

复习最常见的浪费是「每次都从头测」。老师换一台机器、隔天重开应用，学生得重新说一遍自己哪里弱；
而大多数练习系统把掌握情况存在应用自己的表里，换个会话就得重新积累。

拾忆把这件事交给 PowerContext 的学习记忆：

| 记什么 | 记忆 kind | 何时写入 | 何时读回 |
| --- | --- | --- | --- |
| 薄弱知识点与掌握度 | `weakness` | 判分后该知识点低于阈值或连错 | 每次安排复习时 |
| 掌握度变化过程 | `progress` | 每次判分后每个被考察知识点 | 每次安排复习时 |
| 复习偏好（题量、侧重、备注） | `preference` | 用户在「学习记忆」页保存偏好 | 决定题量与候选知识点 |
| 上一次复习安排 | `plan` | 每次生成安排时 | 避免重复占用题量、抑制已掌握项 |

每条记忆都是 PowerContext 的原子记忆条目，页面上任何一句选题依据都能回指到
`atomic-memory/art_…@修订号`，可当场核验。

## 2. 三十秒跑起来

```bash
scripts/start_powercontext.sh   # 启动真实 PowerContext Server（SQLite 持久化，仅监听 127.0.0.1:8777）
scripts/start_app.sh            # 启动拾忆应用（http://127.0.0.1:8642）
```

打开 <http://127.0.0.1:8642> → 「诊断与复习」→ 填一个身份标识（例如 `teacher-wang`）→ 答完 8 题提交 →
点「按新记忆再来一轮」：题目顺序就变了，且每题下面写着它是依据哪几条记忆选出来的。

完整操作与排查见 [`RUNBOOK.md`](RUNBOOK.md)，验收结论与证据见 [`ACCEPTANCE.md`](ACCEPTANCE.md)。

## 3. 闭环怎么转

```
选身份 ──▶ 读 PowerContext 记忆 ──▶ 折叠成知识点掌握状态 ──▶ 打分排序 ──▶ 出题（附依据+记忆标识）
   ▲                                                                   │
   │                                                                   ▼
新会话/重启后再次进入 ◀── 记忆驱动选题 ◀── 写回记忆 ◀── 服务端判分（无模型参与）
```

- **无记忆**：`diagnosis` 模式，覆盖全部 8 个知识点各 1 题，建立基线。
- **有记忆**：`review` 模式，候选知识点 = 薄弱或从未测过的 + 偏好侧重的，按优先级分排序，题量在候选间轮转。
- **优先级分**（`app/mastery.py`）：掌握缺口 `1-mastery` 为基础分，连错加权 `+0.10×min(连错,3)`，
  偏好侧重 `+0.20`，无记忆 `+0.15`，「上次已安排且掌握度≥0.75」`-0.25`，本会话已作答 `-0.05`。
  每一项都会作为「选题依据」显示在题目下方。
- **掌握度**：对每个知识点按作答顺序做指数滑动平均（α=0.45，边界 0.02–0.98），阈值 0.55 判薄弱。
- **重复提交保护**：会话提交状态与已写入的记忆回执都持久记录在本地库。同一会话再次提交不会重新判分、
  不会新增答题流水、也不会再写一遍记忆，服务端直接回放当时留存的判分结果与真实回执（含重启后）。
  批量写入中途失败时，已完成条目记入同一份账本：重试只补齐缺失条目，掌握度不会被叠加两次。

## 4. 只用公开接口

所有记忆读写都走 `powercontext[client]==1.5.1` 的 Python Client SDK（`powercontext.client.PowerContextClient`），
没有私有接口、没有猜测字段。公开契约留档在 `reference/powercontext-1.5.1-openapi.json`（由运行中的服务
`GET /openapi.json` 下载）。

| SDK 方法 | 真实 HTTP 接口 | 在作品里的用途 |
| --- | --- | --- |
| `get_capabilities()` | `GET /v1/capabilities` | 页面如实展示检索模式、是否具备模型抽取 |
| `create_scope()` / `update_scope()` | `POST /v1/scopes`、`PUT /v1/scopes/{scope_id}` | 一个学习身份一个 Scope，`idempotency_key` 幂等复用 |
| `remember_memory()` | `POST /v1/memory/remember` | 写入 `weakness/progress/preference/plan` 四类记忆 |
| `get_atomic_memory_state()` | `GET /v1/scopes/{id}/artifacts/atomic-memory/{aid}/state` | 写入后回读校验，回执里标注校验方式 |
| `list_atomic_memories()` | `POST /v1/atomic-memory/list` | 按 kind/states 分页读回，供折叠与页面清单 |
| `search_memory()` | `POST /v1/memory/search` | 真实检索（未配置嵌入模型时为全文 `mode=text`） |
| `prepare_context()` | `POST /v1/context/prepare` | 生成有界 PreparedContext 供下一轮注入 |
| `change_atomic_memory_lifecycle()` | `POST /v1/atomic-memory/lifecycle` | 以 `state_version` 乐观并发把条目置为 `forgotten` |
| `capture_content_source()` | `POST /v1/sources/content` | 答题流水作为证据留存 |

失败一律如实上抛：服务不可达返回 `503 powercontext_unavailable`，被拒绝的请求带回真实
`code`、`status_code` 与 `X-PowerContext-Request-Id`。页面在这种情况下不显示任何「已保存」状态，
也不会用模拟记忆顶替。

提示语按真实发生的事分别给出（`app/powercontext_service.py:accuracy_notice`），响应里带 `notice` 字段：

| 情况 | 页面提示 | 附带信息 |
| --- | --- | --- |
| 读取失败（列表/检索/prepare/能力探测） | 「本次没有读到 PowerContext 记忆，页面对应区域显示读取失败，不会用模拟数据填充」 | 不谎称写入结果 |
| 请求未送达 / 被明确拒绝 | 「请求未能送达…本次没有产生任何记忆写入」/「拒绝了本次写入，已有记忆没有被改变」 | 4xx 带真实 `code` |
| 写入结果无法确认（5xx、回读不一致） | 「本次记忆写入未获确认…页面不会显示假回执」 | 建议在「操作留痕」核对后重试 |
| 批量写入部分成功 | 「只完成了一部分：N 条已真实写入，失败发生在 X，还有 M 条没有写入」 | `written_receipts`（已写入的真实回执）+ `pending_slots` |

## 5. 目录

```
app/                服务端（FastAPI）
  powercontext_service.py  真实读写封装：SDK 调用、回读校验、错误映射、操作留痕
  mastery.py               记忆折叠 + 掌握度 EWMA + 可解释优先级（纯函数）
  planner.py               记忆驱动的复习安排（诊断/复习两种模式）
  main.py                  接口编排：身份、安排、判分写回、记忆检索/遗忘/导出
  bank.py / store.py / config.py
data/questions.json 内置原创题库：8 个知识点 × 3 题 = 24 题（固定内容，不声称动态 AI 出题）
web/                四个页面 + 样式与前端逻辑（无构建步骤）
site/               静态展示站（真实截图 + 真实验收数字 + 源码链接，不调用本机 API）；dist/ 为发布产物，已公开发布
tests/              离线单元测试 + 针对真实记忆服务的流程测试
scripts/            启停、接口核实、端到端闭环、浏览器验收、展示站检查、总验收
reference/          PowerContext 1.5.1 真实 OpenAPI 契约留档
evidence/           验收输出、真实浏览器截图、Qoder 会话记录
devlog/             开发记录（真实命令与结果）
```

本地 SQLite（`data/runtime/recall-coach.db`）只存三样东西：学习身份 → Scope 的映射、会话与答题流水、
用于轮转的计数器。**学习记忆只存在 PowerContext**，删掉本地库只要 Scope 标识还在，记忆照样读回。

## 6. 环境变量

见 [`.env.example`](.env.example)。核心闭环只需要本机端口配置，没有任何模型密钥项。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `POWERCONTEXT_URL` | `http://127.0.0.1:8777` | 记忆服务地址 |
| `POWERCONTEXT_TOKEN` | 空 | 服务端启用鉴权时填写 |
| `POWERCONTEXT_TIMEOUT` | `8` | SDK 请求超时（秒） |
| `RECALL_DATA_DIR` | `data/runtime` | 本地映射与答题流水 |
| `RECALL_LOG_DIR` | `var/log` | 操作留痕目录 |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8642` | 应用监听（仅回环） |
| `POWERCONTEXT_HOME` | `.powercontext` | PowerContext 数据目录（脚本内设置） |

## 7. 验证与验收

```bash
uv run pytest -q                          # 29 项：单元测试 + 真实记忆流程测试
uv run python scripts/verify_powercontext.py   # 逐个核实公开接口行为
uv run python scripts/e2e_loop.py --learner e2e-demo   # 端到端闭环（含断言）
uv run python scripts/verify_ui.py        # 真实 Chromium 走查页面（44 项断言 + 截图）
bash scripts/acceptance.sh                # 以上全部 + 重启 PowerContext 验证跨重启续接
```

展示站（`site/`，纯静态）——本机预览、发布产物、线上复核三种口径都用同一套 30 项断言：

```bash
python3 -m http.server 4173 --bind 127.0.0.1 --directory site
uv run python scripts/verify_site.py --url http://127.0.0.1:4173/ --out-dir evidence/site-draft      # 30 项页面断言 + 整页截图留档
uv run python scripts/verify_site.py --url http://127.0.0.1:4173/dist/ --out-dir evidence/site-dist  # 发布产物本身
uv run python scripts/verify_site.py --url https://recall-coach-f3jx2506222.qoder.zone/ --out-dir evidence/site-public  # 线上地址
```

**公开展示站：<https://recall-coach-f3jx2506222.qoder.zone/>**（2026-11-16 发布，任何人可访问；
发布与线上复核留档在 `evidence/publish-20261116/` 与 `evidence/site-public/`，逐项结论见 [`ACCEPTANCE.md`](ACCEPTANCE.md) 第 10 节）。
它是**纯静态介绍页**：不含接口调用、不承载在线作答、不含任何学习记录——发布出去的只有产品说明、
真实验收截图与真实验收数字。完整交互项目必须按上面的命令在本机运行。

## 8. 能力边界（不做什么）

- 不做动态 AI 出题：24 道题全部为内置原创题库；也不调用任何模型服务判分。
- 未配置推理模型时不做语义向量检索，也不假装「自动抽取记忆」——页面直接显示 `memory_extraction: false`。
- 不虚构用户数、使用次数、提效百分比或他人反馈；文档里每个数字都来自本次真实执行。
- 演示身份里不放真实个人信息；显示名只存本机，不写入 PowerContext（避免身份泄露与幂等冲突）。
- **不做多用户安全**：本作品没有登录体系，`learner_id` 只是本机用来挑选 Scope 的短标识，
  **不是凭证、也不构成访问控制**。任何能访问该服务端口的人，输入同一个 `learner_id` 就能读写同一份记忆；
  「不同身份互不干扰」指的是 **Scope 数据隔离**（已在验收中用暗号检索证明），不是租户隔离或权限隔离。
  因此应用只应在本机或受信任内网运行；面向公众的多用户场景必须先加认证，并把 Scope 绑定到认证主体
  （`idempotency_key` 由服务端从认证主体派生，绝不能用客户端提交的字符串直接决定），要求见 [`RUNBOOK.md`](RUNBOOK.md) 第 8 步。

## 9. 部署

- **单机演示**：本仓库即可，`scripts/start_powercontext.sh` + `scripts/start_app.sh`。
- **独立后端部署**：PowerContext 需要持久卷（`.powercontext/`）、回环或内网监听、可选 token；
  应用需要 `POWERCONTEXT_URL` 指向该服务。要求逐项见 [`RUNBOOK.md`](RUNBOOK.md) 第 8 步。
- **对外站点**：展示站**已发布** <https://recall-coach-f3jx2506222.qoder.zone/>（经提交人明确授权、任何人可访问）。
  发布出去的只有静态介绍层——产品说明、真实验收截图、真实验收数字与源码链接，
  **不含本机 API、不含学习记录、不含任何 `scp_*`/`art_*` 记忆标识**，所以公网访客不会看到「连不上记忆服务」的误导，
  也不会有人把自己的学习数据写到别人的 Scope 上。发布留痕与线上复核见 [`SUBMISSION.md`](SUBMISSION.md) 第 4/10 节、
  [`ACCEPTANCE.md`](ACCEPTANCE.md) 第 10 节。
- **可交互版本尚未发布，也不该按现状发布**：在线作答需要另外具备函数/后端能力承载 `app/`、把
  `POWERCONTEXT_URL` 指向公网可达且置于 TLS 之后的 PowerContext，并先补认证与按认证主体派生 Scope 幂等键
  （见第 8 节与 [`RUNBOOK.md`](RUNBOOK.md) 第 8 步）。
- **不要把它当多用户服务发布**：应用无认证、`learner_id` 非凭证（见第 8 节）。公开托管需要先加登录并把
  Scope 绑定到认证主体，同时把本地 SQLite（提交状态与写入账本）换成同一用户下的持久存储。
