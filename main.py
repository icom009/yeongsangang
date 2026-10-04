import asyncio
import contextlib
import hashlib
import hmac
import html
import mimetypes
import os
import secrets
import shutil
import socket
import subprocess
import time
from urllib.parse import urlsplit

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from booth import ai, compose, config, effects, frame, mail, records, storage

# 실시간 미리보기 모듈(.mjs)·wasm을 브라우저가 받아들이도록 형식을 명시 (OS마다 기본값이 다르다)
mimetypes.add_type('text/javascript', '.mjs')
mimetypes.add_type('application/wasm', '.wasm')

MAX_UPLOAD = 12 * 1024 * 1024
JPEG = 'image/jpeg'
NO_STORE = {'Cache-Control': 'no-store'}
LONG_CACHE = {'Cache-Control': 'public, max-age=86400'}


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
    yield
    for task in tasks:
        task.cancel()


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


@app.post('/api/log')
async def client_log(request: Request):
    body = (await request.body())[:2000].decode('utf-8', 'replace')
    print(f'[client {request.client.host if request.client else "?"}] {body}', flush=True)
    return {'ok': True}


# 합성은 CPU·메모리를 많이 쓰므로 동시에 COMPOSE_SLOTS장까지만, 나머지는 도착 순서대로 기다린다.
# (여러 부스가 한꺼번에 찍어도 메모리가 넘쳐 서버 전체가 멈추지 않도록)
_compose_gate = asyncio.Semaphore(config.COMPOSE_SLOTS)
_busy = 0  # 지금 합성 중이거나 차례를 기다리는 사진 수 (관리 화면에 보여 준다)


@app.post('/api/shots')
async def create_shot(photo: UploadFile = File(...), bg: int = Form(...)):
    if bg not in config.BG_IDS:
        raise HTTPException(400, '배경을 다시 골라 주세요.')
    ai.note_activity()  # 촬영 중에는 로컬 AI가 GPU를 쓰지 않는다
    t0 = time.perf_counter()
    data = await photo.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, '사진 용량이 너무 커요.')
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, '사진을 읽지 못했어요. 다시 찍어 주세요.')
    t1 = time.perf_counter()

    global _busy
    _busy += 1
    try:
        async with _compose_gate:
            styled, plain, person, alpha = await asyncio.to_thread(compose.compose, img, bg)
    finally:
        _busy -= 1
    t2 = time.perf_counter()
    sid = storage.new_id()
    q = [cv2.IMWRITE_JPEG_QUALITY, 93]
    cv2.imwrite(str(storage.path(sid, 'shot')), styled, q)
    cv2.imwrite(str(storage.path(sid, 'plain')), plain, q)
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
# 사진 이력을 보고, 링크·QR을 다시 꺼내고, 메일로 다시 보내고, 지우는 곳.
# 공개 주소로도 열리므로(YS_OPEN=1) 비밀번호가 유일한 자물쇠다. 현장에서 꼭 바꿔 쓸 것
MANAGE_COOKIE = 'ys_manage'
_manage_tries = {}


def _manage_token():
    """비밀번호를 쿠키에 그대로 담지 않으려고 한 번 섞는다."""
    return hmac.new(config.MANAGE_KEY.encode(), b'ys-manage', hashlib.sha256).hexdigest()


def _need_manage(request: Request):
    if not secrets.compare_digest(request.cookies.get(MANAGE_COOKIE, ''), _manage_token()):
        raise HTTPException(401, '관리 비밀번호를 입력해 주세요.')


class LoginBody(BaseModel):
    password: str = Field('', max_length=200)


class MailBody(BaseModel):
    to: str = Field('', max_length=200)
    attach: bool = True


@app.get('/manage', response_class=HTMLResponse)
def manage_page():
    return FileResponse(config.WEB_DIR / 'manage.html', headers=NO_STORE)


@app.post('/api/manage/login')
async def manage_login(body: LoginBody, request: Request):
    ip = request.client.host if request.client else '?'
    now = time.time()
    hits = [t for t in _manage_tries.get(ip, []) if now - t < 300]
    if len(hits) >= 10:  # 비밀번호 무차별 대입 막기
        raise HTTPException(429, '시도가 너무 많아요. 5분 뒤에 다시 해 주세요.')
    if not secrets.compare_digest(body.password, config.MANAGE_KEY):
        _manage_tries[ip] = hits + [now]
        await asyncio.sleep(0.5)
        raise HTTPException(401, '비밀번호가 달라요.')
    _manage_tries.pop(ip, None)
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
        'mail': config.mail_ready(),
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


@app.post('/api/manage/shots/{sid}/mail')
async def manage_mail(sid: str, body: MailBody, request: Request):
    """사진을 메일로 다시 보낸다. 설정이 없으면 관리 화면에서 단추가 꺼져 있다."""
    _need_manage(request)
    final = _need(sid, 'final')
    to = body.to.strip()
    if '@' not in to or len(to) < 5:
        raise HTTPException(400, '메일 주소를 다시 확인해 주세요.')
    if not config.mail_ready():
        raise HTTPException(400, '메일 설정(YS_SMTP_*)이 없어요. 링크 복사나 QR을 쓰세요.')
    data = await asyncio.to_thread(final.read_bytes) if body.attach else None
    try:
        await asyncio.to_thread(mail.send, to, f'{base_url(request)}/p/{sid}', data)
    except Exception as e:
        print(f'[메일] 실패 {sid} -> {to}: {e}', flush=True)
        raise HTTPException(502, '메일을 보내지 못했어요. 설정을 확인해 주세요.')
    return {'ok': True}


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


@app.post('/p/log')
async def phone_log(request: Request):
    """휴대폰 받기 화면의 스크립트 오류 (어떤 브라우저에서 멈췄는지 보려고). 공개 주소라 짧게만 남긴다."""
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
