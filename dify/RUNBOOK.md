# 运行说明

## 1. 环境和组件版本

| 组件 | 实测版本 / 用途 |
|---|---|
| 操作系统 | Ubuntu 22.04 x86_64；源码部署脚本针对此平台 |
| Python | 3.12.16；Dify 1.9.2 支持 >=3.11,<3.13 |
| Dify | 官方 1.9.2 源码，未修改；核心流程编排及执行日志 |
| DSL | 0.4.0；一个 Workflow 文件，14 个节点，含 5 个错误出口 |
| PowerContext | 1.5.0 官方 Python SDK / builtin SQLite 持久化 |
| PostgreSQL | 14.24；Dify 元数据与工作流日志 |
| Redis | 6.0.16；Dify 所需缓存与执行通知 |
| llama.cpp | b7132，官方 Ubuntu x64 CPU 二进制 |
| 模型 | Qwen2.5-1.5B-Instruct Q4_K_M；本地 OpenAI 兼容接口 |
| 配套后端 | FastAPI 0.121.3、uvicorn 0.38.0、httpx 0.28.1 |

完整 Python 依赖锁定见 `requirements.lock`，顶层依赖见 `requirements.txt`。Dify 自身使用官方 `uv.lock`；脚本只安装 storage/tools 依赖组。本作品无需 Dify 模型插件、工具插件、知识库、Embedding、Rerank、对象存储、外部预置数据和自定义插件安装包。同步 blocking 工作流无需 Celery worker，本作品不使用异步任务。Dify 前端编辑器未部署，网页通过官方 `/v1/workflows/run` 进入真实 Dify；官方导入/发布服务被用于自动化配置，未编写替代工作流执行器。

## 2. 首次自动部署

资源建议：4 CPU 线程、8 GB 内存、至少 6 GB 可用磁盘。没有 GPU 也能运行。系统需要 Ubuntu 22.04 的 apt 软件源可用，以及 `curl`、`unzip`、`apt-get`、`dpkg-deb`、`python3`、uv。uv 安装方式见其官方文档；无需系统 Python 安装依赖。

```bash
cd dify
python3 scripts/native.py prepare
python3 scripts/native.py up
```

`prepare` 下载发行版数据库软件包并解压到 `.runtime/deploy/root`，不会安装系统服务；下载 Dify、llama.cpp、模型，在用户目录创建 Python 环境。`up` 自动初始化/迁移 Dify 数据库，启动数据库、模型、Dify API、工具后端；首次调用官方 AppDslService 导入 DSL、WorkflowService 发布应用，生成本地 App API Token。管理员初始化的随机密码不输出、不提交。控制台 UI 不在本方案部署范围内；应用入口是配套网页和 Dify API。

服务就绪后访问 <http://127.0.0.1:18080>。Ctrl+C 停止本次启动的全部子服务。重启同一条 `up` 保留数据，端口已占用时脚本明确报错，不关闭未知服务。

| 端口 | 监听地址 / 作用 |
|---|---|
| 18080 | 127.0.0.1，作品网页、网关及 Dify 工具 |
| 18081 | 127.0.0.1，本地 Qwen 模型 |
| 15001 | 127.0.0.1，Dify API |
| 15432 | 127.0.0.1，Dify PostgreSQL |
| 16379 | 127.0.0.1，Dify Redis |

所有实际状态在 `.runtime/`，已加入 `.gitignore`。其中 `powercontext.db` 是通过 SDK 创建的记忆数据库，`scheduler.db` 是 PowerContext 调度状态，`events.jsonl` 是运行审计；`deploy/pgdata` 为 Dify 数据库。`deploy/secrets.json`、`dify-credential.json` 存放仅本地使用的自动凭据，权限 0600。提交材料没有真实密钥和这些运行目录。

## 3. 使用已有 Dify

也可以在已有 Dify 1.9.2 控制台选择「导入 DSL」，导入唯一文件 `dsl/echo-pawnshop.yml`，不存在额外导入顺序。设置环境变量后发布并创建应用 API Key：

| 配置 | 示例占位值 | 配置位置 |
|---|---|---|
| ECHO_BASE_URL | `http://host.docker.internal:18080` | Dify Workflow 环境变量 |
| ECHO_TOOL_KEY | `REPLACE_WITH_RANDOM_TOOL_KEY` | Dify secret 环境变量及后端，同值 |
| DIFY_API_URL | `http://127.0.0.1:8080/v1` | 后端环境，按 Dify 实际端口 |
| DIFY_APP_API_KEY | `REPLACE_WITH_DIFY_APP_KEY` | 后端环境，不传给浏览器 |
| ECHO_MODEL_URL | `http://127.0.0.1:18081/v1` | 后端环境，模型接口 |
| ECHO_MODEL | `qwen2.5-1.5b-instruct` | 后端环境，模型名 |
| ECHO_MODEL_API_KEY | `REPLACE_IF_PROVIDER_REQUIRES_KEY` | 可选，远程模型服务所需 |
| ECHO_RUNTIME | `.runtime` 的绝对路径 | 后端环境，可选，默认作品目录下 |

源码部署已自动完成这些配置；不要把 DSL 中的占位密钥直接用于公网服务。启动独立后端：

```bash
uv venv --python 3.12 .venv
uv pip sync --python .venv/bin/python requirements.lock
export ECHO_TOOL_KEY='REPLACE_WITH_RANDOM_TOOL_KEY'
export DIFY_API_URL='http://127.0.0.1:8080/v1'
export DIFY_APP_API_KEY='REPLACE_WITH_DIFY_APP_KEY'
export ECHO_MODEL_URL='http://127.0.0.1:18081/v1'
export ECHO_MODEL='qwen2.5-1.5b-instruct'
.venv/bin/python -m uvicorn echoes.server:app --host 0.0.0.0 --port 18080 --workers 1
```

Docker Linux 需给 Dify API/worker 容器添加 `extra_hosts: ["host.docker.internal:host-gateway"]`，并让后端监听容器可访问地址。Dify 的 SSRF 代理必须允许访问这个工具地址；自动源码部署不使用代理。Dify Cloud 无法访问你电脑的 127.0.0.1，需要实际可达的 HTTPS 工具地址，工具密钥配置为 secret。此公开部署路径未在本作品实测，实测证据只声明本机源码部署。

## 4. 下载来源与网络条件

- Dify：`https://github.com/langgenius/dify/archive/refs/tags/1.9.2.tar.gz`
- PowerContext：PyPI `powercontext[builtin]==1.5.0`，源码 `https://github.com/oceanbase/powercontext`
- llama.cpp：`https://github.com/ggml-org/llama.cpp/releases/download/b7132/llama-b7132-bin-ubuntu-x64.zip`
- 模型：`https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF` 中 `qwen2.5-1.5b-instruct-q4_k_m.gguf`。脚本使用相同仓库的 `hf-mirror.com` 镜像下载；可按网络情况改用官方域名。
- PostgreSQL/Redis：Ubuntu 22.04 官方 apt 源的软件包；下载时解析 apt 源的安全更新版本。

首次需要访问上述站点、PyPI 和 uv 的 Python 下载源。准备完成后核心游戏可以完全在本机运行，无需模型账号、API Key 或云端计费。`prepare` 记录模型和推理程序 SHA256 至本地 `assets-sha256.json`；当前实测哈希随证据提供。

## 5. 验证和故障处理

```bash
.venv/bin/python scripts/verify.py
.venv/bin/python -m pytest -q
```

前者通过网页网关实际调用 Dify，随机新建玩家，不删除旧数据。输出 `evidence/acceptance.json` 及对应事件日志；复验要保留原证据可用 `--output evidence/recheck.json`。`tests/` 是规则回归测试，不能替代端到端运行证据。

日志位于 `.runtime/logs/`。启动失败先看对应 `dify.log`、`model.log`、`echo.log`；下载失败检查相应域名；端口冲突停止原作品实例。模型连接失败会返回错误，交易尚未提交。相同输入重试保持相同 request_id；改了输入需换 request_id。若修改工具密钥，必须同步更新 Dify 环境变量后重新发布。

`ECHO_NARRATOR=rules` 仅供单独后端规则调试；明确显示“规则演示（未调用模型）”，端到端验收脚本拒绝该模式，不属于提交声明的智能体验运行方式。
