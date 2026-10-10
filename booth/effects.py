"""AI 효과 버튼(선택 기능): GPT 이미지 편집으로 내가 주인공인 웹툰·영화 포스터·명화를 만든다.

방문객이 QR로 연 휴대폰 화면에서 버튼을 누를 때만 만든다(비용은 누른 만큼만, 부스 줄은 안 밀린다).
빛 보정(booth/ai.py)과 달리 그림 자체가 바뀌므로 결과를 그대로 쓴다. 얼굴은 살리도록 지시하지만
조금 달라질 수 있어 원본은 항상 함께 둔다. 결과는 세로 한 장(1024x1536)이고 프레임 없이 그 자체가 완성본이다
(말풍선·제목 글씨까지 GPT가 쓴다). 네 컷은 컷 네 장을 한 번에 보내 한 장으로 만든다(요청도 1장으로 센다).
하루 상한(YS_GPT_DAILY)을 넘으면 더 만들지 않는다. GPU를 쓰지 않으므로 촬영 합성과 다투지 않는다.
"""
import asyncio
import base64
import datetime
import json
import threading
import time
import urllib.error
import urllib.request
import uuid

import cv2
import numpy as np
from PIL import Image

from . import compose, config, records, storage

_lock = threading.Lock()
_jobs = {}            # (sid, 효과 id) -> 'pending' | 'failed' | 'limit'
_sem = None           # 동시에 보내는 API 요청 수
_day = {'date': None, 'count': 0, 'cost': 0.0}
# 하루 사용량은 파일에 남긴다. 서버를 다시 켜도 상한(비용 안전장치)이 0부터 다시 세지 않도록
_USAGE = config.OUT_DIR / 'gpt_usage.json'
_loaded = False
_tasks = set()        # 돌고 있는 작업 (가비지 컬렉션에 사라지지 않도록 붙잡아 둔다)
_tries = {}           # (sid, 효과 id) -> 만들어 본 횟수. 같은 사진·같은 효과는 실패해도 다시 한 번까지만
MAX_TRIES = 2
# 만드는 중인 작업의 진행 상황. 휴대폰 화면의 진행 애니메이션이 이걸 보고 움직인다
# stage: send(보내는 중) -> draw(GPT가 그리는 중) -> finish(얼굴 확인·저장)
_progress = {}        # (sid, 효과 id) -> {'stage', 'drawn', 'done', 'total', 't0'}
_avg = {}             # 효과 id -> 최근 걸린 시간(초). 예상 시간으로 보여 준다
DEFAULT_ETA = 32.0    # 실측: 한 장 약 30초 + 마무리
# OpenAI가 키를 거절(401)하면 부스를 다시 켤 때까지 효과 버튼을 내놓지 않는다. 키가 지워지거나 막히면
# 방문객이 누를 때마다 실패만 하기 때문 (2026-10-08 실제로 키가 막혀 모든 요청이 401이었다)
_key_bad = False


def enabled():
    return config.gpt_ready() and not _key_bad


def key_rejected():
    """키는 넣었는데 OpenAI가 거절한 상태 (관리 화면에 알린다)."""
    return config.gpt_ready() and _key_bad


def _reject_key(where):
    global _key_bad
    if not _key_bad:
        print(f'[효과] OpenAI가 키를 거절했어요(401, {where}). 키가 지워졌거나 막혔습니다. '
              '.env의 OPENAI_KEY를 새로 발급한 키로 바꾸고 부스를 다시 켜 주세요. 그때까지 AI 효과 버튼을 숨깁니다.',
              flush=True)
    _key_bad = True


def check_key():
    """부스를 켤 때 키가 살아 있는지 한 번 본다 (무료인 모델 목록 조회). 인터넷이 없거나 다른 오류면 그냥 넘어간다."""
    if not config.gpt_ready():
        return
    req = urllib.request.Request('https://api.openai.com/v1/models',
                                 headers={'Authorization': f'Bearer {config.OPENAI_KEY}'})
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except urllib.error.HTTPError as e:
        if e.code == 401:
            _reject_key('켤 때 확인')
    except Exception:  # noqa: BLE001
        pass


def catalog():
    """휴대폰 화면에 보여 줄 효과 버튼 목록. 키가 없으면 빈 목록(버튼이 안 보인다)."""
    if not enabled():
        return []
    return [{'id': e['id'], 'name': e['name'], 'desc': e['desc']} for e in config.EFFECTS]


def _urls(sid, fx):
    """지금 효과는 세로 그림 한 장이 곧 완성본이라 둘이 같다 (예전 효과만 프레임 없는 사진이 따로 있다)."""
    final = f'/media/{sid}/fxf_{fx}.jpg'
    photo = f'/media/{sid}/fx_{fx}.jpg' if storage.path(sid, f'fx_{fx}').exists() else final
    return {'final': final, 'photo': photo}


def status(sid):
    """효과마다 상태. 파일이 있으면 ready(서버를 다시 켜도 그대로), 아니면 만드는 중·실패·상한.
    예전 효과로 이미 만든 사진도 ready로 알려 준다 (휴대폰 화면에 이름과 함께 그대로 보이게)."""
    out = {}
    for fx in config.EFFECT_IDS:
        if storage.path(sid, f'fxf_{fx}').exists():
            out[fx] = {'state': 'ready', 'name': config.EFFECT_NAMES[fx], **_urls(sid, fx)}
            continue
        with _lock:
            st = _jobs.get((sid, fx))
            p = dict(_progress.get((sid, fx)) or {})
        if st:
            item = {'state': st}
            if st == 'pending' and p:
                item.update(stage=p['stage'], done=p['done'], total=p['total'],
                            elapsed=round(time.time() - p['t0'], 1),
                            eta=round(_avg.get(fx, DEFAULT_ETA), 1))
            out[fx] = item
    return out


def _trim(d, keep=5000):
    """오래된 것부터 버린다 (_lock 안에서 부른다). 축제 내내 쌓여도 메모리가 늘지 않게."""
    while len(d) > keep:
        d.pop(next(iter(d)))


def _step(sid, fx, stage=None, drawn=0, done=0):
    """진행 상황을 한 칸 옮긴다. 네 컷은 네 장을 다 그려야 '마무리'로 넘어간다."""
    with _lock:
        p = _progress.get((sid, fx))
        if not p:
            return
        p['drawn'] += drawn
        p['done'] += done
        if stage:
            p['stage'] = stage
        elif drawn and p['drawn'] >= p['total']:
            p['stage'] = 'finish'


def _today():
    """오늘 사용량을 맞춰 둔다 (_lock 안에서 부른다)."""
    global _loaded
    today = datetime.date.today()
    if not _loaded:
        _loaded = True
        try:
            d = json.loads(_USAGE.read_text(encoding='utf-8'))
            if d.get('date') == str(today):
                _day.update(date=today, count=int(d['count']), cost=float(d['cost']))
        except (OSError, ValueError, KeyError):
            pass
    if _day['date'] != today:
        _day.update(date=today, count=0, cost=0.0)


def _save_usage():
    try:
        _USAGE.write_text(json.dumps({'date': str(_day['date']), 'count': _day['count'],
                                      'cost': round(_day['cost'], 4)}), encoding='utf-8')
    except OSError:
        pass


def _refund(n):
    """보내지도 못하고 끝난 몫(키 거절 등)은 오늘 장수에서 돌려준다."""
    with _lock:
        _day['count'] = max(0, _day['count'] - n)
        _save_usage()


def _spend(n):
    """오늘 쓸 수 있는 만큼 남았으면 n장을 미리 잡는다."""
    with _lock:
        _today()
        if _day['count'] + n > config.GPT_DAILY:
            return False
        _day['count'] += n
        _save_usage()
        return True


def usage():
    with _lock:
        _today()
        return {'today': _day['count'], 'limit': config.GPT_DAILY, 'cost': round(_day['cost'], 3)}


def _cost(u):
    """응답의 실제 사용 토큰으로 이번 요청 비용(달러)을 셈한다."""
    p = config.GPT_PRICE
    d = u.get('input_tokens_details') or {}
    text = d.get('text_tokens', 0)
    image = d.get('image_tokens', max(0, u.get('input_tokens', 0) - text))
    return (text * p['text'] + image * p['image'] + u.get('output_tokens', 0) * p['output']) / 1e6


def request(sid, fx):
    """휴대폰에서 효과 버튼을 눌렀을 때. 이미 있거나 만드는 중이면 그대로 둔다(같은 사진은 한 번만 만든다)."""
    if not enabled():
        return {'state': 'off'}
    if fx not in config.EFFECT_BY_ID:
        return {'state': 'unknown'}
    # 이미 만든 사진은 그대로 돌려주고, 만드는 중이면 그 작업에 합류한다 (같은 일을 두 번 보내지 않는다)
    cur = status(sid).get(fx)
    if cur and cur['state'] in ('ready', 'pending'):
        return cur
    with _lock:
        if _tries.get((sid, fx), 0) >= MAX_TRIES:  # 실패한 것을 끝없이 다시 보내지 않는다
            _jobs[(sid, fx)] = 'max'
            return {'state': 'max'}
        _tries[(sid, fx)] = _tries.get((sid, fx), 0) + 1
        _trim(_tries)
        _trim(_jobs)
    rec = records.get(sid)
    if rec is None or not storage.path(sid, 'final').exists():
        return {'state': 'missing'}
    sources = _sources(sid, rec)
    if not sources:
        return {'state': 'missing'}
    if not _spend(1):  # 네 컷도 한 번에 한 장으로 만든다
        with _lock:
            _jobs[(sid, fx)] = 'limit'
        print(f'[효과] 오늘 상한({config.GPT_DAILY}장)에 닿아 건너뜀 {sid} {fx}', flush=True)
        return {'state': 'limit'}
    with _lock:
        _jobs[(sid, fx)] = 'pending'
        _progress[(sid, fx)] = {'stage': 'send', 'drawn': 0, 'done': 0, 'total': 1, 't0': time.time()}
    task = asyncio.get_running_loop().create_task(_make(sid, fx, rec, sources))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {'state': 'pending'}


def _sources(sid, rec):
    """효과를 입힐 원본 사진들. 1컷은 고른 사진 하나, 네 컷은 컷마다 따로 남겨 둔 사진 네 장."""
    if int(rec.get('cuts') or 1) > 1:
        paths = [storage.path(sid, f'c{i}') for i in range(4)]
    else:
        paths = [storage.path(sid, 'shot')]
    return paths if all(p.exists() for p in paths) else []


def _line(msg, default):
    """그림 속 글씨(말풍선·포스터 문구)로 넣을 한마디. 줄은 띄어 쓰고 따옴표·이모지는 빼며, 없거나 너무 길면 기본 문구."""
    text = ''.join(c for c in (msg or '') if c not in '"“”\'‘’`' and ord(c) < 0x10000 and (c.isprintable() or c == '\n'))
    text = ' '.join(text.split())
    return text if 0 < len(text) <= config.FX_LINE_MAX else default


def _prompt(effect, sid, rec, n, people=0):
    """효과 프롬프트를 이 사진에 맞춰 채운다: 장소별 제목·화풍(네 컷은 첫 컷 장소), 한마디, 찍은 날짜, 사람 수."""
    bgs = rec.get('bgs') or [rec.get('bg')]
    place = config.FX_BY_BG.get(bgs[0], config.FX_DEFAULT)
    _, style, hang = config.ART_STYLES.get(place['art'], config.ART_STYLES['monet'])
    when = datetime.datetime.fromtimestamp(storage.taken_at(sid) or time.time(), config.LOCAL_TZ)
    return (effect['many'] if n > 1 else effect['one']).format(
        n=n, line=_line(rec.get('msg', ''), effect.get('line', '')), title=place['title'],
        date=f'{when.month}월 {when.day}일', style=style,
        display=config.ART_DISPLAY[hang].format(art_title=place['art_title']),
        keep=config.FX_KEEP.format(photo='photos' if n > 1 else 'photo',
                                   count=_count(people, n, effect.get('panels'))))


def _count(people, n, panels):
    """'사진 속 사람은 정확히 2명' 문장. 얼굴을 못 찾았으면(뒷모습 등) 넣지 않는다."""
    if not people:
        return ''
    who = 'person' if people == 1 else 'people'
    out = f'There {"is" if people == 1 else "are"} exactly {people} {who} in {"this photo" if n == 1 else "each photo"}. '
    if panels:
        return out + f'In every panel, draw only {"this person" if people == 1 else f"these {people} people"} and nobody else. '
    return out + f'Draw exactly {people} {who}, each only once, and nobody else. '


async def _make(sid, fx, rec, sources):
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(config.GPT_PARALLEL)
    effect = config.EFFECT_BY_ID[fx]
    t0 = time.time()
    try:
        # 원본에서 가장 많이 보인 얼굴 수. 결과가 이보다 많으면 없던 사람을 그려 넣은 것이다
        # ('가족사진'이라고 했더니 혼자 찍은 아이 옆에 어른 둘을 지어낸 일이 있었다).
        # 웹툰은 같은 사람이 칸마다 나오므로 칸 수(한 장이면 3칸, 네 컷이면 4칸)만큼 늘려 준다
        base = max(await asyncio.to_thread(lambda: [count_faces(cv2.imread(str(p))) for p in sources]))
        limit = base * (max(3, len(sources)) if effect.get('panels') else 1)
        prompt = _prompt(effect, sid, rec, len(sources), base)
        img = None
        for attempt in range(2):
            if attempt and not _spend(1):  # 다시 그리는 것도 하루 상한 안에서만
                break
            async with _sem:
                _step(sid, fx, 'draw')
                out = await asyncio.to_thread(_edit, sources, prompt)
            found = await asyncio.to_thread(count_faces, _bgr(out))
            if base and found > limit:
                print(f'[효과] 얼굴이 {base}명에서 {found}명으로 늘어 버림 ({sid} {fx}, {attempt + 1}번째)', flush=True)
                continue
            img = out
            break
        if img is None:
            raise RuntimeError('GPT가 없던 사람을 그려 넣었다')
        _step(sid, fx, drawn=1)
        await asyncio.to_thread(_save, sid, fx, img)
        took = time.time() - t0
        with _lock:
            _jobs.pop((sid, fx), None)
            _progress.pop((sid, fx), None)
            _avg[fx] = took if fx not in _avg else _avg[fx] * 0.7 + took * 0.3
        print(f'[효과] 완료 {sid} {fx} ({len(sources)}컷 → 1장, {took:.1f}초)', flush=True)
    except Exception as e:  # 실패해도 방문객은 원본을 그대로 받는다
        if _key_bad:
            _refund(1)  # 키가 거절돼 그리지도 못했으니 오늘 장수에 넣지 않는다
        with _lock:
            # 키가 거절됐으면 '다시 시도'를 보여 주지 않는다 (다시 눌러도 같다)
            _jobs[(sid, fx)] = 'off' if _key_bad else 'failed'
            _progress.pop((sid, fx), None)
        print(f'[효과] 실패 {sid} {fx}: {e}', flush=True)


def _save(sid, fx, img):
    """세로 그림 한 장이 완성본이다 (프레임에 넣지 않는다. 제목·말풍선까지 그림 안에 있다)."""
    tmp = storage.path(sid, f'fxf_{fx}').with_suffix('.part')
    img.save(tmp, 'JPEG', quality=93, subsampling=0)
    tmp.replace(storage.path(sid, f'fxf_{fx}'))  # 다 쓴 뒤에 이름을 바꿔, 반쯤 쓴 파일이 '완성'으로 보이지 않게


_faces = None


def _face_session():
    global _faces
    if _faces is None:
        import onnxruntime as ort
        compose._download(config.FACE_MODEL, config.FACE_URL)
        o = ort.SessionOptions()
        o.log_severity_level = 3
        _faces = ort.InferenceSession(str(config.FACE_MODEL), o, providers=['CPUExecutionProvider'])
    return _faces


def _bgr(img):
    return cv2.cvtColor(np.array(img.convert('RGB')), cv2.COLOR_RGB2BGR)


def count_faces(bgr, threshold=0.7):
    """사진 속 얼굴 수. GPT 결과가 원본보다 많으면 없던 사람을 그려 넣은 것이다."""
    sess = _face_session()
    h, w = bgr.shape[:2]
    if h * 4 != w * 3:  # 세로 그림은 4:3으로 검은 여백을 붙여 넣는다 (그냥 줄이면 얼굴이 납작해져 못 찾는다)
        H, W = max(h, w * 3 // 4), max(w, h * 4 // 3)
        bgr = cv2.copyMakeBorder(bgr, (H - h) // 2, H - h - (H - h) // 2, (W - w) // 2, W - w - (W - w) // 2,
                                 cv2.BORDER_CONSTANT)
    x = cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), (640, 480)).astype(np.float32)
    x = ((x - 127) / 128).transpose(2, 0, 1)[None]
    scores, boxes = sess.run(None, {sess.get_inputs()[0].name: x})
    keep = scores[0, :, 1] > threshold
    sc, bx = scores[0, keep, 1], boxes[0, keep]
    area = (bx[:, 2] - bx[:, 0]) * (bx[:, 3] - bx[:, 1])
    order, n = sc.argsort()[::-1], 0
    while order.size:  # 겹치는 상자는 하나로 (NMS)
        i, rest = order[0], order[1:]
        n += 1
        w = np.clip(np.minimum(bx[i, 2], bx[rest, 2]) - np.maximum(bx[i, 0], bx[rest, 0]), 0, None)
        h = np.clip(np.minimum(bx[i, 3], bx[rest, 3]) - np.maximum(bx[i, 1], bx[rest, 1]), 0, None)
        iou = w * h / (area[i] + area[rest] - w * h + 1e-9)
        order = rest[iou < 0.3]
    return n


def _multipart(fields, files):
    """files: [(필드 이름, (파일 이름, 내용, 형식)), ...] (같은 필드 이름을 여러 번 쓸 수 있게 목록으로 받는다)."""
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (name, data, ctype) in files:
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                     f'Content-Type: {ctype}\r\n\r\n'.encode() + data + b'\r\n')
    parts.append(f'--{b}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={b}'


def _edit(paths, prompt):
    """OpenAI 이미지 편집(/v1/images/edits). 사진(네 컷이면 네 장)은 1024x768(4:3)로 줄여 보내고,
    세로 그림(GPT_SIZE) 한 장을 받는다. 여러 장은 image[]로 한꺼번에 보낸다."""
    files = []
    for n, path in enumerate(paths):
        src = cv2.resize(cv2.imread(str(path)), (1024, 768), interpolation=cv2.INTER_AREA)
        jpg = cv2.imencode('.jpg', src, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        files.append(('image[]' if len(paths) > 1 else 'image', (f'photo{n + 1}.jpg', jpg, 'image/jpeg')))
    body, ctype = _multipart(
        {'model': config.GPT_MODEL, 'prompt': prompt, 'size': config.GPT_SIZE,
         'quality': config.GPT_QUALITY, 'n': '1', 'output_format': 'jpeg'}, files)
    req = urllib.request.Request('https://api.openai.com/v1/images/edits', data=body, headers={
        'Authorization': f'Bearer {config.OPENAI_KEY}', 'Content-Type': ctype})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=config.GPT_TIMEOUT) as r:
                res = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            # 요청이 몰려 '잠시 뒤에'(429)나 서버 쪽 오류(5xx)면 조금 쉬었다가 다시 보낸다
            if e.code in (429, 500, 502, 503) and attempt < 2:
                wait = float(e.headers.get('retry-after') or 0) or 5 * (attempt + 1)
                print(f'[효과] HTTP {e.code}, {wait:.0f}초 뒤 다시 보냄', flush=True)
                time.sleep(min(wait, 30))
                continue
            if e.code == 401:
                _reject_key('사진 만들기')
            # 키·요금·내용 검사 같은 이유가 로그에 남도록
            raise RuntimeError(f'HTTP {e.code} {e.read()[:300]!r}') from None
    u = res.get('usage') or {}
    cost = _cost(u)
    with _lock:
        _today()
        _day['cost'] += cost
        _save_usage()
    print(f'[효과] 토큰 입력 {u.get("input_tokens")} · 출력 {u.get("output_tokens")} → ${cost:.4f}', flush=True)
    data = base64.b64decode(res['data'][0]['b64_json'])
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
