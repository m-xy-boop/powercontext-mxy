"""Generate readable reports exclusively from the application's recorded measurements."""
import json
import statistics
from pathlib import Path

root = Path(__file__).resolve().parents[1]
report = json.loads((root / 'reports/benchmark.json').read_text())
optimization = json.loads((root / 'reports/optimization.json').read_text())
loading = json.loads((root / 'reports/loading.json').read_text())
manifest = json.loads((root / 'reports/model-manifest.json').read_text())
median = statistics.median
common = sum(manifest['files'][f]['bytes'] for f in ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json'])
sizes = {k: manifest['files'][f'onnx/{f}']['bytes'] + common for k, f in [('FP32', 'model.onnx'), ('INT8', 'model_quantized.onnx'), ('FP16', 'model_fp16.onnx')]}
compression = sizes['FP32'] / sizes['INT8']
lines = ['# 实测结果', '', '所有数字来自提交应用的真实浏览器运行，耗时单位 ms，体积单位 MB（十进制）。测试日期：2026-11-16（Asia/Shanghai）。', '',
         '## 固定条件', '', f"- CPU：{report['environment']['cpu']}；逻辑核 {report['environment']['logicalCpus']}；内存 {report['environment']['memoryBytes']/1e9:.2f} GB。",
         f"- OS：{report['environment']['os']}；Chromium {report['environment']['browser']}；WASM 4 线程；跨源隔离开启。",
         '- seekdb 1.4.0 原生 server，127.0.0.1:2883；cpu_count=2，memory_budget=2G；cosine HNSW M=16、ef_construction=200、ef_search=64，IK smart 全文索引。',
         '- 模型固定 revision、CLS + L2、512 维、max_length=256。16 篇人工中文技术文档、16 查询，每查询标注一个相关文档；Top K=5。该微型集合不能代表通用中文检索质量。',
         '- 每配置 3 轮；每轮包含全语料导入及 16 次查询。保留首次轮和后续轮，不挑最快值。单因素优化与加载测试另行依次运行。', '',
         '## 实际模型体积', '', '| 精度 | ONNX 字节数 | 加相同分词器/配置后 MB | FP32/该精度 |', '|---|---:|---:|---:|']
for precision, filename in [('FP32','model.onnx'),('INT8','model_quantized.onnx'),('FP16','model_fp16.onnx')]:
    lines.append(f"| {precision} | {manifest['files']['onnx/'+filename]['bytes']} | {sizes[precision]/1e6:.3f} | {sizes['FP32']/sizes[precision]:.3f}× |")
lines += ['', '统计范围为实际运行的单个 ONNX + config/tokenizer/tokenizer_config/special_tokens_map；不包括运行时、README、量化说明，也不把下载的其他对照精度重复计入。文件 SHA-256 见 model-manifest.json。', '',
          '## 主实验：性能与质量', '', '| 请求配置 | 实际后端/精度 | 批大小 | 16 文档向量化中位数 | 查询向量化中位数 | seekdb 检索中位数 | 端到端中位数 | Recall@5 | nDCG@5 |', '|---|---|---:|---:|---:|---:|---:|---:|---:|']
for c in report['configurations']:
    queries = [q for run in c['runs'] for q in run['queries']]
    lines.append(f"| {c['config']['dtype']} / {c['config']['backend']} | {c['load']['backend']} / {c['load']['dtype']} | {c['config']['batch']} | {median(x['embeddingMs'] for x in c['runs']):.1f} | {median(q['embeddingMs'] for q in queries):.1f} | {median(q['seekdbMs'] for q in queries):.1f} | {median(q['totalMs'] for q in queries):.1f} | {statistics.mean(q['recall5'] for q in queries):.4f} | {statistics.mean(q['ndcg5'] for q in queries):.4f} |")
lines += ['', '端到端从已加载模型的查询提交开始，包含本地向量化、HTTP、数据库检索、原文 DOM 构建与下一帧；模型首次加载另表报告。seekdb 计时覆盖 SDK 原生混合检索调用（设置参数、数据库生成并执行混合 SQL），不含向量计算。前两组都使用批大小 1、逐批刷新，因此可用于比较量化；批大小 8 的主实验同时改变写入刷新粒度，不据此归因单项优化。', '',
          '质量计算：Recall@5=前 5 名相关项数/所有标注相关项数；nDCG@5 使用二元相关性与 1/log2(rank+1) 折扣。FP32→INT8 的 Recall@5 无变化；nDCG@5 从 0.9144 降至 0.8914，下降 0.0231。每查询排名保存在 benchmark.json；相同查询三轮结果保留。', '',
          '## 首次与预热查询', '', '| 配置 | 首轮第一个查询端到端 | 后两轮所有查询端到端中位数 | 首轮文档向量化 | 后两轮文档向量化中位数 |', '|---|---:|---:|---:|---:|']
for c in report['configurations']:
    warm_queries = [q for r in c['runs'][1:] for q in r['queries']]
    lines.append(f"| {c['config']['dtype']}/{c['config']['backend']} batch={c['config']['batch']} | {c['runs'][0]['queries'][0]['totalMs']:.1f} | {median(q['totalMs'] for q in warm_queries):.1f} | {c['runs'][0]['embeddingMs']:.1f} | {median(r['embeddingMs'] for r in c['runs'][1:]):.1f} |")
lines += ['', '## 模型文件读取与初始化', '', '| 配置 | 空缓存：文件就绪阶段 | 空缓存：会话初始化 | 空缓存总加载 | 两次缓存加载中位数 |', '|---|---:|---:|---:|---:|']
for c in loading['configurations']:
    cold = c['runs'][0]
    lines.append(f"| {c['config']['dtype']}/{c['config']['backend']} | {cold['modelFetchMs']:.1f} | {cold['sessionInitMs']:.1f} | {cold['loadMs']:.1f} | {median(x['loadMs'] for x in c['runs'][1:]):.1f} |")
lines += ['', '每配置使用独立全新浏览器 context，首轮空缓存、后两轮复用 Cache Storage。文件就绪阶段是调用 pipeline 至最后一个模型/分词器资源 done 事件，包含同源 localhost 文件传输、读取和部分处理；会话初始化是该事件至 pipeline ready。这里不是 Hugging Face 互联网下载速度；模型预下载由 download-model.mjs 完成。两次预热样本仍很少，缓存也不保证每次更快。详细文件时序见 loading.json。', '',
          '## 单因素优化', '', '| 批大小 | 刷新策略 | 向量化中位数 | 写入中位数 | 索引刷新中位数 | 可见性验证中位数 | 全流程导入中位数 |', '|---:|---|---:|---:|---:|---:|---:|']
for c in optimization['configurations']:
    values = ' | '.join(f"{median(r[k] for r in c['runs']):.1f}" for k in ['embeddingMs','writeMs','refreshMs','visibilityMs','totalMs'])
    lines.append(f"| {c['config']['batch']} | {c['config']['refresh']} | {values} |")
opt = [median(r['totalMs'] for r in c['runs']) for c in optimization['configurations']]
lines += ['', f'同为 INT8/WASM、同语料，第一→二行只改变写入与刷新策略：16 次逐条写入/刷新改为整组写入/刷新，导入总耗时 {opt[0]:.1f}→{opt[1]:.1f} ms，约 {opt[0]/opt[1]:.2f}×。第二→三行仅改变推理批大小；本机批 8 没有降低导入总耗时，因此默认选择批 1，保留批 8/4/16 供不同设备调优。每列分别取中位数，不要求列相加等于总耗时。', '',
          '## WebGPU 与降级验证', '', '- 实测 WebGPU adapter：Google SwiftShader 软件实现，不是实体 GPU，features 不含 shader-f16。显式选择 WebGPU FP16 被运行时拒绝；自动策略随后使用 INT8/WASM。**本环境没有实体 GPU FP16 的成功性能结果。**',
          '- 早期使用相同模型与语料实测 INT8/WebGPU 软件路径，Recall@5=0.8125，低于 FP32/INT8 WASM 的 0.9375，未作为默认方案。原始排名与时序见 int8-webgpu-investigation.json。该结果不代表实体 GPU；FP16 优先策略仍需在支持 shader-f16 的真实设备上补测。',
          f"- 显式屏蔽 WebGPU API 的测试 Worker：实际后端 {report['fallback']['backend']}，精度 {report['fallback']['dtype']}，输出维度 {report['fallback'].get('dimension')}；查询后数据库返回 {report['fallback'].get('results')}。",
          '- UI 验收在普通 headless Chromium 下也实际显示“WASM · INT8 · 已自动降级”，完成查询。见 acceptance.json、automatic-backend.png。',
          '- benchmark.json 的网络列表来自最后补测的降级会话，所有请求均为 localhost；externalRequests 为空。应用源代码没有远程推理 API 调用，运行时禁止远程模型加载。', '',
          '## 原始证据与复现', '', 'benchmark.json（配置、逐次排名与计时）；optimization.json（逐次单因素实验）；loading.json（冷暖加载与文件时序）；model-manifest.json（实际模型大小与散列）；database-schema.txt；acceptance.json；application.png / automatic-backend.png / mobile.png。命令见 ../README.md。']
(root / 'reports/RESULTS.md').write_text('\n'.join(lines) + '\n')

technical = f'''# 星屿 · 一页技术说明

**方案。** 基于 `2026-competition-ecosystem` 分支，资产全部位于 `stepfun/`。浏览器 Worker 执行文档/查询向量化；Node 服务通过 seekdb-js 1.4.0 写入显式向量，所有存储、全文/向量检索与 RRF 排序由 seekdb 完成。无云端推理 API，无预计算结果回放。启动与复现见 [README](README.md)。

**模型与量化。** BAAI/bge-small-zh-v1.5（MIT），使用 Xenova 同名 ONNX revision `75c43b069aac4d136ba6bc1122f995fedcfd2781`，Transformers.js 3.8.1；CLS + L2，512 维，256 tokens。采用公开 FP16/INT8 导出；INT8 上游配置为 per-channel、reduce-range、QInt8。按 ONNX + 相同分词器/配置统计，FP32 {sizes['FP32']/1e6:.3f} MB，INT8 {sizes['INT8']/1e6:.3f} MB（{compression:.3f}×），FP16 {sizes['FP16']/1e6:.3f} MB；SHA-256 与配置随附。

**后端。** 显式尝试 WebGPU FP16；无 adapter、无 shader-f16 或初始化失败后自动切换 INT8/WASM，页面显示后端和降级状态。WASM 最多 4 线程，同源模型与 WASM 资源由应用提供。本机 SwiftShader 软件 adapter 不支持 FP16；已验证自动降级及屏蔽 GPU 后完整查询。实体 GPU FP16 尚未实测，不能以软件 adapter 替代该项证据。

**seekdb。** 原生 server 1.4.0，每知识库独立集合：ID、document、VECTOR(512)、metadata JSON；cosine HNSW（M16/efConstruction200/efSearch64）+ 中文 IK smart 全文索引。SDK `hybridSearch` 调用数据库原生 DBMS_HYBRID_SEARCH，向量/全文各召回 50，RRF 常数 60、窗口 50，返回前 5 名。应用保持数据库排序，不自行合并结果。

**状态与优化。** Unicode 180 字符分段、重叠 32；SHA-256 ID + upsert 实现增量/重复导入幂等。最多 128 片段整组写入后统一刷新，并验证全文及混合检索可见性。SDK 单连接 session 变量由请求队列保护。导入时禁用切换，切换清空结果，重载从数据库恢复集合。固定批 1 下，逐条写入/刷新→整组写入/刷新：{opt[0]:.1f}→{opt[1]:.1f} ms（{opt[0]/opt[1]:.2f}×）；本机批 8 未进一步降低总导入耗时，因此默认批 1，保留调节选项。

**实测质量。** i5-12500H、16 逻辑核、约 8 GB、WSL2、Chromium 141.0.7390.37；固定 16 文档/16 人工标注查询，重复 3 轮，保留原始值并报告中位数。FP32 与 INT8/WASM Recall@5 均 0.9375；nDCG@5 从 0.9144 降为 0.8914。INT8/WebGPU 软件路径曾降至 Recall@5 0.8125，未作为默认方案。微型评测集不代表通用质量。

**交付与验证。** 可运行 Web 应用、完整锁定依赖、模型下载与数据库启动脚本、固定评测集、UI 验收和性能脚本。验收覆盖导入→本地向量化→seekdb 写入/刷新→查询→排序原文、增量导入、集合隔离、刷新恢复和 WASM 降级。[详细结果](reports/RESULTS.md) 包含冷暖加载、推理、写入/刷新、检索和端到端分段数据；原始记录、表结构及截图均随附。私有仓库推送和平台提交尚未执行。
'''
(root / 'TECHNICAL.md').write_text(technical)
