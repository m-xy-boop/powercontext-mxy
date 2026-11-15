import { readFile, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
const source = await readFile('TECHNICAL.md', 'utf8');
const escape = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
function inline(value) {
  return escape(value).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1');
}
const paragraphs = source.trim().split(/\n\n+/).map(p => p.startsWith('# ') ? `<h1>${inline(p.slice(2))}</h1>` : `<p>${inline(p)}</p>`).join('\n');
const html = `<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><title>星屿一页技术说明</title>
<style>@page{size:A4;margin:13mm}body{font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif;font-size:9.5pt;line-height:1.55;color:#233d34}h1{font-size:18pt;margin:0 0 12pt;border-bottom:2px solid #315d48;padding-bottom:8pt}p{margin:0 0 8pt}code{font-family:monospace;font-size:8pt;overflow-wrap:anywhere}strong{color:#204a37}</style>${paragraphs}</html>`;
await writeFile('reports/technical.html', html);
const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
try {
  const page = await browser.newPage(); await page.setContent(html); await page.evaluate(() => document.fonts.ready);
  const pdf = await page.pdf({path:'reports/technical.pdf',format:'A4',printBackground:true,preferCSSPageSize:true});
  const pages = [...pdf.toString('latin1').matchAll(/\/Type \/Page\b/g)].length;
  if (pages !== 1) throw new Error(`技术说明应为一页，实际 ${pages} 页`);
  console.log('技术说明 PDF 已生成：1 页 A4');
} finally {await browser.close();}
