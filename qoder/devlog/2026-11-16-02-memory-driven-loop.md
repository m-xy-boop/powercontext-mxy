# 开发记录 02 · 让记忆真的决定选题

日期：2026-11-16　范围：`app/mastery.py`、`app/planner.py`、`app/main.py`、`tests/`

## 关键决定

1. **一个学习身份 = 一个 PowerContext Scope**，四类原子记忆：
   `weakness`（薄弱点）、`progress`（掌握变化）、`preference`（复习偏好）、`plan`（上次安排）。
   正文统一 JSON，schema `recall-coach.memory.v1`，字段含 `app/v/at/seq` —— `at+seq` 保证折叠顺序稳定
   （同一秒内写入多条时，只按时间排序会打乱知识点之间的先后）。
2. **本地 SQLite 不存学习记忆**，只存身份→Scope 映射、会话、答题流水、轮转计数器。
   这样「换会话仍记得薄弱点」这件事只能靠 PowerContext 成立，测试也就无法被本地缓存蒙过去。
3. **掌握度用 EWMA**（α=0.45，边界 0.02–0.98，阈值 0.55 判薄弱），连错次数单独记录：
   单靠均值会掩盖「最近连续错」这种强信号。
4. **优先级分必须可解释**：`prioritize()` 每个知识点返回 `factors`（人类可读依据 + 权重），
   页面直接展示，同时返回 `evidence`（该知识点的所有记忆标识）。选题不能只给结论。

## 踩到并修掉的问题

| 问题 | 现象 | 修法 |
| --- | --- | --- |
| 诊断题量只有 5 | 首次进入默认偏好 5 题，覆盖不满 8 个知识点，基线残缺 | 诊断分支 `limit = max(limit, len(topics))`，强制覆盖全部知识点 |
| 复习题全挤在一个知识点 | `test_preference_changes_plan` 失败：`['tx-acid','tx-acid','tx-acid']` | 候选知识点改为「薄弱/未测 + 偏好侧重 + 高分补齐」，题量按 `per_topic` 轮转分配 |
| 重复出上次同样的题 | 上一轮 `plan` 记忆里的题目又被选中 | 从记忆里取 `last_plan.question_ids` 作为 `avoid` 集；候选题不足时明确标注「按难度轮换复用」并写进依据 |
| Scope 幂等冲突 | 真实报错 `scope_idempotency_conflict` | 见记录 01：稳定 slug + `update_scope` 自愈 |
| 服务不可达时页面仍显示「已保存」 | 早期降级写法会掩盖故障 | 封装层一律抛异常；`MemoryBackendError` → `503/502` + `notice`，前端不写任何成功状态 |
| `/health/ready` 探测抛裸异常 | `httpx.ConnectError` 逃出处理器 | `probe()` 捕获 `HTTPError` 后转 `MemoryUnavailable`，带上真实地址 |
| 导出 Markdown 缺章节 | 测试断言 `## 学习记忆` 失败 | 补 `_render_markdown()`：掌握表、偏好、最近安排、记忆清单（含 `family/artifact_id@revision`）、答题流水 |

## 测试

`tests/test_units.py`（9 项，离线）：题库结构与答案不外泄、折叠忽略 `forgotten`、EWMA 数值、
优先级排序、诊断→复习切换、按上次安排轮转题目。

`tests/test_live_flows.py`（11 项，打真实服务）：

- 诊断后新会话按记忆复习；**重启应用后仍能读回同一 Scope**（新建 `TestClient(create_app(...))`）
- 身份隔离：给 Alice 的偏好备注写只有她有的暗号，Bob 检索该暗号必须 0 命中，两人 `artifact_id` 集合不相交
- 偏好改变安排；检索 `mode=="text"`；PreparedContext 含真实标记头
- 一键清空后 `active` 归零；导出含真实引用
- 服务不可达（端口 60555）→ `503 powercontext_unavailable`、`operation=="create_scope"`、不留假状态
- 超长文本 → `422`，响应里带真实 `request_id`
- 操作留痕文件里确有真实调用记录

```
$ uv run pytest -q
20 passed
```
