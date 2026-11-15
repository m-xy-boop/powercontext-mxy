# 实测结果

所有数字来自提交应用的真实浏览器运行，耗时单位 ms，体积单位 MB（十进制）。测试日期：2026-11-16（Asia/Shanghai）。

## 固定条件

- CPU：12th Gen Intel(R) Core(TM) i5-12500H；逻辑核 16；内存 8.19 GB。
- OS：Linux 6.18.58.1-microsoft-standard-WSL2；Chromium 141.0.7390.37；WASM 4 线程；跨源隔离开启。
- seekdb 1.4.0 原生 server，127.0.0.1:2883；cpu_count=2，memory_budget=2G；cosine HNSW M=16、ef_construction=200、ef_search=64，IK smart 全文索引。
- 模型固定 revision、CLS + L2、512 维、max_length=256。16 篇人工中文技术文档、16 查询，每查询标注一个相关文档；Top K=5。该微型集合不能代表通用中文检索质量。
- 每配置 3 轮；每轮包含全语料导入及 16 次查询。保留首次轮和后续轮，不挑最快值。单因素优化与加载测试另行依次运行。

## 实际模型体积

| 精度 | ONNX 字节数 | 加相同分词器/配置后 MB | FP32/该精度 |
|---|---:|---:|---:|
| FP32 | 94851877 | 95.292 | 1.000× |
| INT8 | 24010842 | 24.451 | 3.897× |
| FP16 | 47497427 | 47.938 | 1.988× |

统计范围为实际运行的单个 ONNX + config/tokenizer/tokenizer_config/special_tokens_map；不包括运行时、README、量化说明，也不把下载的其他对照精度重复计入。文件 SHA-256 见 model-manifest.json。

## 主实验：性能与质量

| 请求配置 | 实际后端/精度 | 批大小 | 16 文档向量化中位数 | 查询向量化中位数 | seekdb 检索中位数 | 端到端中位数 | Recall@5 | nDCG@5 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| fp32 / wasm | wasm / fp32 | 1 | 365.2 | 19.7 | 8.4 | 49.9 | 0.9375 | 0.9144 |
| q8 / wasm | wasm / q8 | 1 | 242.0 | 10.7 | 6.4 | 30.8 | 0.9375 | 0.8914 |
| q8 / wasm | wasm / q8 | 8 | 330.6 | 16.0 | 8.2 | 50.2 | 0.9375 | 0.8914 |
| fp16 / auto | wasm / fp16 | 8 | 700.1 | 45.3 | 12.2 | 76.2 | 0.9375 | 0.9144 |
| quantized / auto | wasm / q8 | 8 | 538.4 | 22.8 | 12.6 | 52.8 | 0.9375 | 0.8914 |

端到端从已加载模型的查询提交开始，包含本地向量化、HTTP、数据库检索、原文 DOM 构建与下一帧；模型首次加载另表报告。seekdb 计时覆盖 SDK 原生混合检索调用（设置参数、数据库生成并执行混合 SQL），不含向量计算。前两组都使用批大小 1、逐批刷新，因此可用于比较量化；批大小 8 的主实验同时改变写入刷新粒度，不据此归因单项优化。

质量计算：Recall@5=前 5 名相关项数/所有标注相关项数；nDCG@5 使用二元相关性与 1/log2(rank+1) 折扣。FP32→INT8 的 Recall@5 无变化；nDCG@5 从 0.9144 降至 0.8914，下降 0.0231。每查询排名保存在 benchmark.json；相同查询三轮结果保留。

## 首次与预热查询

| 配置 | 首轮第一个查询端到端 | 后两轮所有查询端到端中位数 | 首轮文档向量化 | 后两轮文档向量化中位数 |
|---|---:|---:|---:|---:|
| fp32/wasm batch=1 | 40.4 | 49.6 | 526.0 | 341.0 |
| q8/wasm batch=1 | 33.5 | 30.1 | 305.2 | 240.8 |
| q8/wasm batch=8 | 44.0 | 51.4 | 261.5 | 362.0 |
| fp16/auto batch=8 | 75.5 | 77.9 | 677.9 | 824.0 |
| quantized/auto batch=8 | 61.5 | 50.6 | 538.4 | 489.3 |

## 模型文件读取与初始化

| 配置 | 空缓存：文件就绪阶段 | 空缓存：会话初始化 | 空缓存总加载 | 两次缓存加载中位数 |
|---|---:|---:|---:|---:|
| fp32/wasm | 5129.0 | 10214.8 | 15343.9 | 4358.3 |
| q8/wasm | 1989.3 | 1261.6 | 3250.9 | 564.3 |
| quantized/auto | 502.5 | 1155.2 | 1657.7 | 672.1 |

每配置使用独立全新浏览器 context，首轮空缓存、后两轮复用 Cache Storage。文件就绪阶段是调用 pipeline 至最后一个模型/分词器资源 done 事件，包含同源 localhost 文件传输、读取和部分处理；会话初始化是该事件至 pipeline ready。这里不是 Hugging Face 互联网下载速度；模型预下载由 download-model.mjs 完成。两次预热样本仍很少，缓存也不保证每次更快。详细文件时序见 loading.json。

## 单因素优化

| 批大小 | 刷新策略 | 向量化中位数 | 写入中位数 | 索引刷新中位数 | 可见性验证中位数 | 全流程导入中位数 |
|---:|---|---:|---:|---:|---:|---:|
| 1 | each | 998.0 | 4786.7 | 12829.6 | 1007.3 | 17927.2 |
| 1 | group | 551.9 | 380.4 | 704.3 | 298.6 | 1971.0 |
| 8 | group | 537.9 | 530.2 | 505.1 | 458.8 | 2453.2 |

同为 INT8/WASM、同语料，第一→二行只改变写入与刷新策略：16 次逐条写入/刷新改为整组写入/刷新，导入总耗时 17927.2→1971.0 ms，约 9.10×。第二→三行仅改变推理批大小；本机批 8 没有降低导入总耗时，因此默认选择批 1，保留批 8/4/16 供不同设备调优。每列分别取中位数，不要求列相加等于总耗时。

## WebGPU 与降级验证

- 实测 WebGPU adapter：Google SwiftShader 软件实现，不是实体 GPU，features 不含 shader-f16。显式选择 WebGPU FP16 被运行时拒绝；自动策略随后使用 INT8/WASM。**本环境没有实体 GPU FP16 的成功性能结果。**
- 早期使用相同模型与语料实测 INT8/WebGPU 软件路径，Recall@5=0.8125，低于 FP32/INT8 WASM 的 0.9375，未作为默认方案。原始排名与时序见 int8-webgpu-investigation.json。该结果不代表实体 GPU；FP16 优先策略仍需在支持 shader-f16 的真实设备上补测。
- 显式屏蔽 WebGPU API 的测试 Worker：实际后端 wasm，精度 q8，输出维度 512；查询后数据库返回 ['gpu', 'cache', 'privacy', 'refresh', 'batch']。
- UI 验收在普通 headless Chromium 下也实际显示“WASM · INT8 · 已自动降级”，完成查询。见 acceptance.json、automatic-backend.png。
- benchmark.json 的网络列表来自最后补测的降级会话，所有请求均为 localhost；externalRequests 为空。应用源代码没有远程推理 API 调用，运行时禁止远程模型加载。

## 原始证据与复现

benchmark.json（配置、逐次排名与计时）；optimization.json（逐次单因素实验）；loading.json（冷暖加载与文件时序）；model-manifest.json（实际模型大小与散列）；database-schema.txt；acceptance.json；application.png / automatic-backend.png / mobile.png。命令见 ../README.md。
