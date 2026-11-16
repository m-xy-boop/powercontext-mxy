# 环境准备记录

## 已核对事项

- 团队远端：`git@github.com:m-xy-boop/powercontext-mxy.git`。
- 通过 `git ls-remote` 核对远端 `2026-competition-ecosystem` 指向 `d647275c308e09b7b623f322a26f5c4e85734fb0`，与当前工作分支基础一致。
- 独立工作目录：`/home/xy/powercontext-qoder`；分支 `qoder-recall-coach`。
- Qoder 中国区官方 CLI：`/home/xy/.local/bin/qoderclicn`，版本 `1.1.89`。安装使用 `https://qoder.cn/install` 提供的脚本，安装器已校验下载文件 SHA-256。
- Python：系统 `python3` 为 3.10；使用 `/home/xy/.local/bin/uv` 管理的 Python 3.12 运行项目。
- Node.js 和 Docker 不在当前 PATH 中；是否需要安装以实现和部署方式为准。

## 真实开发的启动方式

先完成中国区账号登录：

```bash
/home/xy/.local/bin/qoderclicn login
```

然后在作品目录中启动 Qoder：

```bash
cd /home/xy/powercontext-qoder/qoder
/home/xy/.local/bin/qoderclicn
```

将以下要求发送给 Qoder：

> 阅读 PROJECT_BRIEF.md 和 QODER_TASK.md，先核实 PowerContext 接口，然后完成主要项目实现和真实验收。不要推送或发布，先交付可本机复现的作品。保留此次真实 Qoder 会话，供比赛核验。

## 后续验收重点

1. 从空数据开始，用固定样例提交错题，验证 PowerContext 写入成功。
2. 关闭会话并重启应用，以相同身份获取复习安排，核对先前薄弱点及实际读取的记忆。
3. 同一知识点答对后再次复习，核对掌握变化和后续选题变化。
4. 切换身份，核对记忆隔离；服务不可用时核对明确错误提示。
5. 按运行说明从头复现，输出真实测试结果与操作日志。
6. 发布 Qoder Sites 展示站并设置公开；在未登录状态核对页面与体验入口。
7. 整理真实关键对话截图或录屏、Qoder UID；最后填写 commit、分支和站点链接。

上述为准备记录，不代表项目已经实现、测试通过或网站已发布。账号授权链接与令牌不纳入提交材料。
