# Qoder 开发任务

请阅读同目录 PROJECT_BRIEF.md，承担本比赛作品的主要实现、测试与调试。所有文件放在 qoder/ 内。先核实 PowerContext 指定版本的公开接口和持久化部署方式，再实现完整闭环，不要猜测 API。

请先给出简短设计，然后直接实现。建议采用 Python 3.12 + FastAPI + HTML/CSS/JavaScript，减少构建依赖；如 Sites 部署能力有明确要求，可调整展示站实现。界面采用精致清晰的中文学习工作台，内容表达真实能力。必须保留 PowerContext 的真实写入、读取、跨会话使用和选题依据。

完成 README.md、RUNBOOK.md、ACCEPTANCE.md、SUBMISSION.md 和开发记录目录。提交材料中缺失的账号 UID、截图、最终站点 URL 要明确标为待补充。禁止捏造 Qoder 记录、性能结果和用户反馈。不要读写其他赛题工作目录、不要提交密钥、不要自行推送或发布网站。开发到可本机运行后，执行真实验收并修复问题。最后说明如何通过当前 Qoder Sites 工具发布展示网站，以及独立后端的部署要求。

当前机器有 uv 和 uv 管理的 Python 3.12，系统 python3 为 3.10，请使用 /home/xy/.local/bin/uv。当前目录基于比赛专用生态分支，不包含 PowerContext SDK 源码；应使用锁定版本的公开软件包。核心流程不依赖另外购买模型 API。
