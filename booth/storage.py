"""촬영 결과 파일 관리. 파일 이름은 서버가 만든 ID만 쓰므로 경로 조작이 불가능하다."""
import re
import secrets
import time
from functools import lru_cache

import cv2
import numpy as np

from . import config, records
from .compose import foreground_mask, read_image

_ID = re.compile(r'^[A-Za-z0-9_-]{10,32}$')
# shot: 장소 빛 필터 적용, plain: 필터 없는 합성, ai: AI 빛 보정 버전, aifinal: 그 프레임 사진,
# c0~c3: 네 컷의 낱장(AI 효과용), fxf_효과: AI 효과 완성 그림, fx_효과: 예전 효과의 프레임 없는 사진
KINDS = ('shot', 'plain', 'final', 'ai', 'aifinal', 'c0', 'c1', 'c2', 'c3') + tuple(
    f'{k}_{fx}' for fx in config.EFFECT_IDS for k in ('fx', 'fxf'))


def new_id():
    return secrets.token_urlsafe(12)


def valid_id(sid):
    return bool(sid and _ID.match(sid))


def path(sid, kind):
    if not valid_id(sid) or kind not in KINDS:
        raise ValueError('bad id')
    return config.OUT_DIR / f'{sid}_{kind}.jpg'


def taken_at(sid):
    """완성한 시각 (final 파일의 시각). 나중에 다시 그리는 AI·효과 사진에도 같은 날짜 도장을 찍으려고."""
    try:
        return path(sid, 'final').stat().st_mtime
    except OSError:
        return None


def remove(sid):
    """한 사진의 모든 파일을 지운다 (관리 화면에서 지울 때)."""
    removed = 0
    for kind in KINDS:
        p = path(sid, kind)
        if p.exists():
            p.unlink()
            removed += 1
    return removed


def cleanup():
    """보관 시간이 지난 사진과 그 기록을 지운다."""
    limit = time.time() - config.KEEP_HOURS * 3600
    removed = 0
    for p in config.OUT_DIR.glob('*.jpg'):
        try:
            if p.stat().st_mtime < limit:
                p.unlink()
                removed += 1
        except FileNotFoundError:
            pass
    records.prune(limit)
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



@lru_cache(maxsize=16)
def foreground_webp(bg_id, width=1600):
    """미리보기용 앞 가림 레이어: 배경 색 + 앞쪽 사물 마스크를 알파로 담은 투명 WebP."""
    img = read_image(config.BG_DIR / f'bg_{bg_id}.png')
    h = round(width * img.shape[0] / img.shape[1])
    img = cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)
    m = foreground_mask(bg_id, width, h)
    rgba = np.dstack([img, np.uint8(m * 255 + 0.5)])
    return cv2.imencode('.webp', rgba, [cv2.IMWRITE_WEBP_QUALITY, 85])[1].tobytes()
