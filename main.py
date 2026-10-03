import asyncio
import contextlib
import html
import os
import socket
import subprocess

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from booth import compose, config, frame, storage

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
    await asyncio.to_thread(compose._get_session)
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()


app = FastAPI(title='영산강 AI 포토부스', lifespan=lifespan, docs_url=None, redoc_url=None)
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
def index():
    return FileResponse(config.WEB_DIR / 'index.html', headers=NO_STORE)


@app.get('/api/config')
def get_config():
    return {
        'backgrounds': config.BACKGROUNDS,
        'shot': {'width': config.SHOT_W, 'height': config.SHOT_H},
        'frame': {
            'size': [1024, 1536],
            'hole': config.FRAME_HOLE,
            'radius': config.FRAME_HOLE_RADIUS,
            'text': config.FRAME_TEXT_BOX,
            'color': config.TEXT_COLOR,
        },
        'defaultMessage': config.DEFAULT_MESSAGE,
    }


@app.get('/bg/{bg_id}.jpg')
def background(bg_id: int, w: int = 1600):
    if bg_id not in config.BG_IDS:
        raise HTTPException(404)
    w = 480 if w <= 480 else 800 if w <= 800 else 1600
    return Response(storage.background_jpeg(bg_id, w), media_type=JPEG, headers=LONG_CACHE)


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


@app.post('/api/shots')
async def create_shot(photo: UploadFile = File(...), bg: int = Form(...)):
    if bg not in config.BG_IDS:
        raise HTTPException(400, '배경을 다시 골라 주세요.')
    data = await photo.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, '사진 용량이 너무 커요.')
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, '사진을 읽지 못했어요. 다시 찍어 주세요.')

    try:
        out = await asyncio.to_thread(compose.compose, img, bg)
    except compose.NoPersonError:
        raise HTTPException(422, '사진에서 사람을 찾지 못했어요. 화면 안에 들어와서 다시 찍어 주세요.')
    sid = storage.new_id()
    cv2.imwrite(str(storage.path(sid, 'shot')), out, [cv2.IMWRITE_JPEG_QUALITY, 93])
    return {'id': sid, 'shot': f'/media/{sid}/shot.jpg'}


class FinalBody(BaseModel):
    message: str = Field('', max_length=80)


@app.post('/api/shots/{sid}/final')
async def finalize(sid: str, body: FinalBody, request: Request):
    shot = _need(sid, 'shot')
    await asyncio.to_thread(frame.render, shot, body.message, storage.path(sid, 'final'))
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
        name = 'yeongsangang_frame.jpg' if kind == 'final' else 'yeongsangang_photo.jpg'
        headers['Content-Disposition'] = f'attachment; filename="{name}"'
    return FileResponse(p, media_type=JPEG, headers=headers)


@app.get('/api/shots/{sid}/qr.png')
def qr(sid: str, request: Request):
    _need(sid, 'final')
    return Response(frame.qr_png(f'{base_url(request)}/p/{sid}'), media_type='image/png')


@app.get('/p/{sid}', response_class=HTMLResponse)
def photo_page(sid: str):
    page = (config.WEB_DIR / 'photo.html').read_text(encoding='utf-8')
    ok = storage.valid_id(sid) and storage.path(sid, 'final').exists()
    page = page.replace('{{SID}}', html.escape(sid if ok else ''))
    page = page.replace('{{STATE}}', 'ready' if ok else 'missing')
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
