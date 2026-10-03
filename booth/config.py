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

# 장소 이야기: 촬영 화면에서 배경을 고를 때마다 보여 준다. 현장에 맞게 자유롭게 고쳐 쓰세요
BACKGROUNDS = [
    {'id': 1, 'name': '황포돛배와 영산강 노을', 'place': '영산포 나루',
     'story': '흑산도 홍어와 남도의 곡식을 싣고 영산강을 오르내리던 황포돛배. '
              '해 질 무렵 누런 돛이 노을빛으로 물드는 영산포 나루의 저녁이에요.'},
    {'id': 2, 'name': '느러지 한반도 물돌이', 'place': '나주 동강면 느러지',
     'story': '강물이 크게 휘돌아 흐르며 한반도를 닮은 땅을 빚어낸 곳. '
              '전망대에 오르면 영산강이 그린 지도가 한눈에 내려다보여요.'},
    {'id': 3, 'name': '푸른 영산강 풍경', 'place': '나주 들녘',
     'story': '너른 나주 들판 사이로 느릿하게 흐르는 영산강. '
              '맑은 날이면 강물에 하늘과 구름이 그대로 담겨요.'},
    {'id': 4, 'name': '영산강 빛의 산책로', 'place': '영산강 수변',
     'story': '해가 지면 다리와 물가 산책로에 하나둘 불이 켜지고 강물 위로 빛이 길게 번져요. '
              '저녁 산책길의 반짝이는 순간이에요.'},
    {'id': 5, 'name': '영산강 코스모스 정원', 'place': '영산강 둔치',
     'story': '가을바람이 불면 강변을 가득 채우는 분홍빛 코스모스. '
              '꽃 사이에 서면 누구나 가을의 주인공이 돼요.'},
    {'id': 6, 'name': '황포돛배와 영산강', 'place': '영산강 뱃길',
     'story': '푸른 산자락 아래 물살을 가르며 나아가는 황포돛배. '
              '옛 뱃사람들이 오가던 뱃길을 지금도 배를 타고 따라가 볼 수 있어요.'},
    {'id': 7, 'name': '영산강 양귀비 꽃밭', 'place': '영산강 둔치',
     'story': '봄이 깊어지면 강변이 붉은 양귀비로 물들어요. '
              '강 건너 도시 풍경과 꽃물결이 한 장면에 담기는 곳이에요.'},
    {'id': 8, 'name': '2026 나주영산강축제', 'place': '축제장 입구',
     'story': '강과 사람이 함께 어우러지는 나주영산강축제. '
              '오늘 우리가 여기 왔다는 걸 오래 기억하도록 축제장 입구에서 한 장 남겨요.'},
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
