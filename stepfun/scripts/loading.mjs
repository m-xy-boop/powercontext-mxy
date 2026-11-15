import { chromium } from 'playwright';
import { writeFile } from 'node:fs/promises';
const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
const configurations=[];
try {
  for(const config of [{dtype:'fp32',backend:'wasm'},{dtype:'q8',backend:'wasm'},{dtype:'quantized',backend:'auto'}]) {
    const context=await browser.newContext();const page=await context.newPage();
    await page.goto(process.env.APP_URL||'http://127.0.0.1:5173');await page.waitForFunction(()=>window.stepfun);
    const runs=await page.evaluate(async config=>{
      const result=[];
      for(let i=0;i<3;i++) { const e=new window.stepfun.Embedder();result.push({repeat:i,...await e.load(config)}); e.dispose(); }
      return result;
    },config);
    configurations.push({config,runs});await context.close();
  }
  await writeFile('reports/loading.json',JSON.stringify({at:new Date().toISOString(),browser:browser.version(),note:'每配置独立全新浏览器 context，第一轮为空缓存，后两轮复用 Cache Storage；下载指同源 localhost 资源读取，不代表互联网速度。',configurations},null,2));
} finally {await browser.close();}
