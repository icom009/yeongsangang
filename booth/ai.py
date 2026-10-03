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


async def _wait_for_idle(limit=30):
    """촬영 합성이 자리를 다 쓰고 있으면 AI는 잠깐 비켜 준다 (손님이 기다리는 쪽이 먼저).
    너무 오래 기다리지는 않는다. 계속 밀리면 TTL에 걸려 알아서 버려진다."""
    for _ in range(int(limit * 2)):
        if _gate is None or not _gate.locked():
            return
        await asyncio.sleep(0.5)


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
            await _wait_for_idle()
            _running = job['sid']
            await asyncio.to_thread(_run, job)
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
    return out


def note_final(sid, message):
    """방문객이 한마디를 적어 완성했을 때. AI 버전도 같은 프레임·같은 한마디로 만들어 둔다."""
    if _engine is None:
        return
    with _lock:
        rec = _states.get(sid)
        if rec is None:
            return
        rec['message'] = message
        ready = rec.get('state') == 'ready'
    if ready:
        _render_frame(sid, message)


def _render_frame(sid, message):
    shot = storage.path(sid, 'ai')
    if not shot.exists():
        return
    try:
        frame.render(shot, message, storage.path(sid, 'aifinal'))
    except Exception as e:
        print(f'[AI] 프레임 실패 {sid}: {e}', flush=True)


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


def _run(job):
    sid, bg_id = job['sid'], job['bg']
    plain, alpha8 = job['plain'], job['alpha']
    H, W = plain.shape[:2]
    look = config.BG_BY_ID[bg_id].get('look', {})
    scene = look.get('ai') or config.BG_BY_ID[bg_id]['place']

    w, h = _ai_size(W, H)
    small = _fit(plain, w, h)
    a_small = _fit(alpha8, w, h)
    bg = compose.cover(compose.read_image(config.BG_DIR / f'bg_{bg_id}.png'), w, h)

    t0 = time.time()
    if _engine == 'local':
        # IC-Light(배경 맞춤 재조명): 인물(알파 포함)과 배경을 함께 넘긴다
        fg = cv2.imencode('.png', np.dstack([small, a_small]))[1].tobytes()
        relit = _call_local(fg, cv2.imencode('.jpg', bg, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes(),
                            scene, w, h)
    else:
        # 외부 엔진: 이미 합성된 사진을 넘겨 빛만 맞춰 달라고 한다
        relit = _call_api(cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes(), scene)
    if relit is None:
        raise RuntimeError('AI 결과가 비었어요')
    if relit.shape[:2] != (h, w):
        relit = cv2.resize(relit, (w, h), interpolation=cv2.INTER_AREA)

    af = alpha8.astype(np.float32) / 255
    out = apply_light(plain, relit, small, af) / 255
    # 인물만 뽀샤시 (배경은 그대로 둔다)
    lit = compose.beautify(out, af, config.BEAUTY, look.get('glow', 0.16), look.get('tint', (0, 0))[1])
    out = np.uint8(np.clip((out + (lit - out) * af[..., None]) * 255 + 0.5, 0, 255))
    cv2.imwrite(str(storage.path(sid, 'ai')), out, [cv2.IMWRITE_JPEG_QUALITY, 93])

    with _lock:
        rec = _states.get(sid) or {}
        rec['state'] = 'ready'
        _states[sid] = rec
        message = rec.get('message')
    print(f'[AI] 완료 {sid} ({_engine}, {time.time() - t0:.1f}초)', flush=True)
    if message is not None:  # 이미 완성 화면까지 간 사진이면 프레임 버전도 바로 만든다
        _render_frame(sid, message)


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
