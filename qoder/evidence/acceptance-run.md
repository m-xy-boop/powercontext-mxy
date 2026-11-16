# 验收运行记录（本次真实执行）

- 执行时间：2026-11-16T14:03:07+08:00
- PowerContext：1.5.1，服务地址 http://127.0.0.1:8777，数据目录 .powercontext/
- Python：Python 3.12.16；依赖锁定：uv.lock
- pytest：20 passed, 1 warning in 11.50s
- 页面断言：38 项通过，未捕获 JS 错误 0 条
- 端到端闭环：13 个步骤全部通过；重启后续接 13 个步骤全部通过
- 跨重启记忆：Scope `scp_6yh4ywxypgyzjtj1atk2p953cc`，19 条条目（含 18 条激活）逐条指纹一致
- 模型 API 密钥使用：无（判分与选题在本地服务端完成，检索为 PowerContext 全文模式）

## 留档文件

- `00-start-app.log`（72 字节）
- `00-start-powercontext.log`（136 字节）
- `01-public-api-probe.log`（1943 字节）
- `02-pytest.log`（602 字节）
- `03-e2e-loop.json`（7276 字节）
- `03-e2e-loop.log`（5155 字节）
- `04-before-restart.json`（2000 字节）
- `04-before-restart.log`（267 字节）
- `05-restart.log`（405 字节）
- `06-after-restart.json`（2000 字节）
- `06-after-restart.log`（260 字节）
- `07-e2e-after-restart.json`（6384 字节）
- `07-e2e-after-restart.log`（4572 字节）
- `08-ui-verify.log`（2842 字节）
- `acceptance-exit.txt`（7 字节）
- `acceptance-run.log`（16869 字节）
- `acceptance-run.md`（2211 字节）
- `qoder-development-02.stderr.log`（0 字节）
- `qoder-development-02.stream.jsonl`（12089 字节）
- `qoder-development-03.stderr.log`（0 字节）
- `qoder-development-03.stream.jsonl`（2063563 字节）
- `qoder-development.stderr.log`（0 字节）
- `qoder-development.stream.jsonl`（371721 字节）
- `screenshots/01-home.png`（446179 字节）
- `screenshots/02-diagnose-answered.png`（714144 字节）
- `screenshots/03-diagnose-result.png`（1340210 字节）
- `screenshots/04-diagnose-review.png`（574970 字节）
- `screenshots/05-memory.png`（1086689 字节）
- `screenshots/06-verify.png`（4065600 字节）
- `screenshots/07-cross-session.png`（620043 字节）
- `screenshots/export-process-record.md`（6687 字节）
- `screenshots/ui-report.json`（2815 字节）

复现命令：`scripts/acceptance.sh`（会启动真实服务、跑全部校验并重启 PowerContext 验证跨重启续接）。
