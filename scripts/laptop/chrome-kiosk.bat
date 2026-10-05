@echo off
rem 부스 전용 크롬 프로필로 전체 화면(kiosk)을 연다. 이미 켜 둔 크롬이 있어도 전체 화면으로 뜬다.
rem 카메라 허용은 이 프로필에 한 번만 하면 기억한다. 끝내기: Alt+F4
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
  echo  크롬을 찾지 못했어요. 크롬을 설치해 주세요.
  pause
  exit /b 1
)
start "" "%CHROME%" --kiosk --user-data-dir="%LocalAppData%\ysg-booth-chrome" --autoplay-policy=no-user-gesture-required --no-first-run --disable-features=Translate %1
