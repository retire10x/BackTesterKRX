# BackTesterKRX venv 활성화 (PowerShell)
# 사용: . .\activate.ps1  또는  .\activate.ps1 후 python main.py
$VenvActivate = Join-Path $PSScriptRoot "venv\Scripts\Activate.ps1"
if (-not (Test-Path $VenvActivate)) {
    Write-Error "venv가 없습니다. 최초 1회: python -m venv venv"
    exit 1
}
. $VenvActivate
Write-Host "venv 활성화됨: $(python -c 'import sys; print(sys.executable)')"
