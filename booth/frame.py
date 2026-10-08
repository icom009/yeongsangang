"""완성 사진 프레임 렌더링과 QR코드. 1컷과 4컷(인생네컷)을 같은 프레임에 담는다."""
import io
from functools import lru_cache
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont
from qrcode.image.pil import PilImage

from . import config


def _chain(font_id=None):
    """글씨체 파일 순서: 고른 글씨체 → 기본 손글씨체 → 나눔고딕. 고른 글씨체에 없는 글자(♥, 드문 글자)는 다음 것으로 쓴다."""
    out = []
    for p in (config.font_file(font_id), config.FONT, config.FALLBACK_FONT):
        if p.exists() and str(p) not in out:
            out.append(str(p))
    return out


@lru_cache(maxsize=32)
def _cmap(path):
    """그 글씨체에 들어 있는 글자들. fontTools가 없으면 None(모든 글자가 있다고 본다)."""
    try:
        from fontTools.ttLib import TTFont
        return frozenset(TTFont(path, lazy=True).getBestCmap())
    except Exception:  # noqa: BLE001
        return None


def _font(size, path=None):
    if path is None:
        chain = _chain()
        if not chain:
            return ImageFont.load_default(size)
        path = chain[0]
    return ImageFont.truetype(path, size)


def _runs(text, chain):
    """글자마다 그 글자가 있는 첫 글씨체로 묶는다 [[글씨체 번호, 글자열], ...].
    어느 글씨체에도 없는 글자(그림 문자 등)는 빈 네모로 찍히지 않게 뺀다."""
    runs = []
    for ch in text:
        if ch == ' ' and runs:
            k = runs[-1][0]
        else:
            k = next((i for i, p in enumerate(chain) if (cm := _cmap(p)) is None or ord(ch) in cm), None)
            if k is None:
                continue
        if runs and runs[-1][0] == k:
            runs[-1][1] += ch
        else:
            runs.append([k, ch])
    return runs


def _measurer(draw, chain, size):
    """그 크기에서 글자열 폭을 재는 함수 (글자마다 쓰는 글씨체가 다를 수 있다). .font(k)로 글씨체를 꺼낸다."""
    fonts = {}

    def font(k):
        if k not in fonts:
            fonts[k] = _font(size, chain[k])
        return fonts[k]

    def measure(text):
        return sum(draw.textlength(run, font=font(k)) for k, run in _runs(text, chain))

    measure.font = font
    return measure


def clean_message(msg):
    """방문객이 직접 나눈 줄(최대 3줄). 긴 줄은 layout_message가 상자 폭에 맞춰 다시 나눈다."""
    lines = [x.strip() for x in (msg or '').splitlines() if x.strip()][:3]
    return lines or [config.DEFAULT_MESSAGE]


# 한마디 글자 크기: 72px부터 2씩 줄여 상자에 들어가는 가장 큰 크기. web/app.js의 fitMessage()와 같은 규칙
MSG_MAX, MSG_MIN, MSG_LINE = 72, 22, 1.25


def _wrap(text, width, measure):
    """한 줄을 폭에 맞춰 나눈다. 띄어쓰기에서 나누고, 한 어절이 폭보다 길면 글자 단위로 나눈다."""
    lines, cur = [], ''
    for word in text.split(' '):
        if not word:
            continue
        cand = f'{cur} {word}' if cur else word
        if measure(cand) <= width:
            cur = cand
            continue
        if cur:
            lines.append(cur)
            cur = ''
        for ch in word:
            if cur and measure(cur + ch) > width:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
    if cur:
        lines.append(cur)
    return lines


def _balance(text, width, measure):
    """줄 수는 그대로 두고 폭을 좁혀 줄 길이를 고르게 (마지막 줄에 한 어절만 남지 않게)."""
    lines = _wrap(text, width, measure)
    lo, hi = 1, int(width)
    while lo < hi:
        mid = (lo + hi) // 2
        if len(_wrap(text, mid, measure)) <= len(lines):
            hi = mid
        else:
            lo = mid + 1
    return _wrap(text, hi, measure)


def layout_message(paragraphs, box_w, box_h, measure_at):
    """상자에 들어가는 가장 큰 글자 크기와 그 크기로 나눈 줄들.
    예전에는 직접 나눈 줄만 썼기 때문에, 한 줄로 길게 쓰면 폭에 맞추느라 글자가 아주 작아졌다.
    measure_at(size)는 그 크기에서 글자열 폭을 재는 함수를 돌려준다."""
    size = MSG_MAX
    while size > MSG_MIN:
        measure = measure_at(size)
        lines = [x for p in paragraphs for x in _wrap(p, box_w, measure)]
        if len(lines) * size * MSG_LINE <= box_h:
            break
        size -= 2
    measure = measure_at(size)
    return size, [x for p in paragraphs for x in _balance(p, box_w, measure)]


def _rounded_mask(size, r):
    m = Image.new('L', size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), r, fill=255)
    return m


def _open(p):
    """파일 경로든 이미 열린 그림이든 RGB 그림으로."""
    return p.convert('RGB') if isinstance(p, Image.Image) else Image.open(p).convert('RGB')


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
        out.paste(_fit(_open(p), cw, ch), ((i % 2) * (cw + g), (i // 2) * (ch + g)))
    return out


def save_grid(paths, out_path):
    """프레임 없는 4컷 사진을 파일로 저장한다."""
    grid(paths).save(out_path, quality=93, subsampling=0)
    return out_path


def render(shots, message, out_path, font=None):
    """shots: 사진 하나(1컷) 또는 네 개(4컷). 파일 경로나 이미 열린 그림. font: 한마디 글씨체 id (없으면 기본)."""
    paths = [shots] if isinstance(shots, (str, Path, Image.Image)) else list(shots)
    frame = Image.open(config.FRAME).convert('RGB')
    boxes = cells(len(paths))
    radius = config.FRAME_HOLE_RADIUS if len(paths) == 1 else max(6, config.FRAME_HOLE_RADIUS // 2)
    for p, (x, y, w, h) in zip(paths, boxes):
        frame.paste(_fit(_open(p), w, h), (x, y), _rounded_mask((w, h), radius))

    bx1, by1, bx2, by2 = config.FRAME_TEXT_BOX
    bw, bh = bx2 - bx1, by2 - by1
    d = ImageDraw.Draw(frame)

    # 상자 안에 들어가는 가장 큰 글자 크기를 찾고, 긴 줄은 상자 폭에 맞춰 나눈다
    chain = _chain(font)
    size, lines = layout_message(clean_message(message), bw, bh, lambda sz: _measurer(d, chain, sz))
    measure = _measurer(d, chain, size)
    ascent, descent = measure.font(0).getmetrics()
    lh = size * MSG_LINE
    top = by1 + (bh - lh * len(lines)) / 2 + lh / 2
    for i, line in enumerate(lines):
        # 줄마다 가운데 정렬. 글씨체가 섞여도 같은 기준선에 (고른 글씨체의 위아래 끝 가운데가 줄 가운데)
        x = (bx1 + bx2) / 2 - measure(line) / 2
        y = top + i * lh + (ascent - descent) / 2
        for k, run in _runs(line, chain):
            f = measure.font(k)
            d.text((x, y), run, font=f, fill=config.TEXT_COLOR, anchor='ls')
            x += d.textlength(run, font=f)

    frame.save(out_path, quality=95, subsampling=0)
    return out_path


@lru_cache(maxsize=32)
def font_sample(font_id):
    """글씨체 고르기 단추의 이름 그림 (흰 글자, 투명 바탕). 화면에서는 모양틀(mask)로 써서 단추 글자색을 입힌다.
    글씨체 파일을 다 받지 않아도 고르기 단추에서 모양을 볼 수 있다."""
    f = config.FONT_BY_ID[font_id]
    W, H = 240, 72
    img = Image.new('RGBA', (W, H), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    size = 60
    font = ImageFont.truetype(str(f['file']), size)
    while size > 20 and d.textlength(f['name'], font=font) > W - 16:
        size -= 2
        font = ImageFont.truetype(str(f['file']), size)
    d.text((W / 2, H / 2), f['name'], font=font, fill=(255, 255, 255, 255), anchor='mm')
    buf = io.BytesIO()
    img.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def qr_png(url):
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2, box_size=12)
    q.add_data(url)
    q.make(fit=True)
    img = q.make_image(image_factory=PilImage, fill_color='#0f2a2f', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()
