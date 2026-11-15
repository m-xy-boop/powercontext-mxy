import { mkdir, writeFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
const model = 'Xenova/bge-small-zh-v1.5';
const revision = '75c43b069aac4d136ba6bc1122f995fedcfd2781';
const host = (process.env.HF_ENDPOINT || 'https://huggingface.co').replace(/\/$/, '');
const root = `public/models/${model}`;
await mkdir(`${root}/onnx`, { recursive: true });
const files = ['config.json','tokenizer.json','tokenizer_config.json','special_tokens_map.json','quantize_config.json','README.md','onnx/model.onnx','onnx/model_quantized.onnx','onnx/model_fp16.onnx'];
const manifest = { model, revision, files: {} };
for (const file of files) {
  const path = `${root}/${file}`;
  const response = await fetch(`${host}/${model}/resolve/${revision}/${file}`);
  if (!response.ok) throw new Error(`${file}: HTTP ${response.status}`);
  const { createWriteStream } = await import('node:fs');
  await pipeline(Readable.fromWeb(response.body), createWriteStream(path));
  const hash = createHash('sha256'); for await (const chunk of createReadStream(path)) hash.update(chunk);
  manifest.files[file] = { bytes: (await stat(path)).size, sha256: hash.digest('hex') };
  console.log(file, manifest.files[file]);
}
await writeFile('reports/model-manifest.json', JSON.stringify(manifest, null, 2));
