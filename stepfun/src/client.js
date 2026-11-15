export async function api(path, body) {
  const response = await fetch(`/api${path}`, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || response.statusText);
  return result;
}
export class Embedder {
  constructor(progress = () => {}) {
    this.worker = new Worker('/embedding-worker.js', { type: 'module' });
    this.requests = new Map(); this.next = 0;
    this.worker.onmessage = ({ data }) => {
      if (data.type === 'progress') return progress(data.data);
      const req = this.requests.get(data.id); if (!req) return;
      this.requests.delete(data.id);
      data.error ? req.reject(new Error(data.error)) : req.resolve(data.result);
    };
    this.worker.onerror = e => { for (const r of this.requests.values()) r.reject(new Error(e.message)); this.requests.clear(); };
  }
  call(type, args = {}) {
    const id = ++this.next;
    return new Promise((resolve, reject) => { this.requests.set(id, { resolve, reject }); this.worker.postMessage({ id, type, ...args }); });
  }
  load(options = {}) { return this.call('load', { options }); }
  embed(texts, query = false) { return this.call('embed', { texts, options: { query } }); }
  dispose() { this.worker.terminate(); }
}
export function chunks(text, size = 180, overlap = 32) {
  const chars = Array.from(text.replace(/\r\n/g, '\n').trim());
  const result = [];
  for (let i = 0; i < chars.length; i += size - overlap) {
    const value = chars.slice(i, i + size).join('').trim();
    if (value) result.push(value);
    if (i + size >= chars.length) break;
  }
  return result;
}
export async function recordId(source, index, text) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${source}\0${index}\0${text}`));
  return Array.from(new Uint8Array(bytes), x => x.toString(16).padStart(2, '0')).join('');
}
