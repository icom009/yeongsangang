"""배경 앞쪽 사물(갈대·꽃·수풀·난간) 마스크 backgrounds/fg_N.png 를 만든다. 배경을 바꿀 때 한 번만 실행.
깊이 추정(Depth Anything V2 Small, Apache-2.0)으로 가까운 곳을 고르고, 경계는 원본 그림 윤곽에 맞춘다.
결과는 꼭 눈으로 확인하고, 이상하면 아래 PARAMS를 고치거나 그림 도구로 직접 다듬어도 된다.

    python scripts/make_foreground.py          # PARAMS에 있는 배경 전부
    python scripts/make_foreground.py 5 7      # 일부만
"""
import sys
import urllib.request
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booth import config  # noqa: E402
from booth.compose import _providers  # noqa: E402

DEPTH_URL = 'https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx'
DEPTH_MODEL = config.MODEL_DIR / 'depth_anything_v2_small.onnx'

# 배경 id: (가까움 시작, 가까움 끝, 위쪽 한계). 깊이는 0(멀다)~1(가깝다), 위쪽 한계는 이미지 높이 비율.
# 앞쪽이 땅·물뿐인 배경(2·3·6)은 가릴 것이 없어 뺐다
PARAMS = {
    1: (0.55, 0.65, 0.55),
    4: (0.55, 0.65, 0.60),
    5: (0.62, 0.72, 0.70),
    7: (0.75, 0.85, 0.75),
    8: (0.70, 0.80, 0.60),
}


def depth(sess, img):
    h, w = img.shape[:2]
    s = 1036 / max(h, w)
    nh, nw = int(h * s) // 14 * 14, int(w * s) // 14 * 14
    x = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), (nw, nh), interpolation=cv2.INTER_CUBIC)
    x = (x.astype(np.float32) / 255 - (0.485, 0.456, 0.406)) / (0.229, 0.224, 0.225)
    d = sess.run(None, {sess.get_inputs()[0].name: x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0]
    d = cv2.resize(d, (w, h), interpolation=cv2.INTER_CUBIC)
    return (d - d.min()) / (d.max() - d.min() + 1e-6)


def _box(x, r):
    return cv2.blur(x, (2 * r + 1, 2 * r + 1))


def guided(guide, p, r=4, eps=1e-3):
    """He et al. guided filter: 마스크 경계를 원본 그림의 윤곽에 붙인다."""
    mi, mp = _box(guide, r), _box(p, r)
    a = (_box(guide * p, r) - mi * mp) / (_box(guide * guide, r) - mi * mi + eps)
    b = mp - a * mi
    return _box(a, r) * guide + _box(b, r)


def make(sess, bg_id, t0, t1, ytop):
    img = cv2.imread(str(config.BG_DIR / f'bg_{bg_id}.png'))
    h = img.shape[0]
    m = np.clip((depth(sess, img) - t0) / (t1 - t0), 0, 1)
    m *= np.clip((np.linspace(0, 1, h)[:, None] - ytop) / 0.06, 0, 1)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    m = np.clip((np.clip(guided(gray, m.astype(np.float32)), 0, 1) - 0.15) / 0.7, 0, 1)
    out = config.BG_DIR / f'fg_{bg_id}.png'
    cv2.imwrite(str(out), np.uint8(m * 255 + 0.5), [cv2.IMWRITE_PNG_COMPRESSION, 9])
    print(out)


def main():
    import onnxruntime as ort
    if not DEPTH_MODEL.exists():
        config.MODEL_DIR.mkdir(exist_ok=True)
        urllib.request.urlretrieve(DEPTH_URL, DEPTH_MODEL)
    if hasattr(ort, 'preload_dlls'):
        ort.preload_dlls()
    sess = ort.InferenceSession(str(DEPTH_MODEL), providers=_providers(ort))
    ids = [int(a) for a in sys.argv[1:]] or list(PARAMS)
    for bg_id in ids:
        make(sess, bg_id, *PARAMS[bg_id])


if __name__ == '__main__':
    main()
