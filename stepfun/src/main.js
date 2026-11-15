import './style.css';
import { api, Embedder, chunks, recordId } from './client.js';
document.querySelector('#app').innerHTML = `
<aside><a class="brand" href="/">✳ 星屿 <small>KNOWLEDGE, WITHIN REACH</small></a><div class="section-label">工作空间</div>
<label for="kb">当前知识库</label><select id="kb" aria-label="当前知识库"></select><button id="new" class="secondary">＋ 新建知识库</button>
<div class="side-bottom"><span class="dot"></span> 浏览器本地向量化<p>文本与向量存储于 seekdb<br>模型计算在你的设备完成</p><span id="db">连接数据库中…</span></div></aside>
<main><header><span>个人知识工作台</span><span class="pill" id="backend">模型尚未加载</span></header>
<div class="intro"><span class="eyebrow">LOCAL INTELLIGENCE / 本地智能</span><h1>让知识，彼此连接。</h1><p>导入你的文档，用自然语言找到答案与原文。</p></div>
<div class="layout"><section class="card import"><div class="card-heading"><span class="number">01</span><h2>建立知识连接</h2></div>
<p class="muted">支持 UTF-8 文本与 Markdown。自动分段、批量向量化。</p>
<label class="drop" for="files"><span>↥</span><strong>选择或拖入文本文件</strong><small>.txt / .md · 支持多文件 · 单文件 ≤ 2 MB</small><input id="files" type="file" multiple accept=".txt,.md,.markdown,text/plain,text/markdown"></label>
<label for="paste">也可以直接粘贴文本</label><textarea id="paste" placeholder="把值得保存的知识放在这里…" rows="5"></textarea>
<div class="row"><button id="import">导入知识库 ↗</button><button id="demo" class="secondary">导入示例</button></div>
<progress id="progress" max="100" value="0"></progress><p id="status" role="status" aria-live="polite">准备就绪，首次导入将加载 INT8 模型。</p>
<details><summary>运行配置与测量</summary><label>推理策略<select id="device"><option value="auto">优先 WebGPU，自动降级 WASM</option><option value="wasm">强制 WASM（验证降级流程）</option></select></label><label>模型精度<select id="dtype"><option value="quantized">自动量化 · WebGPU FP16 / WASM INT8</option><option value="q8">INT8 · 对比</option><option value="fp16">FP16 · 对比</option><option value="fp32">FP32 · 质量基线</option></select></label><label>向量化批大小<select id="batch"><option>1</option><option>8</option><option>4</option><option>16</option></select></label><button id="load" class="secondary">加载 / 切换模型</button><button id="refresh" class="secondary">刷新索引</button><button id="export" class="secondary">导出测量 JSON</button><pre id="metrics">暂无测量</pre></details></section>
<section class="card search"><div class="card-heading"><span class="number">02</span><h2>从问题，找到线索</h2></div>
<form id="search"><label for="query">搜索你的知识</label><div class="search-box"><input id="query" required maxlength="1000" placeholder="例如：不支持 WebGPU 时怎么运行？"><button aria-label="搜索">↗</button></div></form>
<div class="results-heading"><span id="result-label">语义 + 全文混合检索</span><small>seekdb · RRF 排序</small></div><div id="results"><div class="empty"><span>⌘</span><h3>你的下一条线索，藏在知识里</h3><p>导入文档后开始搜索，查看排序后的原文片段。</p></div></div></section></div>
<footer>星屿 · seekdb × 阶跃星辰 <span>无需云端推理 API</span></footer></main>`;
const $ = id => document.getElementById(id);
const metrics = []; let modelInfo; let locked = false;
const embedder = new Embedder(p => { if (p.status === 'progress') status(`加载 ${p.file}：${Math.round(p.progress || 0)}%`); });
const status = message => { $('status').textContent = message; };
const record = value => { metrics.push({ at: new Date().toISOString(), ...value }); $('metrics').textContent = JSON.stringify(value, null, 2); };
async function run(fn) {
  if (locked) return; locked = true;
  document.querySelectorAll('button,select,input,textarea').forEach(e => e.disabled = true);
  try { await fn(); } catch (e) { status(`操作失败：${e.message}`); console.error(e); }
  finally { locked = false; document.querySelectorAll('button,select,input,textarea').forEach(e => e.disabled = false); }
}
async function list(selected = $('kb').value) {
  const rows = await api('/kbs'); $('kb').replaceChildren();
  for (const r of rows) { const option = new Option(`${r.name.slice(3)} · ${r.count} 个片段`, r.name); $('kb').add(option); }
  if (rows.some(r => r.name === selected)) $('kb').value = selected;
}
async function load() {
  const dtype = $('dtype').value, device = $('device').value;
  if (modelInfo?.requestedDtype === dtype && modelInfo.requested === device) return;
  status('加载本地模型中…');
  const info = await embedder.load({ dtype, backend: device });
  modelInfo = { ...info, requested: device, requestedDtype: dtype };
  $('backend').textContent = `${info.backend.toUpperCase()} · ${{q8:'INT8',fp16:'FP16',fp32:'FP32'}[info.dtype]}${device === 'auto' && info.backend === 'wasm' ? ' · 已自动降级' : ''}`;
  $('backend').title = info.reason || 'WebGPU 推理';
  status(info.reason ? `${info.reason}；当前使用 ${info.backend.toUpperCase()}` : 'WebGPU 模型加载完成'); record({ phase: 'load', ...info });
}
async function importDocs(docs) {
  const kb = $('kb').value; if (!kb) throw new Error('请先创建知识库');
  await load(); const start = performance.now();
  const records = [];
  for (const doc of docs) for (const [i, text] of chunks(doc.text).entries())
    records.push({ id: await recordId(doc.source, i, text), text, source: doc.source, chunk: i, dtype: modelInfo.dtype });
  if (!records.length) throw new Error('没有可导入的非空文本');
  const batch = Number($('batch').value); let embeddingMs = 0, writeMs = 0, refreshMs = 0, visibilityMs = 0;
  // One refresh per write group (up to 128 chunks), instead of refreshing each embedding batch.
  for (let offset = 0; offset < records.length; offset += 128) {
    const group = records.slice(offset, offset + 128);
    for (let i = 0; i < group.length; i += batch) {
      const part = group.slice(i, i + batch); const result = await embedder.embed(part.map(r => r.text)); embeddingMs += result.embeddingMs;
      part.forEach((r, j) => r.embedding = result.embeddings[j]);
      $('progress').value = Math.round((offset + i + part.length) / records.length * 90);
      status(`本地向量化 ${Math.min(offset + i + part.length, records.length)} / ${records.length}`);
    }
    status('写入 seekdb 并刷新索引…'); const result = await api(`/kbs/${kb}/import`, { records: group });
    writeMs += result.writeMs; refreshMs += result.refreshMs; visibilityMs += result.visibilityMs;
    group.forEach(r => delete r.embedding);
  }
  await list(kb); $('progress').value = 100;
  record({ phase: 'import', chunks: records.length, backend: modelInfo.backend, dtype: modelInfo.dtype, batch, embeddingMs, writeMs, refreshMs, visibilityMs, totalMs: performance.now() - start });
  status(`已导入 ${records.length} 个片段，索引刷新完成，可以检索。`);
}
$('new').onclick = () => { const value = prompt('知识库名称（英文、数字和下划线，1–48 字符）'); if (value) run(async () => { const name = `sf_${value}`; await api('/kbs', { name }); await list(name); $('results').replaceChildren(); }); };
$('load').onclick = () => run(load);
$('import').onclick = () => run(async () => {
  const docs = [];
  for (const f of $('files').files) {
    if (f.size > 2 * 1024 * 1024) throw new Error(`${f.name} 超过 2 MB`);
    if (!/\.(txt|md|markdown)$/i.test(f.name)) throw new Error('只支持文本或 Markdown 文件');
    docs.push({ source: f.name, text: new TextDecoder('utf-8', { fatal: true }).decode(await f.arrayBuffer()) });
  }
  if ($('paste').value.trim()) docs.push({ source: '粘贴文本', text: $('paste').value });
  await importDocs(docs);
});
$('demo').onclick = () => run(async () => { const data = await api('/dataset'); await importDocs(data.documents.map(d => ({ source: d.id, text: d.text }))); });
$('refresh').onclick = () => run(async () => { await api(`/kbs/${$('kb').value}/refresh`, {}); await list(); status('索引已刷新'); });
$('kb').onchange = () => { $('results').replaceChildren(); $('result-label').textContent = '语义 + 全文混合检索'; };
$('search').onsubmit = e => { e.preventDefault(); run(async () => {
  const kb = $('kb').value, text = $('query').value.trim(); if (!kb) throw new Error('请先创建知识库');
  const start = performance.now(); await load(); const embedded = await embedder.embed([text], true);
  const result = await api(`/kbs/${kb}/search`, { text, embedding: embedded.embeddings[0], k: 5 });
  $('results').replaceChildren();
  result.results.forEach((r, i) => {
    const article = document.createElement('article'); const h3 = document.createElement('h3'); h3.textContent = `${String(i + 1).padStart(2, '0')} / ${r.metadata?.source || '文档'}`;
    const p = document.createElement('p'); p.textContent = r.text;
    const small = document.createElement('small'); small.textContent = `片段 ${Number(r.metadata?.chunk || 0) + 1} · seekdb 排序位置 ${i + 1}`;
    article.append(h3, p, small); $('results').append(article);
  });
  await new Promise(requestAnimationFrame);
  const totalMs = performance.now() - start;
  $('result-label').textContent = `${result.results.length} 条线索 · ${Math.round(totalMs)} ms`;
  record({ phase: 'query', query: text, backend: modelInfo.backend, dtype: modelInfo.dtype, embeddingMs: embedded.embeddingMs, seekdbMs: result.seekdbMs, totalMs }); status('检索完成');
}); };
$('export').onclick = () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify({ userAgent: navigator.userAgent, hardwareConcurrency: navigator.hardwareConcurrency, metrics }, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = 'stepfun-measurements.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
const drop = document.querySelector('.drop');
drop.ondragover = e => { e.preventDefault(); };
drop.ondrop = e => { e.preventDefault(); if (!locked) $('files').files = e.dataTransfer.files; };
run(async () => {
  await api('/health'); $('db').textContent = 'seekdb 已连接'; await list();
  if (!$('kb').options.length) { await api('/kbs', { name: 'sf_my_knowledge' }); await list(); }
});
// The benchmark uses the same browser worker and request client as the application.
window.stepfun = { Embedder, api, chunks, recordId };
