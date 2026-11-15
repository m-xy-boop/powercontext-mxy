# 星屿 · 一页技术说明

**方案。** 基于 `2026-competition-ecosystem` 分支，资产全部位于 `stepfun/`。浏览器 Worker 执行文档/查询向量化；Node 服务通过 seekdb-js 1.4.0 写入显式向量，所有存储、全文/向量检索与 RRF 排序由 seekdb 完成。无云端推理 API，无预计算结果回放。启动与复现见 [README](README.md)。

**模型与量化。** BAAI/bge-small-zh-v1.5（MIT），使用 Xenova 同名 ONNX revision `75c43b069aac4d136ba6bc1122f995fedcfd2781`，Transformers.js 3.8.1；CLS + L2，512 维，256 tokens。采用公开 FP16/INT8 导出；INT8 上游配置为 per-channel、reduce-range、QInt8。按 ONNX + 相同分词器/配置统计，FP32 95.292 MB，INT8 24.451 MB（3.897×），FP16 47.938 MB；SHA-256 与配置随附。

**后端。** 显式尝试 WebGPU FP16；无 adapter、无 shader-f16 或初始化失败后自动切换 INT8/WASM，页面显示后端和降级状态。WASM 最多 4 线程，同源模型与 WASM 资源由应用提供。本机 SwiftShader 软件 adapter 不支持 FP16；已验证自动降级及屏蔽 GPU 后完整查询。实体 GPU FP16 尚未实测，不能以软件 adapter 替代该项证据。

**seekdb。** 原生 server 1.4.0，每知识库独立集合：ID、document、VECTOR(512)、metadata JSON；cosine HNSW（M16/efConstruction200/efSearch64）+ 中文 IK smart 全文索引。SDK `hybridSearch` 调用数据库原生 DBMS_HYBRID_SEARCH，向量/全文各召回 50，RRF 常数 60、窗口 50，返回前 5 名。应用保持数据库排序，不自行合并结果。

**状态与优化。** Unicode 180 字符分段、重叠 32；SHA-256 ID + upsert 实现增量/重复导入幂等。最多 128 片段整组写入后统一刷新，并验证全文及混合检索可见性。SDK 单连接 session 变量由请求队列保护。导入时禁用切换，切换清空结果，重载从数据库恢复集合。固定批 1 下，逐条写入/刷新→整组写入/刷新：17927.2→1971.0 ms（9.10×）；本机批 8 未进一步降低总导入耗时，因此默认批 1，保留调节选项。

**实测质量。** i5-12500H、16 逻辑核、约 8 GB、WSL2、Chromium 141.0.7390.37；固定 16 文档/16 人工标注查询，重复 3 轮，保留原始值并报告中位数。FP32 与 INT8/WASM Recall@5 均 0.9375；nDCG@5 从 0.9144 降为 0.8914。INT8/WebGPU 软件路径曾降至 Recall@5 0.8125，未作为默认方案。微型评测集不代表通用质量。

**交付与验证。** 可运行 Web 应用、完整锁定依赖、模型下载与数据库启动脚本、固定评测集、UI 验收和性能脚本。验收覆盖导入→本地向量化→seekdb 写入/刷新→查询→排序原文、增量导入、集合隔离、刷新恢复和 WASM 降级。[详细结果](reports/RESULTS.md) 包含冷暖加载、推理、写入/刷新、检索和端到端分段数据；原始记录、表结构及截图均随附。私有仓库推送和平台提交尚未执行。
