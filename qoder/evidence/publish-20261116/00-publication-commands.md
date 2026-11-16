# 发布留痕 · Qoder Sites（2026-11-16 19:03–19:05，本机真实执行）

授权口径：提交人明确回复「授权公开发布」，范围为 `qoder/site/` 静态展示站（产品说明、真实验收截图、源码链接），
设为任何人可访问；**不发布本机后端、不发布学习数据库、不发布任何记忆数据**。

执行方式：Qoder Sites 主机命令（CLI 宿主，`filePreview: false`），每条命令单独一次前台 shell 调用：

```
/home/xy/.qoder-cn/bin/qoderclicn/qoderclicn-1.1.89 sites --session "a15d71ee-88ed-45ed-9509-3788f0f5e8ac" <operation> --input '<JSON>'
```

凭据由宿主注入，全过程没有读取、传递或记录任何 token。

---

## 0. 打包前的输出目录检查

发布的是**专用输出目录** `site/dist/`（由 `site/index.html` + `site/assets/` 逐文件复制而来，非源码根目录）：

```
site/dist/index.html
site/dist/assets/favicon.svg
site/dist/assets/screenshots/01-home.png
site/dist/assets/screenshots/02-diagnose-answered.png
site/dist/assets/screenshots/03-diagnose-result.png
site/dist/assets/screenshots/04-diagno­se-review.png
site/dist/assets/screenshots/05-memory.png
site/dist/assets/screenshots/07-cross-session.png
site/dist/assets/site.css
site/dist/assets/site.js
```

共 10 个文件、4.7 MiB（远低于 50 MiB 单产物上限）；包内**没有** `.env`、源码映射、密钥、数据库文件、
符号链接或本机预览服务地址的可点击链接。页面引用的资源全部是相对路径（`assets/...`），与部署基址无关。

该输出目录先用真实 Chromium 独立验收过：`evidence/site-dist/`（30 项断言，退出码 0）。

## 1. prepare_site（新建站点，静态，无函数/数据库/Secret）

```
sites prepare_site --input '{"actionId":"2b86f084-8d45-4083-989f-9326e0ca184d","projectRoot":"/home/xy/powercontext-qoder/qoder/site","webDirectory":"dist","createNewSite":true,"displayName":"拾忆 · Recall Coach","slug":"recall-coach","spa":false}'
```

真实返回：

```json
{"schemaVersion":1,"ok":true,"result":{"actionId":"2b86f084-8d45-4083-989f-9326e0ca184d","projectId":"01a1e406-a4f3-71d1-ba18-5fc7cc07a149","siteId":"01a1e406-a4f4-70b6-8743-fbba65098e5a","verificationOperationId":"01a1e406-a8d9-740d-aa59-cb0ea58250ff","descriptorStatus":"pending","descriptorHint":"The draft is not ready. Preserve this action and inputs; after readiness resolves, retry prepare_site with the same action and exact inputs to restore the entry from the frozen ZIP without repackaging or uploading."}}
```

`createNewSite: true` 对应提交人「发布这个静态站，站点名 recall-coach」的明确新建意图；
输入里没有 `functionDirectory`、`databaseAccess`、`secretNames`——即后端能力与数据库一律未申请。

## 2. get_publish_status（草稿就绪，未发布）

```
sites get_publish_status --input '{"actionId":"2b86f084-8d45-4083-989f-9326e0ca184d"}'
```

关键字段（真实返回）：`artifactReady: true`、`dependenciesReady: true`、`blockers: []`、
`preparation.state: "ready"`、`canPublish: true`、`published: false`、`access_mode: "private"`、
`host: "recall-coach-f3jx2506222.qoder.zone"`、`subdomain_prefix: "recall-coach"`、
`operation.state: "succeeded"`、`readiness: "draft_checks"`。

## 3. 同 action 重试 prepare_site（只为落描述文件，不重新打包上传）

```
sites prepare_site --input '<与第 1 步逐字相同的输入>'
```

```json
{..."descriptorPath":"/home/xy/powercontext-qoder/qoder/site/.拾忆 · Recall Coach.qoder.site","descriptorStatus":"written"}
```

描述文件由工具生成（含 `deploymentId`、`artifactSha256`、`indexSha256`），未手写、未修改。

## 4. publish_site（该 action 只调用一次）

```
sites publish_site --input '{"actionId":"2b86f084-8d45-4083-989f-9326e0ca184d"}'
```

```json
{"schemaVersion":1,"ok":true,"result":{"actionId":"2b86f084-8d45-4083-989f-9326e0ca184d","projectId":"01a1e406-a4f3-71d1-ba18-5fc7cc07a149","siteId":"01a1e406-a4f4-70b6-8743-fbba65098e5a","verificationOperationId":"01a1e406-a8d9-740d-aa59-cb0ea58250ff","publishOperationId":"01a1e407-06ec-7a43-be98-ea0f7d1b51ac"}}
```

## 5. get_publish_status（发布已提交）

同一 action 的读取结果（真实）：

```json
{"actionId":"2b86f084-8d45-4083-989f-9326e0ca184d","publishOperationId":"01a1e407-06ec-7a43-be98-ea0f7d1b51ac","artifactReady":true,"canPublish":false,"published":true,"blockers":[],"site":{"hasPublishedContent":true,"lifecycle":"active","governance_status":"normal","access_mode":"private","host":"recall-coach-f3jx2506222.qoder.zone","auth_integration_status":"ready","url":"https://recall-coach-f3jx2506222.qoder.zone"},"preparation":{"state":"ready"},"operation":{"operation_id":"01a1e407-06ec-7a43-be98-ea0f7d1b51ac","state":"succeeded","committed":true,"last_error_code":""},"readiness":"publication_completed"}
```

`published: true` 且 `operation.committed: true` ⇒ 发布成立（不是 `canPublish`、不是上传受理）。

## 6. 受众改为「任何人可访问」

```
sites get_site_access --input '{"siteId":"01a1e406-a4f4-70b6-8743-fbba65098e5a"}'
```

返回 `policy.mode: "private"`、`available_modes` 含 `public`（`requires_confirmation: true`）、
`rule_count: "0"`、`accessRef: "sr_MgDEqRg3…"`（本次读取的不透明引用，下面逐字复用）。

```
sites update_access_policy --input '{"siteId":"01a1e406-a4f4-70b6-8743-fbba65098e5a","requestId":"98eb1eb5-115c-435c-ab9f-e038c5949589","accessRef":"sr_MgDEqRg3…（同上，逐字复用）","mode":"public","confirmPublic":true}'
```

真实返回：`{"policy":{"mode":"public","include_project_members":false,"rule_count":"0","auth_integration_status":"not_required"}}`

`confirmPublic: true` 对应提交人「设为任何人可访问」的明确授权；这是公开站点唯一的受众变更入口，
没有邀请任何访客（`rule_count` 仍为 0）。

## 7. 事后读取站点事实（公开 + 已发布）

```
sites get_site --input '{"siteId":"01a1e406-a4f4-70b6-8743-fbba65098e5a","include":[]}'
sites list_sites
```

`get_site`：`access_mode: "public"`、`hasPublishedContent: true`、`lifecycle: "active"`、
`governance_status: "normal"`、`auth_integration_status: "not_required"`、
`host: "recall-coach-f3jx2506222.qoder.zone"`、`project.display_name: "拾忆 · Recall Coach"`、`slug: "recall-coach"`、
`created_at: 2026-11-16T11:03:23Z`、`updated_at: 2026-11-16T11:03:48Z`（UTC）。

`list_sites`（`scope: "owned"`）：`total_size: 1`，即当前登录账号名下**只有这一个站点**，目标不存在歧义。

> 返回体里 `project.cover.url` 是带临时签名（含 STS 凭据、1 小时过期）的对象存储地址，**未抄录进本留痕**。

## 8. 账号 UID 核对结果（如实）

逐个尝试了宿主暴露的只读入口，均不返回提交人账号 UID：

| 尝试 | 结果 |
| --- | --- |
| `get_context` | 只有 `local`（projectRoot/sites）与 `runtime`（productId `qoder-cn`、region `cn`、environment `prod`、`sitesOpenApiOrigin`、`cloudAgents` 源、`host.mode: cli`） |
| `get_site` / `list_sites` | 只有 `project_id`、`site_id`、`host`、状态与受众；无 owner/UID 字段 |
| 全部 32 个 operations 清单 | 没有 `whoami`/账号信息类操作；`put_access_target` 能**写入**他人 UID，但没有任何操作能读回本人 UID |
| 本机非凭据状态文件 | `~/.qoder-cn/state.json` 只有 `lastLoginMethod: "job_token"`；`installation_id`、`umid-cache.json` 属设备标识，非 Qoder 账号 UID；日志中按 `uid|user_id|userId|accountId` 检索无命中 |

结论：**UID 无法由本次可用的工具面核实**，`SUBMISSION.md` 第 1 节该项保持「待补充」，由提交人在
Qoder 账号页自行填写；本留痕不做任何推测值。可核实的归属证据是：`list_sites(scope=owned)` 返回的
唯一一个站点即本次发布的 `site_id 01a1e406-a4f4-70b6-8743-fbba65098e5a`，在当前登录会话内创建并发布。

## 9. 公开可达性验证（与发布回执相互独立）

见 `10-public-http.txt`、`11-public-assets.txt`、`site-public-index.html`、`site-public-headers.txt`，
以及浏览器层 `evidence/site-public/`。

- `curl -L https://recall-coach-f3jx2506222.qoder.zone/`（无 Cookie、无自定义头）→ `HTTP/2 200`，
  `content-type: text/html; charset=utf-8`，`x-request-id: 053661e5-bfc1-434e-a759-497c581`，
  0 次重定向，TLS 校验通过（`ssl_verify_result=0`）。
- 首页正文 77861 字节 = 本机 `site/dist/index.html`（20819 字节）+ 平台注入的
  `data-qoder-watermark` 脚本（55 行）；**除该注入脚本外逐行相同**（`diff` 只有一个 hunk，位于 `</body>`）。
  注入脚本本身不含 `fetch` / `XMLHttpRequest` / `WebSocket` / `sendBeacon`，不发起额外请求。
- 四个静态资源与两张截图的 SHA-256 与本机 `site/dist/` **完全一致**（`site.css`、`site.js`、
  `favicon.svg`、`01-home.png`、`07-cross-session.png`，见 `11-public-assets.txt`）。
- 真实 Chromium 直接跑在线上地址：`scripts/verify_site.py --url https://recall-coach-f3jx2506222.qoder.zone/ --out-dir evidence/site-public`
  → **30 项断言全部通过**，其中窄屏 390px 整页横向溢出 0px（手机外层修复在线上生效）。

## 10. 未做的事

- 未 `git add` / `commit` / `push`：仓库仍是原来状态，远端地址字段继续标为待补充。
- 未发布后端：`app/`、`web/`、本机 FastAPI、`.powercontext/` 数据目录都不在包内，也不在站点能力里
  （无 Function、无数据库、无 Storage 写入、无 Secret）。
- 未上传任何学习记忆、`learner_id` 对应的 Scope、真实 `scp_*`/`art_*` 标识；页面文字里也不含它们（浏览器断言已核）。
- 未改动历史留档：`evidence/`（首轮）、`evidence/rerun-20261116-fix*/`、`evidence/site-draft/`、
  `evidence/site-dist/` 中已生成的日志与截图均保持原样，本目录为新增。
