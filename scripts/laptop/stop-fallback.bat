@echo off
chcp 65001 >nul
rem 비상 서버를 끈다. 끄면 임시 터널 주소가 사라져, 이미 QR로 나간 주소도 더는 열리지 않는다
cd /d "%~dp0..\.."
echo.
echo  비상 서버를 끄면 지금까지 나간 QR 주소가 더는 열리지 않아요.
choice /M "  정말 끌까요"
if errorlevel 2 exit /b 0
docker compose -f docker-compose.laptop.yml --env-file .env.laptop down
echo  껐어요.
pause
