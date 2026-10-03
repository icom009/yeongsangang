"""완성 사진 프레임 렌더링과 QR코드. 1컷과 4컷(인생네컷)을 같은 프레임에 담는다."""
import io
from functools import lru_cache
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont
from qrcode.image.pil import PilImage

from . import config


@lru_cache(maxsize=1)
def _font_bytes():
    for p in (config.FONT, config.FALLBACK_FONT):
        if p.exists():
            return p.read_bytes()
    return None


def _font(size):
    data = _font_bytes()
    if data is None:
        return ImageFont.load_default(size)
    return ImageFont.truetype(io.BytesIO(data), size)


def clean_message(msg):
    lines = [x.strip() for x in (msg or '').splitlines() if x.strip()][:3]
    return lines or [config.DEFAULT_MESSAGE]


def _rounded_mask(size, r):
    m = Image.new('L', size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), r, fill=255)
    return m


def _fit(img, w, h):
    """비율을 지킨 채 가운데를 잘라 (w, h)에 꽉 맞춘다."""
    s = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    l, t = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((l, t, l + w, t + h))


def cells(n):
    """사진 칸을 1칸(1컷) 또는 2x2(4컷)로 나눈다.
    프레임의 사진 칸이 877x661이라 2x2로 쪼개도 각 칸이 그대로 4:3이다 (새 프레임이 필요 없다)."""
    x1, y1, x2, y2 = config.FRAME_HOLE
    if n <= 1:
        return [(x1, y1, x2 - x1, y2 - y1)]
    g = config.FRAME_GAP
    w, h = (x2 - x1 - g) // 2, (y2 - y1 - g) // 2
    return [(x1 + (i % 2) * (w + g), y1 + (i // 2) * (h + g), w, h) for i in range(4)]


def grid(paths, w=config.SHOT_W, h=config.SHOT_H, bg=(244, 252, 255)):
    """프레임 없는 4컷 사진. 휴대폰 받기 화면의 '프레임 없는 사진'으로 쓴다."""
    g = config.FRAME_GAP
    out = Image.new('RGB', (w, h), bg)
    cw, ch = (w - g) // 2, (h - g) // 2
    for i, p in enumerate(list(paths)[:4]):
        out.paste(_fit(Image.open(p).convert('RGB'), cw, ch), ((i % 2) * (cw + g), (i // 2) * (ch + g)))
    return out


def save_grid(paths, out_path):
    """프레임 없는 4컷 사진을 파일로 저장한다."""
    grid(paths).save(out_path, quality=93, subsampling=0)
    return out_path


def render(shots, message, out_path):
    """shots: 사진 경로 하나(1컷) 또는 네 개(4컷)."""
    paths = [shots] if isinstance(shots, (str, Path)) else list(shots)
    frame = Image.open(config.FRAME).convert('RGB')
    boxes = cells(len(paths))
    radius = config.FRAME_HOLE_RADIUS if len(paths) == 1 else max(6, config.FRAME_HOLE_RADIUS // 2)
    for p, (x, y, w, h) in zip(paths, boxes):
        frame.paste(_fit(Image.open(p).convert('RGB'), w, h), (x, y), _rounded_mask((w, h), radius))

    lines = clean_message(message)
    bx1, by1, bx2, by2 = config.FRAME_TEXT_BOX
    bw, bh = bx2 - bx1, by2 - by1
    d = ImageDraw.Draw(frame)

    # 상자 안에 들어가는 가장 큰 글자 크기를 찾는다
    size = 72
    while size > 22:
        font = _font(size)
        lh = size * 1.25
        widest = max(d.textlength(x, font=font) for x in lines)
        if widest <= bw and lh * len(lines) <= bh:
            break
        size -= 2
    lh = size * 1.25
    top = by1 + (bh - lh * len(lines)) / 2 + lh / 2
    for i, x in enumerate(lines):
        d.text(((bx1 + bx2) / 2, top + i * lh), x, font=font,
               fill=config.TEXT_COLOR, anchor='mm')

    frame.save(out_path, quality=95, subsampling=0)
    return out_path


def qr_png(url):
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2, box_size=12)
    q.add_data(url)
    q.make(fit=True)
    img = q.make_image(image_factory=PilImage, fill_color='#0f2a2f', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()
