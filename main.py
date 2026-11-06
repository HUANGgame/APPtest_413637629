from fastapi import FastAPI,HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import random,secrets,time
app=FastAPI(); rooms={}
SCENES={'自由跑團':['開場','調查','發展','高潮','結局'],'營火':['露營導入','白天準備','河','樹林','營火','天黑守火','結局']}
def code():
  while 1:
    c=''.join(random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
    if c not in rooms:return c
def auth(r,t):
  if t==r['kp']:return 'kp','kp'
  for p in r['ps']:
    if p['token']==t:return 'guest',p['id']
  raise HTTPException(401,'無效身分')
def pub(r,t):
  role,aid=auth(r,t)
  d={k:r[k] for k in ['code','name','scenario','limit','scene','scenes','chat','clues','ended']};d['role']=role;d['aid']=aid
  d['players']=[{k:p[k] for k in ['id','name','char','hp','mp','san']} for p in r['ps']]
  if role=='kp':d['note']=r['note'];d['hidden']=r['hidden'][-30:]
  return d
class Create(BaseModel):name:str;scenario:str='自由跑團';limit:int=4
class Join(BaseModel):code:str;name:str
class T(BaseModel):token:str
class Chat(T):text:str
class Roll(T):label:str='d100';target:int|None=None;hidden:bool=False
class Limit(T):limit:int
class Char(T):pid:str;char:str='';hp:int=10;mp:int=10;san:int=50
class Scene(T):scene:str
class Clue(T):text:str
class Note(T):text:str=''
@app.get('/',response_class=HTMLResponse)
def home():return HTMLResponse(HTML,headers={'Cache-Control':'no-store, max-age=0'})
@app.head('/')
def home_head():return HTMLResponse('',headers={'Cache-Control':'no-store, max-age=0'})
@app.get('/health')
def health():return {'ok':1,'rooms':len(rooms)}
@app.post('/api/create')
def create(x:Create):
  if not 1<=x.limit<=50:raise HTTPException(400,'人數需 1~50')
  c=code();k=secrets.token_urlsafe(20);sc=SCENES.get(x.scenario,SCENES['自由跑團'])
  rooms[c]={'code':c,'name':x.name or '未命名房間','scenario':x.scenario,'limit':x.limit,'kp':k,'ps':[],'scene':sc[0],'scenes':sc,'chat':[{'who':'系統','text':'房間已建立'}],'clues':[],'hidden':[],'note':'','ended':False}
  return {'code':c,'token':k}
@app.post('/api/join')
def join(x:Join):
  r=rooms.get(x.code.upper());
  if not r:raise HTTPException(404,'找不到房間')
  if r['ended']:raise HTTPException(409,'房間已結束')
  if len(r['ps'])>=r['limit']:raise HTTPException(409,'房間已滿')
  p={'id':secrets.token_hex(5),'token':secrets.token_urlsafe(20),'name':x.name or '訪客','char':'','hp':10,'mp':10,'san':50};r['ps'].append(p);r['chat'].append({'who':'系統','text':p['name']+' 加入房間'})
  return {'code':r['code'],'token':p['token']}
@app.get('/api/room/{c}')
def state(c:str,token:str):
  r=rooms.get(c.upper());
  if not r:raise HTTPException(404,'找不到房間')
  return pub(r,token)
@app.post('/api/room/{c}/chat')
def chat(c:str,x:Chat):
  r=rooms.get(c.upper());
  if not r:raise HTTPException(404,'找不到房間')
  role,aid=auth(r,x.token)
  if r['ended']:raise HTTPException(409,'房間已結束')
  who='KP' if role=='kp' else next(p['name'] for p in r['ps'] if p['id']==aid);r['chat'].append({'who':who,'text':x.text[:500]});return {'ok':1}
@app.post('/api/room/{c}/roll')
def roll(c:str,x:Roll):
  r=rooms.get(c.upper());role,aid=auth(r,x.token)
  if r['ended']:raise HTTPException(409,'房間已結束')
  v=random.randint(1,100);res=''
  if x.target:res='極限成功' if v<=x.target//5 else '困難成功' if v<=x.target//2 else '成功' if v<=x.target else '失敗'
  who='KP' if role=='kp' else next(p['name'] for p in r['ps'] if p['id']==aid);m={'who':who,'text':f'🎲 {x.label}: {v}'+(f' → {res}' if res else '')}
  if x.hidden and role=='kp':r['hidden'].append(m)
  else:r['chat'].append(m)
  return {'value':v,'result':res,'hidden':x.hidden and role=='kp'}
@app.post('/api/room/{c}/limit')
def limit(c:str,x:Limit):
  r=rooms.get(c.upper());role,_=auth(r,x.token)
  if role!='kp':raise HTTPException(403,'只有 KP 可以修改')
  if x.limit<len(r['ps']) or not 1<=x.limit<=50:raise HTTPException(409,'人數上限無效')
  r['limit']=x.limit;return {'ok':1}
@app.post('/api/room/{c}/char')
def ch(c:str,x:Char):
  r=rooms.get(c.upper());role,aid=auth(r,x.token);p=next((p for p in r['ps'] if p['id']==x.pid),None)
  if not p:raise HTTPException(404,'找不到角色')
  if role!='kp' and aid!=x.pid:raise HTTPException(403,'不能修改別人角色')
  if r['ended']:raise HTTPException(409,'房間已結束')
  p.update(char=x.char[:60],hp=x.hp,mp=x.mp,san=x.san);return {'ok':1}
@app.post('/api/room/{c}/scene')
def scene(c:str,x:Scene):
  r=rooms.get(c.upper());role,_=auth(r,x.token)
  if role!='kp':raise HTTPException(403,'只有 KP 可以切換場景')
  r['scene']=x.scene;r['chat'].append({'who':'KP','text':'場景切換：'+x.scene});return {'ok':1}
@app.post('/api/room/{c}/clue')
def clue(c:str,x:Clue):
  r=rooms.get(c.upper());role,_=auth(r,x.token)
  if role!='kp':raise HTTPException(403,'只有 KP 可以公開線索')
  r['clues'].append(x.text[:500]);r['chat'].append({'who':'KP','text':'🔎 線索：'+x.text[:500]});return {'ok':1}
@app.post('/api/room/{c}/note')
def note(c:str,x:Note):
  r=rooms.get(c.upper());role,_=auth(r,x.token)
  if role!='kp':raise HTTPException(403,'只有 KP 可以修改')
  r['note']=x.text[:5000];return {'ok':1}
@app.post('/api/room/{c}/end')
def end(c:str,x:T):
  r=rooms.get(c.upper());role,_=auth(r,x.token)
  if role!='kp':raise HTTPException(403,'只有 KP 可以結束房間')
  r['ended']=True;r['chat'].append({'who':'系統','text':'房間已結束'});return {'ok':1}
HTML='''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>克蘇魯調查局</title><style>body{margin:0;background:#07110d;color:#eef7f0;font-family:system-ui}.w{max-width:980px;margin:auto;padding:20px 16px 80px}.hero{text-align:center;padding:28px 0}.grid,.room{display:grid;grid-template-columns:1fr 1fr;gap:14px}.card{background:#0d1d16;border:1px solid #294b3a;border-radius:16px;padding:16px;margin-bottom:14px}input,select,textarea,button{font:inherit}input,select,textarea{width:100%;box-sizing:border-box;margin:5px 0 10px;padding:11px;border-radius:10px;border:1px solid #315642;background:#09140f;color:white}button{padding:11px 14px;border:0;border-radius:10px;background:#7dd3a7;color:#062015;font-weight:800}.alt{background:#173629;color:white}.bad{background:#7a2929;color:white}.hide{display:none!important}.chat{height:48vh;overflow:auto;border:1px solid #294b3a;border-radius:12px;padding:10px}.m{margin:0 0 9px}.who{color:#7dd3a7;font-weight:800}.row{display:flex;gap:8px}.row>*{flex:1}.small{color:#9db1a5;font-size:13px}.pill{display:inline-block;border:1px solid #294b3a;border-radius:99px;padding:5px 9px;margin:3px}.tabs{display:flex;gap:7px;margin:10px 0}.tabs button{background:#173629;color:white}.sec{display:none}.sec.on{display:block}.bottom{display:none;position:fixed;bottom:0;left:0;right:0;background:#0b1812;padding:8px;grid-template-columns:repeat(3,1fr);gap:8px}@media(max-width:720px){.grid,.room{grid-template-columns:1fr}.bottom{display:grid}.desktop{display:none}}</style></head><body><div class="w"><section id="home"><div class="hero"><h1>克蘇魯調查局</h1><div class="small">CoC 7 文字跑團｜訪客免帳號 · 修正版 v2</div></div><div class="grid"><div class="card"><h2>KP 開房</h2><label>房間名稱</label><input id="rn"><label>劇本</label><select id="sc"><option>自由跑團</option><option>營火</option></select><label>調查員人數上限</label><input id="lim" type="number" min="1" max="50" value="4"><button onclick="createRoom()">建立房間</button></div><div class="card"><h2>訪客加入</h2><label>暱稱</label><input id="gn"><label>房號</label><input id="rc"><button onclick="joinRoom()">加入房間</button></div></div><div id="err"></div></section><section id="game" class="hide"><div><h2 id="title"></h2><span class="pill" id="scene"></span><span class="pill" id="count"></span><span class="pill" id="role"></span></div><div id="ended" class="card hide">房間已結束，操作已停用。</div><div class="tabs desktop"><button onclick="tab('story')">故事</button><button onclick="tab('chars')">角色</button><button onclick="tab('dice')">擲骰</button><button class="kp" onclick="tab('kp')">KP 主持台</button></div><div class="room"><main><div id="story" class="sec on"><div id="chat" class="chat"></div><div class="row"><input id="ci" placeholder="輸入訊息"><button onclick="send()">送出</button></div></div><div id="chars" class="sec"><div class="card"><h2>角色</h2><div id="players"></div></div></div><div id="dice" class="sec"><div class="card"><h2>擲骰</h2><label>標籤</label><input id="lab" value="偵查"><label>目標值</label><input id="tar" type="number" placeholder="例如 60"><label class="kp"><input id="hid" type="checkbox" style="width:auto"> KP 暗骰</label><br><button onclick="roll()">擲 d100</button><div id="rr"></div></div></div><div id="kp" class="sec kp"><div class="card"><h2>KP 主持台</h2><label>調查員人數上限</label><div class="row"><input id="nl" type="number" min="1" max="50"><button onclick="setLimit()">修改</button></div><label>場景</label><select id="ss"></select><button onclick="setScene()">切換場景</button><label>公開線索</label><div class="row"><input id="cl"><button onclick="addClue()">公開</button></div><label>KP 私人筆記</label><textarea id="nt"></textarea><button class="alt" onclick="saveNote()">儲存筆記</button><hr><button class="bad" onclick="endRoom()">結束房間</button></div></div></main><aside><div class="card"><h3>線索</h3><div id="clues"></div></div><div class="card"><h3>玩家</h3><div id="side"></div></div></aside></div></section></div><div id="bottom" class="bottom hide"><button onclick="tab('story')">故事</button><button onclick="tab('chars')">角色</button><button onclick="tab('dice')">擲骰</button></div><script>let S={code:'',token:'',st:null};const $=x=>document.getElementById(x);async function api(u,o={}){let r=await fetch(u,{headers:{'Content-Type':'application/json'},...o}),d={};try{d=await r.json()}catch{}if(!r.ok)throw Error(d.detail||'操作失敗');return d}function error(e){$('err').innerHTML='<div class="card">'+e+'</div>'}async function createRoom(){try{let d=await api('/api/create',{method:'POST',body:JSON.stringify({name:$('rn').value||'未命名房間',scenario:$('sc').value,limit:+$('lim').value})});enter(d)}catch(e){error(e.message)}}async function joinRoom(){try{let d=await api('/api/join',{method:'POST',body:JSON.stringify({code:$('rc').value.toUpperCase(),name:$('gn').value||'訪客'})});enter(d)}catch(e){error(e.message)}}function enter(d){S.code=d.code;S.token=d.token;localStorage.removeItem('coc');localStorage.setItem('coc-v2',JSON.stringify(d));$('home').classList.add('hide');$('game').classList.remove('hide');$('bottom').classList.remove('hide');refresh();setInterval(refresh,1500)}async function refresh(){if(!S.code)return;try{S.st=await api('/api/room/'+S.code+'?token='+encodeURIComponent(S.token));render()}catch(e){localStorage.removeItem('coc-v2');S.code='';S.token='';document.getElementById('game').classList.add('hide');document.getElementById('bottom').classList.add('hide');document.getElementById('home').classList.remove('hide');error('原房間已失效，請重新開房或加入。')}}function render(){let s=S.st;$('title').textContent=s.name+' · '+s.code;$('scene').textContent='場景：'+s.scene;$('count').textContent='調查員 '+s.players.length+'/'+s.limit;$('role').textContent=s.role==='kp'?'KP':'訪客';document.querySelectorAll('.kp').forEach(x=>x.classList.toggle('hide',s.role!=='kp'));$('ended').classList.toggle('hide',!s.ended);$('ci').disabled=s.ended;document.querySelector('#story button').disabled=s.ended;document.querySelector('#dice button').disabled=s.ended;$('chat').innerHTML=s.chat.map(m=>'<div class="m"><span class="who">'+m.who+'</span> '+m.text+'</div>').join('');$('chat').scrollTop=999999;$('clues').innerHTML=s.clues.length?s.clues.map(x=>'• '+x).join('<br>'):'尚無公開線索';$('side').innerHTML=s.players.map(p=>'<div><b>'+p.name+'</b><div class="small">'+(p.char||'未設定角色')+' · HP '+p.hp+' / SAN '+p.san+'</div></div><br>').join('')||'尚無調查員';$('players').innerHTML=s.players.map(p=>card(p,s.role==='kp'||p.id===s.aid)).join('');$('nl').value=s.limit;$('ss').innerHTML=s.scenes.map(x=>'<option '+(x===s.scene?'selected':'')+'>'+x+'</option>').join('');if(s.role==='kp')$('nt').value=s.note||''}function card(p,can){return `<div class="card"><b>${p.name}</b><label>角色名稱</label><input id="n${p.id}" value="${p.char||''}" ${can?'':'disabled'}><div class="row"><input id="h${p.id}" type="number" value="${p.hp}" ${can?'':'disabled'}><input id="m${p.id}" type="number" value="${p.mp}" ${can?'':'disabled'}><input id="s${p.id}" type="number" value="${p.san}" ${can?'':'disabled'}></div><div class="small">HP / MP / SAN</div>${can?`<button onclick="saveChar('${p.id}')">儲存</button>`:''}</div>`}async function send(){let t=$('ci').value.trim();if(!t)return;try{await api('/api/room/'+S.code+'/chat',{method:'POST',body:JSON.stringify({token:S.token,text:t})});$('ci').value='';refresh()}catch(e){alert(e.message)}}async function roll(){try{let t=$('tar').value?+$('tar').value:null,d=await api('/api/room/'+S.code+'/roll',{method:'POST',body:JSON.stringify({token:S.token,label:$('lab').value,target:t,hidden:$('hid').checked})});$('rr').textContent=d.hidden?'暗骰完成':'結果：'+d.value+(d.result?' · '+d.result:'');refresh()}catch(e){alert(e.message)}}async function saveChar(id){try{await api('/api/room/'+S.code+'/char',{method:'POST',body:JSON.stringify({token:S.token,pid:id,char:$('n'+id).value,hp:+$('h'+id).value,mp:+$('m'+id).value,san:+$('s'+id).value})});refresh()}catch(e){alert(e.message)}}async function setLimit(){try{await api('/api/room/'+S.code+'/limit',{method:'POST',body:JSON.stringify({token:S.token,limit:+$('nl').value})});refresh()}catch(e){alert(e.message)}}async function setScene(){await api('/api/room/'+S.code+'/scene',{method:'POST',body:JSON.stringify({token:S.token,scene:$('ss').value})});refresh()}async function addClue(){await api('/api/room/'+S.code+'/clue',{method:'POST',body:JSON.stringify({token:S.token,text:$('cl').value})});$('cl').value='';refresh()}async function saveNote(){await api('/api/room/'+S.code+'/note',{method:'POST',body:JSON.stringify({token:S.token,text:$('nt').value})});alert('已儲存')}async function endRoom(){if(confirm('確定結束房間？')){await api('/api/room/'+S.code+'/end',{method:'POST',body:JSON.stringify({token:S.token})});refresh()}}function tab(id){document.querySelectorAll('.sec').forEach(x=>x.classList.remove('on'));$(id).classList.add('on');scrollTo(0,0)}try{let d=JSON.parse(localStorage.getItem('coc-v2')||'null');if(d&&d.code&&d.token)enter(d)}catch(e){}</script></body></html>'''