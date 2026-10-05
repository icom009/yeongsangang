import asyncio
import contextlib
import hashlib
import hmac
import html
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from booth import ai, compose, config, effects, frame, records, storage

# 실시간 미리보기 모듈(.mjs)·wasm을 브라우저가 받아들이도록 형식을 명시 (OS마다 기본값이 다르다)
mimetypes.add_type('text/javascript', '.mjs')
mimetypes.add_type('application/wasm', '.wasm')

MAX_UPLOAD = 12 * 1024 * 1024
JPEG = 'image/jpeg'
NO_STORE = {'Cache-Control': 'no-store'}
LONG_CACHE = {'Cache-Control': 'public, max-age=86400'}


def _ask_tunnel():
    try:
        with urllib.request.urlopen(f'{config.TUNNEL_METRICS}/quicktunnel', timeout=3) as r:
            host = json.loads(r.read()).get('hostname')
        return f'https://{host}' if host else None
    except Exception:
        return None


async def _watch_tunnel():
    """비상용 노트북 서버: 임시 터널 주소를 15초마다 확인해 QR에 넣는다 (요청 처리를 막지 않게 뒤에서)."""
    while True:
        url = await asyncio.to_thread(_ask_tunnel)
        if url and url != config.TUNNEL_URL:
            config.TUNNEL_URL = url
            print(f'[터널] 방문객 받기 주소: {url}', flush=True)
        await asyncio.sleep(15)


async def _cleanup_loop():
    while True:
        await asyncio.to_thread(storage.cleanup)
        await asyncio.sleep(3600)


@contextlib.asynccontextmanager
async def lifespan(_):
    # 모델은 첫 촬영 전에 미리 올려 둔다 (첫 손님이 기다리지 않도록)
    await asyncio.to_thread(compose.warmup)
    # AI 빛 보정은 있으면 쓰고 없으면 그냥 끈다 (촬영 흐름과 무관하게 뒤에서 돈다)
    tasks = [asyncio.create_task(_cleanup_loop()), *await ai.start(_compose_gate)]
    if config.TUNNEL_METRICS:
        tasks.append(asyncio.create_task(_watch_tunnel()))
    yield
    for task in tasks:
        task.cancel()


class _QuietPolls(logging.Filter):
    """휴대폰이 2초마다 묻는 AI 상태 확인과 관리 화면의 10초 새로 고침은 접근 기록에 남기지 않는다.
    축제 내내 남기면 로그가 금방 수백 MB가 된다. 촬영·완성·오류 같은 기록은 그대로 남는다."""
    def filter(self, record):
        msg = record.getMessage()
        return not (('GET /p/' in msg and '/ai HTTP' in msg) or 'GET /api/manage/status' in msg)


logging.getLogger('uvicorn.access').addFilter(_QuietPolls())

app = FastAPI(title='영산강 AI 포토부스', lifespan=lifespan, docs_url=None, redoc_url=None)
# 인터넷(터널·공개 주소)으로 들어온 요청은 방문객 받기 화면에 필요한 것만 연다.
# 촬영·합성 API는 부스 컴퓨터(localhost·같은 Wi-Fi)에서만 쓸 수 있다
PUBLIC_PATHS = ('/p/', '/media/', '/static/', '/font/')


def _from_internet(request: Request):
    if 'cf-connecting-ip' in request.headers:  # Cloudflare Tunnel을 거친 요청
        return True
    public = config.public_base_url()
    return bool(public) and request.url.hostname == urlsplit(public).hostname


BOOTH_COOKIE = 'ys_booth'


def _key_ok(value):
    return bool(config.BOOTH_KEY and value) and secrets.compare_digest(value, config.BOOTH_KEY)


@app.middleware('http')
async def guard_public(request: Request, call_next):
    if config.OPEN or not _from_internet(request) or request.url.path.startswith(PUBLIC_PATHS):
        return await call_next(request)
    # 인터넷으로 부스 화면을 쓰는 기기(플랜 A): https://주소/?key=부스키 로 한 번 열면 쿠키로 기억한다
    if _key_ok(request.cookies.get(BOOTH_COOKIE)):
        return await call_next(request)
    if _key_ok(request.query_params.get('key')):
        response = await call_next(request)
        response.set_cookie(BOOTH_COOKIE, config.BOOTH_KEY, max_age=60 * 60 * 24 * 60,
                            httponly=True, secure=True, samesite='lax')
        return response
    return Response(status_code=404)


app.mount('/static', StaticFiles(directory=config.WEB_DIR), name='static')
app.mount('/bgm', StaticFiles(directory=config.BGM_DIR), name='bgm')


def _lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except OSError:
        return None


def base_url(request: Request):
    """방문객 휴대폰이 열 수 있는 주소. localhost로 접속 중이면 내부 IP로 바꾼다."""
    fixed = config.public_base_url()
    if fixed:
        return fixed
    url = str(request.base_url).rstrip('/')
    host = request.url.hostname or ''
    if host in ('localhost', '127.0.0.1', '0.0.0.0', '::1'):
        ip = _lan_ip()
        if ip:
            url = url.replace(host, ip, 1)
    return url


def _need(sid, kind):
    try:
        p = storage.path(sid, kind)
    except ValueError:
        raise HTTPException(404, '사진을 찾을 수 없어요.')
    if not p.exists():
        raise HTTPException(404, '사진을 찾을 수 없어요.')
    return p


@app.get('/', response_class=HTMLResponse)
def index(request: Request):
    # 미리보기(카카오톡 등)는 썸네일 주소가 완전한 주소여야 해서, 이 서버의 바깥 주소를 넣어 준다
    page = (config.WEB_DIR / 'index.html').read_text(encoding='utf-8')
    return HTMLResponse(page.replace('{{BASE}}', base_url(request)), headers=NO_STORE)


@app.get('/api/config')
def get_config():
    return {
        'backgrounds': [{**b, 'fg': config.has_foreground(b['id'])} for b in config.BACKGROUNDS],
        'shot': {'width': config.SHOT_W, 'height': config.SHOT_H},
        'frame': {
            'size': [1024, 1536],
            'hole': config.FRAME_HOLE,
            'radius': config.FRAME_HOLE_RADIUS,
            'text': config.FRAME_TEXT_BOX,
            'color': config.TEXT_COLOR,
        },
        'defaultMessage': config.DEFAULT_MESSAGE,
        # 외부로 사진이 나가는 기능(외부 빛 보정 엔진이나 GPT 효과)이 켜져 있으면 처음 화면에 안내가 뜬다
        # 방문객 휴대폰이 사진을 받을 주소 (비상용 노트북 서버는 임시 터널 주소가 잡혀야 QR이 맞다)
        'public': config.public_base_url(),
        'ai': {**ai.info(), 'external': ai.external() or effects.enabled()},
        'effects': effects.catalog(),
    }


@app.get('/bg/{bg_id}.jpg')
def background(bg_id: int, w: int = 1600):
    if bg_id not in config.BG_IDS:
        raise HTTPException(404)
    w = 480 if w <= 480 else 800 if w <= 800 else 1600
    return Response(storage.background_jpeg(bg_id, w), media_type=JPEG, headers=LONG_CACHE)


@app.get('/bg/{bg_id}_fg.webp')
def background_foreground(bg_id: int):
    if bg_id not in config.BG_IDS or not config.has_foreground(bg_id):
        raise HTTPException(404)
    return Response(storage.foreground_webp(bg_id), media_type='image/webp', headers=LONG_CACHE)


@app.get('/frame.jpg')
def frame_image():
    return Response(storage.frame_jpeg(), media_type=JPEG, headers=LONG_CACHE)


@app.get('/font/message.ttf')
def message_font():
    return FileResponse(config.FONT, media_type='font/ttf', headers=LONG_CACHE)


_client_logs = []


@app.post('/api/log')
async def client_log(request: Request):
    # 공개 주소(YS_OPEN=1)라 누가 마구 보내도 로그가 넘치지 않게 1분에 60건까지만
    now = time.time()
    _client_logs[:] = [t for t in _client_logs if now - t < 60]
    if len(_client_logs) >= 60:
        return {'ok': False}
    _client_logs.append(now)
    body = (await request.body())[:2000].decode('utf-8', 'replace')
    print(f'[client {request.client.host if request.client else "?"}] {body}', flush=True)
    return {'ok': True}


# 합성은 CPU·메모리를 많이 쓰므로 동시에 COMPOSE_SLOTS장까지만, 나머지는 도착 순서대로 기다린다.
# (여러 부스가 한꺼번에 찍어도 메모리가 넘쳐 서버 전체가 멈추지 않도록)
_compose_gate = asyncio.Semaphore(config.COMPOSE_SLOTS)
_busy = 0  # 지금 합성 중이거나 차례를 기다리는 사진 수 (관리 화면에 보여 준다)


def _save_shot(sid, styled, plain):
    q = [cv2.IMWRITE_JPEG_QUALITY, 93]
    cv2.imwrite(str(storage.path(sid, 'shot')), styled, q)
    cv2.imwrite(str(storage.path(sid, 'plain')), plain, q)


# 합성을 기다리는 사진이 이보다 많으면 정중히 거절한다. 기다리는 사진마다 메모리를 들고 있어서
# (공개 주소라 누가 마구 보내도) 서버가 메모리로 무너지지 않게 한다
MAX_WAITING = config.COMPOSE_SLOTS * 8


@app.post('/api/shots')
async def create_shot(photo: UploadFile = File(...), bg: int = Form(...)):
    global _busy
    if bg not in config.BG_IDS:
        raise HTTPException(400, '배경을 다시 골라 주세요.')
    if _busy >= MAX_WAITING:
        raise HTTPException(503, '찍는 분들이 많아요. 잠시 뒤 다시 찍어 주세요.')
    ai.note_activity()  # 촬영 중에는 로컬 AI가 GPU를 쓰지 않는다
    t0 = time.perf_counter()
    data = await photo.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, '사진 용량이 너무 커요.')
    # 디코딩·저장은 이벤트 루프 밖에서 (안에서 하면 한 장마다 0.3초씩 다른 요청이 모두 멈췄다)
    img = await asyncio.to_thread(cv2.imdecode, np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, '사진을 읽지 못했어요. 다시 찍어 주세요.')
    t1 = time.perf_counter()

    _busy += 1
    try:
        async with _compose_gate:
            styled, plain, person, alpha = await asyncio.to_thread(compose.compose, img, bg)
    finally:
        _busy -= 1
    t2 = time.perf_counter()
    sid = storage.new_id()
    await asyncio.to_thread(_save_shot, sid, styled, plain)
    # 현장에서 느려질 때 어디가 느린지 바로 보이도록 (받기 = 업로드 읽기·디코딩)
    print(f'[촬영] {sid} 받기 {t1 - t0:.2f}초 · 합성 {t2 - t1:.2f}초 · 저장 '
          f'{time.perf_counter() - t2:.2f}초 · {len(data) // 1024}KB', flush=True)
    records.note_shot(sid, bg)  # 어떤 장소였는지 기억했다가 '완성하기' 때 이력에 적는다
    ai.submit(sid, plain, alpha, bg)  # AI 버전은 뒤에서 만든다 (여기서 기다리지 않는다)
    return {
        'id': sid,
        'shot': f'/media/{sid}/shot.jpg',
        'plain': f'/media/{sid}/plain.jpg',
        'look': config.BG_BY_ID[bg].get('look', {}).get('name', '자동 보정'),
        'person': person,  # False면 합성 없이 찍은 그대로 (흐름은 멈추지 않는다)
    }


class FinalBody(BaseModel):
    message: str = Field('', max_length=80)
    filter: bool = True
    cuts: list[str] = Field(default_factory=list)  # 인생네컷이면 나머지 세 장의 id


@app.post('/api/shots/{sid}/final')
async def finalize(sid: str, body: FinalBody, request: Request):
    """1컷이면 사진 한 장, 인생네컷이면 cuts에 온 세 장까지 같은 프레임에 담는다."""
    shot = _need(sid, 'shot')
    done = storage.path(sid, 'final')
    if not done.exists():  # 두 번 눌러도 다시 만들지 않는다
        cut_ids = body.cuts[:3]
        paths = [shot] + [_need(c, 'shot') for c in cut_ids]
        ids = [sid] + cut_ids
        if not body.filter:
            # 원본을 고르면 그 사진을 shot 자리에 둔다 (받기 화면의 '프레임 없는 사진'도 같아지도록)
            for i, p in zip(ids, paths):
                plain = storage.path(i, 'plain')
                if plain.exists():  # 재시도로 이미 정리된 뒤면 그대로 둔다
                    await asyncio.to_thread(shutil.copyfile, plain, p)
        await asyncio.to_thread(frame.render, paths if len(paths) > 1 else shot, body.message, done)
        if len(paths) > 1:
            # 휴대폰에서 AI 효과를 입힐 때 컷마다 따로 쓰도록 낱장을 대표 id 아래에 남긴다
            for i, p in enumerate(paths):
                await asyncio.to_thread(shutil.copyfile, p, storage.path(sid, f'c{i}'))
            # 프레임 없는 4컷도 대표 사진 자리에 합쳐 둔다
            await asyncio.to_thread(frame.save_grid, paths, shot)
        # 고르지 않은 쪽 사진과 합쳐진 낱장은 더 쓸 일이 없으니 바로 지운다 (용량·개인정보)
        for i in ids:
            storage.path(i, 'plain').unlink(missing_ok=True)
        for i in ids[1:]:
            storage.path(i, 'shot').unlink(missing_ok=True)
        ai.note_final(sid, body.message, cut_ids)  # AI 버전도 같은 한마디로 프레임에 담는다
        records.add(sid, body.message, body.filter, ids)  # 관리 화면 이력 (AI 효과도 이걸 보고 만든다)
    return {
        'id': sid,
        'final': f'/media/{sid}/final.jpg',
        'qr': f'/api/shots/{sid}/qr.png',
        'page': f'{base_url(request)}/p/{sid}',
    }


@app.get('/media/{sid}/{kind}.jpg')
def media(sid: str, kind: str, download: int = 0):
    p = _need(sid, kind)
    headers = dict(LONG_CACHE)
    if download:
        name = {'final': 'yeongsangang_frame.jpg', 'aifinal': 'yeongsangang_ai_frame.jpg',
                'ai': 'yeongsangang_ai.jpg'}.get(kind)
        if name is None:  # AI 효과 사진: fxf_webtoon -> yeongsangang_webtoon_frame.jpg
            fx = kind.split('_', 1)[-1]
            name = (f'yeongsangang_{fx}_frame.jpg' if kind.startswith('fxf_') else
                    f'yeongsangang_{fx}.jpg' if kind.startswith('fx_') else 'yeongsangang_photo.jpg')
        headers['Content-Disposition'] = f'attachment; filename="{name}"'
    return FileResponse(p, media_type=JPEG, headers=headers)


@app.get('/api/shots/{sid}/qr.png')
def qr(sid: str, request: Request):
    _need(sid, 'final')
    return Response(frame.qr_png(f'{base_url(request)}/p/{sid}'), media_type='image/png')


# ---------- 관리 화면 (/manage) ----------
# 사진 이력을 보고, 링크·QR을 다시 꺼내고, 지우는 곳.
# 공개 주소로도 열리므로(YS_OPEN=1) 비밀번호가 유일한 자물쇠다. 현장에서 꼭 바꿔 쓸 것
MANAGE_COOKIE = 'ys_manage'
# 비밀번호를 5번 틀린 기기는 1시간 동안 로그인할 수 없다.
# Docker Desktop을 거치면 모든 접속이 같은 주소(172.18.0.1)로 보여서 주소로 막으면 관리자까지 막힌다.
# 그래서 관리 화면을 처음 열 때 기기마다 표식(쿠키)을 주고 그 기기만 막는다.
# 관리 화면을 거치지 않고 로그인 주소만 두드리는 로봇(표식 없음)은 한 묶음으로 세어 막는다
MANAGE_DEVICE = 'ys_mdev'
MANAGE_MAX_FAILS = 5
MANAGE_LOCK_SEC = 3600
NO_DEVICE = '-'
_manage_fails = {}   # 기기 표식 -> 틀린 시각들
_manage_locked = {}  # 기기 표식 -> 풀리는 시각


def _manage_token():
    """비밀번호를 쿠키에 그대로 담지 않으려고 한 번 섞는다."""
    return hmac.new(config.MANAGE_KEY.encode(), b'ys-manage', hashlib.sha256).hexdigest()


def _need_manage(request: Request):
    if not secrets.compare_digest(request.cookies.get(MANAGE_COOKIE, ''), _manage_token()):
        raise HTTPException(401, '관리 비밀번호를 입력해 주세요.')


class LoginBody(BaseModel):
    password: str = Field('', max_length=200)


def _device(request: Request):
    d = request.cookies.get(MANAGE_DEVICE, '')
    return d if re.fullmatch(r'[A-Za-z0-9_-]{16,64}', d) else NO_DEVICE


@app.get('/manage', response_class=HTMLResponse)
def manage_page(request: Request):
    res = FileResponse(config.WEB_DIR / 'manage.html', headers=NO_STORE)
    if _device(request) == NO_DEVICE:  # 이 기기의 표식 (비밀번호를 틀린 기기만 막으려고)
        res.set_cookie(MANAGE_DEVICE, secrets.token_urlsafe(18), max_age=60 * 60 * 24 * 365,
                       httponly=True, secure=request.url.scheme == 'https', samesite='lax')
    return res


@app.post('/api/manage/login')
async def manage_login(body: LoginBody, request: Request):
    dev = _device(request)
    now = time.time()
    if len(_manage_fails) + len(_manage_locked) > 2000:  # 오래된 기록은 버린다
        for k in [k for k, v in _manage_locked.items() if v <= now]:
            _manage_locked.pop(k, None)
        for k in [k for k, v in _manage_fails.items() if not v or now - v[-1] > MANAGE_LOCK_SEC]:
            _manage_fails.pop(k, None)
    until = _manage_locked.get(dev, 0)
    if until > now:
        mins = max(1, round((until - now) / 60))
        raise HTTPException(429, f'비밀번호를 {MANAGE_MAX_FAILS}번 틀려 이 기기는 {mins}분 동안 들어올 수 없어요.')
    if not secrets.compare_digest(body.password, config.MANAGE_KEY):
        fails = [t for t in _manage_fails.get(dev, []) if now - t < MANAGE_LOCK_SEC] + [now]
        _manage_fails[dev] = fails
        await asyncio.sleep(1)  # 마구 넣어 보는 것을 늦춘다
        if len(fails) >= MANAGE_MAX_FAILS:
            _manage_locked[dev] = now + MANAGE_LOCK_SEC
            _manage_fails.pop(dev, None)
            print(f'[관리] 비밀번호 {MANAGE_MAX_FAILS}번 틀림, 이 기기 1시간 정지 '
                  f'({"표식 없음(로봇)" if dev == NO_DEVICE else dev[:6] + "…"})', flush=True)
            raise HTTPException(429, f'비밀번호를 {MANAGE_MAX_FAILS}번 틀려 이 기기는 1시간 동안 들어올 수 없어요.')
        raise HTTPException(401, f'비밀번호가 달라요. ({len(fails)}/{MANAGE_MAX_FAILS}번, '
                                 f'{MANAGE_MAX_FAILS}번 틀리면 1시간 동안 들어올 수 없어요)')
    _manage_fails.pop(dev, None)
    res = JSONResponse({'ok': True})
    res.set_cookie(MANAGE_COOKIE, _manage_token(), max_age=60 * 60 * 12, httponly=True,
                   secure=request.url.scheme == 'https', samesite='lax')
    return res


@app.post('/api/manage/logout')
def manage_logout():
    res = JSONResponse({'ok': True})
    res.delete_cookie(MANAGE_COOKIE)
    return res


@app.get('/api/manage/status')
def manage_status(request: Request):
    _need_manage(request)
    return {
        **records.stats(),
        'compose': {'slots': config.COMPOSE_SLOTS, 'busy': _busy},
        'ai': ai.queue_info(),
        'keepHours': config.KEEP_HOURS,
        'gpt': {**effects.usage(), 'on': effects.enabled()},
    }


@app.get('/api/manage/shots')
def manage_shots(request: Request, offset: int = 0, limit: int = 40, q: str = ''):
    _need_manage(request)
    rows = records.with_orphans()
    key = q.strip()
    if key:
        rows = [r for r in rows
                if key in r.get('msg', '') or key in r.get('place', '') or key == r.get('id')]
    offset = max(0, offset)
    items = []
    for r in rows[offset:offset + min(100, max(1, limit))]:
        sid = r['id']
        ok = storage.valid_id(sid)
        items.append({**r,
                      'alive': ok and storage.path(sid, 'final').exists(),
                      'ai': ok and storage.path(sid, 'aifinal').exists(),
                      'fx': [fx for fx in config.EFFECT_IDS if ok and storage.path(sid, f'fxf_{fx}').exists()],
                      'final': f'/media/{sid}/final.jpg',
                      'shot': f'/media/{sid}/shot.jpg',
                      'qr': f'/api/shots/{sid}/qr.png',
                      'page': f'{base_url(request)}/p/{sid}'})
    return {'total': len(rows), 'offset': offset, 'items': items}


@app.delete('/api/manage/shots/{sid}')
async def manage_delete(sid: str, request: Request):
    _need_manage(request)
    if not storage.valid_id(sid):
        raise HTTPException(404, '사진을 찾을 수 없어요.')
    files = await asyncio.to_thread(storage.remove, sid)
    await asyncio.to_thread(records.remove, sid)
    return {'ok': True, 'files': files}


@app.get('/p/{sid}/ai')
def photo_ai(sid: str):
    """AI 빛 보정과 AI 효과가 준비됐는지. 휴대폰 받기 화면이 물어본다 (인터넷에서도 열리도록 /p/ 아래에 둔다)."""
    if not storage.valid_id(sid):
        raise HTTPException(404)
    return JSONResponse({**ai.status(sid), 'effects': effects.status(sid), 'catalog': effects.catalog()},
                        headers=NO_STORE)


@app.post('/p/{sid}/fx/{fx}')
async def photo_effect(sid: str, fx: str):  # async: 뒤에서 돌 작업을 이 이벤트 루프에 올린다
    """휴대폰에서 AI 효과 버튼을 눌렀을 때. 누를 때만 만들고, 같은 사진·같은 효과는 한 번만 만든다."""
    if not storage.valid_id(sid) or not storage.path(sid, 'final').exists():
        raise HTTPException(404, '사진을 찾을 수 없어요.')
    return JSONResponse(effects.request(sid, fx), headers=NO_STORE)


_phone_logs = []


@app.post('/p/log')
async def phone_log(request: Request):
    """휴대폰 받기 화면의 스크립트 오류 (어떤 브라우저에서 멈췄는지 보려고). 공개 주소라 짧게,
    1분에 60건까지만 남긴다 (누가 마구 보내도 로그가 넘치지 않게)."""
    now = time.time()
    _phone_logs[:] = [t for t in _phone_logs if now - t < 60]
    if len(_phone_logs) >= 60:
        return {'ok': False}
    _phone_logs.append(now)
    body = (await request.body())[:1200].decode('utf-8', 'replace')
    print(f'[휴대폰] {body}', flush=True)
    return {'ok': True}


@app.get('/p/{sid}', response_class=HTMLResponse)
def photo_page(sid: str, request: Request):
    page = (config.WEB_DIR / 'photo.html').read_text(encoding='utf-8')
    page = page.replace('{{BASE}}', base_url(request))
    ok = storage.valid_id(sid) and storage.path(sid, 'final').exists()
    page = page.replace('{{SID}}', html.escape(sid if ok else ''))
    page = page.replace('{{STATE}}', 'ready' if ok else 'missing')
    # 스크립트가 안 도는 브라우저(일부 QR 앱·메신저 안 브라우저)에서도 사진은 보이도록 서버가 넣는다
    page = page.replace('{{HIDE_READY}}', '' if ok else 'hidden')
    page = page.replace('{{HIDE_MISSING}}', 'hidden' if ok else '')
    page = page.replace('{{FINAL}}', f'/media/{sid}/final.jpg' if ok else '')
    return HTMLResponse(page, status_code=200 if ok else 404, headers=NO_STORE)


def self_signed_cert():
    """같은 Wi-Fi의 휴대폰이 https로 접속할 수 있게 자체 서명 인증서를 만든다."""
    d = config.ROOT / 'certs'
    d.mkdir(exist_ok=True)
    cert, key = d / 'cert.pem', d / 'key.pem'
    ip = _lan_ip() or '127.0.0.1'
    stamp = d / 'ip.txt'
    if not (cert.exists() and key.exists() and stamp.exists() and stamp.read_text() == ip):
        subprocess.run([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '365',
            '-keyout', str(key), '-out', str(cert), '-subj', '/CN=yeongsangang-booth',
            '-addext', f'subjectAltName=IP:{ip},IP:127.0.0.1,DNS:localhost',
        ], check=True, capture_output=True)
        stamp.write_text(ip)
    return cert, key, ip


if __name__ == '__main__':
    import uvicorn
    opts = {}
    port = int(os.environ.get('PORT', 8080))
    if os.environ.get('YS_HTTPS') == '1':
        cert, key, ip = self_signed_cert()
        port = int(os.environ.get('PORT', 8443))
        opts = {'ssl_certfile': str(cert), 'ssl_keyfile': str(key)}
        print(f'\n  휴대폰에서 열기: https://{ip}:{port}\n')
    uvicorn.run(app, host='0.0.0.0', port=port,
                proxy_headers=True, forwarded_allow_ips='*', **opts)
