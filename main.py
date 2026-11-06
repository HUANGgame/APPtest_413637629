from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from typing import Optional, Dict, Any
import random, secrets, re, csv, io, urllib.request, urllib.parse, math, base64, binascii

app = FastAPI(title='克蘇魯調查局')
rooms: Dict[str, Dict[str, Any]] = {}

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
        'name': '', 'occupation': '', 'age': '', 'gender': '',
        'position': '', 'birthplace': '', 'residence': '', 'education': '', 'sheet_player': '',
        'trait1': '', 'trait2': '', 'traits': '', 'occupation_notes': '',
        'attrs': {k: 0 for k in ['STR','CON','SIZ','DEX','APP','INT','POW','EDU']},
        'hp': 10, 'mp': 10, 'san': 50, 'luck': 50,
        'mov': 0, 'build': '', 'db': '', 'inspiration': 0, 'knowledge': 0,
        'skills': dict(DEFAULT_SKILLS),
        'currency': '', 'living_standard': '', 'cash': '', 'spending_level': '', 'assets': '', 'asset_details': '',
        'appearance_desc': '', 'personality': '', 'ideology': '', 'scars': '',
        'significant_person': '', 'phobias': '', 'meaningful_place': '', 'treasured_item': '',
        'anchor': '', 'experience_pack': '', 'backstory': '',
        'armor': '', 'insanity_status': '', 'condition_status': '', 'mythos_believer': '',
        'inventory': '', 'weapons': '', 'adventures': '', 'contacts': '', 'spells': '', 'supernatural': '', 'mythos_tomes': '',
        'public_notes': '', 'private_notes': '',
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
    d = {k: r[k] for k in ['code','name','scenario','limit','chat','ended']}
    d.update(role=role, aid=aid, count=investigator_count(r), owner_name=r.get('owner_name','KP'), self_name=actor_name(r,role,aid))
    d['players'] = [sanitize_character_for_view(p, role, aid) for p in r['ps']]
    visible_clues = []
    for clue in r['clues']:
        vis = clue.get('visibility', 'public')
        clue_target_ids=clue.get('targets') or ([clue.get('target')] if clue.get('target') else [])
        if is_kp(role) or vis == 'public' or (vis == 'player' and aid in clue_target_ids):
            item=dict(clue)
            if item.get('has_image'):
                item['image_url']=f"/api/room/{r['code']}/clue-image/{item['id']}?token={urllib.parse.quote(token, safe='')}"
            visible_clues.append(item)
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
    m = re.fullmatch(r'(?i)cc\s*(>=|<=|==|=|>|<)\s*(\d{1,3})\s*(.*?)\s*', raw)
    if not m:
        return None
    op = m.group(1); target = int(m.group(2)); label = (m.group(3) or '').strip()
    if not 0 <= target <= 100:
        raise ValueError('Cc 比較值需為 0～100')
    roll = random.randint(1,100)
    ok = {'>':roll>target,'<':roll<target,'>=':roll>=target,'<=':roll<=target,'=':roll==target,'==':roll==target}[op]
    verdict = '成功' if ok else '失敗'
    growth = '該技能可成長。' if ok else '該技能無成長。'
    prefix = (label + '｜') if label else ''
    return f'{prefix}(1D100{op}{target}) → {roll} → {verdict}\n{growth}'


def coc_check_command(raw: str):
    """Direct CoC checks with optional trailing label: Cc 60 魅惑, Cc1 60 偵查, Ccn1 60 潛行."""
    m = re.fullmatch(r'(?i)cc(?:(n)?([12]))?\s+(\d{1,3})\s*(.*?)\s*', raw)
    if not m:
        return None
    penalty = bool(m.group(1))
    extra = int(m.group(2) or 0)
    target = int(m.group(3))
    label = (m.group(4) or '').strip()
    if not 0 <= target <= 100:
        raise ValueError('Cc 技能值需為 0～100')
    value, candidates = percentile_candidates(extra, penalty)
    level = coc_level(value, target)
    mode = ''
    if extra:
        mode = f"｜{'懲罰骰' if penalty else '獎勵骰'} {extra} 顆｜候選 {candidates}"
    name = (label + '｜') if label else ''
    return f'{name}CoC7 檢定 Cc{("n" if penalty else "")}{extra if extra else ""} {target} → {value} → {level}{mode}'


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
    if re.fullmatch(r'(?i)cc\s*(>=|<=|==|=|>|<)\s*\d{1,3}\s*.*', t): return True
    if re.fullmatch(r'(?i)cc(?:(n)?[12])?\s+\d{1,3}\s*.*', t): return True
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
    raise ValueError('未知骰子指令。可用：Ccyc、Cc 60 魅惑、Cc1 60 魅惑、Ccn1 60 魅惑、Cc>80 魅惑、1D100、3 3D6、D66、3D6kh')

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
    """Parse the CoC7 自動計算人物卡 V3, including background and secondary sections."""
    def txt(r, c):
        return str(cell(rows, r, c) or '').strip()

    def num(r, c, default=None):
        return safe_int(cell(rows, r, c), default)

    def find_row(label, cols=None, start=1, end_row=None, starts=False):
        end_row = min(len(rows), end_row or len(rows))
        cols = cols or tuple(range(1, 27))
        for rr in range(start, end_row + 1):
            for cc in cols:
                value = txt(rr, cc)
                if (value.startswith(label) if starts else value == label):
                    return rr, cc
        return None, None

    def attribute_value(label):
        rr, _ = find_row(label, cols=(15,), start=4, end_row=20)
        if not rr:
            return 0
        final_v = num(rr, 19, None)
        return final_v if final_v is not None else (num(rr, 17, 0) or 0)

    def final_stat(label, fallback):
        rr, _ = find_row(label, cols=(21,), start=4, end_row=20)
        if rr:
            # 右側衍生數值區：Y 為最終值；若該列 Y 不是數字（如 SAN 的 "(49)"）才退回 V。
            final_v = num(rr, 25, None)
            if final_v is not None:
                return final_v
            base_v = num(rr, 22, None)
            if base_v is not None:
                return base_v
        return fallback

    def derived_text(label):
        rr, _ = find_row(label, cols=(21,), start=4, end_row=20)
        if not rr:
            return ''
        final_v = txt(rr, 25)
        return final_v if final_v and not (final_v.startswith('(') and final_v.endswith(')')) else txt(rr, 22)

    def section_rows(start_label, end_label=None, start_offset=1):
        sr, _ = find_row(start_label, cols=(1,), start=1, end_row=len(rows), starts=True)
        if not sr:
            return []
        start_r = sr + start_offset
        end_r = len(rows)
        if end_label:
            er, _ = find_row(end_label, cols=(1,), start=start_r, end_row=len(rows), starts=True)
            if er:
                end_r = er - 1
        return list(range(start_r, end_r + 1))

    def skill_name(base, subtype):
        base = (base or '').strip()
        subtype = (subtype or '').strip()
        if base.startswith('［') or subtype.startswith('［'):
            return ''
        if base in ('技能名稱', '☐', '☑'):
            base = ''
        if subtype in ('技能名稱', '☐', '☑'):
            subtype = ''
        if base and subtype:
            return f"{base.rstrip('：:')}：{subtype}"
        if subtype:
            return subtype
        if base.endswith(('：', ':')):
            return ''
        return base

    # Basic identity.
    r, _ = find_row('角色', cols=(1,), start=1, end_row=20)
    name = txt(r, 2) if r else ''
    r, _ = find_row('玩家', cols=(1,), start=1, end_row=20)
    sheet_player = txt(r, 2) if r else ''
    r, _ = find_row('性別', cols=(1,), start=1, end_row=20)
    gender = txt(r, 2) if r else ''
    r, _ = find_row('年齡', cols=(1,), start=1, end_row=20)
    age = txt(r, 2) if r else ''

    r, _ = find_row('職位', cols=(7,), start=1, end_row=20)
    position = txt(r, 9) if r else ''
    r, _ = find_row('出生地', cols=(7,), start=1, end_row=20)
    birthplace = txt(r, 9) if r else ''
    r, _ = find_row('居住地', cols=(7,), start=1, end_row=20)
    residence = txt(r, 9) if r else ''
    r, _ = find_row('學歷', cols=(7,), start=1, end_row=20)
    education = txt(r, 9) if r else ''

    r, _ = find_row('職業', cols=(1,), start=1, end_row=20)
    occupation = txt(r, 4) if r else ''
    if not occupation:
        occupation = position

    def format_trait(code, desc):
        code=(code or '').strip().replace('，',',').replace('、',',')
        desc=(desc or '').strip()
        if code and desc:
            return f'（{code}）{desc}'
        if desc:
            return desc
        if code:
            return f'（{code}）'
        return ''

    r, _ = find_row('特徵', cols=(1,), start=1, end_row=20)
    trait1 = format_trait(txt(r, 2) if r else '', txt(r, 3) if r else '')
    r2, _ = find_row('特徵2', cols=(7,), start=1, end_row=20)
    trait2 = format_trait(txt(r2, 9) if r2 else '', txt(r2, 10) if r2 else '')
    traits = '、'.join(v for v in (trait1, trait2) if v)

    occupation_notes = ''
    r, _ = find_row('職業備忘', cols=(1,), start=1, end_row=30)
    if r:
        chunks=[]
        for rr in range(r+1, min(r+5, len(rows)+1)):
            v=txt(rr,1)
            if v:
                chunks.append(v)
        occupation_notes='\n'.join(chunks)

    attrs = {
        'STR': attribute_value('力量'), 'DEX': attribute_value('敏捷'),
        'POW': attribute_value('意志'), 'CON': attribute_value('體質'),
        'APP': attribute_value('外貌'), 'EDU': attribute_value('教育'),
        'SIZ': attribute_value('體型'), 'INT': attribute_value('智力'),
    }
    luck = attribute_value('幸運')
    hp_default = math.floor((attrs['CON'] + attrs['SIZ']) / 10) if attrs['CON'] or attrs['SIZ'] else 10
    mp_default = math.floor(attrs['POW'] / 5) if attrs['POW'] else 10
    san_default = attrs['POW'] if attrs['POW'] else 50
    hp = final_stat('生命', hp_default)
    mp = final_stat('魔法', mp_default)
    san = final_stat('心智', san_default)
    mov = safe_int(derived_text('MOV'), 0)
    build = derived_text('體格')
    db = derived_text('DB')
    inspiration = safe_int(derived_text('靈感'), attrs['INT'])
    knowledge = safe_int(derived_text('知識'), attrs['EDU'])

    # Assets / money.
    def right_value(label, label_col, value_col, start=15, end_row=30):
        # 同一標籤可能同時出現在區塊標題與實際資料列；取第一個「值欄非空」的匹配。
        for rr in range(start, min(end_row, len(rows)) + 1):
            if txt(rr, label_col) == label and txt(rr, value_col):
                return txt(rr, value_col)
        # 若真的沒有值，才回傳第一個匹配列的空值。
        rr, _ = find_row(label, cols=(label_col,), start=start, end_row=end_row)
        return txt(rr, value_col) if rr else ''

    # 資產區實際值在 S 欄；現金標籤在 V、值在 W。
    currency = right_value('貨幣',15,19)
    living_standard = right_value('生活水平',15,19)
    spending_level = right_value('消費水平',15,19)
    assets = right_value('資產',15,19)
    asset_details = right_value('資產內容',15,19)
    cash = right_value('現金',22,23)

    armor = right_value('護甲',21,22, start=4, end_row=20)
    insanity_status = right_value('瘋狂狀態',15,17, start=4, end_row=20)
    mythos_believer = right_value('神話相信者',15,17, start=4, end_row=20)
    condition_bits=[]
    injury_row,_=find_row('傷勢',cols=(21,),start=4,end_row=20)
    if injury_row:
        condition_bits.append('傷勢:'+txt(injury_row,22))
        if txt(injury_row,23): condition_bits.append('重傷:'+txt(injury_row,24))
        if txt(injury_row,25): condition_bits.append('瀕死:'+txt(injury_row,26))
    condition_status='｜'.join(condition_bits)

    # Skills.
    skill_header = None
    for rr in range(1, min(len(rows), 150) + 1):
        if txt(rr, 1) == '技能名稱' or txt(rr, 15) == '技能名稱':
            skill_header = rr
            break
    skill_start = (skill_header + 2) if skill_header else 27
    skills = {}
    for rr in range(skill_start, min(len(rows), 150) + 1):
        if txt(rr, 1).startswith('武器'):
            break
        left = skill_name(txt(rr, 2), txt(rr, 3))
        left_val = num(rr, 12, None)
        if left and left_val is not None and 0 <= left_val <= 100:
            skills[left] = left_val
        right = skill_name(txt(rr, 16), txt(rr, 17))
        right_val = num(rr, 25, None)
        if right and right_val is not None and 0 <= right_val <= 100:
            skills[right] = right_val

    # Weapons.
    weapon_lines=[]
    for rr in section_rows('武器(', '調查員背景故事', 2):
        wname=txt(rr,1)
        if not wname:
            continue
        parts=[wname]
        for label,val in [('技能',txt(rr,7)),('%',txt(rr,11)),('傷害',(txt(rr,12)+((' '+txt(rr,14)) if txt(rr,14) else '')).strip()),('範圍',txt(rr,16)),('次數',txt(rr,20)),('彈數',txt(rr,22)),('故障',txt(rr,25))]:
            if val:
                parts.append(f'{label}:{val}')
        weapon_lines.append('｜'.join(parts))
    weapons='\n'.join(weapon_lines)

    # Background blocks.
    def background_value(label, label_col, value_col):
        rr,_=find_row(label,cols=(label_col,),start=80,end_row=min(len(rows),140))
        return txt(rr,value_col) if rr else ''

    appearance_desc=background_value('形象描述',1,3)
    personality=background_value('性格特徵',13,16)
    ideology=background_value('思想/信念',1,3)
    scars=background_value('傷勢及傷痕',13,16)
    significant_person=background_value('重要之人及理由',1,3)
    phobias=background_value('恐懼症及狂躁症',13,16)
    meaningful_place=background_value('意義非凡之地',1,3)
    treasured_item=background_value('寶貴之物',13,16)
    anchor=background_value('心靈支柱',8,11)
    experience_pack=background_value('經驗包',8,11)
    backstory=background_value('生平故事',8,11)

    # Inventory.
    inventory_items=[]
    for rr in section_rows('隨身物品', '遭遇的超自然事物', 1):
        for cc in (1,6,12,17,22):
            v=txt(rr,cc)
            if v:
                inventory_items.append(v)
    inventory='\n'.join(dict.fromkeys(inventory_items))

    # Adventure history.
    adventure_lines=[]
    for rr in section_rows('遭遇的超自然事物', '調查員同伴及關係人', 2):
        date=txt(rr,1)
        if not date or date.startswith('例:') or date.startswith('例：'):
            continue
        vals=[date,txt(rr,3),txt(rr,8),txt(rr,11),txt(rr,14)]
        adventure_lines.append('｜'.join(v for v in vals if v))
    adventures='\n'.join(adventure_lines)

    # Contacts.
    contact_lines=[]
    for rr in section_rows('調查員同伴及關係人', '咒文', 2):
        n=txt(rr,1)
        if not n:
            continue
        vals=[n,txt(rr,3),txt(rr,6),txt(rr,20),txt(rr,24)]
        contact_lines.append('｜'.join(v for v in vals if v))
    contacts='\n'.join(contact_lines)

    # Spells.
    spell_lines=[]
    for rr in section_rows('咒文', '第三類接觸', 2):
        n=txt(rr,1)
        if not n:
            continue
        vals=[n,txt(rr,5),txt(rr,8),txt(rr,11),txt(rr,20),txt(rr,25)]
        spell_lines.append('｜'.join(v for v in vals if v))
    spells='\n'.join(spell_lines)

    # Mythos / supernatural contacts.
    supernatural_lines=[]
    for rr in section_rows('第三類接觸', None, 2):
        n=txt(rr,1)
        if not n or n.startswith('例:') or n.startswith('例：'):
            continue
        vals=[n,txt(rr,5),txt(rr,11),txt(rr,15),txt(rr,20)]
        supernatural_lines.append('｜'.join(v for v in vals if v))
    supernatural='\n'.join(supernatural_lines)

    tome_lines=[]
    for rr in section_rows('神話典籍', None, 2):
        n=txt(rr,1)
        if not n:
            continue
        vals=[n,txt(rr,5),txt(rr,6),txt(rr,8),txt(rr,10),txt(rr,11),txt(rr,15),txt(rr,20)]
        tome_lines.append('｜'.join(v for v in vals if v))
    mythos_tomes='\n'.join(tome_lines)

    ch = blank_char()
    ch.update({
        'name': name, 'occupation': occupation, 'age': age, 'gender': gender,
        'position': position, 'birthplace': birthplace, 'residence': residence,
        'education': education, 'sheet_player': sheet_player,
        'trait1': trait1, 'trait2': trait2, 'traits': traits,
        'occupation_notes': occupation_notes,
        'attrs': attrs, 'hp': hp, 'mp': mp, 'san': san, 'luck': luck or 50,
        'mov': mov, 'build': build, 'db': db, 'inspiration': inspiration, 'knowledge': knowledge,
        'skills': skills or dict(DEFAULT_SKILLS),
        'currency': currency, 'living_standard': living_standard, 'cash': cash,
        'spending_level': spending_level, 'assets': assets, 'asset_details': asset_details,
        'appearance_desc': appearance_desc, 'personality': personality, 'ideology': ideology,
        'scars': scars, 'significant_person': significant_person, 'phobias': phobias,
        'meaningful_place': meaningful_place, 'treasured_item': treasured_item,
        'anchor': anchor, 'experience_pack': experience_pack, 'backstory': backstory,
        'armor': armor, 'insanity_status': insanity_status, 'condition_status': condition_status, 'mythos_believer': mythos_believer,
        'inventory': inventory, 'weapons': weapons, 'adventures': adventures,
        'contacts': contacts, 'spells': spells, 'supernatural': supernatural, 'mythos_tomes': mythos_tomes,
        'source': 'Google Sheets / CoC7 V3'
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
    export = f'https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={q}&range=A1:Z176'
    req = urllib.request.Request(export, headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=12) as resp:
        raw = resp.read().decode('utf-8-sig', errors='replace')
    if '<html' in raw.lower() or 'Sign in' in raw[:1000]:
        raise ValueError('試算表無法公開讀取；請設為「知道連結的任何人可檢視」，或改用貼上資料匯入')
    return list(csv.reader(io.StringIO(raw)))


class Create(BaseModel):
    name: str = '未命名房間'; scenario: str = '自由跑團'; limit: int = 4; kp_name: str = 'KP'
class Join(BaseModel):
    code: str; name: str; resume_key: Optional[str]=None
class JoinKp(BaseModel):
    code: str; name: str; invite: str; resume_key: Optional[str]=None
class Token(BaseModel): token: str
class ChatIn(Token): text: str
class RollIn(Token): label: str='d100'; target: Optional[int]=None; mode: str='normal'; difficulty: str='normal'; hidden: bool=False
class LimitIn(Token): limit: int
class CharIn(Token):
    pid: str; char: Dict[str, Any]
class ClueIn(Token): text: str=''; visibility: str='public'; target: Optional[str]=None; targets: Optional[list[str]]=None; image_data: Optional[str]=None
class NoteIn(Token): text: str=''
class CoKpIn(Token): pid: str; enabled: bool
class KickIn(Token): pid: str
class DeleteCharIn(Token): pid: str
class RenameIn(Token): name: str; pid: Optional[str]=None
class ImportIn(Token): pid: str; url: Optional[str]=None; text: Optional[str]=None


@app.get('/', response_class=HTMLResponse)
def home():
    return HTMLResponse(HTML, headers={'Cache-Control':'no-store, max-age=0'})
@app.head('/')
def home_head():
    return HTMLResponse('', headers={'Cache-Control':'no-store, max-age=0'})
@app.get('/health')
def health(): return {'ok':1,'rooms':len(rooms),'version':'v8.23'}

@app.post('/api/create')
def create(x: Create):
    if not 1 <= x.limit <= 50: raise HTTPException(400,'人數需 1~50')
    c=make_code(); t=secrets.token_urlsafe(24); ki=secrets.token_urlsafe(12)
    rooms[c]={'code':c,'name':x.name.strip() or '未命名房間','scenario':x.scenario,'limit':x.limit,
              'owner':t,'owner_name':(x.kp_name.strip()[:30] or 'KP'),'kp_invite':ki,'ps':[],'chat':[{'who':'系統','text':'房間已建立','kind':'system'}],
              'clues':[],'clue_images':{},'hidden':[],'note':'','last_rolls':{},'ended':False}
    return {'code':c,'token':t}

@app.post('/api/join')
def join(x: Join):
    r=rooms.get(x.code.upper().strip())
    if not r: raise HTTPException(404,'找不到房間')
    if r['ended']: raise HTTPException(409,'房間已結束')
    name=(x.name or '訪客').strip()[:30] or '訪客'
    if x.resume_key:
        p=next((p for p in r['ps'] if not p.get('co_kp') and p.get('resume_key') and secrets.compare_digest(str(p['resume_key']),str(x.resume_key))),None)
        if p:
            r['chat'].append({'who':'系統','text':p['name']+' 重新連線','kind':'system'})
            return {'code':r['code'],'token':p['token'],'resume_key':p['resume_key'],'name':p['name'],'role':'player','resumed':True}
    if any(not p.get('co_kp') and p.get('name','').casefold()==name.casefold() for p in r['ps']):
        raise HTTPException(409,'這個名字已在房間內，請用原本裝置重新進入，避免產生重複角色卡')
    if investigator_count(r) >= r['limit']: raise HTTPException(409,'房間已滿')
    p={'id':secrets.token_hex(5),'token':secrets.token_urlsafe(24),'resume_key':secrets.token_urlsafe(18),'name':name,'co_kp':False,'npc':False,'char':blank_char()}
    r['ps'].append(p); r['chat'].append({'who':'系統','text':p['name']+' 加入房間','kind':'system'})
    return {'code':r['code'],'token':p['token'],'resume_key':p['resume_key'],'name':p['name'],'role':'player'}

@app.post('/api/join-kp')
def join_kp(x: JoinKp):
    r=rooms.get(x.code.upper().strip())
    if not r: raise HTTPException(404,'找不到房間')
    if r['ended']: raise HTTPException(409,'房間已結束')
    if not secrets.compare_digest(str(x.invite or ''), str(r.get('kp_invite',''))):
        raise HTTPException(403,'共同 KP 邀請無效')
    name=(x.name or '共同KP').strip()[:30] or '共同KP'
    if x.resume_key:
        p=next((p for p in r['ps'] if p.get('co_kp') and p.get('resume_key') and secrets.compare_digest(str(p['resume_key']),str(x.resume_key))),None)
        if p:
            r['chat'].append({'who':'系統','text':p['name']+' 重新連線','kind':'system'})
            return {'code':r['code'],'token':p['token'],'resume_key':p['resume_key'],'name':p['name'],'role':'kp','resumed':True}
    if any(p.get('co_kp') and p.get('name','').casefold()==name.casefold() for p in r['ps']):
        raise HTTPException(409,'這個共同 KP 名稱已在房間內，請用原本裝置重新進入')
    p={'id':secrets.token_hex(5),'token':secrets.token_urlsafe(24),'resume_key':secrets.token_urlsafe(18),'name':name,'co_kp':True,'npc':False,'char':blank_char()}
    r['ps'].append(p); r['chat'].append({'who':'系統','text':p['name']+' 以共同 KP 身分加入房間','kind':'system'})
    return {'code':r['code'],'token':p['token'],'resume_key':p['resume_key'],'name':p['name'],'role':'kp'}

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
    text_fields=['name','occupation','age','gender','position','birthplace','residence','education','sheet_player','trait1','trait2','traits','occupation_notes',
                 'build','db','currency','living_standard','cash','spending_level','assets','asset_details',
                 'appearance_desc','personality','ideology','scars','significant_person','phobias','meaningful_place','treasured_item',
                 'anchor','experience_pack','backstory','armor','insanity_status','condition_status','mythos_believer',
                 'inventory','weapons','adventures','contacts','spells','supernatural','mythos_tomes',
                 'public_notes','private_notes','visibility','source']
    ch.update({k:str(data[k])[:12000] for k in text_fields if k in data})
    if isinstance(data.get('attrs'),dict): ch['attrs']={k:safe_int(data['attrs'].get(k),0) for k in ch['attrs']}
    if isinstance(data.get('skills'),dict): ch['skills']={str(k)[:60]:max(0,min(100,safe_int(v,0))) for k,v in data['skills'].items() if str(k).strip()}
    for k in ['hp','mp','san','luck','mov','inspiration','knowledge']: ch[k]=safe_int(data.get(k),ch[k])
    if ch['visibility'] not in ('public','kp_only'): ch['visibility']='kp_only'
    p['char']=ch; return {'ok':1}

@app.post('/api/room/{c}/delete-char')
def delete_char(c:str,x:DeleteCharIn):
    r=rooms.get(c.upper())
    if not r: raise HTTPException(404,'找不到房間')
    role,_=auth(r,x.token)
    if not is_kp(role): raise HTTPException(403,'只有 KP 可以刪除角色卡')
    p=next((p for p in r['ps'] if p['id']==x.pid),None)
    if not p: raise HTTPException(404,'找不到玩家')
    old_name=p.get('char',{}).get('name') or '未命名角色'
    p['char']=blank_char()
    return {'ok':1,'deleted':old_name}

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

@app.post('/api/room/{c}/clue')
def add_clue(c:str,x:ClueIn):
    r=rooms.get(c.upper()); role,aid=auth(r,x.token)
    if not is_kp(role): raise HTTPException(403,'只有 KP 可以新增線索')
    text=(x.text or '').strip()
    image_data=(x.image_data or '').strip()
    if not text and not image_data: raise HTTPException(400,'請輸入線索文字或選擇圖片')
    if x.visibility not in ('public','kp','player'): raise HTTPException(400,'無效可見範圍')
    target_ids=[]; target_names=[]
    if x.visibility=='player':
        requested=list(dict.fromkeys((x.targets or []) + ([x.target] if x.target else [])))
        if not requested: raise HTTPException(400,'請至少選擇一位調查員')
        valid={p['id']:p for p in r['ps'] if not p.get('co_kp')}
        if any(pid not in valid for pid in requested): raise HTTPException(400,'指定調查員無效')
        target_ids=requested
        target_names=[valid[pid]['name'] for pid in target_ids]

    image_bytes=None; image_mime=None
    if image_data:
        m=re.fullmatch(r'data:(image/(?:jpeg|png|webp|gif));base64,([A-Za-z0-9+/=\r\n]+)', image_data)
        if not m: raise HTTPException(400,'圖片格式不支援，請使用 JPG、PNG、WebP 或 GIF')
        image_mime=m.group(1)
        payload=m.group(2).replace('\r','').replace('\n','')
        if len(payload) > 4_500_000: raise HTTPException(413,'圖片太大，請壓縮後再傳送')
        try:
            image_bytes=base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400,'圖片資料損壞')
        if not image_bytes or len(image_bytes) > 3_000_000:
            raise HTTPException(413,'圖片上限為 3 MB')
        if len(r.setdefault('clue_images',{})) >= 40:
            raise HTTPException(409,'此房間圖片線索已達 40 張上限')

    author=actor_name(r,role,aid)
    clue_id=secrets.token_hex(4)
    clue={'id':clue_id,'text':text[:1000],'visibility':x.visibility,
          'target':(target_ids[0] if target_ids else None),'targets':target_ids,
          'target_name':('、'.join(target_names) if target_names else None),'target_names':target_names,
          'author':author,'has_image':bool(image_bytes)}
    r['clues'].append(clue)
    if image_bytes:
        r['clue_images'][clue_id]={'data':image_bytes,'mime':image_mime}
    if x.visibility=='public':
        desc=('：'+text[:500]) if text else ''
        r['chat'].append({'who':author,'text':'🔎 公開'+('圖片' if image_bytes else '')+'線索'+desc,'kind':'clue'})
    return {'ok':1,'clue':clue}

@app.get('/api/room/{c}/clue-image/{clue_id}')
def clue_image(c:str,clue_id:str,token:str):
    r=rooms.get(c.upper())
    if not r: raise HTTPException(404,'找不到房間')
    role,aid=auth(r,token)
    clue=next((q for q in r['clues'] if q.get('id')==clue_id),None)
    if not clue or not clue.get('has_image'): raise HTTPException(404,'找不到圖片線索')
    vis=clue.get('visibility','public')
    clue_target_ids=clue.get('targets') or ([clue.get('target')] if clue.get('target') else [])
    if not (is_kp(role) or vis=='public' or (vis=='player' and aid in clue_target_ids)):
        raise HTTPException(403,'你無權查看此圖片線索')
    img=r.get('clue_images',{}).get(clue_id)
    if not img: raise HTTPException(404,'圖片已不存在')
    return Response(content=img['data'],media_type=img['mime'],headers={'Cache-Control':'private, max-age=3600'})

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
        resumed=join(Join(code=c,name='A',resume_key=g['resume_key'])); assert resumed['token']==g['token'] and len(rooms[c]['ps'])==1; checks.append('resume_join')
        try:
            join(Join(code=c,name='A')); raise AssertionError('duplicate name accepted')
        except HTTPException as e:
            assert e.status_code==409; checks.append('duplicate_guard')
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
        try:
            add_clue(c,ClueIn(token=a['token'],text='   ',visibility='public')); raise AssertionError('blank clue accepted')
        except HTTPException as e:
            assert e.status_code==400; checks.append('clue_blank_guard')
        add_clue(c,ClueIn(token=a['token'],text='KP秘密',visibility='kp'))
        add_clue(c,ClueIn(token=a['token'],text='公開線索',visibility='public'))
        add_clue(c,ClueIn(token=a['token'],text='給A的線索',visibility='player',targets=[pida]))
        pidb0=next(p['id'] for p in rooms[c]['ps'] if p['token']==g2['token'])
        multiclue=add_clue(c,ClueIn(token=a['token'],text='給A與B',visibility='player',targets=[pida,pidb0]))['clue']
        assert multiclue['targets']==[pida,pidb0] and len(multiclue['target_names'])==2; checks.append('clue_multi_target')
        tiny='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
        imgcl=add_clue(c,ClueIn(token=a['token'],text='',visibility='player',targets=[pida],image_data=tiny))['clue']
        assert imgcl['has_image'] and imgcl['id'] in rooms[c]['clue_images']; checks.append('clue_image_store')
        imgresp=clue_image(c,imgcl['id'],g['token']); assert imgresp.media_type=='image/png' and len(imgresp.body)>0; checks.append('clue_image_target')
        try:
            clue_image(c,imgcl['id'],g2['token']); raise AssertionError('private clue image leaked')
        except HTTPException as e:
            assert e.status_code==403; checks.append('clue_image_privacy')
        checks.append('clues')
        ga=state(c,g['token']); gb=state(c,g2['token']); ks=state(c,a['token'])
        assert any(x['text']=='公開線索' for x in ga['clues']) and any(x['text']=='給A的線索' for x in ga['clues']) and any(x['text']=='給A與B' for x in ga['clues'])
        assert all(x['text']!='KP秘密' for x in ga['clues'])
        assert any(x['text']=='公開線索' for x in gb['clues']) and any(x['text']=='給A與B' for x in gb['clues']) and all(x['text']!='給A的線索' for x in gb['clues'])
        assert len(ks['clues'])==5 and any(len(x.get('target_names') or [])==2 for x in ks['clues'] if x['visibility']=='player') and any(x.get('image_url') for x in ks['clues'] if x.get('has_image'))
        checks.append('clue_privacy')
        note(c,NoteIn(token=a['token'],text='自動筆記測試')); assert state(c,a['token'])['note']=='自動筆記測試'; checks.append('kp_note')
        # co-kp
        pidb=next(p['id'] for p in rooms[c]['ps'] if p['token']==g2['token'])
        co_kp(c,CoKpIn(token=a['token'],pid=pidb,enabled=True)); assert state(c,g2['token'])['role']=='kp'; checks.append('co_kp')
        # sheet parser: realistic CoC7 V3 layout
        rows=[['']*26 for _ in range(150)]
        rows[4][0]='角色'; rows[4][1]='哈利'; rows[4][6]='職位'; rows[4][8]='富二代'
        rows[5][0]='玩家'; rows[5][1]='笨魚魚'; rows[5][6]='出生地'; rows[5][8]='海南島'
        rows[6][0]='性別'; rows[6][1]='男'; rows[6][6]='居住地'; rows[6][8]='台北'
        rows[7][0]='年齡'; rows[7][1]='16'; rows[7][6]='學歷'; rows[7][8]='高中'
        rows[8][0]='特徵'; rows[8][1]='5,7'; rows[8][2]='心理測量'; rows[8][6]='特徵2'; rows[8][8]='4，10'; rows[8][9]='其他語言'
        rows[10][0]='職業'; rows[10][3]='罪犯-放高利貸者'
        attr_specs=[('力量',70,-5,65),('敏捷',75,None,75),('意志',70,None,70),('體質',85,None,85),('外貌',60,None,60),('教育',60,-5,55),('體型',60,None,60),('智力',50,None,50)]
        for idx,(label,base,other,finalv) in enumerate(attr_specs,start=6):
            rows[idx-1][14]=label; rows[idx-1][16]=str(base)
            if other is not None: rows[idx-1][17]=str(other)
            rows[idx-1][18]=str(finalv)
        rows[13][14]='幸運'; rows[13][16]='75'  # S 欄空白時回退 Q
        rows[10][20]='生命'; rows[10][21]='8'; rows[10][24]='11'
        rows[11][20]='魔法'; rows[11][21]='10'; rows[11][24]='14'
        rows[12][20]='心智'; rows[12][21]='60'; rows[12][24]='(49)'
        rows[24][0]='技能名稱'; rows[24][14]='技能名稱'
        rows[26][1]='心理學'; rows[26][11]='50'
        rows[26][15]='藝術／手藝：'; rows[26][16]='美術'; rows[26][24]='5'
        rows[27][15]='格鬥'; rows[27][16]='鬥毆'; rows[27][24]='25'
        rows[28][1]='生存:'; rows[28][11]='10'
        rows[16][14]='資產'  # 區塊標題，值欄空白
        rows[18][14]='貨幣'; rows[18][18]='美國-現代'
        rows[19][14]='生活水平'; rows[19][18]='小康'; rows[19][21]='現金'; rows[19][22]='5,000美金'
        rows[20][14]='消費水平'; rows[20][18]='1,000美金'
        rows[21][14]='資產'; rows[21][18]='500,000美金'
        rows[22][14]='資產內容'; rows[22][18]='房產與存款'
        rows[40][0]='武器(詳情)'; rows[41][0]='名稱'; rows[42][0]='空手'; rows[42][6]='格鬥（鬥毆）'; rows[42][10]='25'; rows[42][11]='1d3'
        rows[87][0]='調查員背景故事'
        rows[88][0]='形象描述'; rows[88][2]='像哈利波特'; rows[88][12]='性格特徵'; rows[88][15]='機靈'
        rows[91][0]='思想/信念'; rows[91][2]='人類不可信'
        rows[94][0]='重要之人及理由'; rows[94][2]='母親'
        rows[97][0]='意義非凡之地'; rows[97][2]='草原'; rows[97][12]='寶貴之物'; rows[97][15]='媽媽的眼鏡'
        rows[102][7]='心靈支柱'; rows[102][10]='思想/信念'
        rows[103][7]='經驗包'; rows[103][10]='罪犯經歷包'
        rows[104][7]='生平故事'; rows[104][10]='從小跟動物生活'
        rows[113][0]='隨身物品'; rows[114][0]='眼鏡'; rows[114][5]='皮夾'; rows[114][11]='睡袋'
        rows[119][0]='遭遇的超自然事物(劇本)'
        rows[129][0]='調查員同伴及關係人'
        rows[137][0]='咒文'
        rows[145][0]='第三類接觸'
        parsed=parse_sheet_rows(rows)
        assert parsed['name']=='哈利' and parsed['occupation']=='罪犯-放高利貸者' and parsed['age']=='16'
        assert parsed['attrs']['STR']==65 and parsed['attrs']['EDU']==55 and parsed['luck']==75
        assert parsed['hp']==11 and parsed['mp']==14 and parsed['san']==60
        assert parsed['skills']['心理學']==50 and parsed['skills']['藝術／手藝：美術']==5 and parsed['skills']['格鬥：鬥毆']==25
        assert '生存' not in parsed['skills'] and '生存:' not in parsed['skills']
        assert parsed['gender']=='男' and parsed['birthplace']=='海南島' and parsed['residence']=='台北' and parsed['education']=='高中'
        assert parsed['trait1']=='（5,7）心理測量' and parsed['trait2']=='（4,10）其他語言'
        assert parsed['traits']=='（5,7）心理測量、（4,10）其他語言'; checks.append('trait_codes')
        assert parsed['currency']=='美國-現代' and parsed['living_standard']=='小康'
        assert parsed['cash']=='5,000美金' and parsed['spending_level']=='1,000美金' and parsed['assets']=='500,000美金' and parsed['asset_details']=='房產與存款'
        assert '空手' in parsed['weapons'] and '眼鏡' in parsed['inventory'] and parsed['appearance_desc']=='像哈利波特'
        assert parsed['personality']=='機靈' and parsed['ideology']=='人類不可信' and parsed['backstory']=='從小跟動物生活'
        checks.append('sheet_parse_v3_full')
        # room-management flow
        m=create(Create(name='MGMT',scenario='自由跑團',limit=3)); mc=m['code']
        ma=join(Join(code=mc,name='M-A')); mb=join(Join(code=mc,name='M-B'))
        pma=next(p['id'] for p in rooms[mc]['ps'] if p['token']==ma['token'])
        pmb=next(p['id'] for p in rooms[mc]['ps'] if p['token']==mb['token'])
        tmp=blank_char(); tmp['name']='待刪角色'; tmp['backstory']='秘密故事'; save_char(mc,CharIn(token=m['token'],pid=pmb,char=tmp))
        delete_char(mc,DeleteCharIn(token=m['token'],pid=pmb)); assert rooms[mc]['ps'][1]['char']['name']=='' and rooms[mc]['ps'][1]['char']['backstory']==''; checks.append('char_delete')
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
        return {'ok':True,'checks':checks,'count':len(checks),'version':'v8.23'}
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
:root{--bg:#07110d;--card:#0e1d16;--line:#294b3a;--green:#7dd3a7;--muted:#9db1a5;--red:#8a3030;--gold:#d6bc7a}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#eef7f0;font-family:system-ui,-apple-system,sans-serif}.w{max-width:1100px;margin:auto;padding:18px 14px 90px}.hero{text-align:center;padding:22px 0}.grid,.room{display:grid;grid-template-columns:1fr 1fr;gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:15px;margin-bottom:14px}input,select,textarea,button{font:inherit}input,select,textarea{width:100%;margin:4px 0 9px;padding:10px;border-radius:10px;border:1px solid #315642;background:#09140f;color:#fff}textarea{min-height:90px}.targetlist{display:grid;gap:6px;margin:5px 0 10px}.targetopt{display:flex;align-items:center;gap:9px;padding:9px 10px;border:1px solid #315642;border-radius:10px;background:#09140f}.targetopt input{width:auto;margin:0;transform:scale(1.2)}.targetopt span{flex:1}button{padding:10px 13px;border:0;border-radius:10px;background:var(--green);color:#062015;font-weight:800;cursor:pointer}button:disabled{opacity:.4;cursor:not-allowed}.alt{background:#173629;color:#fff}.bad{background:var(--red);color:#fff}.gold{background:var(--gold);color:#211907}.hide{display:none!important}.chat{height:48vh;overflow:auto;border:1px solid var(--line);border-radius:12px;padding:10px}.m{margin:0 0 9px;white-space:pre-wrap}.who{color:var(--green);font-weight:800}.row{display:flex;gap:8px}.row>*{flex:1}.small{color:var(--muted);font-size:13px}.pill{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:5px 9px;margin:3px}.tabs{display:flex;gap:7px;margin:10px 0;flex-wrap:wrap}.tabs button{background:#173629;color:#fff}.sec{display:none}.sec.on{display:block}.bottom{display:none;position:fixed;bottom:0;left:0;right:0;background:#0b1812;padding:8px;grid-template-columns:repeat(2,1fr);gap:8px;z-index:5}.attrgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.skillgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:6px}.clue{padding:9px;border-left:3px solid var(--green);background:#0a1711;margin:7px 0}.modal{position:fixed;inset:0;background:#000a;display:flex;align-items:center;justify-content:center;padding:15px;z-index:20}.modalbox{max-width:760px;max-height:85vh;overflow:auto;background:#102219;border:1px solid var(--line);border-radius:18px;padding:18px}.topbar{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}.code{font-family:ui-monospace,monospace;font-size:18px;color:var(--gold)}details{border:1px solid var(--line);border-radius:10px;padding:9px;margin:8px 0}.cmdrow{display:flex;align-items:center;justify-content:space-between;gap:8px;border-bottom:1px solid #1f3a2c;padding:8px 0}.cmdrow code{color:var(--gold);font-weight:800}.quickgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:7px}.quickgrid button{background:#173629;color:#fff;text-align:left}a{color:var(--green)}
@media(max-width:720px){.grid,.room{grid-template-columns:1fr}.bottom{display:grid}.desktop{display:none}.attrgrid{grid-template-columns:repeat(2,1fr)}.skillgrid{grid-template-columns:1fr}.chat{height:45vh}}
</style></head><body><div class="w">
<section id="home"><div class="hero"><h1>克蘇魯調查局</h1><div class="small">CoC 7 文字跑團｜訪客免帳號 · v8.23</div><button class="alt" onclick="rules()">規則／骰娘指令</button></div><div class="grid"><div class="card"><h2>KP 開房</h2><label>KP 名稱</label><input id="kpn" placeholder="例如：阿魚"><label>房間名稱</label><input id="rn" placeholder="例如：週五克蘇魯團"><label>劇本</label><select id="sc"><option>自由跑團</option><option>營火</option></select><label>調查員人數上限</label><input id="lim" type="number" min="1" max="50" value="4"><button onclick="createRoom()">建立房間</button></div><div class="card"><h2>訪客加入</h2><label>暱稱</label><input id="gn" placeholder="你的暱稱"><label>房號</label><input id="rc" maxlength="6"><button onclick="joinRoom()">加入房間</button></div><div class="card"><h2>共同 KP 加入</h2><div class="small">通常由房主傳「共同 KP 邀請連結」給你。</div><label>暱稱</label><input id="gkn" placeholder="KP 暱稱"><label>房號</label><input id="gkc" maxlength="6"><label>共同 KP 邀請碼</label><input id="gki" placeholder="邀請連結會自動填入"><button onclick="joinKpRoom()">以共同 KP 加入</button></div></div><div id="err"></div></section>
<section id="game" class="hide"><div class="topbar"><div><h2 id="title"></h2><span class="pill" id="count"></span><span class="pill" id="role"></span></div><div><div class="code" id="roomcode"></div><button class="alt" onclick="copyCode()">複製房號</button> <button class="alt" onclick="copyInvite()">複製玩家邀請</button> <button id="kpinvitebtn" class="alt hide" onclick="copyKpInvite()">複製共同 KP 邀請</button> <button id="kpconsolebtn" class="alt kp hide" onclick="tab('kp')">KP 主持台</button> <button class="alt" onclick="toggleNameEdit()">更改名字</button> <button class="gold" onclick="rules()">規則</button> <button id="leavebtn" class="bad" onclick="leaveRoom()">離開房間</button> <button id="disbandbtn" class="bad hide" onclick="disbandRoom()">解散房間</button></div></div><div id="nameedit" class="card hide"><label>房間顯示名稱</label><div class="row"><input id="selfname" maxlength="30" placeholder="輸入新名稱" oninput="selfNameDirty=true"><button onclick="renameSelf()">儲存名稱</button><button class="alt" onclick="toggleNameEdit(false)">取消</button></div><div class="small">只修改你在房間與聊天中的顯示名稱；角色卡內的角色名稱不會一起改。</div></div><div id="ended" class="card hide">房間已結束，聊天、擲骰與角色修改已停用。</div><div class="tabs desktop"><button onclick="tab('story')">故事</button><button onclick="tab('chars')">角色</button><button onclick="tab('dice')">擲骰</button><button class="kp" onclick="tab('kp')">KP 主持台</button></div><div class="room"><main>
<div id="story" class="sec on"><div id="chat" class="chat"></div><div class="row"><input id="ci" placeholder="留言或輸入骰子指令，例如 Ccyc、Cc>70、3 1d6+1d20" onkeydown="if(event.key==='Enter')send()"><button id="sendbtn" onclick="send()">送出</button></div></div>
<div id="chars" class="sec"><div class="card"><h2>角色卡</h2><div class="small">角色卡預設「僅本人＋KP」。本人可改成全房公開；私人筆記永遠只有本人與 KP 看得到。</div><div id="players"></div></div></div>
<div id="dice" class="sec"><div class="card"><h2>骰子工具 v8</h2><p>採用直接指令風格，不使用 <code>.ra</code> 等舊點指令。</p><label>骰子指令</label><input id="dicecmd" placeholder="例如 Ccyc、Cc 60 魅惑、Cc>80 魅惑、1D100"><button id="rollbtn" onclick="sendDiceCmd()">執行骰子指令</button><hr><h3>快捷指令（按鈕＝複製）</h3><div class="quickgrid"><button onclick="copyCmd('Ccyc')">📋 Ccyc</button><button onclick="copyCmd('Cc 60 魅惑')">📋 Cc 60 魅惑</button><button onclick="copyCmd('Cc1 60 魅惑')">📋 Cc1 60 魅惑</button><button onclick="copyCmd('Ccn1 60 魅惑')">📋 Ccn1 60 魅惑</button><button onclick="copyCmd('Cc>80 魅惑')">📋 Cc&gt;80 魅惑</button><button onclick="copyCmd('1D100')">📋 1D100</button><button onclick="copyCmd('3 3D6')">📋 3 3D6</button><button onclick="copyCmd('D66')">📋 D66</button><button onclick="copyCmd('3D6kh')">📋 3D6kh</button></div></div></div>
<div id="kp" class="sec kp"><div class="card"><h2>KP 主持台</h2><div id="ownernamebox"><label>我的 KP 名稱</label><div class="row"><input id="ownername" oninput="ownerNameDirty=true"><button onclick="renameOwner()">修改名稱</button></div></div><label>調查員人數上限</label><div class="row"><input id="nl" type="number" min="1" max="50" oninput="limitDirty=true"><button onclick="setLimit()">修改</button></div><hr><h3>線索／情報</h3><div class="small">可傳文字、圖片，或文字＋圖片；圖片同樣可以公開、指定玩家或僅 KP 查看。</div><textarea id="cl" placeholder="輸入線索內容，例如：桌上有一張沾血的火車票"></textarea><label>線索圖片（選填）</label><input id="climg" type="file" accept="image/jpeg,image/png,image/webp,image/gif" onchange="prepareClueImage()"><div id="climgpreview" class="hide"><img id="climgpreviewpic" class="cluepreview"><button class="alt" onclick="clearClueImage()">移除圖片</button></div><label>指定調查員（可複選）</label><div id="ct" class="targetlist"></div><div class="row"><button class="alt" onclick="selectAllClueTargets(true)">全選</button><button class="alt" onclick="selectAllClueTargets(false)">清除</button></div><div class="row"><button onclick="addClue('public')">公開給全員</button><button class="alt" onclick="addClue('player')">只給指定玩家</button><button class="alt" onclick="addClue('kp')">僅 KP 保存</button></div><hr><label>KP 私人筆記</label><textarea id="nt" oninput="queueNoteSave()" placeholder="只有 KP 看得到；停止輸入後自動儲存"></textarea><div id="notestatus" class="small">自動儲存</div><hr><div id="kokp"></div><hr><div class="small">結束遊戲＝房間保留但停止操作；解散房間＝所有人立即離開且房間消失。</div><button class="bad" onclick="endRoom()">結束遊戲</button> <button id="kpdisband" class="bad hide" onclick="disbandRoom()">解散房間</button></div></div>
</main><aside><div class="card"><h3>線索</h3><div id="clues"></div></div><div class="card"><h3>玩家／KP</h3><div id="side"></div></div></aside></div></section></div>
<div id="bottom" class="bottom hide"><button onclick="tab('story')">故事</button><button onclick="tab('chars')">角色</button></div><div id="modal" class="modal hide" onclick="if(event.target.id==='modal')closeRules()"><div class="modalbox"><button style="float:right" onclick="closeRules()">關閉</button><h2>骰娘規則</h2><h3>CoC 7 指令</h3><div class="cmdrow"><span><code>Ccyc</code>｜你的創角骰組：7 組 3D6×5、4 組 (2D6+6)×5、2 組 3D6×5</span><button onclick="copyCmd('Ccyc')">複製</button></div><div class="cmdrow"><span><code>Cc 60 魅惑</code>｜百分骰檢定；數值後可直接加技能／用途名稱</span><button onclick="copyCmd('Cc 60 魅惑')">複製</button></div><div class="cmdrow"><span><code>Cc1 60 魅惑</code> / <code>Cc2 60 魅惑</code>｜1／2 顆獎勵骰</span><button onclick="copyCmd('Cc1 60 魅惑')">複製</button></div><div class="cmdrow"><span><code>Ccn1 60 魅惑</code> / <code>Ccn2 60 魅惑</code>｜1／2 顆懲罰骰</span><button onclick="copyCmd('Ccn1 60 魅惑')">複製</button></div><div class="cmdrow"><span><code>Cc&gt;80 魅惑</code>｜成長比較；數值後可加技能名稱，可用 &gt;、&lt;、&gt;=、&lt;=、=</span><button onclick="copyCmd('Cc>80 魅惑')">複製</button></div><h3>基本骰式</h3><div class="cmdrow"><span><code>1D3+1</code>｜一般骰式</span><button onclick="copyCmd('1D3+1')">複製</button></div><div class="cmdrow"><span><code>3 1D6+1D20</code>｜重複 3 次擲同一骰式，最多 30 次</span><button onclick="copyCmd('3 1D6+1D20')">複製</button></div><div class="cmdrow"><span><code>1D100 偵查</code>｜骰式後可附文字註記</span><button onclick="copyCmd('1D100 偵查')">複製</button></div><div class="cmdrow"><span><code>3D6kh</code> / <code>3D6kh2</code>｜保留最高 1／2 顆；亦支援 kl、dh、dl</span><button onclick="copyCmd('3D6kh')">複製</button></div><h3>D66</h3><div class="cmdrow"><span><code>D66</code>｜兩顆 D6 組成兩位數</span><button onclick="copyCmd('D66')">複製</button></div><div class="cmdrow"><span><code>D66s</code>｜較小數字在前；<code>D66n</code>｜較大數字在前</span><button onclick="copyCmd('D66s')">複製</button></div><p class="small">按「複製」後，指令會帶到聊天輸入框；按送出即可執行。</p></div></div>
<script>
let S={code:'',token:'',st:null,timer:null};let lastChatKey='',forceChatBottom=true,lastPlayersKey='',lastCluesKey='',lastSideKey='',lastKpMgmtKey='',noteSaveTimer=null,noteDirty=false,noteEditSeq=0,clueImageData='',refreshFailShown=false,refreshInFlight=false,refreshQueued=false,selfNameDirty=false,ownerNameDirty=false,limitDirty=false,charDrafts={},charUiState={},charDirty=new Set();const $=x=>document.getElementById(x);const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));async function api(u,o={}){let r=await fetch(u,{headers:{'Content-Type':'application/json'},...o}),d={};try{d=await r.json()}catch{}if(!r.ok){let e=new Error(d.detail||'操作失敗');e.status=r.status;throw e}return d}function showErr(e){$('err').innerHTML='<div class="card">⚠️ '+esc(e)+'</div>'}function toggleNameEdit(force){let box=$('nameedit');let open=typeof force==='boolean'?force:box.classList.contains('hide');box.classList.toggle('hide',!open);if(open){selfNameDirty=false;$('selfname').value=(S.st&&S.st.self_name)||'';setTimeout(()=>{$('selfname').focus();$('selfname').select()},30)}else{selfNameDirty=false;if($('selfname'))$('selfname').value=(S.st&&S.st.self_name)||''}}async function renameSelf(){let name=$('selfname').value.trim();if(!name)return notify('名稱不可空白',true);try{let d=await api('/api/room/'+S.code+'/rename',{method:'POST',body:JSON.stringify({token:S.token,name:name})});selfNameDirty=false;toggleNameEdit(false);notify('✓ 名稱已改為 '+d.name);await refresh()}catch(e){notify(e.message,true)}}function notify(msg,bad=false){let toast=document.getElementById('apptoast');if(!toast){toast=document.createElement('div');toast.id='apptoast';toast.style.cssText='position:fixed;left:50%;bottom:78px;transform:translateX(-50%);max-width:88vw;background:#173629;color:#fff;border:1px solid #315642;padding:10px 14px;border-radius:999px;z-index:120;font-weight:800;box-shadow:0 6px 24px #0008;transition:.2s;text-align:center';document.body.appendChild(toast)}toast.style.background=bad?'#6f2b2b':'#173629';toast.textContent=msg;toast.style.opacity='1';clearTimeout(window.__appToastTimer);window.__appToastTimer=setTimeout(()=>{toast.style.opacity='0'},1800)}function rules(){$('modal').classList.remove('hide')}function closeRules(){$('modal').classList.add('hide')}async function copyCmd(t){try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(t)}else{let x=document.createElement('textarea');x.value=t;x.style.position='fixed';x.style.opacity='0';document.body.appendChild(x);x.focus();x.select();document.execCommand('copy');x.remove()}}catch(e){}closeRules();if(S.code){tab('story');$('ci').value=t;setTimeout(()=>{$('ci').focus();try{$('ci').setSelectionRange(t.length,t.length)}catch(e){}},60)}}
function resumeKey(code,role,name){try{let d=JSON.parse(localStorage.getItem('coc-resume-'+code+'-'+role)||'null');return d&&d.name===name?d.key:null}catch(e){return null}}function rememberResume(d){if(d.resume_key&&d.name&&d.role)localStorage.setItem('coc-resume-'+d.code+'-'+d.role,JSON.stringify({name:d.name,key:d.resume_key}))}async function createRoom(){try{let d=await api('/api/create',{method:'POST',body:JSON.stringify({name:$('rn').value||'未命名房間',scenario:$('sc').value,limit:+$('lim').value,kp_name:$('kpn').value||'KP'})});enter(d)}catch(e){showErr(e.message)}}async function joinRoom(){try{let code=$('rc').value.toUpperCase(),name=$('gn').value||'訪客';let d=await api('/api/join',{method:'POST',body:JSON.stringify({code:code,name:name,resume_key:resumeKey(code,'player',name)})});enter(d)}catch(e){showErr(e.message)}}async function joinKpRoom(){try{let code=$('gkc').value.toUpperCase(),name=$('gkn').value||'共同KP';let d=await api('/api/join-kp',{method:'POST',body:JSON.stringify({code:code,name:name,invite:$('gki').value.trim(),resume_key:resumeKey(code,'kp',name)})});enter(d)}catch(e){showErr(e.message)}}function resetUiCaches(){lastChatKey='';forceChatBottom=true;lastPlayersKey='';lastCluesKey='';lastSideKey='';lastKpMgmtKey='';clearTimeout(noteSaveTimer);noteSaveTimer=null;noteDirty=false;noteEditSeq=0;clueImageData='';refreshFailShown=false;refreshInFlight=false;refreshQueued=false;selfNameDirty=false;ownerNameDirty=false;limitDirty=false;charDrafts={};charUiState={};charDirty=new Set();if($('cl'))$('cl').value='';if($('climg'))$('climg').value='';if($('climgpreview'))$('climgpreview').classList.add('hide');if($('climgpreviewpic'))$('climgpreviewpic').removeAttribute('src');if($('ct')){$('ct').innerHTML='';delete $('ct').dataset.sig}if($('ci'))$('ci').value='';if($('dicecmd'))$('dicecmd').value='';if($('nameedit'))$('nameedit').classList.add('hide');if($('modal'))$('modal').classList.add('hide')}function enter(d){rememberResume(d);if(S.timer)clearInterval(S.timer);resetUiCaches();S.code=d.code;S.token=d.token;localStorage.removeItem('coc-v8');localStorage.setItem('coc-v8',JSON.stringify(d));$('err').innerHTML='';$('home').classList.add('hide');$('game').classList.remove('hide');$('bottom').classList.remove('hide');tab('story');refresh();S.timer=setInterval(refresh,1400)}function resetHome(msg=''){if(S.timer)clearInterval(S.timer);clearTimeout(noteSaveTimer);localStorage.removeItem('coc-v8');S={code:'',token:'',st:null,timer:null};resetUiCaches();$('game').classList.add('hide');$('bottom').classList.add('hide');$('home').classList.remove('hide');if(msg)showErr(msg)}async function refresh(){if(!S.code)return;if(refreshInFlight){refreshQueued=true;return}refreshInFlight=true;try{S.st=await api('/api/room/'+S.code+'?token='+encodeURIComponent(S.token));refreshFailShown=false;render()}catch(e){if([401,403,404].includes(e.status)){resetHome('你已離開、被移出，或房間已解散。')}else if(!refreshFailShown){refreshFailShown=true;notify('連線不穩，正在自動重連…',true)}}finally{refreshInFlight=false;if(refreshQueued&&S.code){refreshQueued=false;setTimeout(refresh,0)}}}
function renderChat(items){let el=$('chat');let nearBottom=(el.scrollHeight-el.scrollTop-el.clientHeight)<80;let oldTop=el.scrollTop;let key=items.length+'|'+(items.length?(items[items.length-1].who||'')+'|'+(items[items.length-1].text||''):'');if(key!==lastChatKey){el.innerHTML=items.map(m=>'<div class="m"><span class="who">'+esc(m.who)+'</span> '+esc(m.text)+'</div>').join('');lastChatKey=key;requestAnimationFrame(()=>{if(forceChatBottom||nearBottom){el.scrollTop=el.scrollHeight}else{el.scrollTop=oldTop}forceChatBottom=false})}}function captureCharDraft(id){let root=document.querySelector('#players [data-char-id="'+id+'"]');if(!root)return;let values={};root.querySelectorAll('input[id],textarea[id],select[id]').forEach(el=>{if(el.type==='file')return;values[el.id]={value:el.value,checked:el.checked,height:el.tagName==='TEXTAREA'?el.style.height:''}});charDrafts[id]={values}}function captureCharUiState(){document.querySelectorAll('#players [data-char-id]').forEach(root=>{let id=root.dataset.charId;charUiState[id]={details:[...root.querySelectorAll('details')].map(d=>d.open),heights:Object.fromEntries([...root.querySelectorAll('textarea[id]')].map(t=>[t.id,t.style.height||'']))};if(charDirty.has(id))captureCharDraft(id)})}function restoreCharState(){document.querySelectorAll('#players [data-char-id]').forEach(root=>{let id=root.dataset.charId,u=charUiState[id],d=charDrafts[id];if(u){[...root.querySelectorAll('details')].forEach((x,i)=>x.open=!!u.details[i]);Object.entries(u.heights||{}).forEach(([eid,h])=>{let el=document.getElementById(eid);if(el&&h)el.style.height=h})}if(d&&charDirty.has(id)){Object.entries(d.values||{}).forEach(([eid,v])=>{let el=document.getElementById(eid);if(!el)return;if(el.type==='checkbox'||el.type==='radio')el.checked=!!v.checked;else el.value=v.value;if(el.tagName==='TEXTAREA'&&v.height)el.style.height=v.height})}})}function markCharDirtyFromEvent(e){let root=e.target&&e.target.closest?e.target.closest('[data-char-id]'):null;if(!root)return;let id=root.dataset.charId;charDirty.add(id);captureCharDraft(id)}function renderPlayers(players,role,aid,ended){let key=JSON.stringify({players:players,role:role,aid:aid,ended:ended});let active=document.activeElement;let editing=!!(active&&active.closest&&active.closest('#chars'));if(key!==lastPlayersKey&&!editing){captureCharUiState();$('players').innerHTML=players.map(p=>charCard(p,role,aid,ended)).join('');lastPlayersKey=key;let ids=new Set(players.map(p=>p.id));Object.keys(charDrafts).forEach(id=>{if(!ids.has(id)){delete charDrafts[id];delete charUiState[id];charDirty.delete(id)}});restoreCharState()}}function renderClues(items){let key=JSON.stringify(items);if(key===lastCluesKey)return;$('clues').innerHTML=items.length?items.map(x=>'<div class="clue">'+(x.text?'<div>'+esc(x.text)+'</div>':'')+(x.image_url?'<img class="clueimg" src="'+esc(x.image_url)+'" loading="lazy" alt="線索圖片">':'')+'<div class="small">'+(x.visibility==='public'?'🌐 全員可見':x.visibility==='kp'?'🔒 僅 KP':'👤 指定：'+esc((x.target_names&&x.target_names.length?x.target_names.join('、'):x.target_name)||'玩家'))+(x.author?' · 發布：'+esc(x.author):'')+'</div></div>').join(''):'尚無可見線索';lastCluesKey=key}function renderSide(s){let key=JSON.stringify({owner:s.owner_name,players:s.players.map(p=>[p.id,p.name,p.co_kp,p.char_name])});if(key===lastSideKey)return;$('side').innerHTML='<div><b>'+esc(s.owner_name)+'</b> <span class="pill">房主KP</span></div><br>'+s.players.map(p=>'<div><b>'+esc(p.name)+'</b> '+(p.co_kp?'<span class="pill">共同KP</span>':'<span class="pill">玩家</span>')+'<div class="small">'+esc(p.char_name||'未設定角色')+'</div></div><br>').join('');lastSideKey=key}function renderKpMgmt(s,isKP){let key=JSON.stringify({isKP:isKP,role:s.role,players:s.players.map(p=>[p.id,p.name,p.co_kp,p.char_name])});if(key===lastKpMgmtKey)return;$('kokp').innerHTML=isKP?'<h3>玩家管理</h3>'+s.players.map(p=>'<div class="row"><span>'+esc(p.name)+(p.co_kp?'（共同KP）':'')+(p.char_name?' · '+esc(p.char_name):'')+'</span>'+(s.role==='owner'?'<button class="alt" onclick="setCoKp(\''+p.id+'\','+(!p.co_kp)+')">'+(p.co_kp?'取消共同KP':'設為共同KP')+'</button>':'')+(p.char_name?'<button class="bad" onclick="deleteChar(\''+p.id+'\',\''+esc(p.char_name).replace(/&#39;/g,"\\'")+'\')">刪除角色卡</button>':'')+((s.role==='owner'||!p.co_kp)?'<button class="bad" onclick="kickPlayer(\''+p.id+'\',\''+esc(p.name).replace(/&#39;/g,"\\'")+'\')">踢出</button>':'')+'</div>').join(''):'';lastKpMgmtKey=key}
function render(){let s=S.st,isKP=['owner','kp'].includes(s.role);$('title').textContent=s.name;$('roomcode').textContent='房號 '+s.code;$('count').textContent='調查員 '+s.count+'/'+s.limit;$('role').textContent=s.role==='owner'?(s.self_name+'・房主 KP'):s.role==='kp'?(s.self_name+'・共同 KP'):(s.self_name+'・玩家');if($('selfname')&&!selfNameDirty&&document.activeElement!==$('selfname'))$('selfname').value=s.self_name||'';$('leavebtn').classList.toggle('hide',s.role==='owner');$('disbandbtn').classList.toggle('hide',s.role!=='owner');$('kpdisband').classList.toggle('hide',s.role!=='owner');$('kpinvitebtn').classList.toggle('hide',s.role!=='owner');$('kpconsolebtn').classList.toggle('hide',!isKP);document.querySelectorAll('.kp').forEach(x=>x.classList.toggle('hide',!isKP));$('ended').classList.toggle('hide',!s.ended);$('ci').disabled=s.ended;$('sendbtn').disabled=s.ended;$('rollbtn').disabled=s.ended;renderChat(s.chat);renderClues(s.clues);renderSide(s);renderPlayers(s.players,s.role,s.aid,s.ended);if(!limitDirty&&document.activeElement!==$('nl'))$('nl').value=s.limit;let ct=$('ct');if(ct){let chosen=new Set([...ct.querySelectorAll('input[type="checkbox"]:checked')].map(x=>x.value));let cps=s.players.filter(p=>!p.co_kp),sig=JSON.stringify(cps.map(p=>[p.id,p.name,p.char_name]));if(ct.dataset.sig!==sig){ct.innerHTML=cps.length?cps.map(p=>'<label class="targetopt"><input type="checkbox" value="'+p.id+'" '+(chosen.has(p.id)?'checked':'')+'><span>'+esc(p.name)+(p.char_name?' · '+esc(p.char_name):'')+'</span></label>').join(''):'<div class="small">目前沒有可指定的調查員</div>';ct.dataset.sig=sig}}if(isKP&&$('nt')&&document.activeElement!==$('nt')&&!noteDirty)$('nt').value=s.note||'';if($('ownernamebox'))$('ownernamebox').classList.toggle('hide',s.role!=='owner');if(s.role==='owner'&&$('ownername')&&!ownerNameDirty&&document.activeElement!==$('ownername'))$('ownername').value=s.owner_name||'KP';renderKpMgmt(s,isKP)}
function charCard(p,role,aid,ended){if(p.char_hidden)return '<div class="card" data-char-id="'+p.id+'"><b>'+esc(p.name)+'</b><p>🔒 角色卡已設為私密，只有本人與 KP 可查看。</p></div>';let c=p.char||{},can=!ended&&(['owner','kp'].includes(role)||aid===p.id),dis=can?'':'disabled',attrs=c.attrs||{},skills=c.skills||{};let skillText=Object.entries(skills).map(([k,v])=>k+'='+v).join('\n');let inp=(pre,label,val,type='text')=>'<label>'+label+'</label><input id="'+pre+p.id+'" type="'+type+'" value="'+esc(val??'')+'" '+dis+'>';let ta=(pre,label,val)=>'<label>'+label+'</label><textarea id="'+pre+p.id+'" '+dis+'>'+esc(val||'')+'</textarea>';return '<div class="card" data-char-id="'+p.id+'"><h3>'+esc(p.name)+' 的角色卡</h3>'+inp('dn','房間顯示名稱',p.name)+inp('cn','角色名稱',c.name)+inp('co','職業',c.occupation)+ '<div class="row">'+inp('ca','年齡',c.age)+inp('gender','性別',c.gender)+'</div><details><summary>基本資料／背景</summary>'+inp('position','職位／身分',c.position)+inp('birth','出生地',c.birthplace)+inp('residence','居住地',c.residence)+inp('education','學歷',c.education)+inp('sheetplayer','人物卡上的玩家',c.sheet_player)+inp('trait1','特徵1',c.trait1||'')+inp('trait2','特徵2',c.trait2||'')+ta('occnotes','職業備忘',c.occupation_notes)+'</details><div class="attrgrid">'+['STR','CON','SIZ','DEX','APP','INT','POW','EDU'].map(k=>'<label>'+({'STR':'STR（力量）','CON':'CON（體質）','SIZ':'SIZ（體型）','DEX':'DEX（敏捷）','APP':'APP（外貌）','INT':'INT（智力）','POW':'POW（意志）','EDU':'EDU（教育）'}[k])+'<input id="a'+k+p.id+'" type="number" value="'+(attrs[k]||0)+'" '+dis+'></label>').join('')+'</div><div class="row"><label>HP（生命）<input id="hp'+p.id+'" type="number" value="'+(c.hp??10)+'" '+dis+'></label><label>MP（魔法）<input id="mp'+p.id+'" type="number" value="'+(c.mp??10)+'" '+dis+'></label><label>SAN（心智）<input id="san'+p.id+'" type="number" value="'+(c.san??50)+'" '+dis+'></label><label>幸運<input id="luck'+p.id+'" type="number" value="'+(c.luck??50)+'" '+dis+'></label></div><details><summary>衍生數值</summary><div class="row">'+inp('mov','MOV（移動）',c.mov,'number')+inp('build','Build（體格）',c.build)+inp('db','DB（傷害加成）',c.db)+'</div><div class="row">'+inp('insp','靈感',c.inspiration,'number')+inp('know','知識',c.knowledge,'number')+'</div></details><label>技能（每行 技能=數值）</label><textarea id="sk'+p.id+'" '+dis+'>'+esc(skillText)+'</textarea><details><summary>資產／金錢</summary>'+inp('currency','貨幣',c.currency)+inp('living','生活水平',c.living_standard)+inp('cash','現金',c.cash)+inp('spending','消費水平',c.spending_level)+inp('assets','資產',c.assets)+ta('assetdetails','資產內容',c.asset_details)+'</details><details><summary>調查員背景故事</summary>'+ta('appearance','形象描述',c.appearance_desc)+ta('personality','性格特徵',c.personality)+ta('ideology','思想／信念',c.ideology)+ta('scars','傷勢及傷痕',c.scars)+ta('significant','重要之人及理由',c.significant_person)+ta('phobias','恐懼症及狂躁症',c.phobias)+ta('place','意義非凡之地',c.meaningful_place)+ta('treasure','寶貴之物',c.treasured_item)+ta('anchor','心靈支柱',c.anchor)+ta('exp','經驗包',c.experience_pack)+ta('backstory','生平故事',c.backstory)+'</details><details><summary>狀態</summary>'+inp('armor','護甲',c.armor)+inp('insanity','瘋狂狀態',c.insanity_status)+inp('condition','傷勢狀態',c.condition_status)+inp('mythbeliever','神話相信者',c.mythos_believer)+'</details>'+ta('inv','隨身物品',c.inventory)+'<details><summary>武器／冒險／關係／咒文／神話接觸</summary>'+ta('weapons','武器',c.weapons)+ta('adventures','劇本遭遇／改變',c.adventures)+ta('contacts','同伴及關係人',c.contacts)+ta('spells','咒文',c.spells)+ta('supernatural','第三類接觸／神話知識',c.supernatural)+ta('tomes','神話典籍',c.mythos_tomes)+'</details>'+ta('pn','公開備註',c.public_notes)+ta('pr','私人備註（僅本人＋KP）',c.private_notes)+'<label>角色卡可見範圍</label><select id="vis'+p.id+'" '+dis+'><option value="kp_only" '+(c.visibility!=='public'?'selected':'')+'>僅本人＋KP</option><option value="public" '+(c.visibility==='public'?'selected':'')+'>全房間公開</option></select>'+(can?'<button onclick="saveChar(\''+p.id+'\')">儲存角色卡</button> '+(['owner','kp'].includes(role)&&c.name?'<button class="bad" onclick="deleteChar(\''+p.id+'\',\''+esc(c.name).replace(/&#39;/g,"\\'")+'\')">刪除角色卡</button>':'')+'<details><summary>匯入 CoC7 Google 試算表</summary><div class="small">支援 CoC7版自動計算人物卡V3；會讀取基本資料、屬性、技能、資產、背景、物品、武器、咒文等。</div><input id="url'+p.id+'" placeholder="Google 試算表連結"><button class="alt" onclick="importSheet(\''+p.id+'\',\'url\')">讀取連結</button><textarea id="paste'+p.id+'" placeholder="或貼上試算表 A1:Z176 資料"></textarea><button class="alt" onclick="importSheet(\''+p.id+'\',\'text\')">讀取貼上資料</button></details>':'')+'</div>'}
function parseSkills(t){let o={};t.split(/\n+/).forEach(l=>{let m=l.match(/^(.+?)[=:：]\s*(\d{1,3})\s*$/);if(m)o[m[1].trim()]=Math.max(0,Math.min(100,+m[2]))});return o}async function saveChar(id){try{let attrs={};['STR','CON','SIZ','DEX','APP','INT','POW','EDU'].forEach(k=>attrs[k]=+$('a'+k+id).value);let g=pre=>$(pre+id)?$(pre+id).value:'';let ch={name:g('cn'),occupation:g('co'),age:g('ca'),gender:g('gender'),position:g('position'),birthplace:g('birth'),residence:g('residence'),education:g('education'),sheet_player:g('sheetplayer'),trait1:g('trait1'),trait2:g('trait2'),traits:[g('trait1'),g('trait2')].filter(Boolean).join('、'),occupation_notes:g('occnotes'),attrs,hp:+g('hp'),mp:+g('mp'),san:+g('san'),luck:+g('luck'),mov:+g('mov'),build:g('build'),db:g('db'),inspiration:+g('insp'),knowledge:+g('know'),skills:parseSkills(g('sk')),currency:g('currency'),living_standard:g('living'),cash:g('cash'),spending_level:g('spending'),assets:g('assets'),asset_details:g('assetdetails'),appearance_desc:g('appearance'),personality:g('personality'),ideology:g('ideology'),scars:g('scars'),significant_person:g('significant'),phobias:g('phobias'),meaningful_place:g('place'),treasured_item:g('treasure'),anchor:g('anchor'),experience_pack:g('exp'),backstory:g('backstory'),armor:g('armor'),insanity_status:g('insanity'),condition_status:g('condition'),mythos_believer:g('mythbeliever'),inventory:g('inv'),weapons:g('weapons'),adventures:g('adventures'),contacts:g('contacts'),spells:g('spells'),supernatural:g('supernatural'),mythos_tomes:g('tomes'),public_notes:g('pn'),private_notes:g('pr'),visibility:g('vis'),source:'manual'};await api('/api/room/'+S.code+'/rename',{method:'POST',body:JSON.stringify({token:S.token,pid:id,name:g('dn')||'未命名'})});await api('/api/room/'+S.code+'/char',{method:'POST',body:JSON.stringify({token:S.token,pid:id,char:ch})});charDirty.delete(id);delete charDrafts[id];if(document.activeElement&&document.activeElement.blur)document.activeElement.blur();lastPlayersKey='';await refresh();notify('✓ 角色卡已儲存')}catch(e){notify(e.message,true)}}async function deleteChar(id,name){if(!confirm('確定刪除「'+name+'」的角色卡？角色資料會清空且無法復原。'))return;try{await api('/api/room/'+S.code+'/delete-char',{method:'POST',body:JSON.stringify({token:S.token,pid:id})});charDirty.delete(id);delete charDrafts[id];delete charUiState[id];lastPlayersKey='';notify('✓ 角色卡已刪除');await refresh()}catch(e){notify(e.message,true)}}async function importSheet(id,kind){try{let body={token:S.token,pid:id};if(kind==='url')body.url=$('url'+id).value;else body.text=$('paste'+id).value;let d=await api('/api/room/'+S.code+'/import-sheet',{method:'POST',body:JSON.stringify(body)});applyPreview(id,d.preview);let n=Object.keys(d.preview.skills||{}).length;notify('✓ 已讀取人物卡：'+(d.preview.name||'未命名')+'，'+n+' 項技能與其他資料')}catch(e){notify(e.message,true)}}function applyPreview(id,c){let set=(pre,v)=>{let e=$(pre+id);if(e)e.value=v??''};set('cn',c.name);set('co',c.occupation);set('ca',c.age);set('gender',c.gender);set('position',c.position);set('birth',c.birthplace);set('residence',c.residence);set('education',c.education);set('sheetplayer',c.sheet_player);set('trait1',c.trait1);set('trait2',c.trait2);set('occnotes',c.occupation_notes);Object.entries(c.attrs||{}).forEach(([k,v])=>set('a'+k,v));set('hp',c.hp);set('mp',c.mp);set('san',c.san);set('luck',c.luck);set('mov',c.mov);set('build',c.build);set('db',c.db);set('insp',c.inspiration);set('know',c.knowledge);set('sk',Object.entries(c.skills||{}).map(([k,v])=>k+'='+v).join('\n'));set('currency',c.currency);set('living',c.living_standard);set('cash',c.cash);set('spending',c.spending_level);set('assets',c.assets);set('assetdetails',c.asset_details);set('appearance',c.appearance_desc);set('personality',c.personality);set('ideology',c.ideology);set('scars',c.scars);set('significant',c.significant_person);set('phobias',c.phobias);set('place',c.meaningful_place);set('treasure',c.treasured_item);set('anchor',c.anchor);set('exp',c.experience_pack);set('backstory',c.backstory);set('armor',c.armor);set('insanity',c.insanity_status);set('condition',c.condition_status);set('mythbeliever',c.mythos_believer);set('inv',c.inventory);set('weapons',c.weapons);set('adventures',c.adventures);set('contacts',c.contacts);set('spells',c.spells);set('supernatural',c.supernatural);set('tomes',c.mythos_tomes);charDirty.add(id);captureCharDraft(id)}
async function send(){let t=$('ci').value.trim();if(!t)return;try{await api('/api/room/'+S.code+'/chat',{method:'POST',body:JSON.stringify({token:S.token,text:t})});$('ci').value='';forceChatBottom=true;refresh()}catch(e){notify(e.message,true)}}async function sendDiceCmd(){let t=$('dicecmd').value.trim();if(!t)return;try{await api('/api/room/'+S.code+'/chat',{method:'POST',body:JSON.stringify({token:S.token,text:t})});$('dicecmd').value='';await refresh();tab('story')}catch(e){notify(e.message,true)}}async function renameOwner(){try{let d=await api('/api/room/'+S.code+'/rename',{method:'POST',body:JSON.stringify({token:S.token,name:$('ownername').value||'KP'})});ownerNameDirty=false;$('ownername').value=d.name;notify('✓ KP 名稱已改為 '+d.name);refresh()}catch(e){notify(e.message,true)}}async function setLimit(){try{await api('/api/room/'+S.code+'/limit',{method:'POST',body:JSON.stringify({token:S.token,limit:+$('nl').value})});limitDirty=false;refresh()}catch(e){notify(e.message,true)}}function clearClueImage(){clueImageData='';if($('climg'))$('climg').value='';$('climgpreview').classList.add('hide');$('climgpreviewpic').removeAttribute('src')}async function prepareClueImage(){let file=$('climg').files&&$('climg').files[0];if(!file)return clearClueImage();if(!/^image\/(jpeg|png|webp|gif)$/.test(file.type))return notify('只支援 JPG、PNG、WebP、GIF',true);if(file.size>10*1024*1024){clearClueImage();return notify('原始圖片請小於 10 MB',true)}try{if(file.type==='image/gif'&&file.size<=3*1024*1024){clueImageData=await new Promise((ok,no)=>{let r=new FileReader();r.onload=()=>ok(r.result);r.onerror=no;r.readAsDataURL(file)})}else{let src=await new Promise((ok,no)=>{let r=new FileReader();r.onload=()=>ok(r.result);r.onerror=no;r.readAsDataURL(file)});let im=await new Promise((ok,no)=>{let x=new Image();x.onload=()=>ok(x);x.onerror=no;x.src=src});let max=1920,scale=Math.min(1,max/Math.max(im.width,im.height)),w=Math.max(1,Math.round(im.width*scale)),h=Math.max(1,Math.round(im.height*scale));let cv=document.createElement('canvas');cv.width=w;cv.height=h;cv.getContext('2d').drawImage(im,0,0,w,h);clueImageData=cv.toDataURL('image/jpeg',0.9);if(clueImageData.length>4_400_000)clueImageData=cv.toDataURL('image/jpeg',0.72)}if(clueImageData.length>4_500_000){clearClueImage();return notify('圖片壓縮後仍太大，請換較小圖片',true)}$('climgpreviewpic').src=clueImageData;$('climgpreview').classList.remove('hide');notify('✓ 圖片已準備')}catch(e){clearClueImage();notify('圖片讀取失敗',true)}}function selectAllClueTargets(on){document.querySelectorAll('#ct input[type="checkbox"]').forEach(x=>x.checked=!!on)}async function addClue(visibility){let text=$('cl').value.trim();if(!text&&!clueImageData)return notify('請輸入文字或選擇圖片',true);let targets=visibility==='player'?[...document.querySelectorAll('#ct input[type="checkbox"]:checked')].map(x=>x.value):[];if(visibility==='player'&&!targets.length)return notify('請至少選擇一位調查員',true);try{await api('/api/room/'+S.code+'/clue',{method:'POST',body:JSON.stringify({token:S.token,text:text,visibility:visibility,targets:targets,image_data:clueImageData||null})});$('cl').value='';clearClueImage();notify(visibility==='public'?'✓ 線索已公開給全員':visibility==='player'?'✓ 線索已交給指定玩家':'✓ 線索已保存為 KP 私藏');await refresh()}catch(e){notify(e.message,true)}}function queueNoteSave(){noteDirty=true;let seq=++noteEditSeq,st=$('notestatus');if(st)st.textContent='儲存中…';clearTimeout(noteSaveTimer);noteSaveTimer=setTimeout(()=>saveNoteAuto(seq),500)}async function saveNoteAuto(seq){if(!S.code||!$('nt'))return;let text=$('nt').value;try{await api('/api/room/'+S.code+'/note',{method:'POST',body:JSON.stringify({token:S.token,text:text})});if(seq===noteEditSeq&&$('nt').value===text){noteDirty=false;let st=$('notestatus');if(st){st.textContent='✓ 已自動儲存';clearTimeout(window.__noteStatusTimer);window.__noteStatusTimer=setTimeout(()=>{if(st)st.textContent='自動儲存'},1300)}}}catch(e){if(seq===noteEditSeq){let st=$('notestatus');if(st)st.textContent='⚠ 儲存失敗';notify(e.message,true)}}}async function setCoKp(id,en){try{await api('/api/room/'+S.code+'/co-kp',{method:'POST',body:JSON.stringify({token:S.token,pid:id,enabled:en})});refresh()}catch(e){notify(e.message,true)}}async function kickPlayer(id,name){try{await api('/api/room/'+S.code+'/kick',{method:'POST',body:JSON.stringify({token:S.token,pid:id})});notify('✓ 已將 '+name+' 移出房間');refresh()}catch(e){notify(e.message,true)}}async function leaveRoom(){try{await api('/api/room/'+S.code+'/leave',{method:'POST',body:JSON.stringify({token:S.token})});resetHome();notify('✓ 你已離開房間')}catch(e){notify(e.message,true)}}async function disbandRoom(){if(!confirm('確定要解散房間？所有玩家都會立即離開。'))return;try{await api('/api/room/'+S.code+'/disband',{method:'POST',body:JSON.stringify({token:S.token})});resetHome('房間已解散。')}catch(e){notify(e.message,true)}}async function endRoom(){if(confirm('結束遊戲後將停止聊天、擲骰與角色修改。確定結束？')){try{await api('/api/room/'+S.code+'/end',{method:'POST',body:JSON.stringify({token:S.token})});notify('✓ 遊戲已結束');refresh()}catch(e){notify(e.message,true)}}}function tab(id){document.querySelectorAll('.sec').forEach(x=>x.classList.remove('on'));$(id).classList.add('on');scrollTo(0,0)}async function clip(t){let ok=false;try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(t);ok=true}else{let x=document.createElement('textarea');x.value=t;x.setAttribute('readonly','');x.style.position='fixed';x.style.opacity='0';document.body.appendChild(x);x.select();ok=document.execCommand('copy');x.remove()}}catch(e){ok=false}notify(ok?'✓ 已複製':'複製失敗，請長按文字複製',!ok);return ok}function copyCode(){clip(S.code)}function copyInvite(){clip(location.origin+'/?room='+S.code)}function copyKpInvite(){if(!S.st||!S.st.kp_invite)return notify('共同 KP 邀請尚未載入',true);clip(location.origin+'/?room='+S.code+'&kp='+encodeURIComponent(S.st.kp_invite))}if($('players')){$('players').addEventListener('input',markCharDirtyFromEvent);$('players').addEventListener('change',markCharDirtyFromEvent)}let qp=new URLSearchParams(location.search);if(qp.get('room')){$('rc').value=qp.get('room').toUpperCase();$('gkc').value=qp.get('room').toUpperCase();if(qp.get('kp')){$('gki').value=qp.get('kp');$('gkn').focus()}else{$('gn').focus()}}try{let d=JSON.parse(localStorage.getItem('coc-v8')||'null');if(d&&d.code&&d.token)enter(d)}catch(e){}
</script></body></html>'''