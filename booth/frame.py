"""완성 사진 프레임 렌더링과 QR코드."""
import io
from functools import lru_cache

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


def render(shot_path, message, out_path):
    frame = Image.open(config.FRAME).convert('RGB')
    x1, y1, x2, y2 = config.FRAME_HOLE
    w, h = x2 - x1, y2 - y1

    photo = Image.open(shot_path).convert('RGB')
    s = max(w / photo.width, h / photo.height)
    photo = photo.resize((round(photo.width * s), round(photo.height * s)), Image.LANCZOS)
    l, t = (photo.width - w) // 2, (photo.height - h) // 2
    photo = photo.crop((l, t, l + w, t + h))
    frame.paste(photo, (x1, y1), _rounded_mask((w, h), config.FRAME_HOLE_RADIUS))

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
