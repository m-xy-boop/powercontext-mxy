import { pipeline, env } from '/vendor/transformers.js';
env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = new URL('/models/', import.meta.url).href;
env.backends.onnx.wasm.wasmPaths = new URL('/vendor/', import.meta.url).href;
env.backends.onnx.wasm.numThreads = Math.min(4, self.navigator.hardwareConcurrency || 1);
let extractor, backend, dtype, reason, requested, requestedBackend, loadInfo;
const model = 'Xenova/bge-small-zh-v1.5';
async function load(options = {}) {
  const nextDtype = options.dtype || 'quantized';
  if (extractor && requested === nextDtype && requestedBackend === options.backend) return loadInfo;
  if (extractor) { await extractor.dispose(); extractor = null; }
  requested = nextDtype;
  requestedBackend = options.backend;
  const start = performance.now();
  const files = new Map();
  const progress_callback = data => {
    if (data.file) {
      const timing = files.get(data.file) || { file: data.file, startMs: performance.now() - start };
      if (data.status === 'done') timing.doneMs = performance.now() - start;
      if (data.total) timing.bytes = data.total;
      files.set(data.file, timing);
    }
    self.postMessage({ type: 'progress', data });
  };
  reason = '';
  if (options.backend !== 'wasm') {
    try {
      if (!self.navigator.gpu || !await self.navigator.gpu.requestAdapter()) throw new Error('浏览器未提供 WebGPU adapter');
      dtype = nextDtype === 'quantized' ? 'fp16' : nextDtype;
      extractor = await pipeline('feature-extraction', model, { device: 'webgpu', dtype, progress_callback });
      backend = 'webgpu';
    } catch (e) { reason = String(e.message); }
  } else reason = '按测试配置使用 WASM';
  if (!extractor) {
    dtype = nextDtype === 'quantized' ? 'q8' : nextDtype;
    extractor = await pipeline('feature-extraction', model, { device: 'wasm', dtype, progress_callback });
    backend = 'wasm';
  }
  const loadMs = performance.now() - start;
  const fileTimings = [...files.values()];
  const modelFetchMs = Math.max(0, ...fileTimings.map(f => f.doneMs || 0));
  loadInfo = { backend, dtype, reason, loadMs, modelFetchMs, sessionInitMs: loadMs - modelFetchMs,
    fileTimings, threads: env.backends.onnx.wasm.numThreads };
  return loadInfo;
}
self.onmessage = async ({ data: { id, type, options, texts } }) => {
  try {
    let result;
    if (type === 'load') result = await load(options);
    else {
      if (!extractor) throw new Error('请先加载模型');
      const start = performance.now();
      const input = texts.map(t => options?.query ? `为这个句子生成表示以用于检索相关文章：${t}` : t);
      const output = await extractor(input, { pooling: 'cls', normalize: true, truncation: true, max_length: 256 });
      result = { embeddings: output.tolist(), embeddingMs: performance.now() - start, backend, dtype };
    }
    self.postMessage({ id, result });
  } catch (e) { self.postMessage({ id, error: e.message || String(e) }); }
};
