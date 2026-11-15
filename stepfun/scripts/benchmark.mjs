import { chromium } from 'playwright';
import { writeFile, stat, readFile } from 'node:fs/promises';
import os from 'node:os';
const base = process.env.APP_URL || 'http://127.0.0.1:5173';
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--enable-unsafe-webgpu', '--enable-features=Vulkan', '--use-angle=vulkan'] });
const context = await browser.newContext();
const page = await context.newPage();
page.on('pageerror', e => console.error('pageerror', e.message));
const requests = new Set(); page.on('request', r => requests.add(r.url()));
const stamp = Date.now().toString(36);
try {
  await page.goto(base); await page.waitForFunction(() => window.stepfun && document.querySelector('#kb').options.length);
  const environment = await page.evaluate(async () => {
    const adapter = await navigator.gpu?.requestAdapter();
    return { userAgent: navigator.userAgent, threads: navigator.hardwareConcurrency, crossOriginIsolated,
      webgpu: !!navigator.gpu, adapter: adapter ? { info: { vendor: adapter.info.vendor, architecture: adapter.info.architecture,
        device: adapter.info.device, description: adapter.info.description }, features: [...adapter.features] } : null };
  });
  const dataset = JSON.parse(await readFile('data/evaluation.json', 'utf8'));
  const configurations = process.env.RESUME ? JSON.parse(await readFile('reports/benchmark.partial.json', 'utf8')) : [];
  const configs = [{ dtype: 'fp32', backend: 'wasm', batch: 1 }, { dtype: 'q8', backend: 'wasm', batch: 1 }, { dtype: 'q8', backend: 'wasm', batch: 8 }, { dtype: 'fp16', backend: 'auto', batch: 8 }, { dtype: 'quantized', backend: 'auto', batch: 8 }];
  for (const config of configs.slice(configurations.length)) {
    console.log('Running', config);
    const name = `sf_bench_${stamp}_${configurations.length}`;
    const output = await page.evaluate(async ({ config, name, dataset }) => {
      const { Embedder, api } = window.stepfun;
      const e = new Embedder(); const load = await e.load(config);
      await api('/kbs', { name });
      const runs = [];
      for (let repeat = 0; repeat < 3; repeat++) {
        const start = performance.now(); let embeddingMs = 0, writeMs = 0, refreshMs = 0;
        const records = [];
        for (let i = 0; i < dataset.documents.length; i += config.batch) {
          const docs = dataset.documents.slice(i, i + config.batch);
          const embedded = await e.embed(docs.map(d => d.text)); embeddingMs += embedded.embeddingMs;
          records.push(...docs.map((d, j) => ({ id: d.id, source: d.id, text: d.text, embedding: embedded.embeddings[j], dtype: config.dtype })));
          if (config.batch === 1) {
            const written = await api(`/kbs/${name}/import`, { records: records.splice(0) }); writeMs += written.writeMs; refreshMs += written.refreshMs;
          }
        }
        if (records.length) { const written = await api(`/kbs/${name}/import`, { records }); writeMs += written.writeMs; refreshMs += written.refreshMs; }
        const importTotalMs = performance.now() - start;
        const queries = [];
        for (const q of dataset.queries) {
          const queryStart = performance.now();
          const embedded = await e.embed([q.text], true);
          const result = await api(`/kbs/${name}/search`, { text: q.text, embedding: embedded.embeddings[0], k: 5 });
          // Include actual result construction and the next browser frame in the end-to-end timing.
          const container = document.querySelector('#results'); container.replaceChildren();
          for (const r of result.results) { const p = document.createElement('p'); p.textContent = r.text; container.append(p); }
          await new Promise(requestAnimationFrame);
          const ids = result.results.map(r => r.id);
          const hits = ids.filter(id => q.relevant.includes(id)).length;
          const dcg = ids.reduce((sum, id, i) => sum + (q.relevant.includes(id) ? 1 / Math.log2(i + 2) : 0), 0);
          const ideal = q.relevant.slice(0, 5).reduce((sum, _, i) => sum + 1 / Math.log2(i + 2), 0);
          queries.push({ query: q.text, ids, recall5: hits / q.relevant.length, ndcg5: dcg / ideal,
            embeddingMs: embedded.embeddingMs, seekdbMs: result.seekdbMs, totalMs: performance.now() - queryStart });
        }
        runs.push({ repeat, embeddingMs, writeMs, refreshMs, importTotalMs, queries });
      }
      e.dispose(); const warm = new Embedder(); const warmLoad = await warm.load(config); warm.dispose();
      return { config, name, load, warmLoad, runs };
    }, { config, name, dataset });
    configurations.push(output);
    console.log('Done', output.load, output.runs.map(r => ({ embeddingMs: r.embeddingMs, recall: r.queries.reduce((s,q)=>s+q.recall5,0)/r.queries.length })));
    await writeFile('reports/benchmark.partial.json', JSON.stringify(configurations, null, 2));
  }
  // Disable WebGPU at the browser API boundary, then exercise the actual worker fallback.
  await context.addInitScript(() => Object.defineProperty(Navigator.prototype, 'gpu', { get: () => undefined, configurable: true }));
  const fallback = await page.evaluate(async name => {
    const blob = new Blob([`Object.defineProperty(self.navigator, 'gpu', {value: undefined}); await import('${location.origin}/embedding-worker.js'); self.postMessage({type:'boot'});`], { type: 'text/javascript' });
    const worker = new Worker(URL.createObjectURL(blob), { type: 'module' });
    return new Promise((resolve, reject) => { let loaded; worker.onmessage = async ({data}) => {
      if (data.type === 'boot') worker.postMessage({id:1,type:'load',options:{dtype:'q8',backend:'auto'}});
      if (data.error) { worker.terminate(); return reject(new Error(data.error)); }
      if (data.id === 1) { loaded = data.result; worker.postMessage({id:2,type:'embed',texts:['浏览器不支持 GPU 加速怎么办'],options:{query:true}}); }
      if (data.id === 2) {
        try { const search = await window.stepfun.api(`/kbs/${name}/search`, {text:'浏览器不支持 GPU 加速怎么办',embedding:data.result.embeddings[0],k:5});
          worker.terminate(); resolve({...loaded, dimension:data.result.embeddings[0].length, results:search.results.map(r=>r.id)});
        } catch(e) { reject(e); }
      }
    }; worker.onerror = e => reject(new Error(e.message)); });
  }, configurations.at(-1).name);
  const files = ['config.json','tokenizer.json','tokenizer_config.json','special_tokens_map.json','onnx/model.onnx','onnx/model_quantized.onnx','onnx/model_fp16.onnx'];
  const sizes = Object.fromEntries(await Promise.all(files.map(async f => [f, (await stat(`public/models/Xenova/bge-small-zh-v1.5/${f}`)).size])));
  const report = { at: new Date().toISOString(), environment: { ...environment, browser: browser.version(), os: `${os.type()} ${os.release()}`, cpu: os.cpus()[0].model, logicalCpus: os.cpus().length, memoryBytes: os.totalmem() }, dataset, sizes, configurations, fallback,
    networkRequests: [...requests], externalRequests: [...requests].filter(u => !u.startsWith(base) && !u.startsWith('blob:')) };
  await writeFile('reports/benchmark.json', JSON.stringify(report, null, 2));
  await page.screenshot({ path: 'reports/benchmark.png', fullPage: true });
} finally { await browser.close(); }
