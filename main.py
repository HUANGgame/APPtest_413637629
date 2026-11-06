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


def actor_name(r, role, aid):
    if role == 'owner':
        return r.get('owner_name') or 'KP'
    p = own_player(r, aid)
    return (p.get('name') if p else None) or ('KP' if role == 'kp' else '訪客')


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
    d.update(role=role, aid=aid, count=investigator_count(r), owner_name=r.get('owner_name','KP'), self_name=actor_name(r,role,aid))
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
    if role == 'owner':
        d['kp_invite'] = r['kp_invite']
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




def format_basic_roll(expr: str):
    """Roll a simple + / - dice expression and format each die term like 3[3]+18[18]+1 = 22."""
    expr0 = expr.strip()
    compact = expr0.lower().replace(' ', '')
    if not compact or len(compact) > 80 or not re.fullmatch(r'[0-9d+\-]+', compact):
        raise ValueError('骰式格式錯誤，例如：1D3+1、1d6+1d20')
    tokens = re.findall(r'[+\-]?[^+\-]+', compact)
    if len(tokens) > 12:
        raise ValueError('骰式過長')
    total = 0
    parts = []
    for idx, tok in enumerate(tokens):
        sign = -1 if tok.startswith('-') else 1
        core = tok.lstrip('+-')
        prefix = '-' if sign < 0 else ('+' if idx > 0 else '')
        if 'd' in core:
            a,b = core.split('d',1)
            n = int(a or '1'); sides = int(b)
            if not 1 <= n <= 30 or not 2 <= sides <= 1000:
                raise ValueError('骰子數量或面數超出範圍')
            rs = [random.randint(1,sides) for _ in range(n)]
            subtotal = sum(rs)
            total += sign * subtotal
            shown = '+'.join(str(x) for x in rs)
            parts.append(f"{prefix}{subtotal}[{shown}]")
        else:
            v = int(core)
            total += sign * v
            parts.append(f"{prefix}{v}")
    return total, ''.join(parts)


def basic_roll_command(raw: str):
    """Support `1D3+1` and repeated form `3 1d6+1d20`."""
    m = re.fullmatch(r'\s*(\d+)\s+([0-9dD+\-]+)\s*', raw)
    if m:
        count = int(m.group(1)); expr = m.group(2)
        if not 1 <= count <= 30:
            raise ValueError('重複擲骰次數需為 1～30')
        lines = ['基本骰組：（複數擲骰）']
        for i in range(1, count+1):
            total, detail = format_basic_roll(expr)
            lines.append(f'第{i}次擲骰：{detail} = {total}')
        return '\n'.join(lines)
    if re.fullmatch(r'\s*[0-9dD+\-]+\s*', raw) and re.search(r'[dD]', raw):
        total, detail = format_basic_roll(raw)
        return f'基本骰組：{detail} = {total}'
    return None


def ccyc_command():
    lines = ['COC7 創角骰組（Ccyc）', '', '七次(3d6)*5，自選其中五個結果任意分配於 STR、CON、DEX、APP、POW。']
    for i in range(1,8):
        rs=[random.randint(1,6) for _ in range(3)]; val=sum(rs)*5
        lines.append(f'第{i}次投骰 → [{",".join(map(str,rs))}]*5 →{val}')
    lines += ['', '四次(2d6+6)*5，自選其中三個結果任意分配於 SIZ、INT、EDU。']
    for i in range(1,5):
        rs=[random.randint(1,6) for _ in range(2)]; val=(sum(rs)+6)*5
        lines.append(f'第{i}次投骰 → ([{",".join(map(str,rs))}]+6)*5 →{val}')
    lines += ['', '兩次(3d6)*5，自選其一填入 LUK。']
    for i in range(1,3):
        rs=[random.randint(1,6) for _ in range(3)]; val=sum(rs)*5
        lines.append(f'第{i}次投骰 → [{",".join(map(str,rs))}]*5 →{val}')
    return '\n'.join(lines)


def cc_compare_command(raw: str):
    m = re.fullmatch(r'(?i)cc\s*(>=|<=|==|=|>|<)\s*(\d{1,3})\s*', raw)
    if not m:
        return None
    op = m.group(1); target = int(m.group(2))
    if not 0 <= target <= 100:
        raise ValueError('Cc 比較值需為 0～100')
    roll = random.randint(1,100)
    ok = {'>':roll>target,'<':roll<target,'>=':roll>=target,'<=':roll<=target,'=':roll==target,'==':roll==target}[op]
    verdict = '成功' if ok else '失敗'
    growth = '該技能可成長。' if ok else '該技能無成長。'
    return f'(1D100{op}{target}) → {roll} → {verdict}\n{growth}'


def coc_check_command(raw: str):
    """HKTRPG-style direct CoC checks: Cc 60, Cc1 60/Cc2 60, Ccn1 60/Ccn2 60."""
    m = re.fullmatch(r'(?i)cc(?:(n)?([12]))?\s+(\d{1,3})\s*', raw)
    if not m:
        return None
    penalty = bool(m.group(1))
    extra = int(m.group(2) or 0)
    target = int(m.group(3))
    if not 0 <= target <= 100:
        raise ValueError('Cc 技能值需為 0～100')
    value, candidates = percentile_candidates(extra, penalty)
    level = coc_level(value, target)
    mode = ''
    if extra:
        mode = f"｜{'懲罰骰' if penalty else '獎勵骰'} {extra} 顆｜候選 {candidates}"
    return f'CoC7 檢定 Cc{("n" if penalty else "")}{extra if extra else ""} {target} → {value} → {level}{mode}'


def d66_command(raw: str):
    m = re.fullmatch(r'(?i)d66([sn]?)\s*', raw)
    if not m:
        return None
    a, b = random.randint(1,6), random.randint(1,6)
    mode = m.group(1).lower()
    if mode == 's':
        x, y = sorted((a,b)); label='D66s（小數在前）'
    elif mode == 'n':
        x, y = sorted((a,b), reverse=True); label='D66n（大數在前）'
    else:
        x, y = a, b; label='D66'
    return f'{label} → [{a},{b}] → {x}{y}'


def keepdrop_command(raw: str):
    m = re.fullmatch(r'(?i)(\d*)d(\d+)(kh|kl|dh|dl)(\d*)\s*', raw)
    if not m:
        return None
    n = int(m.group(1) or 1); sides=int(m.group(2)); op=m.group(3).lower(); k=int(m.group(4) or 1)
    if not 1 <= n <= 30 or not 2 <= sides <= 1000:
        raise ValueError('骰子數量或面數超出範圍')
    if not 1 <= k <= n:
        raise ValueError('保留／捨棄數量必須介於 1 到骰子數量之間')
    rs=[random.randint(1,sides) for _ in range(n)]
    indexed=list(enumerate(rs))
    if op=='kh':
        chosen=sorted(indexed,key=lambda x:x[1],reverse=True)[:k]; verb='保留最高'
    elif op=='kl':
        chosen=sorted(indexed,key=lambda x:x[1])[:k]; verb='保留最低'
    elif op=='dh':
        drop={i for i,_ in sorted(indexed,key=lambda x:x[1],reverse=True)[:k]}; chosen=[x for x in indexed if x[0] not in drop]; verb='捨棄最高'
    else:
        drop={i for i,_ in sorted(indexed,key=lambda x:x[1])[:k]}; chosen=[x for x in indexed if x[0] not in drop]; verb='捨棄最低'
    kept=[v for _,v in chosen]
    return f'{n}D{sides}{op}{k if k!=1 else ""} → {rs}｜{verb}{k} → {kept} = {sum(kept)}'


def comment_basic_roll_command(raw: str):
    m = re.fullmatch(r'\s*([0-9dD+\-]+)\s+(.+?)\s*', raw)
    if not m or not re.search(r'[dD]', m.group(1)):
        return None
    expr, note = m.group(1), m.group(2)
    total, detail = format_basic_roll(expr)
    return f'基本骰組：{detail} = {total}｜{note}'


def looks_like_dice_command(text: str):
    t=text.strip()
    if not t: return False
    if re.match(r'(?i)^dr\s+', t):
        return looks_like_dice_command(re.sub(r'(?i)^dr\s+','',t,1))
    if re.fullmatch(r'(?i)ccyc', t): return True
    if re.fullmatch(r'(?i)cc\s*(>=|<=|==|=|>|<)\s*\d{1,3}', t): return True
    if re.fullmatch(r'(?i)cc(?:(n)?[12])?\s+\d{1,3}', t): return True
    if re.fullmatch(r'(?i)d66[sn]?', t): return True
    if re.fullmatch(r'(?i)\d*d\d+(kh|kl|dh|dl)\d*', t): return True
    if re.fullmatch(r'\d+\s+[0-9dD+\-]+', t): return True
    if re.fullmatch(r'[0-9dD+\-]+', t) and re.search(r'[dD]', t): return True
    if re.fullmatch(r'[0-9dD+\-]+\s+.+', t) and re.search(r'[dD]', t.split()[0]): return True
    return False

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
    if re.match(r'(?i)^dr\s+', raw):
        if not is_kp(role):
            raise ValueError('Dr 暗骰只限 KP 使用')
        hidden = True
        raw = re.sub(r'(?i)^dr\s+','',raw,1).strip()
    lower = raw.lower()
    if lower == 'ccyc':
        return ccyc_command(), hidden
    cmp_msg = cc_compare_command(raw)
    if cmp_msg is not None: return cmp_msg, hidden
    cc_msg = coc_check_command(raw)
    if cc_msg is not None: return cc_msg, hidden
    d66_msg = d66_command(raw)
    if d66_msg is not None: return d66_msg, hidden
    kd_msg = keepdrop_command(raw)
    if kd_msg is not None: return kd_msg, hidden
    basic_msg = basic_roll_command(raw)
    if basic_msg is not None: return basic_msg, hidden
    comment_msg = comment_basic_roll_command(raw)
    if comment_msg is not None: return comment_msg, hidden
    raise ValueError('未知骰子指令。可用：Ccyc、Cc 60、Cc1 60、Ccn1 60、Cc>70、1D100、3 3D6、D66、3D6kh、Dr Cc 60')

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
    name: str = '未命名房間'; scenario: str = '自由跑團'; limit: int = 4; kp_name: str = 'KP'
class Join(BaseModel):
    code: str; name: str
class JoinKp(BaseModel):
    code: str; name: str; invite: str
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
class KickIn(Token): pid: str
class RenameIn(Token): name: str; pid: Optional[str]=None
class ImportIn(Token): pid: str; url: Optional[str]=None; text: Optional[str]=None


@app.get('/', response_class=HTMLResponse)
def home():
    return HTMLResponse(HTML, headers={'Cache-Control':'no-store, max-age=0'})
@app.head('/')
def home_head():
    return HTMLResponse('', headers={'Cache-Control':'no-store, max-age=0'})
@app.get('/health')
def health(): return {'ok':1,'rooms':len(rooms),'version':'v8.10'}

@app.post('/api/create')
def create(x: Create):
    if not 1 <= x.limit <= 50: raise HTTPException(400,'人數需 1~50')
    c=make_code(); t=secrets.token_urlsafe(24); ki=secrets.token_urlsafe(12); sc=SCENES.get(x.scenario,SCENES['自由跑團'])
    rooms[c]={'code':c,'name':x.name.strip() or '未命名房間','scenario':x.scenario,'limit':x.limit,
              'owner':t,'owner_name':(x.kp_name.strip()[:30] or 'KP'),'kp_invite':ki,'ps':[],'scene':sc[0],'scenes':sc,'chat':[{'who':'系統','text':'房間已建立','kind':'system'}],
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

@app.post('/api/join-kp')
def join_kp(x: JoinKp):
    r=rooms.get(x.code.upper().strip())
    if not r: raise HTTPException(404,'找不到房間')
    if r['ended']: raise HTTPException(409,'房間已結束')
    if not secrets.compare_digest(str(x.invite or ''), str(r.get('kp_invite',''))):
        raise HTTPException(403,'共同 KP 邀請無效')
    p={'id':secrets.token_hex(5),'token':secrets.token_urlsafe(24),'name':x.name.strip() or '共同KP','co_kp':True,'npc':False,'char':blank_char()}
    r['ps'].append(p); r['chat'].append({'who':'系統','text':p['name']+' 以共同 KP 身分加入房間','kind':'system'})
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
    who=actor_name(r,role,aid)
    text=x.text.strip()
    if not text: return {'ok':1}
    if looks_like_dice_command(text):
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
    who=actor_name(r,role,aid)
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

@app.post('/api/room/{c}/rename')
def rename_member(c:str,x:RenameIn):
    r=rooms.get(c.upper())
    if not r: raise HTTPException(404,'找不到房間')
    role,aid=auth(r,x.token)
    new_name=(x.name or '').strip()[:30]
    if not new_name: raise HTTPException(400,'名稱不可空白')
    if x.pid:
        p=next((p for p in r['ps'] if p['id']==x.pid),None)
        if not p: raise HTTPException(404,'找不到玩家')
        if not is_kp(role) and aid!=x.pid: raise HTTPException(403,'不能修改別人的名稱')
        old=p['name']; p['name']=new_name
    else:
        if role=='owner':
            old=r.get('owner_name','KP'); r['owner_name']=new_name
        else:
            p=own_player(r,aid)
            if not p: raise HTTPException(404,'找不到玩家')
            old=p['name']; p['name']=new_name
    if old!=new_name:
        r['chat'].append({'who':'系統','text':old+' 更名為 '+new_name,'kind':'system'})
    return {'ok':1,'name':new_name}

@app.post('/api/room/{c}/co-kp')
def co_kp(c:str,x:CoKpIn):
    r=rooms.get(c.upper()); role,_=auth(r,x.token)
    if role!='owner': raise HTTPException(403,'只有房主 KP 可以管理共同 KP')
    p=next((p for p in r['ps'] if p['id']==x.pid),None)
    if not p: raise HTTPException(404,'找不到玩家')
    if not x.enabled and investigator_count(r) >= r['limit'] and p.get('co_kp'):
        raise HTTPException(409,'調查員名額已滿，無法取消共同 KP')
    p['co_kp']=bool(x.enabled); return {'ok':1}

@app.post('/api/room/{c}/leave')
def leave_room(c:str,x:Token):
    r=rooms.get(c.upper())
    if not r: raise HTTPException(404,'找不到房間')
    role,aid=auth(r,x.token)
    if role=='owner': raise HTTPException(403,'房主請使用解散房間')
    p=next((p for p in r['ps'] if p['id']==aid),None)
    if not p: raise HTTPException(404,'找不到玩家')
    r['ps']=[q for q in r['ps'] if q['id']!=aid]
    r.setdefault('last_rolls',{}).pop(aid,None)
    r['chat'].append({'who':'系統','text':p['name']+' 已離開房間','kind':'system'})
    return {'ok':1}

@app.post('/api/room/{c}/kick')
def kick_player(c:str,x:KickIn):
    r=rooms.get(c.upper())
    if not r: raise HTTPException(404,'找不到房間')
    role,aid=auth(r,x.token)
    if not is_kp(role): raise HTTPException(403,'只有 KP 可以踢人')
    p=next((p for p in r['ps'] if p['id']==x.pid),None)
    if not p: raise HTTPException(404,'找不到玩家')
    if aid==x.pid: raise HTTPException(400,'請使用離開房間')
    if role=='kp' and p.get('co_kp'): raise HTTPException(403,'共同 KP 不能踢除其他 KP')
    r['ps']=[q for q in r['ps'] if q['id']!=x.pid]
    r.setdefault('last_rolls',{}).pop(x.pid,None)
    r['chat'].append({'who':'系統','text':p['name']+' 已被 KP 移出房間','kind':'system'})
    return {'ok':1}

@app.post('/api/room/{c}/disband')
def disband_room(c:str,x:Token):
    r=rooms.get(c.upper())
    if not r: raise HTTPException(404,'找不到房間')
    role,_=auth(r,x.token)
    if role!='owner': raise HTTPException(403,'只有房主 KP 可以解散房間')
    rooms.pop(c.upper(),None)
    return {'ok':1}

@app.post('/api/room/{c}/end')
def end(c:str,x:Token):
    r=rooms.get(c.upper()); role,_=auth(r,x.token)
    if not is_kp(role): raise HTTPException(403,'只有 KP 可以結束房間')
    r['ended']=True; r['chat'].append({'who':'系統','text':'房間已結束','kind':'system'}); return {'ok':1}

@app.get('/selftest')
def selftest():
    checks=[]; c=None
    try:
        a=create(Create(name='QA',scenario='營火',limit=1,kp_name='守密人魚')); c=a['code']; assert rooms[c]['owner_name']=='守密人魚'; checks.append('create')
        g=join(Join(code=c,name='A')); checks.append('join')
        try:
            join(Join(code=c,name='B')); raise AssertionError('capacity failed')
        except HTTPException as e:
            assert e.status_code==409; checks.append('capacity')
        set_limit(c,LimitIn(token=a['token'],limit=2)); checks.append('limit')
        g2=join(Join(code=c,name='B')); checks.append('join2')
        st=state(c,a['token']); assert st['count']==2 and st['role']=='owner' and st.get('kp_invite') and st['owner_name']=='守密人魚'; checks.append('state')
        rename_member(c,RenameIn(token=a['token'],name='魚KP')); assert state(c,a['token'])['owner_name']=='魚KP'; checks.append('owner_rename')
        pida0=next(p['id'] for p in rooms[c]['ps'] if p['token']==g['token'])
        rename_member(c,RenameIn(token=g['token'],pid=pida0,name='玩家甲')); assert own_player(rooms[c],pida0)['name']=='玩家甲'; checks.append('player_rename')
        assert 'kp_invite' not in state(c,g['token']); checks.append('kp_invite_private')
        kg=join_kp(JoinKp(code=c,name='副KP',invite=rooms[c]['kp_invite']))
        kst=state(c,kg['token']); assert kst['role']=='kp' and kst['count']==2; checks.append('kp_direct_join')
        try:
            join_kp(JoinKp(code=c,name='假KP',invite='wrong')); raise AssertionError('bad kp invite accepted')
        except HTTPException as e:
            assert e.status_code==403; checks.append('kp_invite_guard')
        # character privacy + save
        pida=next(p['id'] for p in rooms[c]['ps'] if p['token']==g['token'])
        ch=blank_char(); ch['name']='調查員A';ch['skills']['偵查']=60;ch['private_notes']='秘密';ch['visibility']='kp_only'
        save_char(c,CharIn(token=g['token'],pid=pida,char=ch)); checks.append('char_save')
        guest2=state(c,g2['token']); other=next(p for p in guest2['players'] if p['id']==pida); assert other['char_hidden']; checks.append('privacy')
        kpst=state(c,a['token']); full=next(p for p in kpst['players'] if p['id']==pida); assert full['char']['private_notes']=='秘密'; checks.append('kp_view')
        # user-defined dice commands only; legacy dot commands must stay ordinary chat
        chat(c,ChatIn(token=g['token'],text='.ra 偵查 60')); assert rooms[c]['chat'][-1]['who']=='玩家甲' and rooms[c]['chat'][-1]['text']=='.ra 偵查 60'; checks.append('legacy_dot_disabled')
        # user-defined raw dice / character generation / comparator commands
        chat(c,ChatIn(token=g['token'],text='Ccyc')); assert '七次(3d6)*5' in rooms[c]['chat'][-1]['text'] and '兩次(3d6)*5' in rooms[c]['chat'][-1]['text']; checks.append('ccyc')
        chat(c,ChatIn(token=g['token'],text='Cc>70')); assert '(1D100>70)' in rooms[c]['chat'][-1]['text'] and ('該技能可成長。' in rooms[c]['chat'][-1]['text'] or '該技能無成長。' in rooms[c]['chat'][-1]['text']); checks.append('cc_compare')
        chat(c,ChatIn(token=g['token'],text='3 1d6+1d20')); assert '基本骰組：（複數擲骰）' in rooms[c]['chat'][-1]['text'] and '第3次擲骰' in rooms[c]['chat'][-1]['text']; checks.append('multi_basic')
        chat(c,ChatIn(token=g['token'],text='1D3+1')); assert rooms[c]['chat'][-1]['text'].startswith('基本骰組：') and ' = ' in rooms[c]['chat'][-1]['text']; checks.append('single_basic')
        chat(c,ChatIn(token=g['token'],text='Cc 60')); assert 'CoC7 檢定' in rooms[c]['chat'][-1]['text']; checks.append('cc_check')
        chat(c,ChatIn(token=g['token'],text='Cc1 60')); assert '獎勵骰 1 顆' in rooms[c]['chat'][-1]['text']; checks.append('cc_bonus')
        chat(c,ChatIn(token=g['token'],text='Ccn1 60')); assert '懲罰骰 1 顆' in rooms[c]['chat'][-1]['text']; checks.append('cc_penalty')
        chat(c,ChatIn(token=g['token'],text='D66s')); assert 'D66s' in rooms[c]['chat'][-1]['text']; checks.append('d66')
        chat(c,ChatIn(token=g['token'],text='3D6kh')); assert '保留最高' in rooms[c]['chat'][-1]['text']; checks.append('keepdrop')
        chat(c,ChatIn(token=a['token'],text='Dr Cc 60')); assert rooms[c]['hidden'] and 'CoC7 檢定' in rooms[c]['hidden'][-1]['text']; checks.append('darkroll')
        chat(c,ChatIn(token=g['token'],text='1D100 偵查')); assert '｜偵查' in rooms[c]['chat'][-1]['text']; checks.append('dice_comment')
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
        # room-management flow
        m=create(Create(name='MGMT',scenario='自由跑團',limit=3)); mc=m['code']
        ma=join(Join(code=mc,name='M-A')); mb=join(Join(code=mc,name='M-B'))
        pma=next(p['id'] for p in rooms[mc]['ps'] if p['token']==ma['token'])
        pmb=next(p['id'] for p in rooms[mc]['ps'] if p['token']==mb['token'])
        co_kp(mc,CoKpIn(token=m['token'],pid=pma,enabled=True)); checks.append('mgmt_promote')
        kick_player(mc,KickIn(token=ma['token'],pid=pmb)); assert len(rooms[mc]['ps'])==1; checks.append('mgmt_kick')
        leave_room(mc,Token(token=ma['token'])); assert len(rooms[mc]['ps'])==0; checks.append('mgmt_leave')
        disband_room(mc,Token(token=m['token']))
        try:
            state(mc,m['token']); raise AssertionError('disband failed')
        except HTTPException as e:
            assert e.status_code==404; checks.append('mgmt_disband')
        end(c,Token(token=a['token']));
        try:
            chat(c,ChatIn(token=g['token'],text='x')); raise AssertionError('end lock failed')
        except HTTPException as e:
            assert e.status_code==409; checks.append('end_lock')
        return {'ok':True,'checks':checks,'count':len(checks),'version':'v8.9'}
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
:root{--bg:#07110d;--card:#0e1d16;--line:#294b3a;--green:#7dd3a7;--muted:#9db1a5;--red:#8a3030;--gold:#d6bc7a}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#eef7f0;font-family:system-ui,-apple-system,sans-serif}.w{max-width:1100px;margin:auto;padding:18px 14px 90px}.hero{text-align:center;padding:22px 0}.grid,.room{display:grid;grid-template-columns:1fr 1fr;gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:15px;margin-bottom:14px}input,select,textarea,button{font:inherit}input,select,textarea{width:100%;margin:4px 0 9px;padding:10px;border-radius:10px;border:1px solid #315642;background:#09140f;color:#fff}textarea{min-height:90px}button{padding:10px 13px;border:0;border-radius:10px;background:var(--green);color:#062015;font-weight:800;cursor:pointer}button:disabled{opacity:.4;cursor:not-allowed}.alt{background:#173629;color:#fff}.bad{background:var(--red);color:#fff}.gold{background:var(--gold);color:#211907}.hide{display:none!important}.chat{height:48vh;overflow:auto;border:1px solid var(--line);border-radius:12px;padding:10px}.m{margin:0 0 9px;white-space:pre-wrap}.who{color:var(--green);font-weight:800}.row{display:flex;gap:8px}.row>*{flex:1}.small{color:var(--muted);font-size:13px}.pill{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:5px 9px;margin:3px}.tabs{display:flex;gap:7px;margin:10px 0;flex-wrap:wrap}.tabs button{background:#173629;color:#fff}.sec{display:none}.sec.on{display:block}.bottom{display:none;position:fixed;bottom:0;left:0;right:0;background:#0b1812;padding:8px;grid-template-columns:repeat(2,1fr);gap:8px;z-index:5}.attrgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.skillgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:6px}.clue{padding:9px;border-left:3px solid var(--green);background:#0a1711;margin:7px 0}.modal{position:fixed;inset:0;background:#000a;display:flex;align-items:center;justify-content:center;padding:15px;z-index:20}.modalbox{max-width:760px;max-height:85vh;overflow:auto;background:#102219;border:1px solid var(--line);border-radius:18px;padding:18px}.topbar{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}.code{font-family:ui-monospace,monospace;font-size:18px;color:var(--gold)}details{border:1px solid var(--line);border-radius:10px;padding:9px;margin:8px 0}.cmdrow{display:flex;align-items:center;justify-content:space-between;gap:8px;border-bottom:1px solid #1f3a2c;padding:8px 0}.cmdrow code{color:var(--gold);font-weight:800}.quickgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:7px}.quickgrid button{background:#173629;color:#fff;text-align:left}a{color:var(--green)}
@media(max-width:720px){.grid,.room{grid-template-columns:1fr}.bottom{display:grid}.desktop{display:none}.attrgrid{grid-template-columns:repeat(2,1fr)}.skillgrid{grid-template-columns:1fr}.chat{height:45vh}}
</style></head><body><div class="w">
<section id="home"><div class="hero"><h1>克蘇魯調查局</h1><div class="small">CoC 7 文字跑團｜訪客免帳號 · v8.10</div><button class="alt" onclick="rules()">規則／骰娘指令</button></div><div class="grid"><div class="card"><h2>KP 開房</h2><label>KP 名稱</label><input id="kpn" placeholder="例如：阿魚"><label>房間名稱</label><input id="rn" placeholder="例如：週五克蘇魯團"><label>劇本</label><select id="sc"><option>自由跑團</option><option>營火</option></select><label>調查員人數上限</label><input id="lim" type="number" min="1" max="50" value="4"><button onclick="createRoom()">建立房間</button></div><div class="card"><h2>訪客加入</h2><label>暱稱</label><input id="gn" placeholder="你的暱稱"><label>房號</label><input id="rc" maxlength="6"><button onclick="joinRoom()">加入房間</button></div><div class="card"><h2>共同 KP 加入</h2><div class="small">通常由房主傳「共同 KP 邀請連結」給你。</div><label>暱稱</label><input id="gkn" placeholder="KP 暱稱"><label>房號</label><input id="gkc" maxlength="6"><label>共同 KP 邀請碼</label><input id="gki" placeholder="邀請連結會自動填入"><button onclick="joinKpRoom()">以共同 KP 加入</button></div></div><div id="err"></div></section>
<section id="game" class="hide"><div class="topbar"><div><h2 id="title"></h2><span class="pill" id="scene"></span><span class="pill" id="count"></span><span class="pill" id="role"></span></div><div><div class="code" id="roomcode"></div><button class="alt" onclick="copyCode()">複製房號</button> <button class="alt" onclick="copyInvite()">複製玩家邀請</button> <button id="kpinvitebtn" class="alt hide" onclick="copyKpInvite()">複製共同 KP 邀請</button> <button class="alt" onclick="toggleNameEdit()">更改名字</button> <button class="gold" onclick="rules()">規則</button> <button id="leavebtn" class="bad" onclick="leaveRoom()">離開房間</button> <button id="disbandbtn" class="bad hide" onclick="disbandRoom()">解散房間</button></div></div><div id="nameedit" class="card hide"><label>房間顯示名稱</label><div class="row"><input id="selfname" maxlength="30" placeholder="輸入新名稱"><button onclick="renameSelf()">儲存名稱</button><button class="alt" onclick="toggleNameEdit(false)">取消</button></div><div class="small">只修改你在房間與聊天中的顯示名稱；角色卡內的角色名稱不會一起改。</div></div><div id="ended" class="card hide">房間已結束，聊天、擲骰與角色修改已停用。</div><div class="tabs desktop"><button onclick="tab('story')">故事</button><button onclick="tab('chars')">角色</button><button onclick="tab('dice')">擲骰</button><button class="kp" onclick="tab('kp')">KP 主持台</button></div><div class="room"><main>
<div id="story" class="sec on"><div id="chat" class="chat"></div><div class="row"><input id="ci" placeholder="留言或輸入骰子指令，例如 Ccyc、Cc>70、3 1d6+1d20" onkeydown="if(event.key==='Enter')send()"><button id="sendbtn" onclick="send()">送出</button></div></div>
<div id="chars" class="sec"><div class="card"><h2>角色卡</h2><div class="small">角色卡預設「僅本人＋KP」。本人可改成全房公開；私人筆記永遠只有本人與 KP 看得到。</div><div id="players"></div></div></div>
<div id="dice" class="sec"><div class="card"><h2>骰子工具 v8</h2><p>採用直接指令風格，不使用 <code>.ra</code> 等舊點指令。</p><label>骰子指令</label><input id="dicecmd" placeholder="例如 Ccyc、Cc 60、Cc>70、1D100、3 3D6"><button id="rollbtn" onclick="sendDiceCmd()">執行骰子指令</button><hr><h3>快捷指令（按鈕＝複製）</h3><div class="quickgrid"><button onclick="copyCmd('Ccyc')">📋 Ccyc</button><button onclick="copyCmd('Cc 60')">📋 Cc 60</button><button onclick="copyCmd('Cc1 60')">📋 Cc1 60</button><button onclick="copyCmd('Ccn1 60')">📋 Ccn1 60</button><button onclick="copyCmd('Cc>70')">📋 Cc&gt;70</button><button onclick="copyCmd('1D100')">📋 1D100</button><button onclick="copyCmd('3 3D6')">📋 3 3D6</button><button onclick="copyCmd('D66')">📋 D66</button><button onclick="copyCmd('3D6kh')">📋 3D6kh</button><button class="kp" onclick="copyCmd('Dr Cc 60')">📋 Dr Cc 60</button></div></div></div>
<div id="kp" class="sec kp"><div class="card"><h2>KP 主持台</h2><div id="ownernamebox"><label>我的 KP 名稱</label><div class="row"><input id="ownername"><button onclick="renameOwner()">修改名稱</button></div></div><label>調查員人數上限</label><div class="row"><input id="nl" type="number" min="1" max="50"><button onclick="setLimit()">修改</button></div><label>場景</label><select id="ss"></select><button onclick="setScene()">切換場景</button><hr><h3>線索</h3><div class="small">公開＝全房看見；僅KP＝所有KP可見；指定玩家＝只有KP與該玩家可見。</div><input id="cl" placeholder="輸入線索內容"><select id="cv"><option value="public">公開</option><option value="kp">僅KP</option><option value="player">指定玩家</option></select><select id="ct"></select><button onclick="addClue()">新增線索</button><hr><label>KP 私人筆記</label><textarea id="nt"></textarea><button class="alt" onclick="saveNote()">儲存筆記</button><hr><div id="kokp"></div><hr><div class="small">結束遊戲＝房間保留但停止操作；解散房間＝所有人立即離開且房間消失。</div><button class="bad" onclick="endRoom()">結束遊戲</button> <button id="kpdisband" class="bad hide" onclick="disbandRoom()">解散房間</button></div></div>
</main><aside><div class="card"><h3>線索</h3><div id="clues"></div></div><div class="card"><h3>玩家／KP</h3><div id="side"></div></div></aside></div></section></div>
<div id="bottom" class="bottom hide"><button onclick="tab('story')">故事</button><button onclick="tab('chars')">角色</button></div><div id="modal" class="modal hide" onclick="if(event.target.id==='modal')closeRules()"><div class="modalbox"><button style="float:right" onclick="closeRules()">關閉</button><h2>骰娘規則 v8</h2><p>這版補齊為「直接輸入骰式／CoC 指令」的脈絡，參考常見 HKTRPG ROLLBOT 語法，但依本平台需求保留你指定的 <b>Ccyc</b> 與 <b>Cc&gt;70</b>。舊的 <code>.ra</code>、<code>.rb</code> 等點指令維持停用。</p><h3>CoC 7 指令</h3><div class="cmdrow"><span><code>Ccyc</code>｜你的創角骰組：7 組 3D6×5、4 組 (2D6+6)×5、2 組 3D6×5</span><button onclick="copyCmd('Ccyc')">複製</button></div><div class="cmdrow"><span><code>Cc 60</code>｜CoC7 百分骰檢定，顯示大成功／極限／困難／一般成功／失敗／大失敗</span><button onclick="copyCmd('Cc 60')">複製</button></div><div class="cmdrow"><span><code>Cc1 60</code> / <code>Cc2 60</code>｜1／2 顆獎勵骰</span><button onclick="copyCmd('Cc1 60')">複製</button></div><div class="cmdrow"><span><code>Ccn1 60</code> / <code>Ccn2 60</code>｜1／2 顆懲罰骰</span><button onclick="copyCmd('Ccn1 60')">複製</button></div><div class="cmdrow"><span><code>Cc&gt;70</code>｜你指定的成長比較：擲 1D100 後比較，可用 &gt;、&lt;、&gt;=、&lt;=、=</span><button onclick="copyCmd('Cc>70')">複製</button></div><h3>基本骰式</h3><div class="cmdrow"><span><code>1D3+1</code>｜一般骰式</span><button onclick="copyCmd('1D3+1')">複製</button></div><div class="cmdrow"><span><code>3 1D6+1D20</code>｜重複 3 次擲同一骰式，最多 30 次</span><button onclick="copyCmd('3 1D6+1D20')">複製</button></div><div class="cmdrow"><span><code>1D100 偵查</code>｜骰式後可附文字註記</span><button onclick="copyCmd('1D100 偵查')">複製</button></div><div class="cmdrow"><span><code>3D6kh</code> / <code>3D6kh2</code>｜保留最高 1／2 顆；亦支援 kl、dh、dl</span><button onclick="copyCmd('3D6kh')">複製</button></div><h3>D66</h3><div class="cmdrow"><span><code>D66</code>｜兩顆 D6 組成兩位數</span><button onclick="copyCmd('D66')">複製</button></div><div class="cmdrow"><span><code>D66s</code>｜較小數字在前；<code>D66n</code>｜較大數字在前</span><button onclick="copyCmd('D66s')">複製</button></div><h3>暗骰</h3><div class="cmdrow"><span><code>Dr Cc 60</code>｜KP 暗骰；<code>Dr 1D100</code> 也可用。結果不出現在玩家聊天區</span><button onclick="copyCmd('Dr Cc 60')">複製</button></div><p class="small">任何不符合以上格式的內容都視為一般聊天。快捷按鈕會複製指令；若已在房間，會自動關閉規則框並把指令帶到聊天輸入框，確認後按送出即可執行。</p></div></div>
<script>
let S={code:'',token:'',st:null,timer:null};let lastChatKey='',forceChatBottom=true,lastPlayersKey='';const $=x=>document.getElementById(x);const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));async function api(u,o={}){let r=await fetch(u,{headers:{'Content-Type':'application/json'},...o}),d={};try{d=await r.json()}catch{}if(!r.ok)throw Error(d.detail||'操作失敗');return d}function showErr(e){$('err').innerHTML='<div class="card">⚠️ '+esc(e)+'</div>'}function toggleNameEdit(force){let box=$('nameedit');let open=typeof force==='boolean'?force:box.classList.contains('hide');box.classList.toggle('hide',!open);if(open){$('selfname').value=(S.st&&S.st.self_name)||'';setTimeout(()=>{$('selfname').focus();$('selfname').select()},30)}}async function renameSelf(){let name=$('selfname').value.trim();if(!name)return notify('名稱不可空白',true);try{let d=await api('/api/room/'+S.code+'/rename',{method:'POST',body:JSON.stringify({token:S.token,name:name})});toggleNameEdit(false);notify('✓ 名稱已改為 '+d.name);await refresh()}catch(e){notify(e.message,true)}}function notify(msg,bad=false){let toast=document.getElementById('apptoast');if(!toast){toast=document.createElement('div');toast.id='apptoast';toast.style.cssText='position:fixed;left:50%;bottom:78px;transform:translateX(-50%);max-width:88vw;background:#173629;color:#fff;border:1px solid #315642;padding:10px 14px;border-radius:999px;z-index:120;font-weight:800;box-shadow:0 6px 24px #0008;transition:.2s;text-align:center';document.body.appendChild(toast)}toast.style.background=bad?'#6f2b2b':'#173629';toast.textContent=msg;toast.style.opacity='1';clearTimeout(window.__appToastTimer);window.__appToastTimer=setTimeout(()=>{toast.style.opacity='0'},1800)}function rules(){$('modal').classList.remove('hide')}function closeRules(){$('modal').classList.add('hide')}async function copyCmd(t){try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(t)}else{let x=document.createElement('textarea');x.value=t;x.style.position='fixed';x.style.opacity='0';document.body.appendChild(x);x.focus();x.select();document.execCommand('copy');x.remove()}}catch(e){}closeRules();if(S.code){tab('story');$('ci').value=t;setTimeout(()=>{$('ci').focus();try{$('ci').setSelectionRange(t.length,t.length)}catch(e){}},60)}}
async function createRoom(){try{let d=await api('/api/create',{method:'POST',body:JSON.stringify({name:$('rn').value||'未命名房間',scenario:$('sc').value,limit:+$('lim').value,kp_name:$('kpn').value||'KP'})});enter(d)}catch(e){showErr(e.message)}}async function joinRoom(){try{let d=await api('/api/join',{method:'POST',body:JSON.stringify({code:$('rc').value.toUpperCase(),name:$('gn').value||'訪客'})});enter(d)}catch(e){showErr(e.message)}}async function joinKpRoom(){try{let d=await api('/api/join-kp',{method:'POST',body:JSON.stringify({code:$('gkc').value.toUpperCase(),name:$('gkn').value||'共同KP',invite:$('gki').value.trim()})});enter(d)}catch(e){showErr(e.message)}}function enter(d){S.code=d.code;S.token=d.token;localStorage.removeItem('coc-v8');localStorage.setItem('coc-v8',JSON.stringify(d));$('home').classList.add('hide');$('game').classList.remove('hide');$('bottom').classList.remove('hide');if(S.timer)clearInterval(S.timer);refresh();S.timer=setInterval(refresh,1400)}function resetHome(msg=''){localStorage.removeItem('coc-v8');S={code:'',token:'',st:null,timer:null};$('game').classList.add('hide');$('bottom').classList.add('hide');$('home').classList.remove('hide');if(msg)showErr(msg)}async function refresh(){if(!S.code)return;try{S.st=await api('/api/room/'+S.code+'?token='+encodeURIComponent(S.token));render()}catch(e){resetHome('你已離開、被移出，或房間已解散。')}}
function renderChat(items){let el=$('chat');let nearBottom=(el.scrollHeight-el.scrollTop-el.clientHeight)<80;let oldTop=el.scrollTop;let key=items.length+'|'+(items.length?(items[items.length-1].who||'')+'|'+(items[items.length-1].text||''):'');if(key!==lastChatKey){el.innerHTML=items.map(m=>'<div class="m"><span class="who">'+esc(m.who)+'</span> '+esc(m.text)+'</div>').join('');lastChatKey=key;requestAnimationFrame(()=>{if(forceChatBottom||nearBottom){el.scrollTop=el.scrollHeight}else{el.scrollTop=oldTop}forceChatBottom=false})}}function renderPlayers(players,role,aid,ended){let key=JSON.stringify({players:players,role:role,aid:aid,ended:ended});let active=document.activeElement;let editing=!!(active&&active.closest&&active.closest('#chars'));if(key!==lastPlayersKey&&!editing){$('players').innerHTML=players.map(p=>charCard(p,role,aid,ended)).join('');lastPlayersKey=key}}function render(){let s=S.st,isKP=['owner','kp'].includes(s.role);$('title').textContent=s.name;$('roomcode').textContent='房號 '+s.code;$('scene').textContent='場景：'+s.scene;$('count').textContent='調查員 '+s.count+'/'+s.limit;$('role').textContent=s.role==='owner'?(s.self_name+'・房主 KP'):s.role==='kp'?(s.self_name+'・共同 KP'):(s.self_name+'・玩家');if($('selfname')&&document.activeElement!==$('selfname'))$('selfname').value=s.self_name||'';$('leavebtn').classList.toggle('hide',s.role==='owner');$('disbandbtn').classList.toggle('hide',s.role!=='owner');$('kpdisband').classList.toggle('hide',s.role!=='owner');$('kpinvitebtn').classList.toggle('hide',s.role!=='owner');document.querySelectorAll('.kp').forEach(x=>x.classList.toggle('hide',!isKP));$('ended').classList.toggle('hide',!s.ended);$('ci').disabled=s.ended;$('sendbtn').disabled=s.ended;$('rollbtn').disabled=s.ended;renderChat(s.chat);$('clues').innerHTML=s.clues.length?s.clues.map(x=>'<div class="clue">'+esc(x.text)+'<div class="small">'+(x.visibility==='public'?'公開':x.visibility==='kp'?'僅KP':'指定玩家')+'</div></div>').join(''):'尚無可見線索';$('side').innerHTML='<div><b>'+esc(s.owner_name)+'</b> <span class="pill">房主KP</span></div><br>'+s.players.map(p=>'<div><b>'+esc(p.name)+'</b> '+(p.co_kp?'<span class="pill">共同KP</span>':'<span class="pill">玩家</span>')+'<div class="small">'+esc(p.char_name||'未設定角色')+'</div></div><br>').join('');renderPlayers(s.players,s.role,s.aid,s.ended);$('nl').value=s.limit;$('ss').innerHTML=s.scenes.map(x=>'<option '+(x===s.scene?'selected':'')+'>'+esc(x)+'</option>').join('');$('ct').innerHTML='<option value="">選擇玩家</option>'+s.players.filter(p=>!p.co_kp).map(p=>'<option value="'+p.id+'">'+esc(p.name)+'</option>').join('');if(isKP)$('nt').value=s.note||'';if($('ownernamebox'))$('ownernamebox').classList.toggle('hide',s.role!=='owner');if(s.role==='owner'&&$('ownername'))$('ownername').value=s.owner_name||'KP';$('kokp').innerHTML=isKP?'<h3>玩家管理</h3>'+s.players.map(p=>'<div class="row"><span>'+esc(p.name)+(p.co_kp?'（共同KP）':'')+'</span>'+(s.role==='owner'?'<button class="alt" onclick="setCoKp(\''+p.id+'\','+(!p.co_kp)+')">'+(p.co_kp?'取消共同KP':'設為共同KP')+'</button>':'')+((s.role==='owner'||!p.co_kp)?'<button class="bad" onclick="kickPlayer(\''+p.id+'\',\''+esc(p.name).replace(/&#39;/g,"\\'")+'\')">踢出</button>':'')+'</div>').join(''):''}
function charCard(p,role,aid,ended){if(p.char_hidden)return '<div class="card"><b>'+esc(p.name)+'</b><p>🔒 角色卡已設為私密，只有本人與 KP 可查看。</p></div>';let c=p.char||{},can=!ended&&(['owner','kp'].includes(role)||aid===p.id);let attrs=c.attrs||{},skills=c.skills||{};let skillText=Object.entries(skills).map(([k,v])=>k+'='+v).join('\n');return '<div class="card"><h3>'+esc(p.name)+' 的角色卡</h3><label>房間顯示名稱</label><input id="dn'+p.id+'" value="'+esc(p.name)+'" '+(can?'':'disabled')+'><label>角色名稱</label><input id="cn'+p.id+'" value="'+esc(c.name||'')+'" '+(can?'':'disabled')+'><label>職業</label><input id="co'+p.id+'" value="'+esc(c.occupation||'')+'" '+(can?'':'disabled')+'><label>年齡</label><input id="ca'+p.id+'" value="'+esc(c.age||'')+'" '+(can?'':'disabled')+'><div class="attrgrid">'+['STR','CON','SIZ','DEX','APP','INT','POW','EDU'].map(k=>'<label>'+k+'<input id="a'+k+p.id+'" type="number" value="'+(attrs[k]||0)+'" '+(can?'':'disabled')+'></label>').join('')+'</div><div class="row"><label>HP<input id="hp'+p.id+'" type="number" value="'+(c.hp??10)+'" '+(can?'':'disabled')+'></label><label>MP<input id="mp'+p.id+'" type="number" value="'+(c.mp??10)+'" '+(can?'':'disabled')+'></label><label>SAN<input id="san'+p.id+'" type="number" value="'+(c.san??50)+'" '+(can?'':'disabled')+'></label><label>幸運<input id="luck'+p.id+'" type="number" value="'+(c.luck??50)+'" '+(can?'':'disabled')+'></label></div><label>技能（每行 技能=數值）</label><textarea id="sk'+p.id+'" '+(can?'':'disabled')+'>'+esc(skillText)+'</textarea><label>隨身物品</label><textarea id="inv'+p.id+'" '+(can?'':'disabled')+'>'+esc(c.inventory||'')+'</textarea><label>公開備註</label><textarea id="pn'+p.id+'" '+(can?'':'disabled')+'>'+esc(c.public_notes||'')+'</textarea><label>私人備註（僅本人＋KP）</label><textarea id="pr'+p.id+'" '+(can?'':'disabled')+'>'+esc(c.private_notes||'')+'</textarea><label>角色卡可見範圍</label><select id="vis'+p.id+'" '+(can?'':'disabled')+'><option value="kp_only" '+(c.visibility!=='public'?'selected':'')+'>僅本人＋KP</option><option value="public" '+(c.visibility==='public'?'selected':'')+'>全房間公開</option></select>'+(can?'<button onclick="saveChar(\''+p.id+'\')">儲存角色卡</button><details><summary>匯入 CoC7 Google 試算表</summary><div class="small">支援你提供的「CoC7版自動計算人物卡V3」格式。連結需可檢視；私密表可改用複製 A1:Z150 後貼上。</div><input id="url'+p.id+'" placeholder="Google 試算表連結"><button class="alt" onclick="importSheet(\''+p.id+'\',\'url\')">讀取連結</button><textarea id="paste'+p.id+'" placeholder="或貼上試算表 A1:Z150 資料"></textarea><button class="alt" onclick="importSheet(\''+p.id+'\',\'text\')">讀取貼上資料</button></details>':'')+'</div>'}
function parseSkills(t){let o={};t.split(/\n+/).forEach(l=>{let m=l.match(/^(.+?)[=:：]\s*(\d{1,3})\s*$/);if(m)o[m[1].trim()]=Math.max(0,Math.min(100,+m[2]))});return o}async function saveChar(id){try{let attrs={};['STR','CON','SIZ','DEX','APP','INT','POW','EDU'].forEach(k=>attrs[k]=+$('a'+k+id).value);let ch={name:$('cn'+id).value,occupation:$('co'+id).value,age:$('ca'+id).value,attrs,hp:+$('hp'+id).value,mp:+$('mp'+id).value,san:+$('san'+id).value,luck:+$('luck'+id).value,skills:parseSkills($('sk'+id).value),inventory:$('inv'+id).value,public_notes:$('pn'+id).value,private_notes:$('pr'+id).value,visibility:$('vis'+id).value,source:'manual'};await api('/api/room/'+S.code+'/rename',{method:'POST',body:JSON.stringify({token:S.token,pid:id,name:$('dn'+id).value||'未命名'})});await api('/api/room/'+S.code+'/char',{method:'POST',body:JSON.stringify({token:S.token,pid:id,char:ch})});if(document.activeElement&&document.activeElement.blur)document.activeElement.blur();lastPlayersKey='';await refresh();notify('✓ 角色卡已儲存')}catch(e){notify(e.message,true)}}async function importSheet(id,kind){try{let body={token:S.token,pid:id};if(kind==='url')body.url=$('url'+id).value;else body.text=$('paste'+id).value;let d=await api('/api/room/'+S.code+'/import-sheet',{method:'POST',body:JSON.stringify(body)});applyPreview(id,d.preview);notify('✓ 已讀取試算表，請檢查後儲存角色卡')}catch(e){notify(e.message,true)}}function applyPreview(id,c){$('cn'+id).value=c.name||'';$('co'+id).value=c.occupation||'';$('ca'+id).value=c.age||'';Object.entries(c.attrs||{}).forEach(([k,v])=>{let e=$('a'+k+id);if(e)e.value=v});$('hp'+id).value=c.hp;$('mp'+id).value=c.mp;$('san'+id).value=c.san;$('luck'+id).value=c.luck;$('sk'+id).value=Object.entries(c.skills||{}).map(([k,v])=>k+'='+v).join('\n')}
async function send(){let t=$('ci').value.trim();if(!t)return;try{await api('/api/room/'+S.code+'/chat',{method:'POST',body:JSON.stringify({token:S.token,text:t})});$('ci').value='';forceChatBottom=true;refresh()}catch(e){notify(e.message,true)}}async function sendDiceCmd(){let t=$('dicecmd').value.trim();if(!t)return;try{await api('/api/room/'+S.code+'/chat',{method:'POST',body:JSON.stringify({token:S.token,text:t})});$('dicecmd').value='';await refresh();tab('story')}catch(e){notify(e.message,true)}}async function renameOwner(){try{let d=await api('/api/room/'+S.code+'/rename',{method:'POST',body:JSON.stringify({token:S.token,name:$('ownername').value||'KP'})});notify('✓ KP 名稱已改為 '+d.name);refresh()}catch(e){notify(e.message,true)}}async function setLimit(){try{await api('/api/room/'+S.code+'/limit',{method:'POST',body:JSON.stringify({token:S.token,limit:+$('nl').value})});refresh()}catch(e){notify(e.message,true)}}async function setScene(){try{await api('/api/room/'+S.code+'/scene',{method:'POST',body:JSON.stringify({token:S.token,scene:$('ss').value})});refresh()}catch(e){notify(e.message,true)}}async function addClue(){try{await api('/api/room/'+S.code+'/clue',{method:'POST',body:JSON.stringify({token:S.token,text:$('cl').value,visibility:$('cv').value,target:$('ct').value||null})});$('cl').value='';refresh()}catch(e){notify(e.message,true)}}async function saveNote(){try{await api('/api/room/'+S.code+'/note',{method:'POST',body:JSON.stringify({token:S.token,text:$('nt').value})});notify('✓ 已儲存')}catch(e){notify(e.message,true)}}async function setCoKp(id,en){try{await api('/api/room/'+S.code+'/co-kp',{method:'POST',body:JSON.stringify({token:S.token,pid:id,enabled:en})});refresh()}catch(e){notify(e.message,true)}}async function kickPlayer(id,name){try{await api('/api/room/'+S.code+'/kick',{method:'POST',body:JSON.stringify({token:S.token,pid:id})});notify('✓ 已將 '+name+' 移出房間');refresh()}catch(e){notify(e.message,true)}}async function leaveRoom(){try{await api('/api/room/'+S.code+'/leave',{method:'POST',body:JSON.stringify({token:S.token})});resetHome();notify('✓ 你已離開房間')}catch(e){notify(e.message,true)}}async function disbandRoom(){if(!confirm('確定要解散房間？所有玩家都會立即離開。'))return;try{await api('/api/room/'+S.code+'/disband',{method:'POST',body:JSON.stringify({token:S.token})});resetHome('房間已解散。')}catch(e){notify(e.message,true)}}async function endRoom(){if(confirm('結束遊戲後將停止聊天、擲骰與角色修改。確定結束？')){try{await api('/api/room/'+S.code+'/end',{method:'POST',body:JSON.stringify({token:S.token})});notify('✓ 遊戲已結束');refresh()}catch(e){notify(e.message,true)}}}function tab(id){document.querySelectorAll('.sec').forEach(x=>x.classList.remove('on'));$(id).classList.add('on');scrollTo(0,0)}async function clip(t){let ok=false;try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(t);ok=true}else{let x=document.createElement('textarea');x.value=t;x.setAttribute('readonly','');x.style.position='fixed';x.style.opacity='0';document.body.appendChild(x);x.select();ok=document.execCommand('copy');x.remove()}}catch(e){ok=false}notify(ok?'✓ 已複製':'複製失敗，請長按文字複製',!ok);return ok}function copyCode(){clip(S.code)}function copyInvite(){clip(location.origin+'/?room='+S.code)}function copyKpInvite(){if(!S.st||!S.st.kp_invite)return notify('共同 KP 邀請尚未載入',true);clip(location.origin+'/?room='+S.code+'&kp='+encodeURIComponent(S.st.kp_invite))}let qp=new URLSearchParams(location.search);if(qp.get('room')){$('rc').value=qp.get('room').toUpperCase();$('gkc').value=qp.get('room').toUpperCase();if(qp.get('kp')){$('gki').value=qp.get('kp');$('gkn').focus()}else{$('gn').focus()}}try{let d=JSON.parse(localStorage.getItem('coc-v8')||'null');if(d&&d.code&&d.token)enter(d)}catch(e){}
</script></body></html>'''