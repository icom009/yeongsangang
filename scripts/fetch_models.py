"""합성 모델(RVM), 실시간 미리보기용 MediaPipe 파일, 한마디 글씨체를 내려받는다. Docker 빌드와 처음 설치 때 실행.
MediaPipe를 로컬에 두면 인터넷이 없는 현장(플랜 B: 공유기만 켠 로컬 운영)에서도 미리보기가 된다."""
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booth import config  # noqa: E402
from booth.compose import _download, ensure_model  # noqa: E402

MP_VERSION = '1.0.1'  # web/live.js의 MP_VERSION과 같게
MP_CDN = f'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@{MP_VERSION}/'
MP_MODELS = 'https://storage.googleapis.com/mediapipe-models/image_segmenter/'
MP_FILES = {
    'vision_bundle.mjs': MP_CDN + 'vision_bundle.mjs',
    **{f'wasm/{n}': f'{MP_CDN}wasm/{n}' for n in (
        'vision_wasm_internal.js', 'vision_wasm_internal.wasm',
        'vision_wasm_module_internal.js', 'vision_wasm_module_internal.wasm',
        'vision_wasm_nosimd_internal.js', 'vision_wasm_nosimd_internal.wasm')},
    'models/selfie_multiclass_256x256.tflite':
        MP_MODELS + 'selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite',
    'models/selfie_segmenter.tflite':
        MP_MODELS + 'selfie_segmenter/float16/latest/selfie_segmenter.tflite',
}


def fetch_mediapipe():
    root = config.WEB_DIR / 'vendor' / 'mediapipe'
    for rel, url in MP_FILES.items():
        dst = root / rel
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + '.part')
        urllib.request.urlretrieve(url, tmp)
        tmp.rename(dst)
        print(dst)
    return root


def fetch_fonts():
    """한마디 글씨체(Google Fonts, SIL OFL). 받지 못한 글씨체는 고르기에서 빠질 뿐 부스는 그대로 돈다."""
    for f in config.FONTS:
        if 'url' not in f or f['file'].exists():
            continue
        f['file'].parent.mkdir(parents=True, exist_ok=True)
        tmp = f['file'].with_name(f['file'].name + '.part')
        try:
            urllib.request.urlretrieve(f['url'], tmp)
            tmp.rename(f['file'])
            print(f['file'])
        except Exception as e:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            print(f'글씨체를 받지 못함 ({f["id"]}): {e}')


print(ensure_model())
print(_download(config.FACE_MODEL, config.FACE_URL))  # AI 효과에서 얼굴 수 세기
print(fetch_mediapipe())
fetch_fonts()
