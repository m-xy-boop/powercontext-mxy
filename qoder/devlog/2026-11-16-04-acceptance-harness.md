# 开发记录 04 · 验收脚手架与两处真实缺陷

日期：2026-11-16　范围：`scripts/`、`evidence/`

## 脚手架

| 脚本 | 作用 | 失败口径 |
| --- | --- | --- |
| `start_powercontext.sh` / `stop_powercontext.sh` | 真实服务启停（`POWERCONTEXT_HOME=.powercontext`，回环 8777），pidfile 管理 | 30 秒内 `/health/ready` 不就绪即失败 |
| `start_app.sh` / `stop_app.sh` | 应用启停（8642），启动前强制确认记忆服务可达 | 20 秒内 `/api/context` 不通即失败 |
| `verify_powercontext.py` | 直接调 SDK 核实 10 项公开接口行为 | 任一断言或异常即非零退出 |
| `e2e_loop.py` | 端到端闭环 13 步，含「故意答错→复习必须覆盖薄弱点→全部答对→掌握度上升」 | 每步 `check()` 失败立即退出 |
| `verify_ui.py` | 真实 Chromium 走查四页 + 下载导出 + 新上下文续接 | 断言失败或未捕获 JS 错误即失败 |
| `acceptance.sh` | 串起以上全部，中间做一次真实重启并比对记忆指纹 | `set -euo pipefail`，任一步失败整体失败 |

## 缺陷 1：重启后指纹「假不一致」

第 6 步比对重启前后指纹时报 `ranking_head 不一致`，但两个 JSON 文件肉眼完全相同。

原因：`before` 来自 `json.load`（嵌套为 list），`after` 是内存里的 tuple 列表，
`("idx-btree", 0.601, False) != ["idx-btree", 0.601, False]` 恒真。

修法：比对前统一 `json.loads(json.dumps(after))`。这是测试脚手架的错，不是记忆服务的问题——
`scope_id / 条数 / artifact_id 集合 / plan 标识 / 偏好` 五项当时就已一致。

## 缺陷 2：pytest 统计行消失

`02-pytest.log` 里只有点阵和告警摘要，没有 `20 passed` 行，汇总脚本取末行时抓到了 Docs 链接。

原因：`pyproject.toml` 里 `addopts = "-q"`，脚本又传 `-q`，`-q -q` 双静默会去掉结果统计行。

修法：移除 `addopts`，由调用方决定详细程度；汇总脚本改为正则提取 `N passed`。

## 其他两处

- 端到端脚本最初想用「服务端返回的判分结果」反推正确答案，绕了一圈；改为直接读内置题库
  `data/questions.json` 构造「故意答错/全部答对」两种输入，脚本立刻短了一截且断言更硬。
- `snap()` 的形参名 `title` 与调用处关键字 `title=` 冲突（`got multiple values for argument 'title'`），
  改名 `label`。

## 最终验收（真实执行）

```
=== 2. 自动化测试 ===                20 passed
=== 3. 端到端闭环 ===                13 步全部通过
=== 5. 停止并重启 PowerContext ===   真实 stop/start，数据目录 .powercontext/ 未动
=== 6. 指纹比对 ===                  7 项全部一致（scope_id、激活条数、全部条数、
                                     artifact_id 集合、最近 plan、优先级前三、偏好）
=== 7. 重启后继续闭环 ===            通过（全新应用实例 + 已重启的记忆服务）
=== 8. 真实浏览器页面验收 ===        38 项断言通过，未捕获 JS 错误 0 条
```

完整输出与截图留档在 `evidence/`，逐项结论写在 `ACCEPTANCE.md`。
