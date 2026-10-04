"""카카오톡·문자 등에 주소를 붙여 넣으면 나오는 미리보기 썸네일(web/og.jpg, 1200x630)을 만든다.

영산강 노을 배경 위에, 부스 프레임(2026 영산강축제 글씨·돛배 그림)에 풍경을 담은 견본 사진과
'영산강 축제 AI 체험부스' 글씨를 얹는다. 방문객 사진은 절대 쓰지 않는다.
    python scripts/make_og.py          (Docker 안: docker compose exec booth python scripts/make_og.py)
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

from booth import compose, config, frame  # noqa: E402

W, H = 1200, 630
NANUM = Path('/usr/share/fonts/truetype/nanum')


def font(name, size):
    for p in (NANUM / name, config.FALLBACK_FONT):
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


def main(out=config.WEB_DIR / 'og.jpg'):
    # 배경: 황포돛배와 노을, 오른쪽은 글씨가 잘 보이게 어둡게
    bg = compose.cover(compose.read_image(config.BG_DIR / 'bg_1.png'), W, H)
    img = Image.fromarray(cv2.cvtColor(bg, cv2.COLOR_BGR2RGB)).convert('RGBA')
    x = np.linspace(0, 1, W, dtype=np.float32)
    shade = np.clip((x - 0.30) / 0.45, 0, 1) * 0.78
    over = np.zeros((H, W, 4), np.uint8)
    over[..., :3] = (8, 22, 27)
    over[..., 3] = np.uint8(np.tile(shade, (H, 1)) * 255)
    img = Image.alpha_composite(img, Image.fromarray(over))

    # 왼쪽: 부스에서 실제로 받는 프레임 사진 견본 (풍경만 담는다)
    with tempfile.TemporaryDirectory() as d:
        sample = Path(d) / 'sample.jpg'
        scene = compose.cover(compose.read_image(config.BG_DIR / 'bg_5.png'), config.SHOT_W, config.SHOT_H)
        cv2.imwrite(str(sample), scene)
        framed = Path(d) / 'framed.jpg'
        frame.render(sample, '영산강에서 우리 가족', framed)
        card = Image.open(framed).convert('RGBA')
    ch = 560
    card = card.resize((round(card.width * ch / card.height), ch), Image.LANCZOS)
    card = card.rotate(4, resample=Image.BICUBIC, expand=True)
    shadow = Image.new('RGBA', card.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 150), mask=card.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    cx, cy = 70, (H - card.height) // 2
    img.alpha_composite(shadow, (cx + 10, cy + 16))
    img.alpha_composite(card, (cx, cy))

    # 오른쪽: 글씨
    d = ImageDraw.Draw(img)
    tx = 520
    d.text((tx, 120), '2026 나주영산강축제', font=font('NanumSquareB.ttf', 34), fill=(255, 210, 122))
    big = font('NanumSquareB.ttf', 84)
    d.text((tx, 172), '영산강 축제', font=big, fill=(255, 255, 255))
    d.text((tx, 270), 'AI 체험부스', font=big, fill=(255, 255, 255))
    body = font('NanumBarunGothic.ttf', 31)
    d.text((tx, 392), '가고 싶은 영산강 풍경 속에', font=body, fill=(255, 255, 255, 225))
    d.text((tx, 436), 'AI가 우리를 담아 드려요', font=body, fill=(255, 255, 255, 225))
    # 아래쪽 알약: 할 수 있는 것
    pill = font('NanumBarunGothicBold.ttf', 23)
    label = 'QR로 바로 받기 · AI 장면 연출 · 네 컷 사진'
    w = d.textlength(label, font=pill)
    d.rounded_rectangle((tx, 512, tx + w + 40, 560), 24, fill=(243, 179, 61))
    d.text((tx + 20, 536), label, font=pill, fill=(42, 26, 3), anchor='lm')

    img.convert('RGB').save(out, quality=90, optimize=True, progressive=True)
    print(out, Path(out).stat().st_size // 1024, 'KB')


if __name__ == '__main__':
    main()
