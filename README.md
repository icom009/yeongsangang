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

**한 컷 / 네 컷(인생네컷)**

장소 고르기 화면 위쪽에서 고릅니다. 네 컷은 서로 다른 장소 네 곳을 담은 뒤(‘아무 곳이나 네 곳’ 단추도 있습니다) 그 배경을 차례로 바꿔 가며 네 번 찍고, 한 장으로 모아 같은 프레임에 담습니다.

- 프레임은 그대로 씁니다. `frame1.png`의 사진 칸(877×661)을 2×2로 나누면 각 칸이 정확히 4:3이라 새 프레임 그림이 필요 없습니다(`booth/frame.py`의 `cells()`).
- 네 컷은 **3초마다 한 장씩 쉬지 않고** 찍습니다. 시작 전에 찍히는 방식을 안내 창으로 확인받고, 합성은 찍는 동안 뒤에서 돌다가 네 장을 다 찍으면 "잠시만 기다려 주세요"와 함께 몇 장까지 됐는지 보여 줍니다.
- 네 컷도 AI 빛 보정이 됩니다. 배경이 서로 달라 컷마다 따로 재조명하고, 네 장이 다 되면 AI 네 컷을 합쳐 휴대폰 화면에 덧붙입니다. RTX 3080 Ti에서 **한 장 약 4.3초, 네 컷 약 19초**였습니다.
- 합쳐진 뒤 낱장은 지웁니다. 남는 것은 대표 사진 하나뿐입니다.

**휴대폰에서 'AI로 바꿔 보기' (GPT, 선택·유료)**

`YS_OPENAI_KEY`를 넣으면 방문객 휴대폰 받기 화면에 **AI 장면 연출 · 수채화 동화 · 웹툰 · 필름 사진** 버튼이 생깁니다. 부스는 받기 화면 사진 위에 "휴대폰에서 AI로 바꿔 보세요" 안내만 얹고, 실제로 만드는 건 휴대폰에서 누를 때입니다.

- **AI 장면 연출**은 표정은 그대로 두고, 그 장소에 어울리는 동작으로 바꿔 줍니다. 장소마다 동작은 `config.py` 배경의 `look.act`에 있습니다.
- **비용**: gpt-image-2 중간 품질 1024×768 기준 장당 약 $0.07(약 100원)입니다. 네 컷은 컷마다 따로 그려 4장입니다. 누를 때만 만들고, 같은 사진·같은 효과는 한 번만 만들며, 하루 상한 `YS_GPT_DAILY`(기본 500장, 하루 최대 약 5만원)를 넘으면 버튼이 '오늘 마감'으로 바뀝니다.
- **외부 전송**: 키를 넣으면 처음 화면에 안내 문구가 뜨고, 휴대폰에서 처음 누를 때 한 번 더 동의를 받습니다.
- 그림 자체가 바뀌므로 얼굴이 조금 달라질 수 있습니다. 기본 사진은 항상 그대로 남습니다.

**관리 화면 `/manage`**

완성된 사진의 이력을 보고, 받기 링크·QR을 다시 꺼내고, 메일로 다시 보내고, 지우는 곳입니다.

- 주소는 `https://<도메인>/manage` (현장 노트북이면 `http://localhost:8080/manage`). 열면 비밀번호 창이 뜹니다.
- **비밀번호는 `YS_MANAGE_KEY`입니다. 기본값(`ysg2026!`)을 그대로 쓰지 말고 `.env`에서 바꾸세요.** 공개 주소로도 열리는 화면이라 비밀번호가 유일한 자물쇠입니다(5분에 10번 틀리면 잠깁니다).
- 보이는 것: 시간, 장소, 한마디, 완성 사진, 빛 필터/원본, AI 빛 보정 여부, 사진 ID. 방문객 이름·연락처는 받지도 남기지도 않습니다.
- 할 수 있는 것: QR 다시 보기, 링크 복사, 사진 저장, **메일로 다시 보내기**(받는 사람 주소를 그때 입력), 삭제(사진 파일과 기록을 함께 지우고 방문객 링크도 닫힙니다).
- 위쪽 숫자는 지금 서버가 지고 있는 일입니다: `합성 자리 1/3`(동시에 합성 중인 사진), `AI 대기 2`(빛 보정 대기줄).
- 이력은 `output/records.jsonl`에 한 줄씩 쌓이고, 사진이 보관 기간을 넘겨 지워질 때 함께 정리됩니다.
- 메일을 쓰려면 `.env`에 `YS_SMTP_*`를 채웁니다(Gmail이면 2단계 인증 뒤 '앱 비밀번호'). 비워 두면 메일 단추만 꺼지고 QR·링크는 그대로 씁니다.

**여러 명이 한꺼번에 찍을 때**

- 합성은 `YS_COMPOSE_SLOTS`장씩 차례로 처리하고 나머지는 도착 순서대로 기다립니다.
- AI 빛 보정은 따로 대기줄(`YS_AI_QUEUE`, 기본 8장)에 쌓입니다.
- **같은 GPU를 두 프로세스가 동시에 쓰면 촬영이 크게 느려집니다**(실측: IC-Light가 도는 동안 촬영 합성이 0.65초에서 17~21초로). 그래서 로컬 AI는 마지막 촬영 뒤 `YS_AI_IDLE`(기본 6초)만큼 조용할 때만 돌고, IC-Light를 부르는 순간에만 촬영 자리를 모두 잡습니다. 네 컷을 찍는 동안에는 AI가 끼어들지 않습니다.
- 서버 로그에 촬영마다 `[촬영] 받기 0.01초 · 합성 0.54초 · 저장 0.27초`처럼 단계별 시간이 찍혀서, 현장에서 느려지면 어디가 느린지 바로 보입니다. 대기줄이 넘치거나 `YS_AI_TTL`(기본 180초)보다 오래 묵은 작업은 그냥 버립니다 — 그 사진은 AI 버전 없이 넘어가고 촬영·QR 흐름은 그대로입니다.

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
| `YS_AI_SIZE` | 640 | AI에 넣는 사진 크기(긴 변). 빛만 뽑아 쓰므로 작아도 거의 같고, GPU를 짧게 씁니다 |
| `YS_AI_QUEUE` | 8 | AI 대기줄 길이. 넘치면 그 사진은 AI 버전 없이 넘어갑니다 |
| `YS_AI_TTL` | 180 | 대기줄에서 이만큼(초) 넘게 묵은 AI 작업은 건너뜁니다 (방문객이 이미 받아 갔음) |
| `YS_AI_IDLE` | 6 | 로컬 AI는 마지막 촬영 뒤 이만큼(초) 조용해야 GPU를 씁니다 |
| `YS_OPENAI_KEY` | 없음 | 휴대폰 'AI로 바꿔 보기' 버튼(GPT 이미지 편집). 비우면 버튼이 안 보입니다 |
| `YS_GPT_MODEL` | gpt-image-2 | GPT 이미지 모델 |
| `YS_GPT_QUALITY` | medium | `low`·`medium`·`high` |
| `YS_GPT_DAILY` | 500 | 하루 최대 생성 장수(비용 상한, 네 컷은 4장) |
| `YS_MANAGE_KEY` | `ysg2026!` | 관리 화면(`/manage`) 비밀번호. **현장에서 꼭 바꾸세요** |
| `YS_SMTP_HOST` | 없음 | 메일 재전송용 SMTP 서버 (예: `smtp.gmail.com`). 비우면 메일 단추가 꺼집니다 |
| `YS_SMTP_PORT` | 587 | SMTP 포트 |
| `YS_SMTP_USER` / `YS_SMTP_PASS` | 없음 | SMTP 계정과 비밀번호 (Gmail은 '앱 비밀번호') |
| `YS_SMTP_FROM` | `YS_SMTP_USER` | 보내는 사람 주소 |
| `YS_SMTP_SECURITY` | starttls | `starttls`·`ssl`·`none` |

Render(`RENDER_EXTERNAL_URL`)와 Hugging Face Spaces(`SPACE_HOST`)에서는 외부 주소를 자동으로 잡습니다.

## 구조

```
main.py            FastAPI 서버 (API, 페이지)
booth/compose.py   인물 매팅·합성
booth/frame.py     프레임·문구 렌더링, QR코드
booth/storage.py   사진 파일 관리, 자동 정리
booth/config.py    배경 목록, 프레임 좌표, 설정
booth/ai.py        생성형 AI 빛 보정 (선택, 뒤에서 돈다)
booth/records.py   완성 사진 이력 (output/records.jsonl)
booth/mail.py      관리 화면의 메일 재전송 (선택)
booth/effects.py   휴대폰 'AI로 바꿔 보기' 버튼, GPT 이미지 편집 (선택·유료)
services/ic-light/ IC-Light 재조명 서비스 (GPU 컨테이너, 선택)
web/               화면 (index.html, app.js, live.js, sound.js, app.css, photo.html, manage.html)
```

배경을 추가하려면 `backgrounds/bg_N.png`를 넣고 `booth/config.py`의 `BACKGROUNDS`에 이름·장소·이야기와 `look`(해 위치, 빛 색 등)을 추가합니다.
