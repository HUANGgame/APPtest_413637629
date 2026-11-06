from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any
import random, secrets, re, csv, io, urllib.request, urllib.parse, math

app = FastAPI(title='克蘇魯調查局')
rooms: Dict[str, Dict[str, Any]] = {}

SCENES = {
    '自由跑團': ['開場', '調查', '發展', '高潮', '結局'],
    '營火': ['露營導入', '白天準備', '河', '樹林', '營火', '天黑守火', '結局'],
}

DEFAULT_SKILLS = {
    '偵查': 25, '聆聽': 20, '圖書館使用': 20, '心理學': 10, '說服': 10,
    '話術': 5, '恐嚇': 15, '取悅': 15, '潛行': 20, '導航': 10,
    '生存': 10, '急救': 30, '鬥毆': 25, '投擲': 20, '閃避': 20,
}


def make_code():
    while True:
        code = ''.join(random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
        if code not in rooms:
            return code


def blank_char():
    return {
        'name': '', 'occupation': '', 'age': '',
        'attrs': {k: 0 for k in ['STR','CON','SIZ','DEX','APP','INT','POW','EDU']},
        'hp': 10, 'mp': 10, 'san': 50, 'luck': 50,
        'skills': dict(DEFAULT_SKILLS),
        'inventory': '', 'public_notes': '', 'private_notes': '',
        'visibility': 'kp_only', 'source': 'manual'
    }


def investigator_count(r):
    return sum(1 for p in r['ps'] if not p.get('co_kp') and not p.get('npc'))


def auth(r, token):
    if token == r['owner']:
        return 'owner', 'owner'
    for p in r['ps']:
        if p.get('token') == token:
            return ('kp' if p.get('co_kp') else 'guest'), p['id']
    raise HTTPException(401, '無效身分')


def is_kp(role):
    return role in ('owner', 'kp')


def sanitize_character_for_view(p, role, aid):
    can_full = is_kp(role) or aid == p['id'] or p['char'].get('visibility') == 'public'
    base = {
        'id': p['id'], 'name': p['name'], 'co_kp': bool(p.get('co_kp')),
        'npc': bool(p.get('npc')), 'char_hidden': not can_full,
        'char_name': p['char'].get('name') or '',
    }
    if can_full:
        ch = dict(p['char'])
        if not (is_kp(role) or aid == p['id']):
            ch['private_notes'] = ''
        base['char'] = ch
    return base


def pub(r, token):
    role, aid = auth(r, token)
    d = {k: r[k] for k in ['code','name','scenario','limit','scene','scenes','chat','ended']}
    d.update(role=role, aid=aid, count=investigator_count(r))
    d['players'] = [sanitize_character_for_view(p, role, aid) for p in r['ps']]
    visible_clues = []
    for clue in r['clues']:
        vis = clue.get('visibility', 'public')
        if is_kp(role) or vis == 'public' or (vis == 'player' and clue.get('target') == aid):
            visible_clues.append(clue)
    d['clues'] = visible_clues
    if is_kp(role):
        d['note'] = r['note']
        d['hidden'] = r['hidden'][-50:]
    return d


def coc_level(value: int, target: int):
    """CoC 7e percentile result tier."""
    target = max(0, min(100, int(target)))
    value = max(1, min(100, int(value)))
    if value == 1:
        return '大成功'
    if (target < 50 and value >= 96) or (target >= 50 and value == 100):
        return '大失敗'
    if target > 0 and value <= max(1, target // 5):
        return '極限成功'
    if target > 0 and value <= target // 2:
        return '困難成功'
    if target > 0 and value <= target:
        return '一般成功'
    return '失敗'


LEVEL_RANK = {'大失敗': 0, '失敗': 1, '一般成功': 2, '困難成功': 3, '極限成功': 4, '大成功': 5}
DIFFICULTY_RANK = {'normal': 2, 'hard': 3, 'extreme': 4}
DIFFICULTY_ZH = {'normal': '一般', 'hard': '困難', 'extreme': '極限'}


def meets_difficulty(level: str, difficulty: str):
    difficulty = difficulty if difficulty in DIFFICULTY_RANK else 'normal'
    return LEVEL_RANK.get(level, 0) >= DIFFICULTY_RANK[difficulty]


def percentile_candidates(extra=0, penalty=False):
    """Roll d100 with 0-2 bonus/penalty tens dice; keep same units die."""
    extra = max(0, min(2, int(extra)))
    units = random.randint(0, 9)
    tens = [random.randint(0, 9) for _ in range(1 + extra)]
    vals = []
    for t in tens:
        v = t * 10 + units
        if v == 0:
            v = 100
        vals.append(v)
    return (max(vals) if penalty else min(vals)), vals


def roll_dice_expr(expr: str):
    expr = expr.lower().replace(' ', '')
    if not expr or len(expr) > 80:
        raise ValueError('骰式無效')
    if not re.fullmatch(r'[0-9d+\-]+', expr):
        raise ValueError('只支援 NdM、+、-，例如 2d6+1')
    tokens = re.findall(r'[+\-]?[^+\-]+', expr)
    total, detail = 0, []
    if len(tokens) > 12:
        raise ValueError('骰式過長')
    for tok in tokens:
        sign = -1 if tok.startswith('-') else 1
        core = tok.lstrip('+-')
        if 'd' in core:
            a, b = core.split('d', 1)
            n = int(a or '1'); sides = int(b)
            if not 1 <= n <= 30 or not 2 <= sides <= 1000:
                raise ValueError('骰子數量或面數超出範圍')
            rs = [random.randint(1, sides) for _ in range(n)]
            total += sum(rs) * sign
            detail.append(f"{('-' if sign < 0 else '')}{n}d{sides}{rs}")
        else:
            v = int(core) * sign
            total += v; detail.append(str(v))
    return total, ' '.join(detail)


def own_player(r, aid):
    return next((p for p in r['ps'] if p['id'] == aid), None)


def resolve_target(r, aid, name, explicit=None):
    if explicit is not None:
        return max(0, min(100, int(explicit)))
    p = own_player(r, aid)
    if not p:
        raise ValueError('請提供檢定值，例如 .ra 偵查 60')
    skills = p['char'].get('skills', {})
    if name in skills:
        return int(skills[name])
    attrs = p['char'].get('attrs', {})
    key = name.upper()
    if key in attrs:
        return int(attrs[key])
    if name.lower() in ('luck',) or name == '幸運':
        return int(p['char'].get('luck', 0))
    if name.upper() == 'SAN' or name in ('心智','理智'):
        return int(p['char'].get('san', 0))
    raise ValueError(f'角色卡找不到「{name}」，請在指令最後加上數值')


def parse_check_args(rest: str):
    parts = rest.strip().split()
    if not parts:
        raise ValueError('格式：.ra 技能 [數值] [n/h/e]')
    difficulty = 'normal'
    diff_alias = {'n':'normal','normal':'normal','一般':'normal','h':'hard','hard':'hard','困難':'hard','e':'extreme','extreme':'extreme','極限':'extreme'}
    if parts and parts[-1].lower() in diff_alias:
        difficulty = diff_alias[parts.pop().lower()]
    explicit = None
    if parts and re.fullmatch(r'\d{1,3}', parts[-1]):
        explicit = int(parts.pop())
    name = ' '.join(parts).strip() or '檢定'
    return name, explicit, difficulty


def check_message(name, target, difficulty='normal', extra=0, penalty=False, prefix='🎲'):
    value, candidates = percentile_candidates(extra, penalty)
    level = coc_level(value, target)
    ok = meets_difficulty(level, difficulty)
    mode = ''
    if extra:
        mode = f"｜{'懲罰骰' if penalty else '獎勵骰'} {extra} 顆｜候選 {candidates}"
    requirement = DIFFICULTY_ZH.get(difficulty, '一般')
    mark = '✅' if ok else '❌'
    return value, level, f'{prefix} {name} {target}%｜需求：{requirement}｜{value} → {level} {mark}{mode}'


def remember_check(r, aid, name, target, difficulty, extra, penalty, value, level, pushable=True):
    if aid == 'owner':
        return
    r.setdefault('last_rolls', {})[aid] = {
        'name': name, 'target': target, 'difficulty': difficulty,
        'extra': extra, 'penalty': penalty, 'value': value, 'level': level,
        'pushable': bool(pushable), 'pushed': False,
    }


def opposed_roll_message(left_name, left_target, right_name, right_target):
    lv, _ = percentile_candidates(); rv, _ = percentile_candidates()
    ll = coc_level(lv, left_target); rl = coc_level(rv, right_target)
    lr, rr = LEVEL_RANK[ll], LEVEL_RANK[rl]
    if lr > rr:
        winner = '左方勝出'
    elif rr > lr:
        winner = '右方勝出'
    elif left_target > right_target:
        winner = '同級成功，左方技能值較高而勝出'
    elif right_target > left_target:
        winner = '同級成功，右方技能值較高而勝出'
    elif lv < rv:
        winner = '技能值相同，左方骰值較低而勝出'
    elif rv < lv:
        winner = '技能值相同，右方骰值較低而勝出'
    else:
        winner = '完全平手，請重骰'
    return f'⚔️ 對抗檢定｜{left_name} {left_target}%：{lv} → {ll}｜{right_name} {right_target}%：{rv} → {rl}｜{winner}'


def bot_command(r, role, aid, text):
    raw = text.strip()
    hidden = False
    if raw.startswith('/h ') or raw.startswith('.h '):
        if not is_kp(role):
            raise ValueError('暗骰指令僅 KP 可用')
        hidden = True; raw = raw[3:].strip()
    if raw.startswith('/'):
        raw = '.' + raw[1:]
    lower = raw.lower()
    if lower in ('.help', '.?', '.rules'):
        return ('骰娘 v5｜.r 2d6+1｜.ra 偵查 [60] [n/h/e]｜.rb/.rbb 獎勵骰｜.rp/.rpp 懲罰骰｜'
                '.push 推骰上一個失敗檢定｜.op 偵查 60 | 潛行 50｜.sc 0/1d6 [SAN]｜.luck [數值]｜KP 暗骰：.h .ra 偵查 60'), hidden
    if lower.startswith('.r ') and not lower.startswith(('.ra ', '.rb ', '.rp ', '.rbb ', '.rpp ')):
        expr = raw[3:].strip(); total, detail = roll_dice_expr(expr)
        return f'🎲 {expr} = {total}（{detail}）', hidden
    for cmd, bonus, penalty in [('.ra',0,False),('.rb',1,False),('.rbb',2,False),('.rp',1,True),('.rpp',2,True)]:
        if lower == cmd or lower.startswith(cmd + ' '):
            name, explicit, difficulty = parse_check_args(raw[len(cmd):].strip())
            target = resolve_target(r, aid, name, explicit)
            value, level, message = check_message(name,target,difficulty,bonus,penalty)
            remember_check(r,aid,name,target,difficulty,bonus,penalty,value,level,pushable=True)
            return message, hidden
    if lower == '.push' or lower.startswith('.push '):
        if is_kp(role) and aid == 'owner':
            raise ValueError('房主 KP 沒有角色卡；推骰請由調查員本人執行')
        last = r.setdefault('last_rolls', {}).get(aid)
        if not last or last.get('pushed') or not last.get('pushable'):
            raise ValueError('沒有可推骰的上一個失敗技能／屬性檢定')
        if LEVEL_RANK.get(last.get('level'), 0) >= DIFFICULTY_RANK.get(last.get('difficulty','normal'),2):
            raise ValueError('上一個檢定已達成需求，不能推骰')
        value, level, msg = check_message(last['name'],last['target'],last['difficulty'],last['extra'],last['penalty'],prefix='🔁 推骰')
        last['pushed'] = True; last['value'] = value; last['level'] = level
        if not meets_difficulty(level,last['difficulty']):
            msg += '｜推骰失敗：由 KP 套用事前說明的嚴重後果'
        return msg, hidden
    if lower.startswith('.op '):
        body = raw[4:].strip()
        if '|' not in body:
            raise ValueError('格式：.op 偵查 60 | 潛行 50')
        left,right = [x.strip() for x in body.split('|',1)]
        ln, le, _ = parse_check_args(left); rn, rexp, _ = parse_check_args(right)
        lt = resolve_target(r,aid,ln,le); rt = resolve_target(r,aid,rn,rexp)
        return opposed_roll_message(ln,lt,rn,rt), hidden
    if lower.startswith('.luck'):
        rest = raw[5:].strip(); explicit = int(rest) if rest.isdigit() else None
        target = resolve_target(r, aid, '幸運', explicit)
        value, level, message = check_message('幸運', target, 'normal', prefix='🍀')
        return message, hidden
    if lower.startswith('.sc '):
        rest = raw[4:].strip().split()
        if not rest or '/' not in rest[0]:
            raise ValueError('格式：.sc 0/1d6 [SAN值]')
        success_loss, fail_loss = rest[0].split('/',1)
        explicit = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else None
        target = resolve_target(r, aid, 'SAN', explicit)
        value, _ = percentile_candidates(); ok = value <= target
        loss_expr = success_loss if ok else fail_loss
        try:
            loss, _ = roll_dice_expr(loss_expr)
        except Exception:
            loss = int(loss_expr)
        return f'🧠 SAN {target}%：{value} → {"成功" if ok else "失敗"}｜SAN 損失 {loss}（不自動扣除）', hidden
    raise ValueError('未知指令，輸入 .help 查看骰娘指令')

def safe_int(v, default=0):
    try:
        if v is None or str(v).strip() == '': return default
        return int(float(str(v).replace('%','').strip()))
    except Exception:
        return default


def cell(rows, r, c):
    try:
        return rows[r-1][c-1] if c-1 < len(rows[r-1]) else ''
    except Exception:
        return ''


def parse_sheet_rows(rows):
    def nearest_number(row, start_col, end_col):
        for col in range(start_col, end_col+1):
            v = cell(rows,row,col)
            if str(v).strip() != '':
                n = safe_int(v, None)
                if n is not None: return n
        return 0
    attrs = {
        'STR': safe_int(cell(rows,6,19)), 'DEX': safe_int(cell(rows,7,19)),
        'POW': safe_int(cell(rows,8,19)), 'CON': safe_int(cell(rows,9,19)),
        'APP': safe_int(cell(rows,10,19)), 'EDU': safe_int(cell(rows,11,19)),
        'SIZ': safe_int(cell(rows,12,19)), 'INT': safe_int(cell(rows,13,19)),
    }
    luck = nearest_number(14,17,20)
    hp_raw = cell(rows,11,22); mp_raw = cell(rows,12,22); san_raw = cell(rows,13,22)
    hp = safe_int(hp_raw, math.floor((attrs['CON'] + attrs['SIZ']) / 10) if attrs['CON'] or attrs['SIZ'] else 10)
    mp = safe_int(mp_raw, math.floor(attrs['POW'] / 5) if attrs['POW'] else 10)
    san = safe_int(san_raw, attrs['POW'] if attrs['POW'] else 50)
    if hp == 0 and attrs['CON'] + attrs['SIZ'] > 0: hp = math.floor((attrs['CON']+attrs['SIZ'])/10)
    if mp == 0 and attrs['POW'] > 0: mp = math.floor(attrs['POW']/5)
    if san == 0 and attrs['POW'] > 0: san = attrs['POW']
    skills = {}
    for row in range(27, 77):
        left1, left2 = str(cell(rows,row,2) or '').strip(), str(cell(rows,row,3) or '').strip()
        if left1 not in ('［交際類］','［移動類］','［隱密類］','［學問類］','［語言］'):
            name = (left1 + (('：' + left2) if left2 else '')).strip('：')
            val = safe_int(cell(rows,row,12), -1)
            if name and val >= 0: skills[name] = val
        right1, right2 = str(cell(rows,row,16) or '').strip(), str(cell(rows,row,17) or '').strip()
        name2 = (right1 + (('：' + right2) if right2 else '')).strip('：')
        val2 = safe_int(cell(rows,row,25), -1)
        if name2 and not name2.startswith('［') and val2 >= 0: skills[name2] = val2
    skills = {k:v for k,v in skills.items() if k and not k.startswith('技能名稱') and len(k) < 40}
    ch = blank_char()
    ch.update({
        'name': str(cell(rows,5,2) or '').strip(),
        'occupation': str(cell(rows,11,4) or '').strip(),
        'age': str(cell(rows,8,2) or '').strip(),
        'attrs': attrs, 'hp': hp, 'mp': mp, 'san': san, 'luck': luck or 50,
        'skills': skills or dict(DEFAULT_SKILLS), 'source': 'Google Sheets'
    })
    return ch


def parse_text_table(text):
    if '\t' in text:
        return [line.split('\t') for line in text.splitlines()]
    return list(csv.reader(io.StringIO(text)))


def fetch_google_sheet(url):
    m = re.search(r'/spreadsheets/d/([a-zA-Z0-9_-]+)', url)
    if not m:
        raise ValueError('不是有效的 Google 試算表連結')
    sid = m.group(1)
    q = urllib.parse.quote('人物卡')
    export = f'https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={q}&range=A1:Z150'
    req = urllib.request.Request(export, headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=12) as resp:
        raw = resp.read().decode('utf-8-sig', errors='replace')
    if '<html' in raw.lower() or 'Sign in' in raw[:1000]:
        raise ValueError('試算表無法公開讀取；請設為「知道連結的任何人可檢視」，或改用貼上資料匯入')
    return list(csv.reader(io.StringIO(raw)))


class Create(BaseModel):
    name: str = '未命名房間'; scenario: str = '自由跑團'; limit: int = 4
class Join(BaseModel):
    code: str; name: str
class Token(BaseModel): token: str
class ChatIn(Token): text: str
class RollIn(Token): label: str='d100'; target: Optional[int]=None; mode: str='normal'; difficulty: str='normal'; hidden: bool=False
class LimitIn(Token): limit: int
class CharIn(Token):
    pid: str; char: Dict[str, Any]
class SceneIn(Token): scene: str
class ClueIn(Token): text: str; visibility: str='public'; target: Optional[str]=None
class NoteIn(Token): text: str=''
class CoKpIn(Token): pid: str; enabled: bool
class ImportIn(Token): pid: str; url: Optional[str]=None; text: Optional[str]=None


@app.get('/', response_class=HTMLResponse)
def home():
    return HTMLResponse(HTML, headers={'Cache-Control':'no-store, max-age=0'})
@app.head('/')
def home_head():
    return HTMLResponse('', headers={'Cache-Control':'no-store, max-age=0'})
@app.get('/health')
def health(): return {'ok':1,'rooms':len(rooms),'version':'v5'}

@app.post('/api/create')
def create(x: Create):
    if not 1 <= x.limit <= 50: raise HTTPException(400,'人數需 1~50')
    c=make_code(); t=secrets.token_urlsafe(24); sc=SCENES.get(x.scenario,SCENES['自由跑團'])
    rooms[c]={'code':c,'name':x.name.strip() or '未命名房間','scenario':x.scenario,'limit':x.limit,
              'owner':t,'ps':[],'scene':sc[0],'scenes':sc,'chat':[{'who':'系統','text':'房間已建立','kind':'system'}],
              'clues':[],'hidden':[],'note':'','last_rolls':{},'ended':False}
    return {'code':c,'token':t}

@app.post('/api/join')
def join(x: Join):
    r=rooms.get(x.code.upper().strip())
    if not r: raise HTTPException(404,'找不到房間')
    if r['ended']: raise HTTPException(409,'房間已結束')
    if investigator_count(r) >= r['limit']: raise HTTPException(409,'房間已滿')
    p={'id':secrets.token_hex(5),'token':secrets.token_urlsafe(24),'name':x.name.strip() or '訪客','co_kp':False,'npc':False,'char':blank_char()}
    r['ps'].append(p); r['chat'].append({'who':'系統','text':p['name']+' 加入房間','kind':'system'})
    return {'code':r['code'],'token':p['token']}

@app.get('/api/room/{c}')
def state(c:str, token:str):
    r=rooms.get(c.upper());
    if not r: raise HTTPException(404,'找不到房間')
    return pub(r,token)

@app.post('/api/room/{c}/chat')
def chat(c:str,x:ChatIn):
    r=rooms.get(c.upper());
    if not r: raise HTTPException(404,'找不到房間')
    role,aid=auth(r,x.token)
    if r['ended']: raise HTTPException(409,'房間已結束')
    who='KP' if is_kp(role) else own_player(r,aid)['name']
    text=x.text.strip()
    if not text: return {'ok':1}
    if text.startswith(('.', '/')):
        try:
            result,hidden=bot_command(r,role,aid,text)
            msg={'who':'骰娘','text':result,'kind':'dice','by':who}
            if hidden:
                r['hidden'].append(msg)
            else:
                r['chat'].append(msg)
            return {'ok':1,'bot':True,'hidden':hidden}
        except ValueError as e:
            r['chat'].append({'who':'骰娘','text':'⚠️ '+str(e),'kind':'dice'})
            return {'ok':1,'bot':True,'error':str(e)}
    r['chat'].append({'who':who,'text':text[:800],'kind':'chat'})
    return {'ok':1}

@app.post('/api/room/{c}/roll')
def roll(c:str,x:RollIn):
    r=rooms.get(c.upper())
    if not r: raise HTTPException(404,'找不到房間')
    role,aid=auth(r,x.token)
    if r['ended']: raise HTTPException(409,'房間已結束')
    mode=x.mode.lower(); extra=2 if mode in ('bb','pp') else 1 if mode in ('b','p') else 0; penalty=mode in ('p','pp')
    difficulty=x.difficulty if x.difficulty in DIFFICULTY_RANK else 'normal'
    hidden=bool(x.hidden and is_kp(role))
    who='KP' if is_kp(role) else own_player(r,aid)['name']
    if x.target is None:
        v,cands=percentile_candidates(extra,penalty); result=''
        text=f'🎲 {x.label}: {v}'+(f'｜候選 {cands}' if extra else '')
    else:
        v,result,text=check_message(x.label,max(0,min(100,x.target)),difficulty,extra,penalty)
        cands=[]
        if aid!='owner': remember_check(r,aid,x.label,max(0,min(100,x.target)),difficulty,extra,penalty,v,result,pushable=True)
    msg={'who':'骰娘','text':text,'kind':'dice','by':who}
    (r['hidden'] if hidden else r['chat']).append(msg)
    return {'value':v,'result':result,'meets':meets_difficulty(result,difficulty) if result else None,'hidden':hidden}

@app.post('/api/room/{c}/limit')
def set_limit(c:str,x:LimitIn):
    r=rooms.get(c.upper()); role,_=auth(r,x.token)
    if not is_kp(role): raise HTTPException(403,'只有 KP 可以修改')
    if x.limit < investigator_count(r) or not 1 <= x.limit <= 50: raise HTTPException(409,'人數上限無效')
    r['limit']=x.limit; return {'ok':1}

@app.post('/api/room/{c}/char')
def save_char(c:str,x:CharIn):
    r=rooms.get(c.upper()); role,aid=auth(r,x.token)
    if r['ended']: raise HTTPException(409,'房間已結束')
    p=next((p for p in r['ps'] if p['id']==x.pid),None)
    if not p: raise HTTPException(404,'找不到角色')
    if not is_kp(role) and aid != x.pid: raise HTTPException(403,'不能修改別人角色')
    ch=blank_char(); data=x.char or {}
    ch.update({k:data[k] for k in ['name','occupation','age','inventory','public_notes','private_notes','visibility','source'] if k in data})
    if isinstance(data.get('attrs'),dict): ch['attrs']={k:safe_int(data['attrs'].get(k),0) for k in ch['attrs']}
    if isinstance(data.get('skills'),dict): ch['skills']={str(k)[:40]:max(0,min(100,safe_int(v,0))) for k,v in data['skills'].items() if str(k).strip()}
    for k in ['hp','mp','san','luck']: ch[k]=safe_int(data.get(k),ch[k])
    if ch['visibility'] not in ('public','kp_only'): ch['visibility']='kp_only'
    p['char']=ch; return {'ok':1}

@app.post('/api/room/{c}/import-sheet')
def import_sheet(c:str,x:ImportIn):
    r=rooms.get(c.upper()); role,aid=auth(r,x.token)
    p=next((p for p in r['ps'] if p['id']==x.pid),None)
    if not p: raise HTTPException(404,'找不到角色')
    if not is_kp(role) and aid != x.pid: raise HTTPException(403,'不能匯入別人角色')
    try:
        rows = fetch_google_sheet(x.url) if x.url else parse_text_table(x.text or '')
        ch = parse_sheet_rows(rows)
        return {'ok':1,'preview':ch}
    except Exception as e:
        raise HTTPException(400,str(e))

@app.post('/api/room/{c}/scene')
def set_scene(c:str,x:SceneIn):
    r=rooms.get(c.upper()); role,_=auth(r,x.token)
    if not is_kp(role): raise HTTPException(403,'只有 KP 可以切換場景')
    if x.scene not in r['scenes']: raise HTTPException(400,'無效場景')
    r['scene']=x.scene; r['chat'].append({'who':'KP','text':'場景切換：'+x.scene,'kind':'system'}); return {'ok':1}

@app.post('/api/room/{c}/clue')
def add_clue(c:str,x:ClueIn):
    r=rooms.get(c.upper()); role,_=auth(r,x.token)
    if not is_kp(role): raise HTTPException(403,'只有 KP 可以新增線索')
    if x.visibility not in ('public','kp','player'): raise HTTPException(400,'無效可見範圍')
    if x.visibility=='player' and not any(p['id']==x.target for p in r['ps']): raise HTTPException(400,'請選擇玩家')
    clue={'id':secrets.token_hex(4),'text':x.text[:1000],'visibility':x.visibility,'target':x.target}
    r['clues'].append(clue)
    if x.visibility=='public': r['chat'].append({'who':'KP','text':'🔎 公開線索：'+x.text[:500],'kind':'clue'})
    return {'ok':1}

@app.post('/api/room/{c}/note')
def note(c:str,x:NoteIn):
    r=rooms.get(c.upper()); role,_=auth(r,x.token)
    if not is_kp(role): raise HTTPException(403,'只有 KP 可以修改')
    r['note']=x.text[:8000]; return {'ok':1}

@app.post('/api/room/{c}/co-kp')
def co_kp(c:str,x:CoKpIn):
    r=rooms.get(c.upper()); role,_=auth(r,x.token)
    if role!='owner': raise HTTPException(403,'只有房主 KP 可以管理共同 KP')
    p=next((p for p in r['ps'] if p['id']==x.pid),None)
    if not p: raise HTTPException(404,'找不到玩家')
    if not x.enabled and investigator_count(r) >= r['limit'] and p.get('co_kp'):
        raise HTTPException(409,'調查員名額已滿，無法取消共同 KP')
    p['co_kp']=bool(x.enabled); return {'ok':1}

@app.post('/api/room/{c}/end')
def end(c:str,x:Token):
    r=rooms.get(c.upper()); role,_=auth(r,x.token)
    if not is_kp(role): raise HTTPException(403,'只有 KP 可以結束房間')
    r['ended']=True; r['chat'].append({'who':'系統','text':'房間已結束','kind':'system'}); return {'ok':1}

@app.get('/selftest')
def selftest():
    checks=[]; c=None
    try:
        a=create(Create(name='QA',scenario='營火',limit=1)); c=a['code']; checks.append('create')
        g=join(Join(code=c,name='A')); checks.append('join')
        try:
            join(Join(code=c,name='B')); raise AssertionError('capacity failed')
        except HTTPException as e:
            assert e.status_code==409; checks.append('capacity')
        set_limit(c,LimitIn(token=a['token'],limit=2)); checks.append('limit')
        g2=join(Join(code=c,name='B')); checks.append('join2')
        st=state(c,a['token']); assert st['count']==2 and st['role']=='owner'; checks.append('state')
        # character privacy + save
        pida=next(p['id'] for p in rooms[c]['ps'] if p['token']==g['token'])
        ch=blank_char(); ch['name']='調查員A';ch['skills']['偵查']=60;ch['private_notes']='秘密';ch['visibility']='kp_only'
        save_char(c,CharIn(token=g['token'],pid=pida,char=ch)); checks.append('char_save')
        guest2=state(c,g2['token']); other=next(p for p in guest2['players'] if p['id']==pida); assert other['char_hidden']; checks.append('privacy')
        kpst=state(c,a['token']); full=next(p for p in kpst['players'] if p['id']==pida); assert full['char']['private_notes']=='秘密'; checks.append('kp_view')
        # dice rule boundaries + bot
        assert coc_level(1,60)=='大成功'; checks.append('critical')
        assert coc_level(12,60)=='極限成功'; checks.append('extreme')
        assert coc_level(30,60)=='困難成功'; checks.append('hard')
        assert coc_level(60,60)=='一般成功'; checks.append('regular')
        assert coc_level(61,60)=='失敗'; checks.append('failure')
        assert coc_level(96,40)=='大失敗' and coc_level(99,60)=='失敗' and coc_level(100,60)=='大失敗'; checks.append('fumble')
        assert meets_difficulty('一般成功','normal') and not meets_difficulty('一般成功','hard') and meets_difficulty('極限成功','extreme'); checks.append('difficulty')
        chat(c,ChatIn(token=g['token'],text='.ra 偵查 60 h')); assert rooms[c]['chat'][-1]['who']=='骰娘' and '需求：困難' in rooms[c]['chat'][-1]['text']; checks.append('bot')
        chat(c,ChatIn(token=g['token'],text='.rb 偵查 60')); assert '獎勵骰' in rooms[c]['chat'][-1]['text']; checks.append('bonus')
        chat(c,ChatIn(token=g['token'],text='.rp 偵查 60')); assert '懲罰骰' in rooms[c]['chat'][-1]['text']; checks.append('penalty')
        rooms[c]['last_rolls'][pida]={'name':'偵查','target':60,'difficulty':'hard','extra':0,'penalty':False,'value':80,'level':'失敗','pushable':True,'pushed':False}
        chat(c,ChatIn(token=g['token'],text='.push')); assert '推骰' in rooms[c]['chat'][-1]['text'] and rooms[c]['last_rolls'][pida]['pushed']; checks.append('push')
        chat(c,ChatIn(token=g['token'],text='.op 偵查 60 | 潛行 50')); assert '對抗檢定' in rooms[c]['chat'][-1]['text']; checks.append('opposed')
        chat(c,ChatIn(token=g['token'],text='.sc 0/1d6 55')); assert 'SAN 損失' in rooms[c]['chat'][-1]['text']; checks.append('san')
        chat(c,ChatIn(token=a['token'],text='.h .ra 偵查 60')); assert rooms[c]['hidden']; checks.append('hidden_roll')
        qr=roll(c,RollIn(token=g['token'],label='偵查',target=60,mode='normal',difficulty='hard')); assert 'meets' in qr and qr['result'] in LEVEL_RANK; checks.append('quick_roll')
        # clues
        add_clue(c,ClueIn(token=a['token'],text='KP秘密',visibility='kp')); add_clue(c,ClueIn(token=a['token'],text='公開線索',visibility='public')); checks.append('clues')
        assert len(state(c,g['token'])['clues'])==1 and len(state(c,a['token'])['clues'])==2; checks.append('clue_privacy')
        # co-kp
        pidb=next(p['id'] for p in rooms[c]['ps'] if p['token']==g2['token'])
        co_kp(c,CoKpIn(token=a['token'],pid=pidb,enabled=True)); assert state(c,g2['token'])['role']=='kp'; checks.append('co_kp')
        # sheet parser synthetic
        rows=[['']*26 for _ in range(80)]; rows[4][1]='測試角色'; rows[10][3]='記者';
        for rr,val in [(6,60),(7,70),(8,50),(9,55),(10,65),(11,75),(12,60),(13,80)]: rows[rr-1][18]=str(val)
        rows[26][1]='心理學';rows[26][11]='55';rows[26][15]='藝術／手藝：';rows[26][16]='攝影';rows[26][24]='45'
        parsed=parse_sheet_rows(rows); assert parsed['name']=='測試角色' and parsed['attrs']['STR']==60 and parsed['skills']['心理學']==55; checks.append('sheet_parse')
        end(c,Token(token=a['token']));
        try:
            chat(c,ChatIn(token=g['token'],text='x')); raise AssertionError('end lock failed')
        except HTTPException as e:
            assert e.status_code==409; checks.append('end_lock')
        return {'ok':True,'checks':checks,'count':len(checks),'version':'v5'}
    except Exception as e:
        return JSONResponse({'ok':False,'checks':checks,'error':repr(e)},status_code=500)
    finally:
        if c: rooms.pop(c,None)


@app.on_event('startup')
def startup_selftest():
    result = selftest()
    print('COC_SELFTEST', result if isinstance(result, dict) else getattr(result, 'body', result), flush=True)


HTML = r'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>克蘇魯調查局</title>
<style>
:root{--bg:#07110d;--card:#0e1d16;--line:#294b3a;--green:#7dd3a7;--muted:#9db1a5;--red:#8a3030;--gold:#d6bc7a}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#eef7f0;font-family:system-ui,-apple-system,sans-serif}.w{max-width:1100px;margin:auto;padding:18px 14px 90px}.hero{text-align:center;padding:22px 0}.grid,.room{display:grid;grid-template-columns:1fr 1fr;gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:15px;margin-bottom:14px}input,select,textarea,button{font:inherit}input,select,textarea{width:100%;margin:4px 0 9px;padding:10px;border-radius:10px;border:1px solid #315642;background:#09140f;color:#fff}textarea{min-height:90px}button{padding:10px 13px;border:0;border-radius:10px;background:var(--green);color:#062015;font-weight:800;cursor:pointer}button:disabled{opacity:.4;cursor:not-allowed}.alt{background:#173629;color:#fff}.bad{background:var(--red);color:#fff}.gold{background:var(--gold);color:#211907}.hide{display:none!important}.chat{height:48vh;overflow:auto;border:1px solid var(--line);border-radius:12px;padding:10px}.m{margin:0 0 9px;white-space:pre-wrap}.who{color:var(--green);font-weight:800}.row{display:flex;gap:8px}.row>*{flex:1}.small{color:var(--muted);font-size:13px}.pill{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:5px 9px;margin:3px}.tabs{display:flex;gap:7px;margin:10px 0;flex-wrap:wrap}.tabs button{background:#173629;color:#fff}.sec{display:none}.sec.on{display:block}.bottom{display:none;position:fixed;bottom:0;left:0;right:0;background:#0b1812;padding:8px;grid-template-columns:repeat(3,1fr);gap:8px;z-index:5}.attrgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.skillgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:6px}.clue{padding:9px;border-left:3px solid var(--green);background:#0a1711;margin:7px 0}.modal{position:fixed;inset:0;background:#000a;display:flex;align-items:center;justify-content:center;padding:15px;z-index:20}.modalbox{max-width:760px;max-height:85vh;overflow:auto;background:#102219;border:1px solid var(--line);border-radius:18px;padding:18px}.topbar{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}.code{font-family:ui-monospace,monospace;font-size:18px;color:var(--gold)}details{border:1px solid var(--line);border-radius:10px;padding:9px;margin:8px 0}a{color:var(--green)}
@media(max-width:720px){.grid,.room{grid-template-columns:1fr}.bottom{display:grid}.desktop{display:none}.attrgrid{grid-template-columns:repeat(2,1fr)}.skillgrid{grid-template-columns:1fr}.chat{height:45vh}}
</style></head><body><div class="w">
<section id="home"><div class="hero"><h1>克蘇魯調查局</h1><div class="small">CoC 7 文字跑團｜訪客免帳號 · v5</div><button class="alt" onclick="rules()">規則／骰娘指令</button></div><div class="grid"><div class="card"><h2>KP 開房</h2><label>房間名稱</label><input id="rn" placeholder="例如：週五克蘇魯團"><label>劇本</label><select id="sc"><option>自由跑團</option><option>營火</option></select><label>調查員人數上限</label><input id="lim" type="number" min="1" max="50" value="4"><button onclick="createRoom()">建立房間</button></div><div class="card"><h2>訪客加入</h2><label>暱稱</label><input id="gn" placeholder="你的暱稱"><label>房號</label><input id="rc" maxlength="6"><button onclick="joinRoom()">加入房間</button></div></div><div id="err"></div></section>
<section id="game" class="hide"><div class="topbar"><div><h2 id="title"></h2><span class="pill" id="scene"></span><span class="pill" id="count"></span><span class="pill" id="role"></span></div><div><div class="code" id="roomcode"></div><button class="alt" onclick="copyCode()">複製房號</button> <button class="alt" onclick="copyInvite()">複製邀請連結</button> <button class="gold" onclick="rules()">規則</button></div></div><div id="ended" class="card hide">房間已結束，聊天、擲骰與角色修改已停用。</div><div class="tabs desktop"><button onclick="tab('story')">故事</button><button onclick="tab('chars')">角色</button><button onclick="tab('dice')">擲骰</button><button class="kp" onclick="tab('kp')">KP 主持台</button></div><div class="room"><main>
<div id="story" class="sec on"><div id="chat" class="chat"></div><div class="row"><input id="ci" placeholder="留言或輸入 .help 查看骰娘指令" onkeydown="if(event.key==='Enter')send()"><button id="sendbtn" onclick="send()">送出</button></div></div>
<div id="chars" class="sec"><div class="card"><h2>角色卡</h2><div class="small">角色卡預設「僅本人＋KP」。本人可改成全房公開；私人筆記永遠只有本人與 KP 看得到。</div><div id="players"></div></div></div>
<div id="dice" class="sec"><div class="card"><h2>快速擲骰 v5</h2><label>檢定名稱</label><input id="lab" value="偵查"><label>目標值</label><input id="tar" type="number" placeholder="例如 60"><label>需求難度</label><select id="diff"><option value="normal">一般成功以上</option><option value="hard">困難成功以上</option><option value="extreme">極限成功以上</option></select><label>獎勵／懲罰</label><select id="mode"><option value="normal">無</option><option value="b">1 顆獎勵骰</option><option value="bb">2 顆獎勵骰</option><option value="p">1 顆懲罰骰</option><option value="pp">2 顆懲罰骰</option></select><label class="kp"><input id="hid" type="checkbox" style="width:auto"> KP 暗骰</label><br><div class="row"><button id="rollbtn" onclick="roll()">擲 d100</button><button class="alt" onclick="pushLast()">推骰上一個失敗檢定</button></div><div id="rr"></div><hr><div class="small">聊天骰娘可輸入：<b>.ra 偵查</b>、<b>.ra 偵查 60 h</b>、<b>.rb 偵查 60</b>、<b>.push</b>、<b>.op 偵查 60 | 潛行 50</b>、<b>.sc 0/1d6 55</b>。輸入 <b>.help</b> 查看全部。</div></div></div>
<div id="kp" class="sec kp"><div class="card"><h2>KP 主持台</h2><label>調查員人數上限</label><div class="row"><input id="nl" type="number" min="1" max="50"><button onclick="setLimit()">修改</button></div><label>場景</label><select id="ss"></select><button onclick="setScene()">切換場景</button><hr><h3>線索</h3><div class="small">公開＝全房看見；僅KP＝所有KP可見；指定玩家＝只有KP與該玩家可見。</div><input id="cl" placeholder="輸入線索內容"><select id="cv"><option value="public">公開</option><option value="kp">僅KP</option><option value="player">指定玩家</option></select><select id="ct"></select><button onclick="addClue()">新增線索</button><hr><label>KP 私人筆記</label><textarea id="nt"></textarea><button class="alt" onclick="saveNote()">儲存筆記</button><hr><div id="kokp"></div><hr><button class="bad" onclick="endRoom()">結束房間</button></div></div>
</main><aside><div class="card"><h3>線索</h3><div id="clues"></div></div><div class="card"><h3>玩家／KP</h3><div id="side"></div></div></aside></div></section></div>
<div id="bottom" class="bottom hide"><button onclick="tab('story')">故事</button><button onclick="tab('chars')">角色</button><button onclick="tab('dice')">擲骰</button></div><div id="modal" class="modal hide" onclick="if(event.target.id==='modal')closeRules()"><div class="modalbox"><button style="float:right" onclick="closeRules()">關閉</button><h2>CoC 7 骰子規則 v5</h2><p><b>核心：</b>只有結果不確定且有戲劇性時才擲骰。D100 採低骰制；骰值越低越好。</p><p><b>結果等級：</b>01＝大成功；≤技能 1/5＝極限成功；≤技能 1/2＝困難成功；≤技能＝一般成功；超過技能＝失敗。大失敗：技能值未滿 50 時為 96–100；技能值 50 以上時只有 100。</p><p><b>難度：</b>KP 可要求一般／困難／極限。骰娘會同時顯示「擲出的成功等級」與「是否達到需求」。</p><p><b>獎勵／懲罰骰：</b>保留同一顆個位骰，多擲 1 或 2 顆十位骰；獎勵取較低結果，懲罰取較高結果。</p><p><b>推骰：</b>僅能對上一個未達成需求的技能／屬性檢定推骰一次；需由 KP 允許並先說明失敗代價。推骰再次失敗時，由 KP 套用更嚴重後果。SAN、Luck、戰鬥檢定不應以一般推骰處理。</p><p><b>對抗：</b>雙方比較成功等級；等級相同時技能值較高者勝；技能值也相同時骰值較低者勝。</p><p><b>SAN：</b>骰娘只計算檢定與損失值，不會自動修改角色 SAN，由 KP／玩家確認後再改角色卡。</p><h3>聊天骰娘指令</h3><ul><li><b>.r 2d6+1</b>：一般骰式</li><li><b>.ra 偵查</b>：讀角色卡技能值，做一般需求檢定</li><li><b>.ra 偵查 60 h</b>：指定 60%，需求困難成功；<b>e</b> 代表極限</li><li><b>.rb / .rbb</b>：1／2 顆獎勵骰</li><li><b>.rp / .rpp</b>：1／2 顆懲罰骰</li><li><b>.push</b>：推骰上一個失敗檢定</li><li><b>.op 偵查 60 | 潛行 50</b>：對抗檢定</li><li><b>.sc 0/1d6 55</b>：SAN 檢定與損失</li><li><b>.luck 60</b>：幸運檢定</li><li><b>.h .ra 偵查 60</b>：KP 暗骰</li><li><b>.help</b>：聊天室顯示指令摘要</li></ul><p class="small">依 Call of Cthulhu 7th Edition Quick-Start 的技能、難度、推骰、對抗與獎勵／懲罰骰邏輯整理；實際裁定仍以 KP 為準。</p></div></div>
<script>
let S={code:'',token:'',st:null,timer:null};const $=x=>document.getElementById(x);const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));async function api(u,o={}){let r=await fetch(u,{headers:{'Content-Type':'application/json'},...o}),d={};try{d=await r.json()}catch{}if(!r.ok)throw Error(d.detail||'操作失敗');return d}function showErr(e){$('err').innerHTML='<div class="card">⚠️ '+esc(e)+'</div>'}function rules(){$('modal').classList.remove('hide')}function closeRules(){$('modal').classList.add('hide')}
async function createRoom(){try{let d=await api('/api/create',{method:'POST',body:JSON.stringify({name:$('rn').value||'未命名房間',scenario:$('sc').value,limit:+$('lim').value})});enter(d)}catch(e){showErr(e.message)}}async function joinRoom(){try{let d=await api('/api/join',{method:'POST',body:JSON.stringify({code:$('rc').value.toUpperCase(),name:$('gn').value||'訪客'})});enter(d)}catch(e){showErr(e.message)}}function enter(d){S.code=d.code;S.token=d.token;localStorage.removeItem('coc-v5');localStorage.setItem('coc-v5',JSON.stringify(d));$('home').classList.add('hide');$('game').classList.remove('hide');$('bottom').classList.remove('hide');if(S.timer)clearInterval(S.timer);refresh();S.timer=setInterval(refresh,1400)}function resetHome(msg=''){localStorage.removeItem('coc-v5');S={code:'',token:'',st:null,timer:null};$('game').classList.add('hide');$('bottom').classList.add('hide');$('home').classList.remove('hide');if(msg)showErr(msg)}async function refresh(){if(!S.code)return;try{S.st=await api('/api/room/'+S.code+'?token='+encodeURIComponent(S.token));render()}catch(e){resetHome('原房間已失效，請重新開房或加入。')}}
function render(){let s=S.st,isKP=['owner','kp'].includes(s.role);$('title').textContent=s.name;$('roomcode').textContent='房號 '+s.code;$('scene').textContent='場景：'+s.scene;$('count').textContent='調查員 '+s.count+'/'+s.limit;$('role').textContent=s.role==='owner'?'房主 KP':s.role==='kp'?'共同 KP':'訪客';document.querySelectorAll('.kp').forEach(x=>x.classList.toggle('hide',!isKP));$('ended').classList.toggle('hide',!s.ended);$('ci').disabled=s.ended;$('sendbtn').disabled=s.ended;$('rollbtn').disabled=s.ended;$('chat').innerHTML=s.chat.map(m=>'<div class="m"><span class="who">'+esc(m.who)+'</span> '+esc(m.text)+'</div>').join('');$('chat').scrollTop=999999;$('clues').innerHTML=s.clues.length?s.clues.map(x=>'<div class="clue">'+esc(x.text)+'<div class="small">'+(x.visibility==='public'?'公開':x.visibility==='kp'?'僅KP':'指定玩家')+'</div></div>').join(''):'尚無可見線索';$('side').innerHTML=s.players.map(p=>'<div><b>'+esc(p.name)+'</b> '+(p.co_kp?'<span class="pill">共同KP</span>':'')+'<div class="small">'+esc(p.char_name||'未設定角色')+'</div></div><br>').join('')||'尚無玩家';$('players').innerHTML=s.players.map(p=>charCard(p,s.role,s.aid,s.ended)).join('');$('nl').value=s.limit;$('ss').innerHTML=s.scenes.map(x=>'<option '+(x===s.scene?'selected':'')+'>'+esc(x)+'</option>').join('');$('ct').innerHTML='<option value="">選擇玩家</option>'+s.players.filter(p=>!p.co_kp).map(p=>'<option value="'+p.id+'">'+esc(p.name)+'</option>').join('');if(isKP)$('nt').value=s.note||'';$('kokp').innerHTML=s.role==='owner'?'<h3>共同 KP</h3>'+s.players.map(p=>'<div class="row"><span>'+esc(p.name)+'</span><button class="alt" onclick="setCoKp(\''+p.id+'\','+(!p.co_kp)+')">'+(p.co_kp?'取消共同KP':'設為共同KP')+'</button></div>').join(''):''}
function charCard(p,role,aid,ended){if(p.char_hidden)return '<div class="card"><b>'+esc(p.name)+'</b><p>🔒 角色卡已設為私密，只有本人與 KP 可查看。</p></div>';let c=p.char||{},can=!ended&&(['owner','kp'].includes(role)||aid===p.id);let attrs=c.attrs||{},skills=c.skills||{};let skillText=Object.entries(skills).map(([k,v])=>k+'='+v).join('\n');return '<div class="card"><h3>'+esc(p.name)+' 的角色卡</h3><label>角色名稱</label><input id="cn'+p.id+'" value="'+esc(c.name||'')+'" '+(can?'':'disabled')+'><label>職業</label><input id="co'+p.id+'" value="'+esc(c.occupation||'')+'" '+(can?'':'disabled')+'><label>年齡</label><input id="ca'+p.id+'" value="'+esc(c.age||'')+'" '+(can?'':'disabled')+'><div class="attrgrid">'+['STR','CON','SIZ','DEX','APP','INT','POW','EDU'].map(k=>'<label>'+k+'<input id="a'+k+p.id+'" type="number" value="'+(attrs[k]||0)+'" '+(can?'':'disabled')+'></label>').join('')+'</div><div class="row"><label>HP<input id="hp'+p.id+'" type="number" value="'+(c.hp??10)+'" '+(can?'':'disabled')+'></label><label>MP<input id="mp'+p.id+'" type="number" value="'+(c.mp??10)+'" '+(can?'':'disabled')+'></label><label>SAN<input id="san'+p.id+'" type="number" value="'+(c.san??50)+'" '+(can?'':'disabled')+'></label><label>幸運<input id="luck'+p.id+'" type="number" value="'+(c.luck??50)+'" '+(can?'':'disabled')+'></label></div><label>技能（每行 技能=數值）</label><textarea id="sk'+p.id+'" '+(can?'':'disabled')+'>'+esc(skillText)+'</textarea><label>隨身物品</label><textarea id="inv'+p.id+'" '+(can?'':'disabled')+'>'+esc(c.inventory||'')+'</textarea><label>公開備註</label><textarea id="pn'+p.id+'" '+(can?'':'disabled')+'>'+esc(c.public_notes||'')+'</textarea><label>私人備註（僅本人＋KP）</label><textarea id="pr'+p.id+'" '+(can?'':'disabled')+'>'+esc(c.private_notes||'')+'</textarea><label>角色卡可見範圍</label><select id="vis'+p.id+'" '+(can?'':'disabled')+'><option value="kp_only" '+(c.visibility!=='public'?'selected':'')+'>僅本人＋KP</option><option value="public" '+(c.visibility==='public'?'selected':'')+'>全房間公開</option></select>'+(can?'<button onclick="saveChar(\''+p.id+'\')">儲存角色卡</button><details><summary>匯入 CoC7 Google 試算表</summary><div class="small">支援你提供的「CoC7版自動計算人物卡V3」格式。連結需可檢視；私密表可改用複製 A1:Z150 後貼上。</div><input id="url'+p.id+'" placeholder="Google 試算表連結"><button class="alt" onclick="importSheet(\''+p.id+'\',\'url\')">讀取連結</button><textarea id="paste'+p.id+'" placeholder="或貼上試算表 A1:Z150 資料"></textarea><button class="alt" onclick="importSheet(\''+p.id+'\',\'text\')">讀取貼上資料</button></details>':'')+'</div>'}
function parseSkills(t){let o={};t.split(/\n+/).forEach(l=>{let m=l.match(/^(.+?)[=:：]\s*(\d{1,3})\s*$/);if(m)o[m[1].trim()]=Math.max(0,Math.min(100,+m[2]))});return o}async function saveChar(id){try{let attrs={};['STR','CON','SIZ','DEX','APP','INT','POW','EDU'].forEach(k=>attrs[k]=+$('a'+k+id).value);let ch={name:$('cn'+id).value,occupation:$('co'+id).value,age:$('ca'+id).value,attrs,hp:+$('hp'+id).value,mp:+$('mp'+id).value,san:+$('san'+id).value,luck:+$('luck'+id).value,skills:parseSkills($('sk'+id).value),inventory:$('inv'+id).value,public_notes:$('pn'+id).value,private_notes:$('pr'+id).value,visibility:$('vis'+id).value,source:'manual'};await api('/api/room/'+S.code+'/char',{method:'POST',body:JSON.stringify({token:S.token,pid:id,char:ch})});await refresh();alert('角色卡已儲存')}catch(e){alert(e.message)}}async function importSheet(id,kind){try{let body={token:S.token,pid:id};if(kind==='url')body.url=$('url'+id).value;else body.text=$('paste'+id).value;let d=await api('/api/room/'+S.code+'/import-sheet',{method:'POST',body:JSON.stringify(body)});applyPreview(id,d.preview);alert('已讀取試算表，請檢查後按「儲存角色卡」')}catch(e){alert(e.message)}}function applyPreview(id,c){$('cn'+id).value=c.name||'';$('co'+id).value=c.occupation||'';$('ca'+id).value=c.age||'';Object.entries(c.attrs||{}).forEach(([k,v])=>{let e=$('a'+k+id);if(e)e.value=v});$('hp'+id).value=c.hp;$('mp'+id).value=c.mp;$('san'+id).value=c.san;$('luck'+id).value=c.luck;$('sk'+id).value=Object.entries(c.skills||{}).map(([k,v])=>k+'='+v).join('\n')}
async function send(){let t=$('ci').value.trim();if(!t)return;try{await api('/api/room/'+S.code+'/chat',{method:'POST',body:JSON.stringify({token:S.token,text:t})});$('ci').value='';refresh()}catch(e){alert(e.message)}}async function pushLast(){try{await api('/api/room/'+S.code+'/chat',{method:'POST',body:JSON.stringify({token:S.token,text:'.push'})});refresh();tab('story')}catch(e){alert(e.message)}}async function roll(){try{let d=await api('/api/room/'+S.code+'/roll',{method:'POST',body:JSON.stringify({token:S.token,label:$('lab').value,target:$('tar').value?+$('tar').value:null,mode:$('mode').value,difficulty:$('diff').value,hidden:$('hid').checked})});$('rr').textContent=d.hidden?'暗骰完成':'結果：'+d.value+(d.result?' · '+d.result+(d.meets?' ✅':' ❌'):'');refresh()}catch(e){alert(e.message)}}async function setLimit(){try{await api('/api/room/'+S.code+'/limit',{method:'POST',body:JSON.stringify({token:S.token,limit:+$('nl').value})});refresh()}catch(e){alert(e.message)}}async function setScene(){try{await api('/api/room/'+S.code+'/scene',{method:'POST',body:JSON.stringify({token:S.token,scene:$('ss').value})});refresh()}catch(e){alert(e.message)}}async function addClue(){try{await api('/api/room/'+S.code+'/clue',{method:'POST',body:JSON.stringify({token:S.token,text:$('cl').value,visibility:$('cv').value,target:$('ct').value||null})});$('cl').value='';refresh()}catch(e){alert(e.message)}}async function saveNote(){try{await api('/api/room/'+S.code+'/note',{method:'POST',body:JSON.stringify({token:S.token,text:$('nt').value})});alert('已儲存')}catch(e){alert(e.message)}}async function setCoKp(id,en){try{await api('/api/room/'+S.code+'/co-kp',{method:'POST',body:JSON.stringify({token:S.token,pid:id,enabled:en})});refresh()}catch(e){alert(e.message)}}async function endRoom(){if(confirm('確定結束房間？')){try{await api('/api/room/'+S.code+'/end',{method:'POST',body:JSON.stringify({token:S.token})});refresh()}catch(e){alert(e.message)}}}function tab(id){document.querySelectorAll('.sec').forEach(x=>x.classList.remove('on'));$(id).classList.add('on');scrollTo(0,0)}async function clip(t){try{await navigator.clipboard.writeText(t);alert('已複製')}catch{prompt('請複製：',t)}}function copyCode(){clip(S.code)}function copyInvite(){clip(location.origin+'/?room='+S.code)}let qp=new URLSearchParams(location.search);if(qp.get('room')){$('rc').value=qp.get('room').toUpperCase();$('gn').focus()}try{let d=JSON.parse(localStorage.getItem('coc-v5')||'null');if(d&&d.code&&d.token)enter(d)}catch(e){}
</script></body></html>'''