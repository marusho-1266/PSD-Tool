# リポジトリルートで exe をビルドする（.venv 前提）
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error ".venv が見つかりません。先に: python -m venv .venv"
}
& $py -m pip install -r requirements.txt -r requirements-build.txt
& $py -m PyInstaller --clean --noconfirm PSDTool.spec
Write-Host ""
Write-Host "出力: dist\PSDResizeTool.exe"
