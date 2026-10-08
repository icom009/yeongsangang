"""AI 효과 버튼(선택 기능): GPT 이미지 편집으로 'AI 장면 연출'·그림체를 바꾼 사진을 만든다.

방문객이 QR로 연 휴대폰 화면에서 버튼을 누를 때만 만든다(비용은 누른 만큼만, 부스 줄은 안 밀린다).
빛 보정(booth/ai.py)과 달리 그림 자체가 바뀌므로 결과를 그대로 쓴다. 표정은 살리도록 지시하지만
얼굴이 조금 달라질 수 있어 원본은 항상 함께 둔다. 네 컷은 컷마다 따로 만들어 한 장으로 모은다.
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

from . import compose, config, frame, records, storage

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
# stage: send(보내는 중) -> draw(GPT가 그리는 중) -> finish(얼굴 확인·배경 맞추기·프레임)
_progress = {}        # (sid, 효과 id) -> {'stage', 'drawn', 'done', 'total', 't0'}
_avg = {}             # 효과 id -> 최근 걸린 시간(초). 예상 시간으로 보여 준다
DEFAULT_ETA = 32.0    # 실측: 한 장 약 30초 + 마무리


def enabled():
    return config.gpt_ready()


def catalog():
    """휴대폰 화면에 보여 줄 효과 버튼 목록. 키가 없으면 빈 목록(버튼이 안 보인다)."""
    if not enabled():
        return []
    return [{'id': e['id'], 'name': e['name'], 'desc': e['desc']} for e in config.EFFECTS]


def _urls(sid, fx):
    return {'final': f'/media/{sid}/fxf_{fx}.jpg', 'photo': f'/media/{sid}/fx_{fx}.jpg'}


def status(sid):
    """효과마다 상태. 파일이 있으면 ready(서버를 다시 켜도 그대로), 아니면 만드는 중·실패·상한."""
    out = {}
    for fx in config.EFFECT_IDS:
        if storage.path(sid, f'fxf_{fx}').exists():
            out[fx] = {'state': 'ready', **_urls(sid, fx)}
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
    if not _spend(len(sources)):
        with _lock:
            _jobs[(sid, fx)] = 'limit'
        print(f'[효과] 오늘 상한({config.GPT_DAILY}장)에 닿아 건너뜀 {sid} {fx}', flush=True)
        return {'state': 'limit'}
    with _lock:
        _jobs[(sid, fx)] = 'pending'
        _progress[(sid, fx)] = {'stage': 'send', 'drawn': 0, 'done': 0, 'total': len(sources),
                                't0': time.time()}
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


async def _make(sid, fx, rec, sources):
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(config.GPT_PARALLEL)
    effect = config.EFFECT_BY_ID[fx]
    bgs = rec.get('bgs') or [rec.get('bg')] * len(sources)
    t0 = time.time()

    async def one(path, bg_id):
        keep = effect.get('keep_background') and bg_id in config.BG_BY_ID
        base = await asyncio.to_thread(count_faces, cv2.imread(str(path)))
        for attempt in range(2):
            if attempt and not _spend(1):  # 다시 그리는 것도 하루 상한 안에서만
                break
            async with _sem:
                _step(sid, fx, 'draw')
                img = await asyncio.to_thread(_edit, path, effect['prompt'])
            found = await asyncio.to_thread(count_faces, _bgr(img))
            if base and found > base:
                # '가족사진'이라고 했더니 혼자 찍은 아이 옆에 어른 둘을 지어내 넣은 일이 있었다
                print(f'[효과] 얼굴이 {base}명에서 {found}명으로 늘어 버림 ({sid} {fx}, {attempt + 1}번째)',
                      flush=True)
                continue
            _step(sid, fx, drawn=1)
            if keep:
                img = await asyncio.to_thread(_keep_background, img, bg_id)
            _step(sid, fx, done=1)
            return img
        raise RuntimeError('GPT가 없던 사람을 그려 넣었다')

    try:
        imgs = await asyncio.gather(*[one(p, b) for p, b in zip(sources, bgs)])
        _step(sid, fx, 'finish')
        await asyncio.to_thread(_save, sid, fx, imgs, rec.get('msg', ''), rec.get('font'))
        took = time.time() - t0
        with _lock:
            _jobs.pop((sid, fx), None)
            _progress.pop((sid, fx), None)
            _avg[fx] = took if fx not in _avg else _avg[fx] * 0.7 + took * 0.3
        print(f'[효과] 완료 {sid} {fx} ({len(imgs)}장, {took:.1f}초)', flush=True)
    except Exception as e:  # 실패해도 방문객은 원본을 그대로 받는다
        with _lock:
            _jobs[(sid, fx)] = 'failed'
            _progress.pop((sid, fx), None)
        print(f'[효과] 실패 {sid} {fx}: {e}', flush=True)


def _save(sid, fx, imgs, message, font=None):
    if len(imgs) == 1:
        imgs[0].save(storage.path(sid, f'fx_{fx}'), quality=93, subsampling=0)
        frame.render(imgs[0], message, storage.path(sid, f'fxf_{fx}'), font)
    else:
        frame.save_grid(imgs, storage.path(sid, f'fx_{fx}'))
        frame.render(imgs, message, storage.path(sid, f'fxf_{fx}'), font)


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


def _keep_background(img, bg_id):
    """GPT가 새로 그린 사진에서 사람만 오려 원래 배경 위에 다시 얹는다.
    말로 '배경은 그대로'라고 해도 GPT는 꽃밭을 데크로 바꾸는 식으로 배경을 조금씩 다시 그린다.
    이렇게 하면 배경은 원본과 똑같고, 사람의 포즈·빛만 GPT가 정한 대로 남는다."""
    W, H = config.SHOT_W, config.SHOT_H
    photo = _bgr(img)
    a = compose.matte(photo)
    if float((a > 0.5).mean()) < 0.01:  # 사람을 못 찾으면 GPT 결과를 그대로 쓴다
        return img
    bg = compose.cover(compose.read_image(config.BG_DIR / f'bg_{bg_id}.png'), W, H).astype(np.float32) / 255
    # 본 합성과 같이: 경계에 묻은 GPT 배경색·역광 테두리를 걷어 낸 뒤 원래 배경 빛으로 감싼다
    F = compose.decontaminate(compose.estimate_foreground(photo.astype(np.float32) / 255, a), a)
    F = compose.light_wrap(F, a, bg)
    out = compose._finish(F, a, bg, compose.foreground_mask(bg_id, W, H))
    return Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))


def _multipart(fields, files):
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (name, data, ctype) in files.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                     f'Content-Type: {ctype}\r\n\r\n'.encode() + data + b'\r\n')
    parts.append(f'--{b}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={b}'


def _edit(path, prompt):
    """OpenAI 이미지 편집(/v1/images/edits). 사진은 1024x768(4:3)로 줄여 보내고, 받은 그림은 사진 크기로 키운다."""
    w, h = (int(x) for x in config.GPT_SIZE.split('x'))
    src = cv2.imread(str(path))
    src = cv2.resize(src, (w, h), interpolation=cv2.INTER_AREA)
    jpg = cv2.imencode('.jpg', src, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
    body, ctype = _multipart(
        {'model': config.GPT_MODEL, 'prompt': prompt, 'size': config.GPT_SIZE,
         'quality': config.GPT_QUALITY, 'n': '1', 'output_format': 'jpeg'},
        {'image': ('photo.jpg', jpg, 'image/jpeg')})
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
    img = cv2.resize(img, (config.SHOT_W, config.SHOT_H), interpolation=cv2.INTER_CUBIC)
    return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
