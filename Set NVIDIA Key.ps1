$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$credentialDirectory = Join-Path $env:LOCALAPPDATA 'AgentShield'
$credentialPath = Join-Path $credentialDirectory 'nvidia-key.dpapi'
$key = Read-Host 'NVIDIA API key' -AsSecureString
if ($key.Length -eq 0) { throw 'Enter a nonempty NVIDIA API key.' }
New-Item -ItemType Directory -Path $credentialDirectory -Force | Out-Null
ConvertFrom-SecureString -SecureString $key | Set-Content -LiteralPath $credentialPath -Encoding ascii
Write-Output 'NVIDIA API key saved for this Windows account.'
