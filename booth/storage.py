"""촬영 결과 파일 관리. 파일 이름은 서버가 만든 ID만 쓰므로 경로 조작이 불가능하다."""
import re
import secrets
import time
from functools import lru_cache

import cv2

from . import config
from .compose import read_image

_ID = re.compile(r'^[A-Za-z0-9_-]{10,32}$')
KINDS = ('shot', 'final')


def new_id():
    return secrets.token_urlsafe(12)


def valid_id(sid):
    return bool(sid and _ID.match(sid))


def path(sid, kind):
    if not valid_id(sid) or kind not in KINDS:
        raise ValueError('bad id')
    return config.OUT_DIR / f'{sid}_{kind}.jpg'


def cleanup():
    """보관 시간이 지난 사진을 지운다."""
    limit = time.time() - config.KEEP_HOURS * 3600
    removed = 0
    for p in config.OUT_DIR.glob('*.jpg'):
        try:
            if p.stat().st_mtime < limit:
                p.unlink()
                removed += 1
        except FileNotFoundError:
            pass
    return removed


@lru_cache(maxsize=32)
def background_jpeg(bg_id, width):
    """원본 PNG(장당 수 MB) 대신 화면용 JPEG을 만들어 캐시한다."""
    img = read_image(config.BG_DIR / f'bg_{bg_id}.png')
    h = round(width * img.shape[0] / img.shape[1])
    img = cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)
    return cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 86])[1].tobytes()


@lru_cache(maxsize=1)
def frame_jpeg():
    img = read_image(config.FRAME)
    return cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()

