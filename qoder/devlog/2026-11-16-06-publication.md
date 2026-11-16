# 2026-11-16 · 06 展示站公开发布与线上复核

提交人在上一轮明确回复「授权公开发布」，范围限定为 `qoder/site/` 静态展示站（产品说明、真实验收截图、
源码链接），受众设为任何人可访问；**不发布本机后端、不发布学习数据库**。这一篇记录从冻结产物到线上复核的
真实过程，包含两处只有真正发出去才会遇到的坑。

## 为什么先发 `site/dist/` 而不是 `site/`

Sites 的 `webDirectory` 要求是**专用的发布产物目录**，不接受源码根目录，也不允许 `.`。
展示站没有构建步骤，所以按静态项目的做法把最终文件复制一份出来当产物：

```bash
rm -rf site/dist && mkdir -p site/dist
cp site/index.html site/dist/ && cp -a site/assets site/dist/assets
```

10 个文件、4.7 MiB。检查过包内没有 `.env`、源码映射、密钥、数据库、符号链接；页面引用全是相对路径
（`assets/...`），换域名/换基址都不用改代码。然后**用同一套检查脚本验产物本身**，不是只验源码目录：

```bash
uv run python scripts/verify_site.py --url http://127.0.0.1:4173/dist/ --out-dir evidence/site-dist   # 30/30 通过
```

`prepare_site` 的真实输入里刻意没有 `functionDirectory` / `databaseAccess` / `secretNames`——授权范围就是
「静态前端」，不去申请任何后端能力，也不给平台一个可以写数据的入口。`projectRoot` 用 `qoder/site`，
描述文件因而也落在 `qoder/site/` 内，满足「站点资产只在本目录」。

## 流程：prepare → 读状态 → 发布一次 → 复核

`prepare_site` 第一次返回 `descriptorStatus: "pending"`（新建站点的显式意图走 `createNewSite: true`）。
这不是失败：草稿还没 ready 时描述文件不写。正确做法是保留同一个 `actionId` 与逐字相同的输入，
先 `get_publish_status` 读到 `preparation.state=ready` / `canPublish=true` / `blockers=[]`，
再**用同一 action 重发一次 prepare_site**——它从已冻结的 ZIP 恢复描述文件，不重新打包、不重新上传：

```
descriptorPath: site/.拾忆 · Recall Coach.qoder.site   descriptorStatus: written
```

（描述文件由工具生成，含 `deploymentId`、`artifactSha256`、`indexSha256`；不手写、不手改。）

`publish_site` 对该 action 只调一次，然后读同 action 状态：`published=true`、Operation `succeeded` 且
`committed=true`、`readiness=publication_completed`。受众变更走独立入口，`confirmPublic=true` 对应
「设为任何人可访问」这句明确授权，`accessRef` 逐字复用上一步 `get_site_access` 的返回值。
事后 `get_site` 显示 `access_mode: public`，`list_sites(scope=owned)` 返回 `total_size: 1`——
当前登录账号名下只有这一个站点，目标没有歧义。

## 线上复核：三个和「本机看着没问题」不一样的地方

1. **平台会往页面注水。** 线上 `index.html` 77861 字节 vs 本机 20819 字节，差的是平台注入的
   `data-qoder-watermark` 脚本（55 行，`</body>` 前）。所以比对不能只比 `sha256sum`，得看 `diff` 的
   hunk 结构：只有 1 处 hunk，正文其余逐行相同。顺手确认注入脚本里没有 `fetch`/`XMLHttpRequest`/
   `WebSocket`/`sendBeacon`——否则 `verify_site.py` 的「无接口调用」断言会因为它而红，而那是宿主行为不是我们的代码。
2. **本机经 HTTPS 代理出网，`curl` 的 `remote_ip` 显示 127.0.0.1。** 单看这一列会误判「我根本没出网」。
   真正的证据是响应头里的平台字段（`x-request-id`、OSS 相关头）与两次抓取逐字节一致。留痕里写明了这一点，
   免得后来人以为验证走了本地。另外明文 `http://` 在代理上直接 502，所以**不声称「HTTP 自动跳转 HTTPS」**，
   只声称已核实的 `https://` 入口。
3. **手机外层修复必须在线上再看一次。** `verify_site.py --url <线上地址>` 跑满 30 项断言，其中
   窄屏 390px 整页横向溢出 0px、`.table-wrap` 仍可横滑、提示文案在断点内可见——这轮的 CSS 改动确实发出去了。
   资源侧另做了一次按文件 SHA-256 比对：`site.css` / `site.js` / `favicon.svg` / 6 张截图全部与本机一致，
   不存在的路径返回 404。

## 一个查不到的字段：Qoder 账号 UID

按要求要「核对账号 UID」，但**这次可用的工具面读不到它**，逐项试过后如实记为待补充：

| 试过的地方 | 结果 |
| --- | --- |
| `get_context` | 只有 `local`（projectRoot、已验证的 sites）与 `runtime`（productId/region/environment/各 origin/host 能力） |
| `get_site` / `list_sites` | 有 `project_id`、`site_id`、`host`、`lifecycle`、`access_mode`，没有 owner/UID |
| 全部 32 个 operations | 没有读取本人账号 UID 的操作；`put_access_target` 能写入别人的 UID，但读不回自己的 |
| 本机非凭据状态 | `state.json` 只有 `lastLoginMethod: job_token`；`installation_id`、`umid-cache.json` 是设备标识，不是账号 UID；日志按 `uid|user_id|userId|accountId` 检索无命中 |

顺带一条纪律：`get_site`/`list_sites` 返回体里 `project.cover.url` 是带临时签名（含 STS 凭据、1 小时过期）的
对象存储地址，**没有抄进任何留痕文件**——那是宿主凭据，写进仓库就是把不该公开的东西公开了。

## 文档口径改了哪些

`SUBMISSION.md`：第 1 节站点行从「待补充」改为真实地址 + 受众 + 留痕（UID 行仍待补充，但补了「为什么核实不到」的
逐项说明）；第 3 节演示脚本加了「第 0 步先看公开展示站，作答仍须本机运行」；第 4 节从「以后要怎么发布」改写成
「已经怎么发布」（含平台注水、最终地址来自工具返回、更新流程）；第 7 节合规声明拆成「未推送」与「已按授权发布（仅静态）」；
新增第 10 节最终发布状态表。`ACCEPTANCE.md`：第 7 节在线可达性从待补充改为已完成并指向新增第 10 节，
第 8 节补上三种口径的 `verify_site.py` 命令，第 10 节写发布与线上复核表 + UID 那条负面结论。
`README.md`：顶部与第 7、9 节给出线上地址与「静态站不含在线演示」的边界；`RUNBOOK.md` 第 8 步把
「本次只到草稿」改成已发布事实与改版流程。历史日志一个字没动。

## 这一轮没做的事

`git add`/`commit`/`push` 全都没执行（仍等本人确认）；本机后端、PowerContext 数据目录、任何真实记忆条目
都没有上线；`site/` 源码没有因为发布成功而再改动（改动就得重新走一遍 prepare+publish，线上正文与本机产物
必须仍然逐字节对得上）。
