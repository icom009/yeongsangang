@echo off
chcp 65001 >nul
rem 평소 모드: 집 서버(ysg26.cloud)에 붙는 부스 화면을 연다. 노트북은 카메라와 화면 역할만 한다
call "%~dp0chrome-kiosk.bat" https://ysg26.cloud/
