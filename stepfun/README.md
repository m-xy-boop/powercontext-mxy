# 星屿：浏览器本地向量化知识库

OceanBase × 阶跃星辰赛题实现。基于 `oceanbase/powercontext` 的 `2026-competition-ecosystem` 分支，所有项目资产均在 `stepfun/`。文本与查询由浏览器 Worker 中的 Transformers.js / ONNX Runtime Web 计算；seekdb-js 将显式向量、原文和元数据写入 seekdb，调用数据库原生混合检索和 RRF 返回排序结果。

## 本地运行

要求 Node.js 22+、seekdb **1.4.0+**、最新版 Chrome / Edge。推理无需 API Key。默认服务只监听 `127.0.0.1`，不要通过非 localhost 的 HTTP 地址访问 WebGPU。

```bash
cd stepfun
npm ci
# 官方模型站不可达时可在命令前设置 HF_ENDPOINT=https://hf-mirror.com
node scripts/download-model.mjs
bash scripts/start-db.sh
# 首次数据库初始化可能需等待。已有本地 seekdb 时跳过启动。
npm run build
npm start
```

打开 http://127.0.0.1:5173 。选择文件或粘贴文本，点击“导入知识库”，等待模型加载、向量化、写入与索引刷新完成，再输入查询。点击“导入示例”可导入项目自带 16 篇中文示例。运行配置中显示当前后端，支持强制 WASM、切换 FP32 基线、调整批大小和导出测量。

本机依赖和模型已经准备完成，可执行 `bash scripts/run-local.sh` 启动（数据库需运行；端口可通过 `PORT` 修改）。题目要求对应与未完成的硬件验证项见 [ACCEPTANCE.md](ACCEPTANCE.md)。

开发模式：`npm run dev`。模型为同源静态资源；首次运行浏览器会读取模型文件，后续使用浏览器 Cache Storage。文档/向量的持久存储与检索全部使用 seekdb。

### 数据库

可从 https://github.com/oceanbase/seekdb/releases 安装官方 1.4.0+。本次实测使用已有的 Linux 原生 seekdb 1.4.0。`scripts/start-db.sh` 使用独立 `.runtime/seekdb` 目录启动原生实例；已有实例时直接配置连接，跳过启动脚本。

启动脚本对 WSL 的 `/run/systemd/container` 标记作兼容处理：在子进程的用户/挂载命名空间中隐藏该标记，避免官方 1.4.0 遥测初始化误判；宿主文件不变。此路径需系统允许非特权用户命名空间，当前机器已有实例的同一方式已实测。普通 Linux 使用原生启动。

配置通过环境变量注入，不在前端暴露数据库密码：

| 变量 | 默认值 |
|---|---|
| `SEEKDB_HOST` | `127.0.0.1` |
| `SEEKDB_PORT` | `2883` |
| `SEEKDB_USER` | `root` |
| `SEEKDB_PASSWORD` | 空 |
| `SEEKDB_DATABASE` | `stepfun_kb` |
| `PORT` | `5173` |

数据库不存在时自动创建。集合创建时使用 512 维 cosine HNSW、中文 IK smart 全文索引。所有集合带 `sf_` 前缀，数据库与现有其他项目隔离。默认 root 仅用于本地开发；远程部署需自行配置适当数据库账号、TLS 和应用访问控制。

### 模型来源与量化

- 原模型：[BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)，MIT；ONNX 转换：[Xenova/bge-small-zh-v1.5](https://huggingface.co/Xenova/bge-small-zh-v1.5)。
- 固定 ONNX revision：`75c43b069aac4d136ba6bc1122f995fedcfd2781`。
- 运行时 Transformers.js `3.8.1`。模型输出 CLS 池化与 L2 归一化，512 维；查询前缀为 `为这个句子生成表示以用于检索相关文章：`。
- 使用公开发布的 FP16 和 INT8 ONNX，不自行训练。FP16 为半精度权重；INT8 发布配置包含 `per_channel=true`、`reduce_range=true`、`weight_type=QInt8`，完整配置随下载获取。上游没有在该模型仓库完整公开转换工具链版本，不虚构自行量化过程。
- 下载脚本获取相同 revision 的 FP32、FP16、INT8 文件，模型本体不提交 Git。文件字节大小与 SHA-256 见 `reports/model-manifest.json`。比较体积时使用实际运行的 ONNX 加相同分词器/配置资源，排除运行时和模型说明文件。

### 状态、写入与检索

每个知识库对应独立集合；片段 ID 为来源、序号和正文的 SHA-256，重复导入相同内容使用 upsert，不重复增长。修改后的同名文档作为增量版本保留；没有实现“替换整篇文档”或删除旧片段语义。

UTF-8 `.txt/.md/.markdown` 文件先按 Unicode 字符切成 180 字符、重叠 32 字符的片段，浏览器按默认批大小 1（可调 8） 推理，最多累计 128 个片段后统一写入、刷新索引。单文件上限 2 MB，浏览器仍需为选择的文件与片段列表分配内存。模型单次最多 256 tokens，连续英文长串可能被截断，建议预分段。

导入成功前调用 `collection.refresh_index()`，逐片段检查全文可见性和混合检索可见性，超时明确报错。向量、正文与元数据均留在 seekdb。全文和向量候选各 50，数据库 `DBMS_HYBRID_SEARCH.GET_SQL` 执行 RRF（常数 60、窗口 50），应用只展示返回顺序，不执行客户端召回、排序或融合。

SDK 在单连接中设置混合检索 session 变量，所以所有数据库操作排队执行，避免并发请求覆盖变量。导入期间禁用切换，成功后刷新数量；切换清空旧结果；页面刷新从数据库恢复集合列表。界面不记住上一次选择，刷新后选择数据库列表首项。

## 复现验收与评测

启动应用后，在另一个终端依次运行（不要并行执行性能测试）：

```bash
npx playwright install chromium
APP_URL=http://127.0.0.1:5173 node scripts/acceptance.mjs
APP_URL=http://127.0.0.1:5173 node scripts/benchmark.mjs
APP_URL=http://127.0.0.1:5173 node scripts/optimization.mjs
APP_URL=http://127.0.0.1:5173 node scripts/loading.mjs
```

验收通过实际 UI 检查导入、查询、增量写入、集合隔离、刷新恢复与错误向量处理。评测使用 `data/evaluation.json` 的固定语料、查询和人工相关性标注。每配置导入/查询重复 3 次，保留第一轮和预热轮，报告中位数。每次测试创建唯一名称集合，保留在 seekdb 方便复核；它们会出现在知识库列表中。

`reports/benchmark.json` 包含每次排名、质量、计时、配置、环境、模型大小及网络请求；`reports/acceptance.json` 和截图是验收证据。一页技术说明见 `TECHNICAL.md`，详细测量见 `reports/RESULTS.md`。

可直接提交的一页 A4 文件为 `reports/technical.pdf`。如需从原始结果重新生成说明：`python3 scripts/summarize.py`，再执行 `node scripts/technical-pdf.mjs` 导出 PDF。

WebGPU 在不同驱动、GPU 与浏览器上的数值和性能存在差异。基准脚本启用 Chromium WebGPU / Vulkan 实验参数，具体实际 adapter 在报告中说明；强制模拟“无 WebGPU”只验证自动降级分支，不能替代所有真实设备覆盖。

## 依赖与交付

`npm ci` 使用锁文件。`.npmrc` 的 `legacy-peer-deps` 规避 seekdb 默认 embedding 可选 peer 的上游 workspace 发布错误；`ignore-scripts` 避免下载本应用不使用的 Node ONNX 原生运行时。浏览器使用 npm 包自带的 ONNX Runtime Web WASM 资源，不执行服务端 embedding。

交付文件包括应用源代码、依赖锁、数据库启动脚本、固定模型下载脚本、评测数据、测试脚本、原始结果和一页技术说明。提交比赛时使用此分支的 commit hash；私有比赛仓库推送和平台提交另行进行。
