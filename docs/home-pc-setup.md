# 집 PC(Windows + NVIDIA) 설치 안내

맥북에서 만든 포토부스를 집 PC로 옮겨 Docker로 돌리는 순서입니다.
명령은 **Ubuntu(WSL) 터미널**에서 실행합니다. 맥과 같은 명령을 그대로 쓸 수 있습니다.

## 1. 준비 (한 번만)

1. **Windows 업데이트**: Windows 11 또는 Windows 10 22H2 이상.
2. **NVIDIA 드라이버**: nvidia.com에서 최신 Game Ready(또는 Studio) 드라이버를 설치합니다.
   WSL 안에는 리눅스용 드라이버를 따로 설치하지 않습니다. Windows 드라이버가 WSL과 Docker에도 GPU를 넘겨줍니다.
3. **WSL2 + Ubuntu**: PowerShell을 *관리자 권한*으로 열고 아래를 실행한 뒤 재부팅합니다.
   ```powershell
   wsl --install
   ```
   재부팅 후 Ubuntu 창이 뜨면 사용자 이름과 비밀번호를 정합니다.
4. **Docker Desktop for Windows** 설치. 설정에서 다음을 켭니다.
   - General → *Use the WSL 2 based engine*, *Start Docker Desktop when you sign in*
   - Resources → WSL integration → *Ubuntu* 켜기

## 2. 확인

Ubuntu 터미널에서 실행합니다.

```bash
docker run --rm hello-world                                                # Docker 동작
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi  # GPU가 Docker 안에서 보이는지
```

두 번째 명령에서 그래픽카드 이름과 메모리 표가 나오면 됩니다.

## 3. 코드 받기와 실행

```bash
sudo apt update && sudo apt install -y git
cd ~ && git clone -b upgrade/v2 https://github.com/icom009/yeongsangang.git
cd yeongsangang

cp .env.local.example .env.local
# .env.local의 YS_PUBLIC_URL을 이 PC의 내부 IP로 고칩니다. Windows PowerShell에서 ipconfig로 확인 (예: http://192.168.0.20:8080)
# 8코어 이상 CPU면 YS_COMPOSE_SLOTS=3 을 추가하면 더 빠릅니다

docker compose --env-file .env.local up -d --build   # 처음 빌드는 몇 분 걸립니다
```

- **부스 화면:** PC 브라우저에서 http://localhost:8080
- **같은 Wi-Fi 휴대폰:** QR로 받기 화면이 열리는지 확인합니다. 안 열리면 Windows 방화벽에서 8080 포트를 허용합니다.
- 코드는 WSL 안(`~/yeongsangang`)에 두는 편이 Windows 폴더(`/mnt/c/...`)보다 빌드와 파일 접근이 빠릅니다.

## 4. 행사용 설정

- **전원:** 설정 → 전원에서 절전·화면 끄기를 '안 함'으로 둡니다.
- **Windows Update:** 행사 기간에는 업데이트를 일시 중지합니다.
- **자동 시작:** Docker Desktop이 로그인할 때 시작되면 `restart: unless-stopped`로 부스도 자동으로 다시 켜집니다.
- **인터넷 공개(플랜 A):** README의 "축제 현장 배포"를 따라 `.env`에 도메인·터널 토큰·부스 키를 넣고 아래를 실행합니다.
  ```bash
  docker compose --profile online up -d
  ```

## 5. Claude Code로 이어서 작업하기

Ubuntu 터미널에서 Claude Code를 설치하고 `~/yeongsangang`에서 실행하면, 저장소의 `CLAUDE.md`를 읽고 이어서 작업합니다.

## 생성형 AI 빛 보정 (선택 기능, 만들어 둠)

사람이 실제로 그 배경에서 찍은 것처럼 조명·그림자·색이 녹아든 **AI 버전**을 한 장 더 만듭니다.
빠른 합성(필터·원본)을 먼저 내보낸 뒤 뒤에서 돌고, 준비되면 방문객 휴대폰 화면에 '기본 / AI 빛 보정' 고르기가 생깁니다.
촬영·QR 흐름은 절대 기다리지 않습니다.

```bash
# 집 GPU 서버에서 IC-Light 컨테이너까지 함께 띄우기
docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.ai.yml --profile web up -d --build
docker compose logs -f ic-light   # '준비 완료'가 뜨면 부스가 저절로 붙습니다 (모델 약 6GB, 처음 한 번만)
```

- **자동 적용:** `YS_AI=auto`면 로컬 IC-Light가 응답할 때 로컬, 아니면 `.env`의 `YS_AI_KEY`가 있을 때 외부 API, 둘 다 없으면 끕니다. 현장 노트북(플랜 B)은 꺼진 채로 돕니다.
- **얼굴은 그대로:** AI 결과에서 밝기·색 '비율'만 가져와 인물에 곱합니다. 얼굴 생김새와 디테일, 배경 풍경은 원본 그대로이고, 인물 주변에 생긴 그림자만 받습니다.
- **외부 엔진 안내:** `YS_AI_KEY`를 넣으면 얼굴 사진이 바깥으로 나가므로 처음 화면에 안내 문구가 자동으로 뜹니다.
- **남은 일:** 같은 사진으로 로컬 IC-Light와 외부 API(Gemini 이미지 편집)를 비교해 축제에서 쓸 쪽을 고릅니다.
  외부 엔진만 써 보려면 `YS_AI=api YS_AI_KEY=<키>`로 띄우면 됩니다.
