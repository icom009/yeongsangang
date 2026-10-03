import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BG_DIR = ROOT / 'backgrounds'
FRAME = ROOT / 'frame' / 'frame1.png'
BGM_DIR = ROOT / 'bgm'
WEB_DIR = ROOT / 'web'
MODEL_DIR = ROOT / 'model'
FONT = ROOT / 'font' / 'OwnglyphPDH.ttf'
FALLBACK_FONT = Path('/usr/share/fonts/truetype/nanum/NanumGothic.ttf')

OUT_DIR = Path(os.environ.get('YS_OUT_DIR', ROOT / 'output'))
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 사진 보관 시간(시간 단위). 지나면 자동 삭제
KEEP_HOURS = float(os.environ.get('YS_KEEP_HOURS', 72))

# Robust Video Matting (MobileNetV3). 여러 명·작은 인물·머리카락 경계에 강하다
MATTING_MODEL = MODEL_DIR / 'rvm_mobilenetv3.onnx'
MATTING_URL = ('https://github.com/PeterL1n/RobustVideoMatting/releases/download/'
               'v1.0.0/rvm_mobilenetv3_fp32.onnx')
# 매팅 시 내부 해상도(긴 변). 높을수록 경계가 정밀하지만 느려진다
MATTING_SIZE = int(os.environ.get('YS_MATTING_SIZE', 640))

# 합성 결과 크기. 프레임 사진 칸(876x660)과 같은 4:3 비율
SHOT_W, SHOT_H = 1600, 1200

# 프레임 안 사진 칸 위치 (frame1.png 1024x1536 기준, 노란 테두리 안쪽)
FRAME_HOLE = (74, 318, 951, 979)
FRAME_HOLE_RADIUS = 18
FRAME_TEXT_BOX = (90, 1000, 934, 1185)
TEXT_COLOR = '#4a3420'
DEFAULT_MESSAGE = '오늘도 함께 행복하자'

BACKGROUNDS = [
    {'id': 1, 'name': '황포돛배와 영산강 노을', 'note': '돛배 너머로 해가 지는 저녁 강가'},
    {'id': 2, 'name': '느러지 한반도 물돌이', 'note': '강물이 한반도 모양으로 휘감아 도는 곳'},
    {'id': 3, 'name': '푸른 영산강 풍경', 'note': '들판 사이로 흐르는 맑은 날의 강'},
    {'id': 4, 'name': '영산강 빛의 산책로', 'note': '불빛이 강물에 번지는 해 질 녘 다리'},
    {'id': 5, 'name': '영산강 코스모스 정원', 'note': '하늘 아래 끝없이 핀 코스모스'},
    {'id': 6, 'name': '황포돛배와 영산강', 'note': '초록 산을 배경으로 떠가는 황포돛배'},
    {'id': 7, 'name': '영산강 양귀비 꽃밭', 'note': '강 건너 도시를 마주한 붉은 꽃밭'},
    {'id': 8, 'name': '2026 나주영산강축제', 'note': '축제장 입구, 오늘 여기 왔다는 기념'},
]
BG_IDS = {b['id'] for b in BACKGROUNDS}


def public_base_url():
    """QR코드에 넣을 외부 접속 주소. 배포 환경 변수 → 내부 IP 순으로 찾는다."""
    for key in ('YS_PUBLIC_URL', 'RENDER_EXTERNAL_URL'):
        if os.environ.get(key):
            return os.environ[key].rstrip('/')
    host = os.environ.get('SPACE_HOST')  # Hugging Face Spaces
    if host:
        return f'https://{host}'
    return None
