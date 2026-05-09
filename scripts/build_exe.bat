@echo off
setlocal
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo [.venv が見つかりません] 先に: python -m venv .venv
  exit /b 1
)
call .venv\Scripts\activate.bat
pip install -r requirements.txt -r requirements-build.txt
pyinstaller --clean --noconfirm PSDTool.spec
echo.
echo 出力: dist\PSDResizeTool.exe
endlocal
