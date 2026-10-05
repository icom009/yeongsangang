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
# 몸통 윤곽 보강(BiRefNet lite, MIT). 옷이 배경과 비슷한 색이면 RVM이 어깨를 반투명 점박이로 잡는데,
# 이 모델로 몸통을 꽉 채우고 머리카락 디테일은 RVM 것을 쓴다. GPU 서버용(약 0.2초), 비우면 끔
SEGMENT_NAME = os.environ.get('YS_SEGMENT_MODEL', '')
SEGMENT_MODEL = MODEL_DIR / 'birefnet_lite.onnx'
SEGMENT_URL = 'https://huggingface.co/onnx-community/BiRefNet_lite-ONNX/resolve/main/onnx/model.onnx'
# 매팅 시 내부 해상도(긴 변). RVM은 512~640쯤이 가장 좋고, 1000 넘게 올리면 오히려 경계가 뭉개진다
MATTING_SIZE = int(os.environ.get('YS_MATTING_SIZE', 640))
# 1이면 인터넷(공개 주소)에서도 부스 키 없이 촬영 화면·API를 모두 연다
OPEN = os.environ.get('YS_OPEN') == '1'
# 매팅 장치. auto면 GPU(CUDA)가 있을 때 GPU, cpu면 항상 CPU
DEVICE = os.environ.get('YS_DEVICE', 'auto').lower()
# 매팅이 쓸 GPU 메모리 상한(MB). 0이면 상한 없음. 추론마다 다 쓴 메모리를 돌려주므로 평소엔 필요 없다
# (BiRefNet 1024x1024는 잠깐 수 GB를 써서 5GB 상한으로는 켜지지도 않았다)
GPU_MEM_MB = int(os.environ.get('YS_GPU_MEM_MB') or 0)
# 동시에 합성하는 사진 수. 나머지는 차례로 기다린다 (한 장에 메모리 약 350MB, CPU를 나눠 쓴다)
COMPOSE_SLOTS = max(1, int(os.environ.get('YS_COMPOSE_SLOTS', 2)))

# 인물 보정(뽀샤시) 세기. 0이면 끔, 1이 기본, 1.5쯤까지 올려도 자연스럽다
BEAUTY = float(os.environ.get('YS_BEAUTY', 1.0))

# 생성형 AI 빛 보정(선택). 빠른 합성을 먼저 내보낸 뒤 뒤에서 'AI 버전'을 한 장 더 만든다.
# auto면 환경에 맞춰 자동: 로컬 IC-Light 서비스가 응답하면 local, 외부 API 키가 있으면 api, 둘 다 없으면 끔
AI_MODE = os.environ.get('YS_AI', 'auto').lower()
AI_URL = os.environ.get('YS_AI_URL', 'http://ic-light:8000').rstrip('/')
# 외부 엔진(Gemini 이미지 편집) 키. 넣으면 얼굴 사진이 바깥 서버로 나가므로 부스에 안내 문구가 뜬다
AI_KEY = os.environ.get('YS_AI_KEY', '')
AI_API_MODEL = os.environ.get('YS_AI_API_MODEL', 'gemini-3.1-flash-image')
AI_TIMEOUT = float(os.environ.get('YS_AI_TIMEOUT', 120))
# 기다릴 수 있는 AI 작업 수. 넘치면 그냥 건너뛴다 (촬영 흐름은 절대 기다리지 않는다)
AI_QUEUE = max(1, int(os.environ.get('YS_AI_QUEUE', 8)))
# 대기줄에서 이만큼(초) 넘게 묵은 작업은 건너뛴다 (방문객이 이미 사진을 받아 갔다)
AI_TTL = float(os.environ.get('YS_AI_TTL', 180))
# 로컬 AI는 마지막 촬영 뒤 이만큼(초) 조용해야 GPU를 쓴다. 같은 GPU를 촬영 합성과 나눠 쓰면
# 둘 다 몇 배씩 느려지기 때문이다 (네 컷 촬영 간격 3초보다 길게)
AI_IDLE = float(os.environ.get('YS_AI_IDLE', 6))
# AI에 넣는 사진 크기(긴 변). 결과에서 '빛'만 뽑아 쓰므로 작아도 거의 차이가 없고,
# GPU를 짧게 쓸수록 촬영이 덜 기다린다 (640이면 한 장 약 1.6초)
AI_SIZE = int(os.environ.get('YS_AI_SIZE', 640))

# ---------- AI 효과 버튼 (GPT 이미지 편집, 선택 기능) ----------
# 빛 보정(로컬 IC-Light)은 무료라 촬영마다 저절로 만들고, 아래 효과들은 누를 때만 GPT로 만든다(유료).
# 표정은 살리도록 지시하지만 그림 자체가 바뀌므로 얼굴이 조금 달라질 수 있다. 원본은 항상 함께 준다.
# 키가 없으면 효과 버튼이 아예 안 보인다. 넣으면 얼굴 사진이 OpenAI로 나가므로 부스에 안내 문구가 뜬다
OPENAI_KEY = (os.environ.get('YS_OPENAI_KEY') or os.environ.get('OPENAI_KEY')
              or os.environ.get('OPENAI_API_KEY') or '')
# 비용 계산용 단가(달러, 100만 토큰당). gpt-image-2 기준: 글 입력 5, 사진 입력 8, 그림 출력 30
GPT_PRICE = {'text': 5.0, 'image': 8.0, 'output': 30.0}
GPT_MODEL = os.environ.get('YS_GPT_MODEL') or 'gpt-image-2'
GPT_QUALITY = os.environ.get('YS_GPT_QUALITY') or 'medium'  # low | medium | high
GPT_SIZE = '1024x768'  # 사진과 같은 4:3. gpt-image-2는 16의 배수면 어떤 크기든 받는다
GPT_DAILY = int(os.environ.get('YS_GPT_DAILY') or 500)  # 하루 최대 생성 장수 (비용 상한, 네 컷은 4장)
GPT_PARALLEL = max(1, int(os.environ.get('YS_GPT_PARALLEL') or 4))  # 동시에 보내는 요청 수 (네 컷을 한 번에)
GPT_TIMEOUT = float(os.environ.get('YS_GPT_TIMEOUT') or 150)

_KEEP = ('Keep every person\'s face, identity, facial expression, hairstyle, skin tone and clothing '
         'exactly as they are, and keep the same number of people. ')
EFFECTS = [
    {'id': 'scene', 'name': 'AI 장면 연출', 'desc': '표정은 그대로, 이곳에 어울리는 기념사진으로',
     # 포즈는 정해 주지 않고 GPT에게 맡긴다. 배경은 effects.py가 원본으로 다시 덮어 그대로 둔다
     'keep_background': True,
     # '가족사진'이라고 하면 GPT가 없던 가족을 지어내 넣었다(혼자 찍은 아이 옆에 어른 둘). 그 말은 쓰지 않고,
     # 사진 속 사람만 쓰라고 못 박는다
     'prompt': 'Turn this into a natural, heartwarming commemorative photo of the people in this picture at '
               'this place, as if a professional photographer took it on the spot. Use only the people who are '
               'already in the photo: do not add, remove, duplicate or replace anyone. If there is only one '
               'person, keep it a photo of that one person. Freely choose natural, relaxed poses and positions '
               'for them that suit the scene. Keep each person\'s face, identity, facial expression, hairstyle, '
               'skin tone and clothing exactly as they are. Do not change the background at all: keep the '
               'scenery, sky, water, boats, buildings and colors exactly as they are. Photorealistic, lit by '
               'the same light as the scene.'},
    {'id': 'watercolor', 'name': '수채화 동화', 'desc': '부드러운 수채화 그림책처럼',
     'prompt': 'Turn this photo into a soft, hand-painted watercolor storybook illustration with gentle washes '
               'and paper texture. Keep the same composition and scenery, and keep the same people with '
               'recognizable faces, the same facial expressions, poses and clothing colors.'},
    {'id': 'webtoon', 'name': '웹툰', 'desc': '밝고 깔끔한 웹툰 그림체로',
     'prompt': 'Redraw this photo as a clean, bright Korean webtoon-style illustration with crisp line art and '
               'soft cel shading. Keep the same composition and scenery, and keep the same people with '
               'recognizable faces, the same facial expressions, poses and clothing.'},
    {'id': 'film', 'name': '필름 사진', 'desc': '90년대 필름 카메라 느낌으로',
     'prompt': 'Make this photo look like a warm vintage 35mm film photograph from the 1990s: soft film grain, '
               'gently faded colors and a faint light leak. ' + _KEEP + 'Keep the poses and the composition '
               'exactly the same.'},
]
EFFECT_IDS = [e['id'] for e in EFFECTS]
EFFECT_BY_ID = {e['id']: e for e in EFFECTS}


# 얼굴 세기(UltraFace RFB-640, MIT, 1.5MB). GPT가 없던 사람을 그려 넣었는지 확인한다 (CPU로 돈다)
FACE_MODEL = MODEL_DIR / 'ultraface_rfb640.onnx'
FACE_URL = ('https://github.com/onnx/models/raw/main/validated/vision/body_analysis/'
            'ultraface/models/version-RFB-640.onnx')


def gpt_ready():
    return bool(OPENAI_KEY)


# 관리 화면(/manage) 비밀번호. 사진 이력을 보고 다시 보내는 곳이라 현장에서 꼭 바꿔 쓰세요
# (compose가 빈 값을 넘길 수 있으므로 비어 있으면 기본값으로 되돌린다. 빈 비밀번호는 절대 두지 않는다)
MANAGE_KEY = os.environ.get('YS_MANAGE_KEY') or 'ysg2026!'

# 관리 화면에서 사진을 메일로 다시 보낼 때 쓰는 계정. 비우면 메일 보내기 단추가 꺼진다
# (Gmail이면 2단계 인증 뒤 '앱 비밀번호'를 YS_SMTP_PASS에 넣는다)
SMTP_HOST = os.environ.get('YS_SMTP_HOST', '')
SMTP_PORT = int(os.environ.get('YS_SMTP_PORT') or 587)
SMTP_USER = os.environ.get('YS_SMTP_USER', '')
SMTP_PASS = os.environ.get('YS_SMTP_PASS', '')
SMTP_FROM = os.environ.get('YS_SMTP_FROM', '') or SMTP_USER
SMTP_SECURITY = os.environ.get('YS_SMTP_SECURITY') or 'starttls'  # starttls | ssl | none


def mail_ready():
    return bool(SMTP_HOST and SMTP_FROM)


# 합성 결과 크기. 프레임 사진 칸(876x660)과 같은 4:3 비율
SHOT_W, SHOT_H = 1600, 1200

# 프레임 안 사진 칸 위치 (frame1.png 1024x1536 기준, 노란 테두리 안쪽)
FRAME_HOLE = (74, 318, 951, 979)
FRAME_HOLE_RADIUS = 18
FRAME_GAP = 10  # 4컷일 때 사진 사이 틈 (사진 칸을 2x2로 나눠도 각 칸이 4:3이다)
FRAME_TEXT_BOX = (90, 1000, 934, 1185)
TEXT_COLOR = '#4a3420'
DEFAULT_MESSAGE = '오늘도 함께 행복하자'

# 장소 이야기: 촬영 화면에서 배경을 고를 때마다 보여 준다. 현장에 맞게 자유롭게 고쳐 쓰세요
# look: 그 장소의 빛에 맞춘 자동 필터. sun은 원본 배경 이미지 안 해의 위치(0~1, 화면 밖이면 음수·1 초과),
#       tint는 인물에 비치는 빛 색(LAB a·b 더하기), exposure는 인물 밝기 조정,
#       rim은 해 쪽 인물 가장자리에 비치는 빛의 세기, glow는 뽀샤시 정도, preview는 PC 미리보기용 CSS 필터,
#       ai는 생성형 AI 빛 보정에 넘기는 장면 설명(영어, booth/ai.py)
BACKGROUNDS = [
    {'id': 1, 'name': '황포돛배와 영산강 노을', 'place': '영산포 나루',
     'story': '흑산도 홍어와 남도의 곡식을 싣고 영산강을 오르내리던 황포돛배. '
              '해 질 무렵 누런 돛이 노을빛으로 물드는 영산포 나루의 저녁이에요.',
     'look': {'name': '노을빛', 'sun': (0.83, 0.45), 'rim': 0.6, 'glow': 0.22, 'tint': (5, 12), 'exposure': -4, 'ai': 'golden hour sunset over a wide river, warm orange backlight, soft glowing rim light', 'preview': 'brightness(0.98) saturate(1.15) sepia(0.22)'}},
    {'id': 2, 'name': '느러지 한반도 물돌이', 'place': '나주 동강면 느러지',
     'story': '강물이 크게 휘돌아 흐르며 한반도를 닮은 땅을 빚어낸 곳. '
              '전망대에 오르면 영산강이 그린 지도가 한눈에 내려다보여요.',
     'look': {'name': '한낮 햇살', 'sun': (0.5, -0.4), 'tint': (0, 2), 'exposure': 2, 'rim': 0.2, 'glow': 0.12, 'ai': 'bright midday sunlight over a wide river bend, clear sky, soft natural daylight', 'preview': 'brightness(1.06) saturate(1.12) contrast(1.04)'}},
    {'id': 3, 'name': '푸른 영산강 풍경', 'place': '나주 들녘',
     'story': '너른 나주 들판 사이로 느릿하게 흐르는 영산강. '
              '맑은 날이면 강물에 하늘과 구름이 그대로 담겨요.',
     'look': {'name': '맑은 하늘', 'sun': (0.5, -0.4), 'tint': (0, 2), 'exposure': 2, 'rim': 0.2, 'glow': 0.12, 'ai': 'sunny day beside a calm wide river, clear blue sky, soft natural daylight', 'preview': 'brightness(1.06) saturate(1.12) contrast(1.04)'}},
    {'id': 4, 'name': '영산강 빛의 산책로', 'place': '영산강 수변',
     'story': '해가 지면 다리와 물가 산책로에 하나둘 불이 켜지고 강물 위로 빛이 길게 번져요. '
              '저녁 산책길의 반짝이는 순간이에요.',
     'look': {'name': '노을빛', 'sun': (0.87, 0.33), 'rim': 0.55, 'glow': 0.22, 'tint': (5, 12), 'exposure': -4, 'ai': 'evening riverside promenade, warm lamp light glowing, cool blue hour ambience', 'preview': 'brightness(0.98) saturate(1.15) sepia(0.22)'}},
    {'id': 5, 'name': '영산강 코스모스 정원', 'place': '영산강 둔치',
     'story': '가을바람이 불면 강변을 가득 채우는 분홍빛 코스모스. '
              '꽃 사이에 서면 누구나 가을의 주인공이 돼요.',
     'look': {'name': '꽃밭 햇살', 'sun': (0.3, -0.3), 'tint': (0, 2), 'exposure': 2, 'rim': 0.2, 'glow': 0.18, 'ai': 'sunny autumn flower field by a river, soft warm afternoon sunlight', 'preview': 'brightness(1.06) saturate(1.12) contrast(1.04)'}},
    {'id': 6, 'name': '황포돛배와 영산강', 'place': '영산강 뱃길',
     'story': '푸른 산자락 아래 물살을 가르며 나아가는 황포돛배. '
              '옛 뱃사람들이 오가던 뱃길을 지금도 배를 타고 따라가 볼 수 있어요.',
     'look': {'name': '강바람 햇살', 'sun': (0.2, -0.3), 'tint': (0, 2), 'exposure': 2, 'rim': 0.2, 'glow': 0.12, 'ai': 'sunny day on a river with green hills, bright natural daylight, light breeze', 'preview': 'brightness(1.06) saturate(1.12) contrast(1.04)'}},
    {'id': 7, 'name': '영산강 양귀비 꽃밭', 'place': '영산강 둔치',
     'story': '봄이 깊어지면 강변이 붉은 양귀비로 물들어요. '
              '강 건너 도시 풍경과 꽃물결이 한 장면에 담기는 곳이에요.',
     'look': {'name': '꽃밭 햇살', 'sun': (0.5, -0.4), 'tint': (0, 2), 'exposure': 2, 'rim': 0.2, 'glow': 0.16, 'ai': 'bright red poppy field by a river, clear sunny daylight', 'preview': 'brightness(1.06) saturate(1.12) contrast(1.04)'}},
    {'id': 8, 'name': '2026 나주영산강축제', 'place': '축제장 입구',
     'story': '강과 사람이 함께 어우러지는 나주영산강축제. '
              '오늘 우리가 여기 왔다는 걸 오래 기억하도록 축제장 입구에서 한 장 남겨요.',
     'look': {'name': '축제 햇살', 'sun': (0.5, -0.4), 'tint': (0, 2), 'exposure': 2, 'rim': 0.2, 'glow': 0.14, 'ai': 'sunny outdoor festival entrance, bright cheerful daylight', 'preview': 'brightness(1.06) saturate(1.12) contrast(1.04)'}},
]
BG_IDS = {b['id'] for b in BACKGROUNDS}
BG_BY_ID = {b['id']: b for b in BACKGROUNDS}


def has_foreground(bg_id):
    """배경 앞쪽 사물 마스크(backgrounds/fg_N.png)가 있는지. scripts/make_foreground.py로 만든다."""
    return (BG_DIR / f'fg_{bg_id}.png').exists()


# 비상용 노트북 서버: Cloudflare 임시 터널(trycloudflare.com)의 주소를 터널 프로그램이 알려 주는 곳.
# 주소는 터널을 켤 때마다 바뀌므로 서버가 뒤에서 주기적으로 물어 QR에 넣는다 (main.py의 _watch_tunnel)
TUNNEL_METRICS = os.environ.get('YS_TUNNEL_METRICS', '').rstrip('/')
TUNNEL_URL = None  # 마지막으로 알아낸 터널 주소 (https://xxxx.trycloudflare.com)


def public_base_url():
    """QR코드에 넣을 외부 접속 주소. 배포 환경 변수 → 임시 터널 → 내부 IP 순으로 찾는다."""
    if os.environ.get('YS_PUBLIC_URL'):
        return os.environ['YS_PUBLIC_URL'].rstrip('/')
    if TUNNEL_URL:
        return TUNNEL_URL
    if os.environ.get('RENDER_EXTERNAL_URL'):
        return os.environ['RENDER_EXTERNAL_URL'].rstrip('/')
    host = os.environ.get('SPACE_HOST')  # Hugging Face Spaces
    if host:
        return f'https://{host}'
    return None
