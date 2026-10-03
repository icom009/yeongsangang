# 영산강 AI 포토부스

2026 나주영산강축제 체험 부스용 웹앱입니다. 영산강 배경을 고르면 카메라 화면에서 바로 합성된 모습을 보고, 찍은 사진에 한마디를 남겨 QR코드로 휴대폰에 받아 갑니다.

## 흐름

1. **배경 고르고 찍기**: 웹캠 영상에서 사람만 분리해 고른 배경 위에 실시간으로 보여줍니다(브라우저의 MediaPipe). 타이머는 3, 5, 10초 중에서 고릅니다.
2. **합성**: 서버가 원본 사진을 RVM 매팅 모델로 다시 합성합니다. 머리카락 경계와 원본 배경 번짐을 보정하고, 색감을 맞추고, 가장자리에 빛이 감싸는 효과를 줍니다.
3. **한마디 남기기**: 프레임에 들어갈 문구를 손글씨체 미리보기로 확인합니다.
4. **사진 받기**: 완성 사진을 BGM과 함께 보여준 뒤 QR코드를 띄웁니다. 휴대폰에서는 저장하거나 공유할 수 있습니다.

## 실행

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch_models.py   # 합성 모델 받기 (약 15MB, 처음 한 번)
python main.py                   # http://localhost:8080
```

카메라는 **https 주소나 localhost에서만** 켜집니다. 부스 노트북에서는 `http://localhost:8080`으로 여세요. QR코드에는 노트북의 내부 IP가 자동으로 들어가므로, 방문객 휴대폰이 같은 Wi-Fi에 있으면 바로 열립니다.

## 환경 변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `PORT` | 8080 | 서버 포트 |
| `YS_PUBLIC_URL` | 자동 | QR코드에 넣을 외부 주소 (예: `https://booth.example.com`) |
| `YS_KEEP_HOURS` | 72 | 사진 보관 시간. 지나면 자동 삭제 |
| `YS_MATTING_SIZE` | 640 | 합성 정밀도(내부 해상도). 서버가 느리면 512로 낮추세요 |
| `YS_OUT_DIR` | `output/` | 사진 저장 위치 |

Render(`RENDER_EXTERNAL_URL`)와 Hugging Face Spaces(`SPACE_HOST`)에서는 외부 주소를 자동으로 잡습니다.

## 구조

```
main.py            FastAPI 서버 (API, 페이지)
booth/compose.py   인물 매팅·합성
booth/frame.py     프레임·문구 렌더링, QR코드
booth/storage.py   사진 파일 관리, 자동 정리
booth/config.py    배경 목록, 프레임 좌표, 설정
web/               화면 (index.html, app.js, live.js, sound.js, app.css, photo.html)
```

배경을 추가하려면 `backgrounds/bg_N.png`를 넣고 `booth/config.py`의 `BACKGROUNDS`에 이름과 설명을 추가합니다.
