# 영산강 AI 포토부스

2026 나주영산강축제 체험 부스용 웹앱입니다. 영산강 배경을 고르면 카메라 화면에서 바로 합성된 모습을 보고, 찍은 사진에 한마디를 남겨 QR코드로 휴대폰에 받아 갑니다.

## 흐름

1. **배경 고르고 찍기**: 웹캠 영상에서 사람만 분리해 고른 배경 위에 실시간으로 보여줍니다(브라우저의 MediaPipe). 타이머는 3, 5, 10초 중에서 고릅니다.
2. **합성**: 서버가 원본 사진을 RVM 매팅 모델로 다시 합성합니다. 머리카락 경계와 원본 배경 번짐을 보정하고, 색감을 맞추고, 가장자리에 빛이 감싸는 효과를 줍니다. 여기에 고른 장소의 빛(노을빛·한낮 햇살 등)에 맞춰 인물의 톤·빛 색·해 쪽 역광을 맞추고 피부를 화사하게 다듬은 필터 사진을 함께 만들어, 찍은 뒤 '필터'와 '원본' 중에서 고릅니다. 배경은 선명한 그대로 둡니다. 사람을 찾지 못하면 멈추지 않고 찍은 사진 그대로 담습니다.
3. **한마디 남기기**: 프레임에 들어갈 문구를 손글씨체 미리보기로 확인합니다.
4. **사진 받기**: 완성 사진을 BGM과 함께 보여준 뒤 QR코드를 띄웁니다. 휴대폰에서는 저장하거나 공유할 수 있습니다.

## 실행

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch_models.py   # 합성 모델·미리보기 파일 받기 (약 70MB, 처음 한 번)
python main.py                   # http://localhost:8080
```

카메라는 **https 주소나 localhost에서만** 켜집니다. 부스 노트북에서는 `http://localhost:8080`으로 여세요. QR코드에는 노트북의 내부 IP가 자동으로 들어가므로, 방문객 휴대폰이 같은 Wi-Fi에 있으면 바로 열립니다.

### 휴대폰 카메라로 쓰기

휴대폰 브라우저는 https 주소에서만 카메라를 켜 줍니다. 같은 Wi-Fi에서 쓸 때는 https 모드로 실행하세요.

```bash
YS_HTTPS=1 python main.py        # 화면에 나오는 https://<내부IP>:8443 을 휴대폰에서 열기
```

자체 서명 인증서라서 처음 접속할 때 "안전하지 않음" 경고가 나옵니다. iPhone은 '세부사항 보기 → 이 웹 사이트 방문', 안드로이드는 '고급 → 계속'을 누르면 됩니다. 촬영 화면의 '카메라 바꾸기'로 전면과 후면 카메라를 전환합니다. 전면은 거울처럼, 후면은 보이는 그대로 찍힙니다.

경고 없이 쓰려면 Hugging Face Spaces 같은 https 서버에 배포하거나 Cloudflare Tunnel로 주소를 여세요.

## 축제 현장 배포 (Docker)

방문객은 휴대폰 LTE로 QR을 열기 때문에 평소에는 **인터넷에서 열리는 https 주소**가 필요합니다. 서버 상황이 나쁠 때를 대비해 같은 이미지로 현장 로컬 운영(플랜 B)도 준비해 둡니다. 실행 방법은 `docker-compose.yml` 맨 위 주석에 있습니다.

**플랜 A-1: 집 컴퓨터 서버 + Cloudflare Tunnel**

```bash
cp .env.example .env                              # 도메인, TUNNEL_TOKEN, YS_BOOTH_KEY
docker compose --profile online up -d --build
```

- **부스 기기:** `https://<도메인>/?key=<YS_BOOTH_KEY>`를 한 번 열면 쿠키로 기억해서, 그 기기는 계속 촬영 화면을 쓸 수 있습니다.
- **방문객:** QR로 `/p/<ID>` 받기 화면만 열 수 있습니다.
- **https:** Cloudflare Tunnel이 인증서를 맡고, 공유기 포트포워딩이 필요 없습니다. Cloudflare에 연결한 도메인이 있어야 주소가 바뀌지 않습니다. 임시 주소는 다시 켤 때마다 바뀌어서 이미 나눠 준 QR이 열리지 않게 됩니다.

**플랜 A-2: 집 컴퓨터 서버 + 공유기 포트포워딩 + Caddy**

```bash
cp .env.example .env                              # YS_PUBLIC_URL, YS_DOMAIN, YS_BOOTH_KEY
docker compose --profile web up -d --build
```

- **공유기:** TCP 80·443과 UDP 443을 이 컴퓨터의 내부 IP로 포워딩하고, 내부 IP는 DHCP 예약으로 고정하세요. 도메인 A 레코드는 집 공인 IP로 둡니다(유동 IP면 DDNS 필요).
- **https:** Caddy가 Let's Encrypt 인증서를 자동으로 받고 갱신합니다. 인증서는 `caddy_data` 볼륨에 남습니다.
- **로봇 막기:** Caddy가 robots.txt와 `X-Robots-Tag`로 검색 수집을 막고, 크롤러·스크립트 도구·빈 User-Agent는 403, 취약점 스캐너가 찾는 주소(`*.php`, `/.env` 등)는 404로 돌려보냅니다(`Caddyfile`).
- **접근 제한:** Caddy는 `YS_DOMAIN`으로 온 요청만 부스에 넘깁니다. Host가 공개 주소와 같으므로 `guard_public`이 플랜 A-1과 똑같이 방문객 화면만 엽니다.

**GPU 서버 (NVIDIA)**

`docker-compose.gpu.yml`을 덧붙이면 `onnxruntime-gpu`와 `resnet50` 매팅 모델로 빌드하고 GPU를 붙입니다. `.env`에 `COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml`을 넣어 두면 `-f` 없이 그대로 쓸 수 있습니다. GPU가 없는 현장 노트북은 이 파일 없이 CPU 이미지를 씁니다.

**생성형 AI 빛 보정 (선택)**

빠른 합성을 먼저 내보낸 뒤, 뒤에서 'AI 버전'을 한 장 더 만들어 방문객 휴대폰 화면에 덧붙입니다. 촬영·QR 흐름은 이것을 절대 기다리지 않고, 실패하거나 느리면 없는 셈 칩니다.

```bash
# 집 GPU 서버: IC-Light 컨테이너를 함께 띄운다 (.env의 COMPOSE_FILE에 더해 두어도 됩니다)
docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.ai.yml --profile web up -d --build
```

- **엔진은 환경에 따라 자동으로 고릅니다**(`YS_AI=auto`): 로컬 IC-Light 서비스가 응답하면 그것, 아니면 `YS_AI_KEY`가 있을 때 외부 API, 둘 다 없으면 끕니다. 현장 노트북(플랜 B)은 그냥 꺼진 채로 돌아갑니다.
- **얼굴은 바뀌지 않습니다.** AI 결과에서 밝기·색 '비율'만 뽑아 인물에 곱하므로(`booth/ai.py`의 `apply_light`) 얼굴 생김새와 디테일은 원본 그대로입니다. 배경은 인물 주변에 생긴 그림자만 받고 풍경은 그대로 둡니다.
- **IC-Light 컨테이너**는 처음 켤 때 모델 약 6GB를 내려받습니다(`ai_cache` 볼륨). 준비되기 전에는 `/health`가 503이라 부스는 AI 없이 돌다가, 준비되면 저절로 붙습니다.
- **외부 API**를 쓰면 얼굴 사진이 바깥 서버로 나가므로 처음 화면에 안내 문구가 자동으로 뜹니다.

**플랜 B: 현장 노트북 + 공유기 (인터넷 없이)**

```bash
cp .env.local.example .env.local                  # 노트북의 공유기 내부 IP
docker compose --env-file .env.local up -d        # 부스 화면: http://localhost:8080
```

- **이미지:** 인터넷이 되는 곳에서 **미리 빌드**해 두세요. 합성 모델과 실시간 미리보기(MediaPipe) 파일을 빌드할 때 이미지 안에 받아 두므로, 현장에서는 인터넷 없이 돌아갑니다.
- **방문객:** 같은 공유기 Wi-Fi에 붙어 QR을 열어야 해서, 현장에서 받아 가야 합니다. 공유기에서 노트북 IP를 고정(DHCP 예약)해 두세요.

**사진 주고받기와 파일 관리**

- **부스 → 서버:** 촬영 원본 JPEG을 `POST /api/shots`로 보냅니다.
- **서버 → 방문객:** `/p/<ID>` 화면에서 받습니다.
- **접근 제한:** 공개 주소로 들어온 요청은 `/p/`, `/media/`, `/static/`, `/font/`만 열고 나머지는 부스 키가 있어야 합니다(`main.py`의 `guard_public`).
- **저장:** `output/`에 `<ID>_shot.jpg`(고른 사진)와 `<ID>_final.jpg`(프레임 사진)가 남고, 고르지 않은 쪽은 완성 직후 지웁니다. AI 빛 보정을 켜면 `<ID>_ai.jpg`·`<ID>_aifinal.jpg`도 함께 남습니다.
- **보관:** `YS_KEEP_HOURS`(compose 기본 7일)가 지나면 자동으로 지웁니다.
- **ID:** 추측할 수 없는 무작위 16자이고 목록 보기 기능은 없습니다.
- **용량:** 방문객 한 명당 약 1.5MB입니다.
- **동시 접속:**
  - 합성은 `YS_COMPOSE_SLOTS`장씩 차례로 만들어서, 여러 부스가 한꺼번에 찍어도 메모리가 늘지 않습니다.
  - 4코어에서 16장을 한꺼번에 찍으면 첫 장은 1.8초, 마지막은 16초, 메모리는 최대 1.2GB였습니다.
  - 방문객 300명이 동시에 사진을 받아도 2초 안에 끝났습니다. 실제로는 서버 쪽 인터넷 업로드 속도가 한계입니다(사진 한 장 약 0.7MB).
- **행사 기간 체크:** 절전·화면 꺼짐 끄기, OS 자동 업데이트 재부팅 미루기. `restart: unless-stopped`로 Docker가 켜지면 자동 시작됩니다.

## 환경 변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `PORT` | 8080 (https 모드는 8443) | 서버 포트 |
| `YS_HTTPS` | 꺼짐 | `1`이면 자체 서명 인증서로 https 실행 (휴대폰 카메라용) |
| `YS_PUBLIC_URL` | 자동 | QR코드에 넣을 외부 주소 (예: `https://booth.example.com`) |
| `YS_BOOTH_KEY` | 없음 | 공개 주소에서 부스 화면을 열 때 쓰는 열쇠 (`/?key=값`으로 한 번 열기) |
| `YS_OPEN` | 꺼짐 | `1`이면 공개 주소에서도 부스 키 없이 촬영 화면과 API를 모두 엽니다 |
| `YS_COMPOSE_SLOTS` | 2 | 동시에 합성하는 사진 수. 나머지는 차례로 기다립니다. 한 장에 메모리 약 350MB. 코어가 8개 이상이면 3~4 |
| `YS_KEEP_HOURS` | 72 | 사진 보관 시간. 지나면 자동 삭제 |
| `YS_MATTING_SIZE` | 640 | 합성 정밀도(내부 해상도). 서버가 느리면 512로 낮추세요 |
| `YS_DEVICE` | auto | 매팅 장치. `auto`면 GPU(CUDA)가 있을 때 GPU, `cpu`면 항상 CPU |
| `YS_MATTING_MODEL` | mobilenetv3 | `resnet50`은 머리카락 경계가 더 섬세합니다(GPU 서버용, 이미지 빌드 때 받아 둠) |
| `YS_SEGMENT_MODEL` | 없음 | `birefnet`이면 몸통 윤곽을 BiRefNet으로 보강합니다(어깨가 반투명하게 비는 문제). GPU에서만 켜집니다 |
| `YS_BEAUTY` | 1 | 장소 빛 필터·인물 보정 세기. `0`이면 끄고, 더 진하게는 `1.3` 정도 |
| `YS_OUT_DIR` | `output/` | 사진 저장 위치 |
| `YS_AI` | auto | 생성형 AI 빛 보정. `auto`면 로컬 IC-Light → 외부 API 키 → 끔 순으로 자동, `off`면 끔, `local`·`api`는 고정 |
| `YS_AI_URL` | `http://ic-light:8000` | 로컬 IC-Light 서비스 주소 |
| `YS_AI_KEY` | 없음 | 외부 이미지 편집 API 키 (넣으면 얼굴 사진이 바깥으로 나가고, 부스에 안내 문구가 뜹니다) |
| `YS_AI_API_MODEL` | gemini-3.1-flash-image | 외부 엔진 모델 이름 |
| `YS_AI_SIZE` | 1024 | AI에 넣는 사진 크기(긴 변) |
| `YS_AI_QUEUE` | 3 | AI 대기줄 길이. 넘치면 그 사진은 AI 버전 없이 넘어갑니다 |

Render(`RENDER_EXTERNAL_URL`)와 Hugging Face Spaces(`SPACE_HOST`)에서는 외부 주소를 자동으로 잡습니다.

## 구조

```
main.py            FastAPI 서버 (API, 페이지)
booth/compose.py   인물 매팅·합성
booth/frame.py     프레임·문구 렌더링, QR코드
booth/storage.py   사진 파일 관리, 자동 정리
booth/config.py    배경 목록, 프레임 좌표, 설정
booth/ai.py        생성형 AI 빛 보정 (선택, 뒤에서 돈다)
services/ic-light/ IC-Light 재조명 서비스 (GPU 컨테이너, 선택)
web/               화면 (index.html, app.js, live.js, sound.js, app.css, photo.html)
```

배경을 추가하려면 `backgrounds/bg_N.png`를 넣고 `booth/config.py`의 `BACKGROUNDS`에 이름·장소·이야기와 `look`(해 위치, 빛 색 등)을 추가합니다.
