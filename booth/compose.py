"""인물 합성: RVM 매팅 → 전경색 복원 → 배경과 색감 맞추기 → 합성.
필터 버전은 여기에 장소의 빛 맞추기(톤·색·해 방향 빛)와 인물 보정을 더한다."""
import os
import threading
import urllib.request

import cv2
import numpy as np

from . import config

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
                # 동시에 합성하는 수만큼 CPU를 나눠 써서 서로 다투지 않게 한다
                opts.intra_op_num_threads = max(1, (os.cpu_count() or 4) // config.COMPOSE_SLOTS)
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                _session = ort.InferenceSession(
                    str(ensure_model()), opts, providers=['CPUExecutionProvider'])
    return _session


def read_image(path):
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


def _cover_box(iw, ih, w, h):
    s = max(w / iw, h / ih)
    nw, nh = round(iw * s), round(ih * s)
    return s, nw, nh, (nw - w) // 2, (nh - h) // 2


def cover(img, w, h):
    """비율을 유지한 채 (w, h)를 가득 채우도록 가운데를 잘라 맞춘다."""
    ih, iw = img.shape[:2]
    s, nw, nh, x, y = _cover_box(iw, ih, w, h)
    img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
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


def _skin_mask(F, a, s):
    """인물 영역 안의 피부(얼굴·목·손)만 고른다. 눈·눈썹·머리카락은 빠진다."""
    ycc = cv2.cvtColor(np.uint8(np.clip(F, 0, 1) * 255), cv2.COLOR_BGR2YCrCb)
    m = cv2.inRange(ycc, (40, 135, 85), (255, 180, 135)).astype(np.float32) / 255
    m *= (a > 0.6)
    k = max(3, int(5 * s) | 1)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    return cv2.GaussianBlur(m, (0, 0), 4 * s)


def beautify(F, a, strength=1.0, glow_amount=0.16, warm=0.0):
    """인물만 뽀샤시하게: 피부 결 정리, 화사한 피부 톤, 생기, 부드러운 빛 번짐.
    배경은 건드리지 않아 선명한 풍경은 그대로 두고 인물만 그 안에 어울리게 한다."""
    if strength <= 0:
        return F
    s = max(F.shape[:2]) / 1600
    skin = _skin_mask(F, a, s)
    sk = skin[..., None]

    # 1) 피부 결: 경계는 살리고 잡티·모공만 부드럽게
    smooth = cv2.bilateralFilter(F, 0, 0.08, 6 * s)
    smooth = cv2.bilateralFilter(smooth, 0, 0.06, 3 * s)
    F = F + (smooth - F) * sk * (0.6 * strength)

    # 2) 피부 톤: 조금 밝고 맑게, 노란기는 덜고 혈색은 살짝
    lab = _lab(F)
    lab[..., 0] += skin * 4.0 * strength
    lab[..., 1] += skin * 1.5 * strength
    lab[..., 2] -= skin * 2.0 * strength * max(0.0, 1 - warm / 4)  # 노을빛에서는 노란기를 빼지 않는다
    # 3) 생기: 칙칙한 실내 조명에서 빠진 채도를 인물 전체에 조금 되살린다 (피부는 덜)
    chroma = 1 + (0.14 - 0.07 * skin) * strength
    lab[..., 1:] *= chroma[..., None]
    lab[..., 0] = np.clip(lab[..., 0], 0, 100)
    F = np.clip(cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), 0, 1)

    # 4) 뽀샤시: 인물 빛만 번지게 (배경색이 섞이지 않도록 알파로 가중한 블러)
    a3 = a[..., None]
    r = 10 * s
    glow = cv2.GaussianBlur(F * a3, (0, 0), r) / (cv2.GaussianBlur(a, (0, 0), r)[..., None] + 1e-4)
    glow = np.clip(glow, 0, 1)
    screen = 1 - (1 - F) * (1 - glow)
    F = F + (screen - F) * (glow_amount * strength)

    # 5) 선명한 배경 옆에서 흐려 보이지 않게: 빛 번짐으로 풀린 대비를 되돌리고,
    #    머리카락·눈썹·눈(피부가 아닌 곳)은 살짝 또렷하게
    lab = _lab(F)
    body = a > 0.85
    if body.sum() >= 500:
        mid = float(lab[..., 0][body].mean())
        lab[..., 0] = np.clip((lab[..., 0] - mid) * (1 + 0.08 * strength) + mid, 0, 100)
        F = np.clip(cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), 0, 1)
    detail = F - cv2.GaussianBlur(F, (0, 0), 1.2 * s)
    return np.clip(F + detail * ((1 - sk) * 0.4 * strength), 0, 1)


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def scene_match(F, a, bg, look, strength=1.0):
    """장소의 빛에 맞춘다: 밝기 분포와 밝은 곳·어두운 곳의 빛 색을 배경 쪽으로 옮긴다.
    노을이면 인물에도 금빛이 돌고, 맑은 낮이면 그림자에 하늘빛이 살짝 도는 식."""
    body = a > 0.85
    if body.sum() < 500 or strength <= 0:
        return F
    fl, bl = _lab(F), _lab(bg)
    bL = bl[..., 0]
    fL = fl[..., 0][body]
    # 밝기 분포(어두운 곳·중간·밝은 곳)를 배경 쪽으로 35% 당긴다
    qs = np.percentile(fL, [3, 50, 97])
    ps = np.percentile(bL, [3, 50, 97])
    t = qs + np.clip((ps - qs) * 0.35 * strength, -12, 12)
    t = np.maximum.accumulate(np.clip(t, 1, 99))
    src = np.concatenate([[0], qs, [100]])
    dst = np.concatenate([[0], t, [100]])
    if np.all(np.diff(src) > 0):
        fl[..., 0] = np.interp(fl[..., 0], src, dst).astype(np.float32)
    # 장소의 노출: 노을 역광이면 얼굴을 조금 어둡게, 맑은 낮이면 조금 밝게
    fl[..., 0] += look.get('exposure', 0) * strength
    # 밝은 곳에는 배경 하이라이트의 빛 색, 어두운 곳에는 배경 그림자 색을 입힌다.
    # 해처럼 하얗게 날아간 곳은 빛 색이 없으므로 그 아래(60~95%) 밝기에서 색을 잡는다
    band = (bL >= np.percentile(bL, 60)) & (bL <= np.percentile(bL, 95))
    hi = bl[band][:, 1:].mean(0)
    sh = bl[bL <= np.percentile(bL, 10)][:, 1:].mean(0)
    L = fl[..., 0]
    wh = _smoothstep(45, 85, L)[..., None]
    ws = (1 - _smoothstep(15, 50, L))[..., None]
    tint = np.array(look.get('tint', (0, 0)), np.float32)
    wm = _smoothstep(20, 60, L)[..., None]
    fl[..., 1:] += (hi * wh * 0.30 + sh * ws * 0.20 + tint * wm) * strength
    return np.clip(cv2.cvtColor(fl, cv2.COLOR_LAB2BGR), 0, 1)


def rim_light(F, a, bg, sun, amount):
    """배경 속 해가 있는 쪽 인물 가장자리에 빛이 비치게 한다 (역광·측광 느낌)."""
    if amount <= 0:
        return F
    h, w = a.shape
    s = max(h, w) / 1600
    sx, sy = sun
    # 알파 경계의 바깥쪽 방향(법선)과 해 방향이 맞는 곳만 밝힌다
    ab = cv2.GaussianBlur(a, (0, 0), 12 * s)
    gx = -cv2.Sobel(ab, cv2.CV_32F, 1, 0, ksize=3)
    gy = -cv2.Sobel(ab, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy) + 1e-6
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    lx, ly = sx * w - xx, sy * h - yy
    ln = np.sqrt(lx * lx + ly * ly) + 1e-6
    facing = np.clip((gx * lx + gy * ly) / (mag * ln), 0, 1) ** 1.5
    edge = mag / (np.percentile(mag[a > 0.05], 99) + 1e-6) if (a > 0.05).any() else mag
    rim = np.clip(edge, 0, 1) * facing * a
    rim = cv2.GaussianBlur(rim, (0, 0), 5 * s)[..., None]
    # 빛 색은 배경에서 가장 밝은 곳의 색
    lum = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    color = bg[lum >= np.percentile(lum, 98)].mean(0)
    color = color / (color.max() + 1e-6)
    lit = 1 - (1 - F) * (1 - np.clip(color * rim * amount * 2.2, 0, 1))
    return np.clip(lit, 0, 1)


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


def _sun_in_shot(bg_id, look, w, h):
    """원본 배경 기준 해 위치를 잘라 맞춘 결과 사진 좌표(0~1)로 바꾼다."""
    sx, sy = look.get('sun', (0.5, -0.4))
    src = read_image(config.BG_DIR / f'bg_{bg_id}.png')
    ih, iw = src.shape[:2]
    _, nw, nh, x, y = _cover_box(iw, ih, w, h)
    return ((sx * nw - x) / w, (sy * nh - y) / h)


def _finish(F, a, bgf):
    a3 = a[..., None]
    out = vignette(F * a3 + bgf * (1 - a3))
    return np.uint8(np.clip(out * 255 + 0.5, 0, 255))


def compose(photo_bgr, bg_id, w=config.SHOT_W, h=config.SHOT_H):
    """(필터 적용 사진, 원본 합성 사진, 사람을 찾았는지)를 함께 만든다. 매팅은 한 번만 한다.
    사람을 찾지 못해도(뒷모습·탈 인형·너무 먼 거리 등) 멈추지 않고 찍은 사진 그대로 돌려준다."""
    photo = cover(photo_bgr, w, h)
    bg = cover(read_image(config.BG_DIR / f'bg_{bg_id}.png'), w, h)
    look = config.BG_BY_ID[bg_id].get('look', {})

    a = matte(photo)
    if float((a > 0.5).mean()) < 0.01:
        return photo, photo, False
    img = photo.astype(np.float32) / 255
    bgf = bg.astype(np.float32) / 255

    F = harmonize(estimate_foreground(img, a), a, bgf)
    plain = _finish(light_wrap(F, a, bgf), a, bgf)

    k = config.BEAUTY
    S = scene_match(F, a, bgf, look, k)
    S = beautify(S, a, k, look.get('glow', 0.16), look.get('tint', (0, 0))[1])
    S = rim_light(S, a, bgf, _sun_in_shot(bg_id, look, w, h), look.get('rim', 0.2) * k)
    S = light_wrap(S, a, bgf)
    return _finish(S, a, bgf), plain, True
