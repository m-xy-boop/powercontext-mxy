import json
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path('evidence/independent-review');out.mkdir(exist_ok=True)
report=[]
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,args=['--no-sandbox'])
 for width in [390,768,1440]:
  page=b.new_page(viewport={'width':width,'height':900},device_scale_factor=1)
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  for name,path in [('home','/'),('diagnose','/diagnose'),('memory','/memory'),('verify','/verify')]:
   r=page.goto('http://127.0.0.1:8642'+path,wait_until='networkidle')
   page.wait_for_timeout(300)
   size=page.evaluate('({scroll:document.documentElement.scrollWidth,width:innerWidth})')
   row={'page':name,'width':width,'http_status':r.status,'layout':size,'js_errors':list(errors)}
   report.append(row)
   if width==390:page.screenshot(path=str(out/f'{name}-390.png'),full_page=True)
  page.close()
 b.close()
(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
