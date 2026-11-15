import { chromium } from 'playwright';
console.log('launching');
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
console.log('launched');
const page = await browser.newPage();
page.on('console', msg => console.log('browser:', msg.type(), msg.text()));
page.on('pageerror', error => console.log('PAGE ERROR', error.message));
page.on('response', r => { if (r.status() >= 400) console.log('HTTP', r.status(), r.url()); });
try {
  await page.goto(process.env.APP_URL || 'http://127.0.0.1:5173');
  console.log('page loaded');
  await page.waitForFunction(() => window.stepfun && document.querySelector('#kb').options.length);
  console.log(await page.evaluate(async () => {
    const e = new window.stepfun.Embedder(p => console.log(JSON.stringify(p)));
    const load = await e.load({backend:'wasm'}); const result = await e.embed(['浏览器中运行本地模型']);
    e.dispose(); return { load, dimension: result.embeddings[0].length, ms: result.embeddingMs };
  }));
  await page.screenshot({ path: 'reports/app.png', fullPage: true });
} finally { await browser.close(); }
