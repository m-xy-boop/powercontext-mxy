# 赛题要求与交付对应

| 要求 | 实现与证据 |
|---|---|
| 基于指定分支，资产放 stepfun | `stepfun-browser` 从 `origin/2026-competition-ecosystem` 的 `d647275c` 建立；所有新增文件均位于 `stepfun/` |
| 可运行 Web 应用与完整源码 | `server.mjs`、`src/`、锁文件、启动和下载脚本；本地生产构建与真实浏览器验收通过 |
| 导入文本、进度与排序原文 | UTF-8 文本/Markdown、多文件和粘贴、分段进度、来源/片段序号；`reports/application.png` |
| 文档与查询在浏览器本地计算 | `src/embedding-worker.js`，Transformers.js / ONNX Runtime Web，Web Worker；后端仅接受显式向量，无 embedding 函数 |
| 公开开源模型与版本 | BAAI / Xenova bge-small-zh-v1.5，固定 revision；下载脚本与模型 SHA-256 清单 |
| INT8 或 FP16、体积和质量变化 | 默认 WebGPU FP16 / WASM INT8；FP32 对照；`reports/RESULTS.md` 与逐次原始记录 |
| WebGPU 优先与 WASM 自动降级 | 显式 WebGPU 初始化，失败后自动 WASM；UI 显示“已自动降级”；`reports/automatic-backend.png` |
| WebGPU 成功运行的实测 | 已测软件 WebGPU INT8，但有质量下降，未作为默认；**实体 GPU FP16 成功路径未在当前机器实测**，不能视为该项实测证据齐备 |
| seekdb-js 接入并保存向量/正文/元数据 | SDK 1.4.0，seekdb 1.4.0；`reports/database-schema.txt` |
| 混合检索由数据库完成 | SDK `hybridSearch` → DBMS_HYBRID_SEARCH；RRF 在 seekdb 内执行，无客户端两路融合 |
| 增量导入与索引刷新 | upsert、组写入后 refresh_index、全文和混合检索可见性验证；真实 UI 新内容立即可查 |
| 集合与会话状态 | 独立集合，切换清空结果，导入期间锁定，重载恢复列表；SDK session 变量请求串行化 |
| 基础性能优化与前后实测 | 分别测量量化、写入/刷新策略和批大小；`reports/optimization.json`；默认按本机测试选择批 1 |
| 固定条件、冷暖请求和阶段计时 | 固定评测数据、3 轮原始值、中位数、CPU/浏览器/数据库配置；`benchmark.json`、`loading.json` |
| 一页技术说明 | `TECHNICAL.md` 与 `reports/technical.pdf`（已验证 1 页 A4） |
| 禁止云端推理/预计算回放/替代数据库 | 模型仅同源读取，浏览器实时计算，所有持久存储与搜索使用 seekdb；源代码和网络记录可复核 |

完整运行步骤见 `README.md`。当前完成本地构建、运行与验收；尚未向比赛私有仓库推送或填写测评平台提交表单。除实体 GPU FP16 外，浏览器覆盖范围为本次记录的 Chromium 环境，不能推断所有 Chrome/Edge 设备均已测试。
