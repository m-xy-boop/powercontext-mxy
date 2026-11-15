import { chromium } from 'playwright';
import { writeFile } from 'node:fs/promises';
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
try {
  const page = await browser.newPage(); await page.goto(process.env.APP_URL || 'http://127.0.0.1:5173');
  await page.waitForFunction(() => window.stepfun && document.querySelector('#kb').options.length);
  const result = await page.evaluate(async () => {
    const { api, Embedder } = window.stepfun; const data = await api('/dataset');
    const e = new Embedder(); await e.load({dtype:'q8',backend:'wasm'});
    await e.embed([data.documents[0].text]);
    const configurations = [];
    for (const config of [{ batch:1, refresh:'each' }, { batch:1, refresh:'group' }, { batch:8, refresh:'group' }]) {
      const name = `sf_opt_${Date.now().toString(36)}_${configurations.length}`; await api('/kbs',{name});
      const runs = [];
      for (let repeat=0;repeat<3;repeat++) {
        let embeddingMs=0,writeMs=0,refreshMs=0,visibilityMs=0; const start=performance.now(); const records=[];
        async function flush() {
          const r=await api(`/kbs/${name}/import`,{records:records.splice(0)});
          writeMs+=r.writeMs; refreshMs+=r.refreshMs; visibilityMs+=r.visibilityMs;
        }
        for (let i=0;i<data.documents.length;i+=config.batch) {
          const docs=data.documents.slice(i,i+config.batch); const r=await e.embed(docs.map(d=>d.text)); embeddingMs+=r.embeddingMs;
          records.push(...docs.map((d,j)=>({id:d.id,source:d.id,text:d.text,embedding:r.embeddings[j],dtype:'q8'})));
          if(config.refresh==='each') await flush();
        }
        if(records.length) await flush();
        runs.push({repeat,embeddingMs,writeMs,refreshMs,visibilityMs,totalMs:performance.now()-start});
      }
      configurations.push({config,name,runs});
    }
    e.dispose(); return {at:new Date().toISOString(),dtype:'q8',backend:'wasm',documents:data.documents.length,configurations};
  });
  await writeFile('reports/optimization.json',JSON.stringify(result,null,2)); console.log(JSON.stringify(result));
} finally { await browser.close(); }
