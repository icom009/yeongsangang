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

## 다음 작업: 생성형 AI 합성 (선택 기능)

목표는 사람이 실제로 그 배경에서 찍은 것처럼 조명·그림자·색이 녹아든 **AI 버전**을 추가로 만드는 것입니다.

**요구 사항 (사용자 결정)**

- **선택 기능, 환경에 따라 자동 적용:** 집 PC GPU 서비스가 응답하면 로컬 엔진, 외부 API 키가 있고 인터넷이 되면 외부 엔진, 둘 다 없으면 꺼짐.
- **플랜 B를 절대 막지 않을 것:** 지금의 빠른 합성(필터·원본)은 그대로 먼저 주고, AI 버전은 뒤에서 만들어 휴대폰 받기 화면에 준비되면 덧붙입니다. AI가 실패하거나 느려도 촬영·QR 흐름은 멈추지 않습니다.
- **두 엔진을 같은 사진으로 비교한 뒤 결정**
  - 외부: Gemini 이미지 편집 API (`gemini-3.1-flash-image` 등)
  - 로컬: IC-Light(배경 맞춤 재조명, SD1.5 기반)를 RTX GPU 컨테이너로 따로 실행
- **알릴 것:** AI는 얼굴을 조금 바꿀 수 있으므로 원본 합성본도 함께 줍니다. 외부 엔진은 얼굴 사진이 외부로 나가므로 부스에 안내 문구를 둡니다.
