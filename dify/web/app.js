const $ = s => document.querySelector(s);
const id = () => crypto.randomUUID();
let session = id(), selected = 'umbrella', pending = null;
$('#player').value = localStorage.getItem('echo-player') || `traveler-${id().slice(0,8)}`;
localStorage.setItem('echo-player', $('#player').value);
function display(text) {
  // Only create fixed markup; all generated text remains escaped.
  const escaped = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  $('#story').innerHTML = escaped.replace(/^### (.*)$/gm,'<h3>$1</h3>')
    .replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>').replace(/^---$/gm,'<hr>')
    .replace(/&lt;small&gt;(.*?)&lt;\/small&gt;/gs,'<small>$1</small>');
}
async function act(action) {
  const player = $('#player').value.trim();
  if (!/^[a-zA-Z0-9_-]{1,48}$/.test(player)) { $('#error').textContent='旅人编号请用 1–48 位字母、数字、下划线或短横线。'; return; }
  localStorage.setItem('echo-player',player);
  const input = {player_id:player,session_id:session,action,memory:selected,words:$('#words').value};
  const signature = JSON.stringify(input);
  if (!pending || pending.signature !== signature) pending = {signature,request:id()};
  document.querySelectorAll('button').forEach(b=>b.disabled=true);
  $('#error').textContent=''; $('#status').textContent='乌有正在翻阅旧账，请稍候……';
  try {
    const response = await fetch('/api/play',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...input,request_id:pending.request})});
    const result = await response.json();
    if(!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '服务暂时不可用。');
    display(result.story); pending=null; $('#words').value='';
    $('#status').textContent=`已记入账本 · Dify 执行 ${result.workflow_run_id}`;
  } catch(error) { $('#error').textContent=error.message; $('#status').textContent='可以重试相同行动；系统会保留请求编号，避免重复交易。'; }
  finally { document.querySelectorAll('button').forEach(b=>b.disabled=false); }
}
document.querySelectorAll('[data-memory]').forEach(button=>button.addEventListener('click',()=>{
  selected=button.dataset.memory; document.querySelectorAll('.memory').forEach(b=>b.classList.toggle('selected',b===button));
}));
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',()=>act(button.dataset.action)));
$('#visit').addEventListener('click',()=>{ session=id(); pending=null; act('enter'); });
