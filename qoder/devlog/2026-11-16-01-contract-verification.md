# 开发记录 01 · 先核实公开接口，再写第一行集成代码

日期：2026-11-16　范围：环境与契约核实

## 做了什么

1. 确认已安装版本与命令位置，避免凭记忆猜接口：

```
$ ~/.local/bin/powercontext --version     # PowerContext 1.5.1
$ ls ~/.local/share/uv/tools/powercontext/bin
```

2. 启动真实服务并留档 OpenAPI 契约（`reference/powercontext-1.5.1-openapi.json`）：

```
POWERCONTEXT_HOME=$PWD/.powercontext POWERCONTEXT_SERVER_HTTP_PORT=8777 \
  powercontext server run --host 127.0.0.1 --port 8777
curl -s http://127.0.0.1:8777/openapi.json > reference/powercontext-1.5.1-openapi.json
curl -s http://127.0.0.1:8777/health/ready
{"status":"ready","checks":{"runtime":"ready","database":"ready", ... "access_mode":"disabled"}}
```

3. 用 SDK 逐个方法核实本作品要用的接口，把返回值原样记下来（后来固化成 `scripts/verify_powercontext.py`）：

| 核实项 | 真实结果 |
| --- | --- |
| `GET /v1/capabilities` | `search_modes=["auto","fts"]`、`memory_extraction=false`、`context_versions=["powercontext.prepared-context.v1"]` |
| `POST /v1/scopes` | 同 `idempotency_key` 复用同一 `scp_…`，第二次调用 `scope_id` 相同 |
| `POST /v1/scopes/{id}`（PUT） | 带 `expected_version` 才能改，`version 1 → 2` |
| `POST /v1/memory/remember` | 返回 `atomic-memory/art_…@1`、`changed=true`、`state=active`、`state_version=0` |
| `GET …/artifacts/atomic-memory/{id}/state` | 与写入返回一致，用于「写入后回读校验」 |
| `POST /v1/atomic-memory/list` | `kind`/`states` 过滤有效，`next_cursor=None`（单页） |
| `POST /v1/memory/search` | `mode=text`，命中 `matched_by=["text"]`，分数形如 `0.01639` |
| `POST /v1/context/prepare` | `status=ready`、`content_bytes=684`、正文含 `BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1` |
| `POST /v1/sources/content` | `source_ref=content/probe-attempts`、`status=accepted` |
| `POST /v1/atomic-memory/lifecycle` | `active/v0 → forgotten/v1`，之后 `list(states=["active"])` 不再返回 |
| 超长文本 | `422 invalid_request`，`X-PowerContext-Request-Id=a304d1125aaeba24` |

## 由此定下的设计约束

- **没有嵌入模型 = 没有语义检索**。页面与文档一律写 `mode=text`，不写「AI 语义回忆」。
- **`memory_extraction=false`**：记忆必须由应用自己结构化写入（`kind` + JSON 正文），不能声称服务自动抽取。
- **旧接口 `POST /v1/memory/entries/revise|retire` 不可用**（实测被拒），遗忘只能走
  `POST /v1/atomic-memory/lifecycle` + `state_version` 乐观并发。
- **Scope 幂等键里不能放会变的字段**：标题带上显示名后，改名会触发
  `scope_idempotency_conflict: The Scope creation key identifies different parameters`。
  最终方案：`idempotency_key = recall-coach:v2:learner:<标识>`、`title = 拾忆-<标识>`、摘要固定，
  显示名只留本机（既避免幂等冲突，也避免把个人信息写进记忆服务）。
- 错误响应统一是 `{"error":{"code","message","details"}}` + `X-PowerContext-Request-Id`，
  封装层原样上抛，页面显示真实错误码。

## 结论

集成层只允许使用上表里核实过的方法与字段；任何未核实的能力（向量检索、自动抽取、动态出题）
在代码与文档里都不出现。
