param([int]$Port = 8765)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$credential = Join-Path $env:LOCALAPPDATA 'AgentShield\nvidia-key.dpapi'
if (-not $env:NVIDIA_API_KEY -and (Test-Path -LiteralPath $credential)) {
    Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
    $key = Get-Content -Raw -LiteralPath $credential
    $secureKey = ConvertTo-SecureString $key.Trim()
    $env:NVIDIA_API_KEY = [System.Net.NetworkCredential]::new('', $secureKey).Password
    $secureKey.Dispose()
}
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $python -c "import importlib.util as m, sys; sys.exit(not all(m.find_spec(name) for name in ('promptscreen', 'pypdf')))"
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $python -B app.py --port $Port
exit $LASTEXITCODE
