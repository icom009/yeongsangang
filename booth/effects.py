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
        if st:
            out[fx] = {'state': st}
    return out


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
    cur = status(sid).get(fx)
    if cur and cur['state'] in ('ready', 'pending'):
        return cur
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
        async with _sem:
            img = await asyncio.to_thread(_edit, path, effect['prompt'])
        if effect.get('keep_background') and bg_id in config.BG_BY_ID:
            img = await asyncio.to_thread(_keep_background, img, bg_id)
        return img

    try:
        imgs = await asyncio.gather(*[one(p, b) for p, b in zip(sources, bgs)])
        await asyncio.to_thread(_save, sid, fx, imgs, rec.get('msg', ''))
        with _lock:
            _jobs.pop((sid, fx), None)
        print(f'[효과] 완료 {sid} {fx} ({len(imgs)}장, {time.time() - t0:.1f}초)', flush=True)
    except Exception as e:  # 실패해도 방문객은 원본을 그대로 받는다
        with _lock:
            _jobs[(sid, fx)] = 'failed'
        print(f'[효과] 실패 {sid} {fx}: {e}', flush=True)


def _save(sid, fx, imgs, message):
    if len(imgs) == 1:
        imgs[0].save(storage.path(sid, f'fx_{fx}'), quality=93, subsampling=0)
        frame.render(imgs[0], message, storage.path(sid, f'fxf_{fx}'))
    else:
        frame.save_grid(imgs, storage.path(sid, f'fx_{fx}'))
        frame.render(imgs, message, storage.path(sid, f'fxf_{fx}'))


def _keep_background(img, bg_id):
    """GPT가 새로 그린 사진에서 사람만 오려 원래 배경 위에 다시 얹는다.
    말로 '배경은 그대로'라고 해도 GPT는 꽃밭을 데크로 바꾸는 식으로 배경을 조금씩 다시 그린다.
    이렇게 하면 배경은 원본과 똑같고, 사람의 포즈·빛만 GPT가 정한 대로 남는다."""
    W, H = config.SHOT_W, config.SHOT_H
    photo = cv2.cvtColor(np.array(img.convert('RGB')), cv2.COLOR_RGB2BGR)
    a = compose.matte(photo)
    if float((a > 0.5).mean()) < 0.01:  # 사람을 못 찾으면 GPT 결과를 그대로 쓴다
        return img
    bg = compose.cover(compose.read_image(config.BG_DIR / f'bg_{bg_id}.png'), W, H).astype(np.float32) / 255
    F = compose.estimate_foreground(photo.astype(np.float32) / 255, a)
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
