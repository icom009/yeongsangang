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

# 인터넷(터널)으로 부스 화면을 열 때 쓰는 열쇠. https://주소/?key=값 으로 한 번 열면 그 기기는 계속 쓸 수 있다.
# 비어 있으면 인터넷에서는 방문객 받기 화면만 열리고, 부스 화면은 이 컴퓨터·같은 공유기에서만 열린다
BOOTH_KEY = os.environ.get('YS_BOOTH_KEY', '')

# 사진 보관 시간(시간 단위). 지나면 자동 삭제
KEEP_HOURS = float(os.environ.get('YS_KEEP_HOURS', 72))

# Robust Video Matting (MobileNetV3). 여러 명·작은 인물·머리카락 경계에 강하다
# mobilenetv3: 가볍다 (CPU 노트북). resnet50: 머리카락 경계가 더 섬세하고 잘못 잡는 일이 적다 (GPU 서버, 매팅 약 30ms)
MATTING_NAME = os.environ.get('YS_MATTING_MODEL', 'mobilenetv3')
if MATTING_NAME not in ('mobilenetv3', 'resnet50'):
    raise ValueError(f'YS_MATTING_MODEL은 mobilenetv3 또는 resnet50: {MATTING_NAME}')
MATTING_MODEL = MODEL_DIR / f'rvm_{MATTING_NAME}.onnx'
MATTING_URL = ('https://github.com/PeterL1n/RobustVideoMatting/releases/download/'
               f'v1.0.0/rvm_{MATTING_NAME}_fp32.onnx')
# 매팅 시 내부 해상도(긴 변). RVM은 512~640쯤이 가장 좋고, 1000 넘게 올리면 오히려 경계가 뭉개진다
MATTING_SIZE = int(os.environ.get('YS_MATTING_SIZE', 640))
# 1이면 인터넷(공개 주소)에서도 부스 키 없이 촬영 화면·API를 모두 연다
OPEN = os.environ.get('YS_OPEN') == '1'
# 매팅 장치. auto면 GPU(CUDA)가 있을 때 GPU, cpu면 항상 CPU
DEVICE = os.environ.get('YS_DEVICE', 'auto').lower()
# 동시에 합성하는 사진 수. 나머지는 차례로 기다린다 (한 장에 메모리 약 350MB, CPU를 나눠 쓴다)
COMPOSE_SLOTS = max(1, int(os.environ.get('YS_COMPOSE_SLOTS', 2)))

# 인물 보정(뽀샤시) 세기. 0이면 끔, 1이 기본, 1.5쯤까지 올려도 자연스럽다
BEAUTY = float(os.environ.get('YS_BEAUTY', 1.0))

# 합성 결과 크기. 프레임 사진 칸(876x660)과 같은 4:3 비율
SHOT_W, SHOT_H = 1600, 1200

# 프레임 안 사진 칸 위치 (frame1.png 1024x1536 기준, 노란 테두리 안쪽)
FRAME_HOLE = (74, 318, 951, 979)
FRAME_HOLE_RADIUS = 18
FRAME_TEXT_BOX = (90, 1000, 934, 1185)
TEXT_COLOR = '#4a3420'
DEFAULT_MESSAGE = '오늘도 함께 행복하자'

# 장소 이야기: 촬영 화면에서 배경을 고를 때마다 보여 준다. 현장에 맞게 자유롭게 고쳐 쓰세요
# look: 그 장소의 빛에 맞춘 자동 필터. sun은 원본 배경 이미지 안 해의 위치(0~1, 화면 밖이면 음수·1 초과),
#       tint는 인물에 비치는 빛 색(LAB a·b 더하기), exposure는 인물 밝기 조정,
#       rim은 해 쪽 인물 가장자리에 비치는 빛의 세기, glow는 뽀샤시 정도, preview는 PC 미리보기용 CSS 필터
BACKGROUNDS = [
    {'id': 1, 'name': '황포돛배와 영산강 노을', 'place': '영산포 나루',
     'story': '흑산도 홍어와 남도의 곡식을 싣고 영산강을 오르내리던 황포돛배. '
              '해 질 무렵 누런 돛이 노을빛으로 물드는 영산포 나루의 저녁이에요.',
     'look': {'name': '노을빛', 'sun': (0.83, 0.45), 'rim': 0.6, 'glow': 0.22, 'tint': (5, 12), 'exposure': -4, 'preview': 'brightness(0.98) saturate(1.15) sepia(0.22)'}},
    {'id': 2, 'name': '느러지 한반도 물돌이', 'place': '나주 동강면 느러지',
     'story': '강물이 크게 휘돌아 흐르며 한반도를 닮은 땅을 빚어낸 곳. '
              '전망대에 오르면 영산강이 그린 지도가 한눈에 내려다보여요.',
     'look': {'name': '한낮 햇살', 'sun': (0.5, -0.4), 'tint': (0, 2), 'exposure': 2, 'rim': 0.2, 'glow': 0.12, 'preview': 'brightness(1.06) saturate(1.12) contrast(1.04)'}},
    {'id': 3, 'name': '푸른 영산강 풍경', 'place': '나주 들녘',
     'story': '너른 나주 들판 사이로 느릿하게 흐르는 영산강. '
              '맑은 날이면 강물에 하늘과 구름이 그대로 담겨요.',
     'look': {'name': '맑은 하늘', 'sun': (0.5, -0.4), 'tint': (0, 2), 'exposure': 2, 'rim': 0.2, 'glow': 0.12, 'preview': 'brightness(1.06) saturate(1.12) contrast(1.04)'}},
    {'id': 4, 'name': '영산강 빛의 산책로', 'place': '영산강 수변',
     'story': '해가 지면 다리와 물가 산책로에 하나둘 불이 켜지고 강물 위로 빛이 길게 번져요. '
              '저녁 산책길의 반짝이는 순간이에요.',
     'look': {'name': '노을빛', 'sun': (0.87, 0.33), 'rim': 0.55, 'glow': 0.22, 'tint': (5, 12), 'exposure': -4, 'preview': 'brightness(0.98) saturate(1.15) sepia(0.22)'}},
    {'id': 5, 'name': '영산강 코스모스 정원', 'place': '영산강 둔치',
     'story': '가을바람이 불면 강변을 가득 채우는 분홍빛 코스모스. '
              '꽃 사이에 서면 누구나 가을의 주인공이 돼요.',
     'look': {'name': '꽃밭 햇살', 'sun': (0.3, -0.3), 'tint': (0, 2), 'exposure': 2, 'rim': 0.2, 'glow': 0.18, 'preview': 'brightness(1.06) saturate(1.12) contrast(1.04)'}},
    {'id': 6, 'name': '황포돛배와 영산강', 'place': '영산강 뱃길',
     'story': '푸른 산자락 아래 물살을 가르며 나아가는 황포돛배. '
              '옛 뱃사람들이 오가던 뱃길을 지금도 배를 타고 따라가 볼 수 있어요.',
     'look': {'name': '강바람 햇살', 'sun': (0.2, -0.3), 'tint': (0, 2), 'exposure': 2, 'rim': 0.2, 'glow': 0.12, 'preview': 'brightness(1.06) saturate(1.12) contrast(1.04)'}},
    {'id': 7, 'name': '영산강 양귀비 꽃밭', 'place': '영산강 둔치',
     'story': '봄이 깊어지면 강변이 붉은 양귀비로 물들어요. '
              '강 건너 도시 풍경과 꽃물결이 한 장면에 담기는 곳이에요.',
     'look': {'name': '꽃밭 햇살', 'sun': (0.5, -0.4), 'tint': (0, 2), 'exposure': 2, 'rim': 0.2, 'glow': 0.16, 'preview': 'brightness(1.06) saturate(1.12) contrast(1.04)'}},
    {'id': 8, 'name': '2026 나주영산강축제', 'place': '축제장 입구',
     'story': '강과 사람이 함께 어우러지는 나주영산강축제. '
              '오늘 우리가 여기 왔다는 걸 오래 기억하도록 축제장 입구에서 한 장 남겨요.',
     'look': {'name': '축제 햇살', 'sun': (0.5, -0.4), 'tint': (0, 2), 'exposure': 2, 'rim': 0.2, 'glow': 0.14, 'preview': 'brightness(1.06) saturate(1.12) contrast(1.04)'}},
]
BG_IDS = {b['id'] for b in BACKGROUNDS}
BG_BY_ID = {b['id']: b for b in BACKGROUNDS}


def public_base_url():
    """QR코드에 넣을 외부 접속 주소. 배포 환경 변수 → 내부 IP 순으로 찾는다."""
    for key in ('YS_PUBLIC_URL', 'RENDER_EXTERNAL_URL'):
        if os.environ.get(key):
            return os.environ[key].rstrip('/')
    host = os.environ.get('SPACE_HOST')  # Hugging Face Spaces
    if host:
        return f'https://{host}'
    return None
