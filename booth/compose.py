"""인물 합성: RVM 매팅 → 전경색 복원 → 배경과 색감 맞추기 → 합성."""
import threading
import urllib.request

import cv2
import numpy as np

from . import config

class NoPersonError(Exception):
    pass


_session = None
_lock = threading.Lock()


def ensure_model():
    if not config.MATTING_MODEL.exists():
        config.MODEL_DIR.mkdir(exist_ok=True)
        tmp = config.MATTING_MODEL.with_suffix('.part')
        urllib.request.urlretrieve(config.MATTING_URL, tmp)
        tmp.rename(config.MATTING_MODEL)
    return config.MATTING_MODEL


def _get_session():
    global _session
    if _session is None:
        with _lock:
            if _session is None:
                import onnxruntime as ort
                opts = ort.SessionOptions()
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                _session = ort.InferenceSession(
                    str(ensure_model()), opts, providers=['CPUExecutionProvider'])
    return _session


def read_image(path):
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


def cover(img, w, h):
    """비율을 유지한 채 (w, h)를 가득 채우도록 가운데를 잘라 맞춘다."""
    ih, iw = img.shape[:2]
    s = max(w / iw, h / ih)
    nw, nh = round(iw * s), round(ih * s)
    img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    x, y = (nw - w) // 2, (nh - h) // 2
    return img[y:y + h, x:x + w]


def matte(bgr):
    """인물 알파(0~1, float32)를 반환한다."""
    h, w = bgr.shape[:2]
    x = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255
    x = np.ascontiguousarray(x.transpose(2, 0, 1)[None])
    z = np.zeros((1, 1, 1, 1), np.float32)
    ratio = np.array([min(1.0, config.MATTING_SIZE / max(h, w))], np.float32)
    sess = _get_session()
    _, a, *_ = sess.run(None, {'src': x, 'r1i': z, 'r2i': z, 'r3i': z, 'r4i': z,
                                'downsample_ratio': ratio})
    a = a[0, 0]
    # 배경의 옅은 잡음은 지우고, 몸 안쪽의 반투명은 채운다
    return np.clip((a - 0.03) / 0.94, 0, 1).astype(np.float32)


def _blur_fusion(F, B, img, a, r):
    """Forte & Pitié(2021) 근사 전경색 추정 한 단계."""
    a3 = a[..., None]
    ba = cv2.blur(a, (r, r))[..., None]
    bF = cv2.blur(F * a3, (r, r)) / (ba + 1e-5)
    bB = cv2.blur(B * (1 - a3), (r, r)) / ((1 - ba) + 1e-5)
    F = bF + a3 * (img - a3 * bF - (1 - a3) * bB)
    return np.clip(F, 0, 1), bB


def estimate_foreground(img, a):
    """원본 배경색이 머리카락 경계에 번져 보이는 현상(헤일로)을 없앤다."""
    F, bB = _blur_fusion(img, img, img, a, 91)
    F, _ = _blur_fusion(F, bB, img, a, 7)
    return F


def _lab(x):
    return cv2.cvtColor(np.clip(x, 0, 1).astype(np.float32), cv2.COLOR_BGR2LAB)


def harmonize(F, a, bg, strength=1.0):
    """인물의 밝기·색온도를 배경 쪽으로 살짝 당겨 붙여 넣은 티를 줄인다."""
    body = a > 0.85
    if body.sum() < 500:
        return F
    fl, bl = _lab(F), _lab(bg)
    fm, fs = fl[body].mean(0), fl[body].std(0) + 1e-3
    bm = bl.reshape(-1, 3).mean(0)
    out = fl.copy()
    # 밝기: 어두운 실내 조명을 보정하되 배경 쪽으로만 일부 이동
    shift = 0.30 * (bm[0] - fm[0])
    if fm[0] < 45:  # 어두운 실내 조명이면 조금 더 밝힌다
        shift = max(shift, (45 - fm[0]) * 0.6)
    shift = float(np.clip(shift, -8, 10))
    target_L = fm[0] + shift
    out[..., 0] += shift * strength
    # 대비가 낮으면 조금 살린다
    if fs[0] < 14:
        out[..., 0] = (out[..., 0] - target_L) * min(1.25, 14 / fs[0]) + target_L
    # 색: 배경 색조를 18%만 입힌다 (노을이면 따뜻하게, 낮이면 맑게)
    out[..., 1:] += (bm[1:] - fm[1:]) * 0.18 * strength
    out[..., 0] = np.clip(out[..., 0], 0, 100)
    return np.clip(cv2.cvtColor(out, cv2.COLOR_LAB2BGR), 0, 1)


def light_wrap(F, a, bg, amount=0.35):
    """배경 빛이 인물 가장자리를 감싸도록 해 경계를 자연스럽게 한다."""
    inner = cv2.GaussianBlur(a, (0, 0), 6)
    edge = np.clip(a - inner, 0, 1) * 2.2
    edge = np.clip(edge + (1 - inner) * a * 0.6, 0, 1)[..., None] * amount
    glow = cv2.GaussianBlur(bg, (0, 0), 14)
    return F * (1 - edge) + glow * edge


def vignette(img, k=0.10):
    h, w = img.shape[:2]
    y, x = np.ogrid[-1:1:h * 1j, -1:1:w * 1j]
    v = 1 - k * np.clip(x * x * 0.8 + y * y, 0, 1.6)
    return img * v[..., None].astype(np.float32)


def compose(photo_bgr, bg_id, w=config.SHOT_W, h=config.SHOT_H):
    photo = cover(photo_bgr, w, h)
    bg = cover(read_image(config.BG_DIR / f'bg_{bg_id}.png'), w, h)

    a = matte(photo)
    if float((a > 0.5).mean()) < 0.01:
        raise NoPersonError()
    img = photo.astype(np.float32) / 255
    bgf = bg.astype(np.float32) / 255

    F = estimate_foreground(img, a)
    F = harmonize(F, a, bgf)
    F = light_wrap(F, a, bgf)

    a3 = a[..., None]
    out = vignette(F * a3 + bgf * (1 - a3))
    return np.uint8(np.clip(out * 255 + 0.5, 0, 255))
