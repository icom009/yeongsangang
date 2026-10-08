"""생성형 AI 빛 보정(선택 기능).

빠른 합성(필터·원본)을 먼저 내보낸 뒤, 뒤에서 'AI 버전'을 한 장 더 만든다.
촬영·QR 흐름은 이것을 절대 기다리지 않는다. 실패하거나 느리면 그냥 없는 셈 친다
(인터넷 없는 현장 노트북=플랜 B에서는 보통 꺼진 채로 돈다).

엔진은 환경에 맞춰 자동으로 고른다 (YS_AI=auto):
  로컬 IC-Light 서비스(GPU 컨테이너)가 응답하면 local → 외부 API 키가 있으면 api → 둘 다 없으면 끔.

AI 결과는 '빛'으로만 쓴다. 원본 합성본과 AI 결과의 밝기·색 비율만 뽑아 인물에 곱하므로
얼굴 생김새는 바뀌지 않는다. 배경은 인물 주변에서 어두워지는 쪽(그림자)만 받아
풍경의 선명함과 색은 그대로 둔다.
"""
import asyncio
import base64
import json
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict

import cv2
import numpy as np

from . import compose, config, frame, storage

_engine = None          # None | 'local' | 'api'
_queue = None           # asyncio.Queue
_gate = None            # 촬영 합성 자리(main의 세마포어). 손님이 기다리는 쪽을 먼저 보낸다
_running = None         # 지금 만들고 있는 사진 id
_last_activity = 0.0    # 마지막으로 촬영이 들어온 시각
_states = OrderedDict()  # sid -> {'state': pending|ready|failed, 'message': 한마디}
_MAX_STATES = 256
_lock = threading.Lock()

NEGATIVE = 'lowres, bad anatomy, bad hands, cropped, worst quality, text, watermark'
API_PROMPT = (
    'Relight the person in this photo so that the lighting direction, shadows, '
    'color temperature and ambience match the background scene ({scene}). '
    'Keep the face, identity, pose, clothing and the background exactly the same. '
    'Photorealistic, natural light, no style change.'
)


def enabled():
    return _engine is not None


def external():
    """얼굴 사진이 바깥 서버로 나가는 엔진인지 (부스에 안내 문구를 띄운다)."""
    return _engine == 'api'


def info():
    return {'on': enabled(), 'external': external()}


def queue_info():
    """관리 화면에 보여 줄 대기줄 상태."""
    return {'engine': _engine or 'off', 'waiting': _queue.qsize() if _queue else 0,
            'running': _running is not None, 'size': config.AI_QUEUE}


# ---------- 엔진 고르기 ----------

def _probe_local():
    try:
        with urllib.request.urlopen(f'{config.AI_URL}/health', timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def detect():
    """환경을 보고 엔진을 고른다. 로컬 AI 컨테이너는 모델을 올리느라 늦게 뜨므로 가끔 다시 본다."""
    global _engine
    mode = config.AI_MODE if config.AI_MODE in ('auto', 'local', 'api', 'off') else 'auto'
    found = None
    if mode != 'off':
        if mode in ('auto', 'local') and _probe_local():
            found = 'local'
        elif mode in ('auto', 'api') and config.AI_KEY:
            found = 'api'
    if found != _engine:
        _engine = found
        print(f'[AI] 빛 보정 엔진: {found or "끔"}', flush=True)
    return found


# ---------- 대기줄 ----------

async def start(gate=None):
    """작업 일꾼과 엔진 감시를 띄운다. 돌려준 작업들은 서버가 내려갈 때 취소한다.
    gate는 촬영 합성 자리를 재는 세마포어. AI는 그 자리가 빌 때까지 기다린다."""
    global _queue, _gate
    _gate = gate
    if config.AI_MODE == 'off':
        print('[AI] 빛 보정 끔 (YS_AI=off)', flush=True)
        return []
    _queue = asyncio.Queue(maxsize=config.AI_QUEUE)
    return [asyncio.create_task(_worker()), asyncio.create_task(_watch())]


async def _watch():
    while True:
        await asyncio.to_thread(detect)
        await asyncio.sleep(30 if _engine is None else 600)


def note_activity():
    """촬영이 들어올 때마다 부른다. 로컬 AI는 부스가 잠깐 한가해진 뒤에만 GPU를 쓴다."""
    global _last_activity
    _last_activity = time.time()


async def _wait_quiet():
    """마지막 촬영 뒤 AI_IDLE초가 지날 때까지 기다린다. 네 컷을 찍는 동안(3초 간격)에는
    AI가 끼어들지 않고, 방문객이 한마디를 쓰는 동안 같은 빈틈에 돈다."""
    while time.time() - _last_activity < config.AI_IDLE:
        await asyncio.sleep(0.5)


async def _take_gpu():
    """조용해질 때까지 기다렸다가 GPU를 잡는다. 잡는 사이 촬영이 들어왔으면 다시 양보한다."""
    while True:
        await _wait_quiet()
        await _hold_gpu()
        if time.time() - _last_activity >= config.AI_IDLE:
            return
        _free_gpu()


async def _hold_gpu():
    """AI가 GPU를 쓰는 동안에는 촬영 합성이 끼어들지 못하게 촬영 자리를 전부 잡는다.

    같은 GPU를 두 프로세스가 번갈아 쓰면(부스의 onnxruntime + IC-Light) 서로 어마어마하게
    느려진다. 실측: AI가 도는 동안 촬영 합성이 0.65초 -> 17초. AI 사진을 작게 돌려도 마찬가지였다.
    그래서 번갈아 쓰지 않고 아예 겹치지 않게 한다. 촬영이 기다리는 시간은 AI 한 장(1~2초)까지다."""
    if _gate is None:
        return
    for _ in range(config.COMPOSE_SLOTS):
        await _gate.acquire()


def _free_gpu():
    if _gate is None:
        return
    for _ in range(config.COMPOSE_SLOTS):
        _gate.release()


async def _worker():
    global _running
    while True:
        job = await _queue.get()
        try:
            waited = time.time() - job['t']
            if waited > config.AI_TTL:  # 방문객이 이미 사진을 받아 갔다
                _mark(job['sid'], 'failed')
                print(f'[AI] {waited:.0f}초 묵어 건너뜀 {job["sid"]}', flush=True)
                continue
            prep = await asyncio.to_thread(_prepare, job)  # CPU: 크기 줄이기·인코딩
            # 같은 GPU를 쓰는 로컬 엔진만 비켜서 기다린다. 외부 API는 GPU와 무관하므로 바로 보낸다
            local = _engine == 'local'
            if local:
                await _take_gpu()
            _running = job['sid']
            t0 = time.time()
            try:
                relit = await asyncio.to_thread(_relight, prep)  # GPU는 이 순간에만 잡는다
            finally:
                if local:
                    _free_gpu()
            await asyncio.to_thread(_finish, job, prep, relit, time.time() - t0)  # CPU: 빛 옮기기·저장
        except Exception as e:  # 어떤 실패도 촬영 흐름에 영향이 없어야 한다
            _mark(job['sid'], 'failed')
            print(f'[AI] 실패 {job["sid"]}: {e}', flush=True)
        finally:
            _running = None
            _queue.task_done()


def submit(sid, plain, alpha, bg_id):
    """합성이 끝난 사진을 AI 대기줄에 올린다. 자리가 없으면 건너뛴다 (절대 기다리지 않는다)."""
    if _queue is None or _engine is None or alpha is None:
        return False
    try:
        _queue.put_nowait({'sid': sid, 'plain': plain, 'alpha': alpha, 'bg': bg_id, 't': time.time()})
    except asyncio.QueueFull:
        print(f'[AI] 대기줄이 꽉 차 건너뜀 {sid}', flush=True)
        return False
    _mark(sid, 'pending')
    return True


def _mark(sid, state):
    with _lock:
        rec = _states.get(sid) or {}
        rec['state'] = state
        _states[sid] = rec
        _states.move_to_end(sid)
        while len(_states) > _MAX_STATES:
            _states.popitem(last=False)
        return dict(rec)


def status(sid):
    """휴대폰 받기 화면이 물어보는 상태. off면 화면에 아무것도 띄우지 않는다."""
    # 다 만든 빛 보정은 파일로 남는다. 서버를 다시 켜 메모리가 비어도(배포할 때마다) 그대로 보여 준다
    if storage.path(sid, 'aifinal').exists():
        return {'state': 'ready', 'photo': f'/media/{sid}/ai.jpg', 'final': f'/media/{sid}/aifinal.jpg'}
    if _engine is None:
        return {'state': 'off'}
    with _lock:
        rec = dict(_states.get(sid) or {})
    state = rec.get('state', 'off')
    if state != 'ready':
        return {'state': state}
    out = {'state': 'ready', 'photo': f'/media/{sid}/ai.jpg'}
    if storage.path(sid, 'aifinal').exists():
        out['final'] = f'/media/{sid}/aifinal.jpg'
    elif rec.get('cuts'):
        return {'state': 'pending'}  # 4컷은 네 장을 다 합치기 전까지는 보여 주지 않는다
    return out


def note_final(sid, message, cuts=(), font=''):
    """방문객이 한마디를 적어 완성했을 때. AI 버전도 같은 프레임·같은 한마디로 만들어 둔다.
    4컷이면 cuts에 나머지 세 장의 id가 온다. 네 장이 다 돼야 AI 4컷을 합친다."""
    if _engine is None:
        return
    with _lock:
        rec = _states.get(sid)
        if rec is None:
            return
        rec['message'] = message
        rec['font'] = font
        rec['cuts'] = list(cuts)
        for c in cuts:  # 어느 컷이 끝나든 대표 사진을 찾아갈 수 있도록
            member = _states.get(c)
            if member is not None:
                member['lead'] = sid
    _finish_ready(sid)


def _finish_ready(sid):
    """한 장이 끝날 때마다 본다. 1컷이면 바로, 4컷이면 네 장이 다 됐을 때 프레임을 만든다."""
    with _lock:
        rec = _states.get(sid) or {}
        lead = rec.get('lead', sid)
        lrec = _states.get(lead) or {}
        message = lrec.get('message')
        font = lrec.get('font')
        members = [lead] + list(lrec.get('cuts') or [])
        if message is None or lrec.get('framed'):
            return  # 아직 '완성하기' 전이거나 이미 만들었다
        states = [(_states.get(m) or {}).get('state') for m in members]
        if 'failed' in states:
            lrec['state'] = 'failed'  # 한 컷이라도 실패하면 AI 4컷은 포기한다
            return
        if not all(s == 'ready' for s in states):
            return
        lrec['framed'] = True
    _render_group(lead, members, message, font)


def _render_group(lead, members, message, font=None):
    paths = [storage.path(m, 'ai') for m in members]
    if not all(p.exists() for p in paths):
        return
    try:
        frame.render(paths if len(paths) > 1 else paths[0], message, storage.path(lead, 'aifinal'), font)
        if len(paths) > 1:
            # 프레임 없는 AI 4컷을 대표 사진 자리에 합쳐 두고, 낱장은 지운다
            frame.save_grid(paths, storage.path(lead, 'ai'))
            for p in paths[1:]:
                p.unlink(missing_ok=True)
    except Exception as e:
        print(f'[AI] 프레임 실패 {lead}: {e}', flush=True)


# ---------- 빛만 옮기기 ----------

def apply_light(base, relit, base_small, alpha, bg_amount=0.7):
    """AI 결과에서 '빛'만 가져온다: 원본 합성본 대비 밝기·색 비율을 뽑아 인물에 곱한다.
    얼굴 모양과 디테일은 원본 그대로 남는다. 배경은 인물 주변에서 어두워지는 쪽(그림자)만 받는다."""
    h, w = base.shape[:2]
    eps = 6.0
    ratio = (relit.astype(np.float32) + eps) / (base_small.astype(np.float32) + eps)
    ratio = cv2.GaussianBlur(ratio, (0, 0), 1.5)
    ratio = cv2.resize(ratio, (w, h), interpolation=cv2.INTER_LINEAR)
    a = alpha[..., None]

    person = np.clip(ratio, 0.45, 2.2)
    # 배경: AI가 배경 전체를 조금 밝게·어둡게 다시 그린 것은 무시하고(중앙값으로 나눈다)
    # 인물 주변에 새로 생긴 그림자만 받는다
    far = alpha < 0.1
    med = np.median(ratio[far].reshape(-1, 3), axis=0) if far.sum() > 1000 else np.ones(3, np.float32)
    near = cv2.GaussianBlur(alpha, (0, 0), 0.05 * max(h, w))
    near = np.clip(near / (near.max() + 1e-6) * 2.5, 0, 1)[..., None]
    shadow = 1 + (np.clip(ratio / np.maximum(med, 1e-3), 0.6, 1.0) - 1) * bg_amount * near

    out = base.astype(np.float32) * (person * a + shadow * (1 - a))
    return np.clip(out, 0, 255)


def _fit(img, w, h):
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA if img.shape[1] > w else cv2.INTER_LINEAR)


def _ai_size(w, h):
    """SD는 8의 배수 크기를 쓴다."""
    s = min(1.0, config.AI_SIZE / max(w, h))
    return max(8, int(w * s) // 8 * 8), max(8, int(h * s) // 8 * 8)


def _prepare(job):
    """AI에 보낼 사진을 준비한다 (CPU만 쓴다)."""
    bg_id = job['bg']
    plain, alpha8 = job['plain'], job['alpha']
    H, W = plain.shape[:2]
    look = config.BG_BY_ID[bg_id].get('look', {})
    w, h = _ai_size(W, H)
    small = _fit(plain, w, h)
    prep = {'small': small, 'w': w, 'h': h, 'look': look, 'engine': _engine,
            'scene': look.get('ai') or config.BG_BY_ID[bg_id]['place']}
    q = [cv2.IMWRITE_JPEG_QUALITY, 92]
    if _engine == 'local':
        # IC-Light(배경 맞춤 재조명): 인물(알파 포함)과 배경을 함께 넘긴다
        bg = compose.cover(compose.read_image(config.BG_DIR / f'bg_{bg_id}.png'), w, h)
        prep['fg'] = cv2.imencode('.png', np.dstack([small, _fit(alpha8, w, h)]))[1].tobytes()
        prep['bg'] = cv2.imencode('.jpg', bg, q)[1].tobytes()
    else:
        # 외부 엔진: 이미 합성된 사진을 넘겨 빛만 맞춰 달라고 한다
        prep['jpg'] = cv2.imencode('.jpg', small, q)[1].tobytes()
    return prep


def _relight(prep):
    """엔진을 부른다. 로컬이면 이 동안만 GPU를 쓴다."""
    if prep['engine'] == 'local':
        relit = _call_local(prep['fg'], prep['bg'], prep['scene'], prep['w'], prep['h'])
    else:
        relit = _call_api(prep['jpg'], prep['scene'])
    if relit is None:
        raise RuntimeError('AI 결과가 비었어요')
    return relit


def _finish(job, prep, relit, took=0.0):
    """AI 결과에서 빛만 옮겨 저장한다 (CPU만 쓴다)."""
    sid = job['sid']
    w, h, look = prep['w'], prep['h'], prep['look']
    if relit.shape[:2] != (h, w):
        relit = cv2.resize(relit, (w, h), interpolation=cv2.INTER_AREA)
    af = job['alpha'].astype(np.float32) / 255
    out = apply_light(job['plain'], relit, prep['small'], af) / 255
    # 인물만 뽀샤시 (배경은 그대로 둔다)
    lit = compose.beautify(out, af, config.BEAUTY, look.get('glow', 0.16), look.get('tint', (0, 0))[1])
    out = np.uint8(np.clip((out + (lit - out) * af[..., None]) * 255 + 0.5, 0, 255))
    cv2.imwrite(str(storage.path(sid, 'ai')), out, [cv2.IMWRITE_JPEG_QUALITY, 93])

    with _lock:
        rec = _states.get(sid) or {}
        rec['state'] = 'ready'
        _states[sid] = rec
    print(f'[AI] 완료 {sid} ({prep["engine"]}, 재조명 {took:.1f}초)', flush=True)
    _finish_ready(sid)  # 이미 완성 화면까지 간 사진이면 프레임 버전도 바로 만든다


def _run(job):
    """세 단계를 한 번에 (시험용)."""
    prep = _prepare(job)
    t0 = time.time()
    _finish(job, prep, _relight(prep), time.time() - t0)


# ---------- 엔진 ----------

def _post_json(url, payload, headers=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=config.AI_TIMEOUT) as r:
        return json.loads(r.read())


def _decode(b64):
    buf = np.frombuffer(base64.b64decode(b64), np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _call_local(fg_png, bg_jpg, scene, w, h):
    """services/ic-light 컨테이너에 재조명을 맡긴다."""
    res = _post_json(f'{config.AI_URL}/relight', {
        'fg': base64.b64encode(fg_png).decode(),
        'bg': base64.b64encode(bg_jpg).decode(),
        'prompt': scene,
        'negative': NEGATIVE,
        'width': w, 'height': h,
    })
    return _decode(res['image']) if res.get('image') else None


def _call_api(jpg, scene):
    """외부 이미지 편집 API(Gemini). 얼굴 사진이 바깥으로 나가므로 부스에 안내 문구를 둔다."""
    url = (f'https://generativelanguage.googleapis.com/v1beta/models/'
           f'{config.AI_API_MODEL}:generateContent')
    res = _post_json(url, {
        'contents': [{'parts': [
            {'text': API_PROMPT.format(scene=scene)},
            {'inline_data': {'mime_type': 'image/jpeg', 'data': base64.b64encode(jpg).decode()}},
        ]}],
    }, {'x-goog-api-key': config.AI_KEY})
    for part in res.get('candidates', [{}])[0].get('content', {}).get('parts', []):
        data = (part.get('inline_data') or part.get('inlineData') or {}).get('data')
        if data:
            return _decode(data)
    return None
