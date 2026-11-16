# RUNBOOK · 拾忆 · Recall Coach 操作手册

面向评委与使用者：从零开始把作品跑起来，并按赛题要求逐条验证「记忆真的跨会话、跨重启生效」。
所有命令都在本仓库目录内执行，不读写其他赛题目录。

```bash
cd /home/xy/powercontext-qoder/qoder
```

---

## 第 1 步 · 环境检查

```bash
~/.local/bin/uv --version
python3 --version                     # 需要 3.12（.python-version 已固定）
powercontext --version                # 期望 1.5.1；命令位于 ~/.local/bin/powercontext
```

如果 `powercontext` 不存在：

```bash
~/.local/bin/uv tool install "powercontext[cli,server]==1.5.1" \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

安装应用依赖（依赖已锁定在 `uv.lock`）：

```bash
~/.local/bin/uv sync --group dev
```

## 第 2 步 · 启动真实 PowerContext 并核实公开接口

```bash
scripts/start_powercontext.sh          # 监听 127.0.0.1:8777，数据目录 .powercontext/
uv run python scripts/verify_powercontext.py
```

`verify_powercontext.py` 直接用公开 Client SDK 逐项核实（输出留档在 `evidence/01-public-api-probe.log`）：

- `GET /v1/capabilities`：`search_modes = ["auto","fts"]`、`memory_extraction = false`（未配置推理模型时如实为 false）
- `POST /v1/scopes`：同一 `idempotency_key` 复用同一 `scp_…`
- `POST /v1/memory/remember` + `GET …/artifacts/atomic-memory/{id}/state`：写入后回读一致
- `POST /v1/atomic-memory/list`：按 kind/states 分页读回，含 `state_version`
- `POST /v1/memory/search`：`mode = text`（全文检索，无嵌入模型时不会假装语义检索）
- `POST /v1/context/prepare`：返回 `powercontext.prepared-context.v1` 与 `content_bytes`
- `POST /v1/sources/content`：证据留存，返回 `content/<source_id>`
- `POST /v1/atomic-memory/lifecycle`：`active/v0 → forgotten/v1`
- 超长文本：服务端 `422 invalid_request`，客户端保留真实 `X-PowerContext-Request-Id`

## 第 3 步 · 启动拾忆应用

```bash
scripts/start_app.sh                   # http://127.0.0.1:8642
```

四个页面：`/` 概览、`/diagnose` 诊断与复习、`/memory` 学习记忆、`/verify` 验证说明。
顶栏右侧显示真实连接结果；服务不可达时顶栏变红并显示真实错误码，页面不会显示任何「已保存」。

## 第 4 步 · 手工走一遍核心闭环（约 3 分钟）

1. `/diagnose` 输入身份标识 `teacher-wang` + 显示名「王老师」→「进入」。
   页面提示：`身份就绪：Scope scp_…（标题 拾忆-teacher-wang）`。
2. 首屏为 **基础诊断**：8 个知识点各 1 题。每题下方写着选题依据（「PowerContext 中没有该知识点的掌握记录，先补齐诊断」）。
3. 答完提交 → 结果区显示判分、每题解析、以及本次写入的每条记忆标识（`atomic-memory/art_…@1`，
   校验方式列写 `get_atomic_memory_state`）。
4. 点「按新记忆再来一轮」→ 模式变为 **记忆驱动复习**，题量按偏好（默认 5），
   答错的知识点排在前面，题目下方出现「依据记忆：atomic-memory/art_…@1」。
5. `/memory`：清单里能看到 `weakness / progress / preference / plan` 四类真实条目；
   检索框输入「薄弱点」→ 结果显示 `mode=text` 与命中条目的真实分数；
   「生成」PreparedContext → 显示 `powercontext.prepared-context.v1` 与字节数；
   改题量为 3、勾选侧重知识点 → 保存，回执给出新记忆标识；
   「遗忘此条」→ 条目状态变 `forgotten`，切到「全部状态」仍能看到历史。
6. 「导出 Markdown 记录」→ 文件里每条记忆都带 `family/artifact_id@revision` 与答题流水。
7. 关掉浏览器标签页，重新打开 `/diagnose`，用同一个身份进入：直接进入复习模式，
   薄弱点顺序与刚才一致（这一步证明记忆不属于浏览器，而属于 PowerContext）。
8. 回到刚才那一轮的结果页：提交按钮已变为「本轮已判分并写入记忆」并保持禁用。
   即使绕过页面直接对同一 `session_id` 再次 `POST submit`，服务端也会返回当时的判分结果并标注
   `"repeated_submit": true`，`/memory` 的条数与导出流水的行数都不变（幂等保护在服务端，且已持久化）。

## 第 5 步 · 服务重启后仍然续接（赛题硬性要求）

```bash
scripts/stop_app.sh && scripts/stop_powercontext.sh
scripts/start_powercontext.sh && scripts/start_app.sh
uv run python scripts/e2e_loop.py --learner teacher-wang --existing-learner \
  --json-out evidence/e2e-after-restart.json
```

脚本会断言：同一 `scope_id` 读回记忆、模式为 `review`、薄弱知识点被覆盖、每条依据都引用真实记忆标识。
`scripts/acceptance.sh` 把这一步做成「重启前后记忆逐条指纹比对」：`scope_id`、激活条数、全部条数、
`artifact_id` 集合、最近一次 `plan` 记忆、优先级前三、复习偏好，七项全部一致才算通过。

只想看指纹，不跑闭环：

```bash
curl -s http://127.0.0.1:8642/api/learners/teacher-wang | python3 -m json.tool | head -40
```

## 第 6 步 · 身份隔离与撤回验证

```bash
uv run python scripts/e2e_loop.py --learner alice-isolate --json-out evidence/e2e-alice.json
uv run python scripts/e2e_loop.py --learner bob-isolate   --json-out evidence/e2e-bob.json
```

两个身份得到两个不同 `scp_…`。在 `/memory` 页面切换身份，清单、检索、偏好、统计全部随之改变；
Alice 的偏好备注里写一个只属于她的关键词，Bob 页面检索该关键词返回真实空结果。
「清空该身份的全部记忆」会逐条走 lifecycle 接口，页面显示每条的 `state_version` 递增结果。

> 这一步证明的是 **Scope 层面的数据隔离**（不同标识落在不同 Scope，互相读不到），
> 不是权限隔离：本机任何人都能输入同一个标识。公开多用户部署的前提见第 8 步第 7 项。

## 第 7 步 · 自动化验收

```bash
uv run pytest -q -s                    # 离线单元测试 + 针对真实记忆服务的流程测试
uv run python scripts/e2e_loop.py --learner e2e-demo
uv run python scripts/verify_ui.py     # 真实 Chromium：38 项断言 + 截图 + 导出下载
bash scripts/acceptance.sh             # 以上全部 + 真实重启 + 汇总 evidence/acceptance-run.md
```

`verify_ui.py` 需要 Chromium：优先复用本机 Playwright 缓存里的浏览器；
若没有，执行 `uv run playwright install chromium`，或用 `RECALL_CHROMIUM=/path/to/chrome` 指定。

## 第 8 步 · 独立后端部署要点

拾忆只依赖 PowerContext 的公开 HTTP 契约，因此记忆服务可以独立部署：

1. **持久卷**：`POWERCONTEXT_HOME` 指向的目录必须持久（本仓库默认 `.powercontext/`，内含
   `powercontext.db` 与 WAL/SHM、`*.cursor-key`、迁移状态目录）。换机器时带走这个目录即可。
2. **监听与传输**：`powercontext server run --host <地址> --port <端口>`。默认回环 HTTP 可直接用；
   跨主机部署必须放在 TLS 反向代理之后，并在客户端设置 `POWERCONTEXT_URL` 为 `https://…`
   （CLI 对不安全远程 HTTP 需要显式 `--allow-insecure-remote-http`，本作品不鼓励公网明文）。
3. **鉴权**：服务端启用 token 时，应用侧设置 `POWERCONTEXT_TOKEN`；封装层会把 401/403 的真实
   `code` 与 `request_id` 原样显示在页面上。
4. **配置来源**：`powercontext server run` 支持 `--env-file` 加载服务端设置，`--role` 可拆分 API 与后台进程。
   本作品只用到默认能力：全文检索（无需嵌入模型）、原子记忆、PreparedContext、lifecycle、content source。
5. **存储后端**：默认 SQLite；如需换成 OceanBase/MySQL 等后端，按 PowerContext 官方 Server 配置文档调整，
   拾忆侧无需改代码（它只走公开接口）。
6. **应用侧**：`uv run uvicorn app.main:app --host 0.0.0.0 --port 8642` 配合进程管理即可；
   本地 SQLite（`data/runtime/`）只存身份→Scope 映射、会话与答题流水（含提交状态与记忆写入账本），可安全重建。
7. **信任边界（必须先想清楚）**：本作品**没有登录与授权**，`learner_id` 只是「选哪个 Scope」的短标识，
   不是凭证。把应用监听在 `0.0.0.0` 或暴露到公网，等于把「任何人只要猜到标识就能读写同一份记忆」
   摆出来——这不是多用户安全，文档与页面也从不这样声称。安全的做法按优先级：
   - 只监听 `127.0.0.1`（本仓库默认），或放在需要 VPN/内网ACL 的位置；
   - 反向代理层做认证后，把认证主体（用户 ID / OIDC `sub`）映射成 `learner_id`，**不接受客户端自由填写**；
   - Scope 幂等键由服务端从认证主体派生（例如 `recall-coach:v2:learner:<auth0-sub>`），
     并让 `LocalStore` 的用户表与会话表按认证主体隔离；
   - 需要真正租户隔离时，为每个认证主体使用独立 PowerContext Scope 并配合服务端鉴权/token 分域。
8. **不要把展示站当后端**：`site/` 里的静态介绍页不含学习记录，也不代理本机 API。
   本机预览与页面检查（与 `127.0.0.1:8642` 的应用互不影响，可并行运行）：

   ```bash
   python3 -m http.server 4173 --bind 127.0.0.1 --directory site
   uv run python scripts/verify_site.py --url http://127.0.0.1:4173/ --out-dir evidence/site-draft
   uv run python scripts/verify_site.py --url http://127.0.0.1:4173/dist/ --out-dir evidence/site-dist  # 发布产物
   uv run python scripts/verify_site.py --url https://recall-coach-f3jx2506222.qoder.zone/ --out-dir evidence/site-public  # 线上
   ```

   发布路径与前置条件见 `SUBMISSION.md` 第 4 节。展示站已于 2026-11-16 19:03–19:05 按提交人明确授权发布
   （`site/dist/` 静态产物，受众「任何人可访问」，未发布后端与学习数据库），留痕见
   `evidence/publish-20261116/` 与 `ACCEPTANCE.md` 第 10 节。改版流程：改 `site/` 源码 → 重新复制到
   `site/dist/` → 跑上面的本机检查 → 对同一 `projectId` 用**新的 `actionId`** 重新 `prepare_site` + `publish_site`。

## 第 9 步 · 停止与重置

```bash
scripts/stop_app.sh
scripts/stop_powercontext.sh
```

- 只清演示记忆：在 `/memory` 页「清空该身份的全部记忆」（条目置为 `forgotten`，历史仍可核验）。
- 重置本机映射：删除 `data/runtime/recall-coach.db`。用同一个身份标识重新进入时，
  `idempotency_key = recall-coach:v2:learner:<标识>` 会复用同一个 Scope，**旧记忆照样读回**。
- 彻底清空服务：停止服务后删除 `.powercontext/`（不可恢复，仅在需要全新演示时执行）。

## 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 顶栏红色 `powercontext_unavailable` | 服务未启动或端口不通 | `scripts/start_powercontext.sh`；`curl http://127.0.0.1:8777/health/ready` |
| 页面提示 `503`，且没有任何「已保存」 | 设计如此：不可用时绝不写假记忆 | 恢复服务后重试同一操作 |
| `scope_idempotency_conflict` | 同一幂等键被改了标题/摘要 | 封装层会自动 `update_scope` 对齐；若手工改过，用新标识重跑 |
| 检索只有 `mode=text` | 未配置嵌入模型 | 正常现象；页面已如实标注，需要语义检索时按官方文档配置推理服务 |
| 中文检索命中偏少 | 全文检索按词匹配 | 用「薄弱点」「索引结构」等完整词组，或先看 `/memory` 清单 |
| 端口被占用 | 8642/8777 已有进程 | `APP_PORT=8650 scripts/start_app.sh`，同时设 `POWERCONTEXT_PORT` |
| 浏览器验收启动失败 | 找不到 Chromium | `uv run playwright install chromium` 或设 `RECALL_CHROMIUM` |
