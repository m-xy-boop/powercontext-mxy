import express from 'express';
import { createServer as createViteServer } from 'vite';
import { SeekdbClient, SeekdbAdminClient, Schema, VectorIndexConfig, FulltextIndexConfig } from 'seekdb';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { readFile } from 'node:fs/promises';

process.chdir(fileURLToPath(new URL('.', import.meta.url)));
const config = { host: process.env.SEEKDB_HOST || '127.0.0.1', port: Number(process.env.SEEKDB_PORT || 2883),
  user: process.env.SEEKDB_USER || 'root', password: process.env.SEEKDB_PASSWORD || '' };
const database = process.env.SEEKDB_DATABASE || 'stepfun_kb';
if (!/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(database)) throw new Error('Invalid database name');
const admin = new SeekdbAdminClient(config);
if (!(await admin.listDatabases()).some(d => d.name === database)) await admin.createDatabase(database);
await admin.close();
const db = new SeekdbClient({ ...config, database, queryTimeout: 120000 });
// SDK hybridSearch uses a connection session variable. Serialize the complete operation,
// including refresh and count, so concurrent imports/searches cannot mix session state.
let queue = Promise.resolve();
function serial(fn) { const task = queue.then(fn); queue = task.catch(() => {}); return task; }
const prefix = 'sf_';
const nameOf = name => {
  if (!/^sf_[a-zA-Z0-9_]{1,48}$/.test(name || '')) throw Object.assign(new Error('知识库名称格式无效'), { status: 400 });
  return name;
};
function vector(v) {
  if (!Array.isArray(v) || v.length !== 512 || !v.every(x => typeof x === 'number' && Number.isFinite(x)) || !v.some(x => x !== 0))
    throw Object.assign(new Error('需要浏览器生成的 512 维有效向量'), { status: 400 });
}
const app = express();
app.use((req, res, next) => {
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
  // The application is a local service; reject requests from a different origin.
  if (req.headers.origin && req.headers.origin !== `${req.protocol}://${req.headers.host}`)
    return res.status(403).json({ error: '仅接受同源请求' });
  next();
});
app.use(express.json({ limit: '12mb' }));
const api = (method, path, fn) => app[method](path, async (req, res, next) => {
  try { res.json(await serial(() => fn(req))); } catch (e) { next(e); }
});
api('get', '/api/health', async () => ({ status: 'ok', database, version: await db.execute('SELECT VERSION() AS version') }));
api('get', '/api/kbs', async () => {
  const all = await db.listCollections({ withEmbeddingFunction: false });
  return Promise.all(all.filter(c => c.name.startsWith(prefix)).map(async c => ({ name: c.name, count: await c.count() })));
});
api('post', '/api/kbs', async req => {
  const name = nameOf(req.body.name);
  if (await db.hasCollection(name)) throw Object.assign(new Error('知识库已存在'), { status: 409 });
  await db.createCollection({ name, embeddingFunction: null, schema: new Schema({
    vectorIndex: new VectorIndexConfig({ hnsw: { dimension: 512, distance: 'cosine' }, embeddingFunction: null }),
    fulltextIndex: new FulltextIndexConfig('ik', { ik_mode: 'smart' })
  }) });
  return { name, count: 0 };
});
api('post', '/api/kbs/:name/import', async req => {
  const c = await db.getCollection({ name: nameOf(req.params.name), embeddingFunction: null });
  const { records } = req.body;
  if (!Array.isArray(records) || !records.length || records.length > 256)
    throw Object.assign(new Error('每次写入 1–256 个片段'), { status: 400 });
  for (const r of records) {
    vector(r.embedding);
    if (typeof r.id !== 'string' || r.id.length > 128 || typeof r.text !== 'string' || !r.text.trim() || r.text.length > 4000)
      throw Object.assign(new Error('片段 ID / 正文格式无效'), { status: 400 });
  }
  const start = performance.now();
  await c.upsert({ ids: records.map(r => r.id), documents: records.map(r => r.text), embeddings: records.map(r => r.embedding),
    metadatas: records.map(r => ({ source: String(r.source || '未命名').slice(0, 255), chunk: r.chunk || 0,
      model: 'Xenova/bge-small-zh-v1.5', revision: '75c43b069aac4d136ba6bc1122f995fedcfd2781', dtype: String(r.dtype || 'q8'), importedAt: new Date().toISOString() })) });
  const writeMs = performance.now() - start;
  const refreshStart = performance.now();
  await c.refresh_index();
  const refreshMs = performance.now() - refreshStart;
  // Verify full-text visibility too. A successful response guarantees every upserted
  // record is visible to search, rather than treating an asynchronous write as ready.
  const deadline = performance.now() + 20000;
  for (const r of records) {
    const probe = r.text.slice(0, 80);
    while (true) {
      const fulltext = await c.get({ ids: [r.id], whereDocument: { $contains: probe }, include: ['documents'] });
      const found = await c.hybridSearch({ query: { whereDocument: { $contains: probe }, where: { source: String(r.source || '未命名').slice(0, 255) }, nResults: 256 },
        knn: { queryEmbeddings: r.embedding, nResults: 256 }, nResults: 256 });
      if (found.ids[0].includes(r.id) && fulltext.ids.includes(r.id)) break;
      if (performance.now() > deadline) throw new Error('写入已保存，但索引可见性验证超时；请稍后重试刷新');
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }
  return { written: records.length, count: await c.count(), writeMs, refreshMs, visibilityMs: performance.now() - refreshStart - refreshMs };
});
api('post', '/api/kbs/:name/search', async req => {
  const { text, embedding, k = 5 } = req.body;
  vector(embedding);
  if (typeof text !== 'string' || !text.trim() || text.length > 1000 || !Number.isInteger(k) || k < 1 || k > 20)
    throw Object.assign(new Error('查询格式无效'), { status: 400 });
  const c = await db.getCollection({ name: nameOf(req.params.name), embeddingFunction: null });
  const start = performance.now();
  const result = await c.hybridSearch({ query: { whereDocument: { $contains: text }, nResults: 50 },
    knn: { queryEmbeddings: embedding, nResults: 50 }, rank: { rrf: { rankWindowSize: 50, rankConstant: 60 } },
    nResults: k, include: ['documents', 'metadatas', 'distances'] });
  const seekdbMs = performance.now() - start;
  return { seekdbMs, results: result.ids[0].map((id, i) => ({ id, text: result.documents?.[0]?.[i],
    metadata: result.metadatas?.[0]?.[i], score: result.distances?.[0]?.[i] })) };
});
api('post', '/api/kbs/:name/refresh', async req => {
  const c = await db.getCollection({ name: nameOf(req.params.name), embeddingFunction: null });
  await c.refresh_index(); return { count: await c.count() };
});
api('get', '/api/dataset', async () => JSON.parse(await readFile('data/evaluation.json', 'utf8')));
app.use('/vendor', express.static('node_modules/@huggingface/transformers/dist', { maxAge: '1d' }));
app.get('/embedding-worker.js', (req, res) => res.sendFile(fileURLToPath(new URL('src/embedding-worker.js', import.meta.url))));
app.use('/models', express.static('public/models', { maxAge: '1d' }));
if (process.env.NODE_ENV === 'production') app.use(express.static('dist'));
else { const vite = await createViteServer({ server: { middlewareMode: true }, appType: 'spa' }); app.use(vite.middlewares); }
app.use((err, req, res, next) => {
  console.error(err.message);
  res.status(err.status || 500).json({ error: err.message });
});
const port = Number(process.env.PORT || 5173);
const server = app.listen(port, '127.0.0.1', () => console.log(`星屿知识库 http://127.0.0.1:${port}`));
async function stop() { server.close(); await queue; await db.close(); process.exit(); }
process.on('SIGINT', stop); process.on('SIGTERM', stop);
