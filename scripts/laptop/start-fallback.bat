@echo off
chcp 65001 >nul
rem 비상 모드: 집 서버가 안 될 때 이 노트북에서 부스 서버를 켠다 (Docker Desktop이 켜져 있어야 한다).
rem 방문객은 임시 터널 주소(https://xxxx.trycloudflare.com)로 자기 데이터를 써서 받는다
cd /d "%~dp0..\.."
if not exist ".env.laptop" copy ".env.laptop.example" ".env.laptop" >nul
echo.
echo  비상 서버를 켜는 중이에요. 처음이면 몇 분 걸려요...
docker compose -f docker-compose.laptop.yml --env-file .env.laptop up -d
if errorlevel 1 (
  echo.
  echo  켜지 못했어요. Docker Desktop이 켜져 있는지 확인해 주세요.
  pause
  exit /b 1
)
echo  방문객 받기 주소(임시 터널)를 기다리는 중이에요...
set "PUBLIC="
for /f "usebackq delims=" %%u in (`powershell -NoProfile -Command "for($i=0;$i -lt 90;$i++){try{$c=Invoke-RestMethod http://localhost:8080/api/config -TimeoutSec 3;if($c.public -like '*trycloudflare*'){$c.public;exit 0}}catch{};Start-Sleep 2};exit 1"`) do set "PUBLIC=%%u"
if not defined PUBLIC (
  echo.
  echo  받기 주소를 아직 못 받았어요. 노트북 인터넷 연결을 확인해 주세요.
  echo  부스 화면은 열지만, 방문객이 QR로 받으려면 인터넷이 있어야 해요.
  pause
) else (
  echo  받기 주소: %PUBLIC%
)
call "%~dp0chrome-kiosk.bat" http://localhost:8080/
