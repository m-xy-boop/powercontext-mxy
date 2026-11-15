# 提交信息

作品：回声当铺——记忆有价，后果自负。

比赛技术组合：Dify + PowerContext。比赛基线：`oceanbase/powercontext` 的 `2026-competition-ecosystem`，提交 `d647275c308e09b7b623f322a26f5c4e85734fb0`。开发分支：`dify-memory-theater`。所有作品材料在 `dify/`。

评审从 `README.md` 开始，依照 `RUNBOOK.md` 启动；导入的唯一 DSL 是 `dsl/echo-pawnshop.yml`。验证玩法请依次执行 `ACCEPTANCE.md` 的主样例与对照样例；也可以运行 `scripts/verify.py` 自动完成 17 项真实 Dify 验收。

证据包括实际 DSL 导入成功记录、端到端结果、PowerContext 读写与模型输出日志、104 条 Dify 节点执行记录、重启恢复结果、桌面和手机截图。规则回归测试 4 项通过。没有把截图当成可运行应用的替代品。

本地源码部署无需模型账号、API Key、Docker 或管理员权限。首次需要下载依赖与开源模型；本地网页入口为 `http://127.0.0.1:18080`。公网链接、Dify Cloud 和其他平台未在本提交声明为已验证部署。

Git commit 与分支必须实际推送到参赛者的比赛私有仓库后，才能将对应分支/commit hash 填到测评平台。本地 commit 或压缩包本身不代表测评平台已经提交成功。本作品未自动提交比赛平台。

若需从 Git bundle 恢复完整比赛基线与作品提交，可执行：

```bash
git clone dify-submission.bundle dify-entry
cd dify-entry
git checkout dify-memory-theater
```

再将该分支推送至参赛者有权限的比赛仓库。请保留它相对比赛基线的 ancestry，不要把文件误提交到其他赛题分支。
