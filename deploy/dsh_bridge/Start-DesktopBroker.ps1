param([Parameter(Mandatory=$true)][string]$Python)

$ErrorActionPreference = 'Stop'
if ($env:USERNAME -ine 'FishHomeDesktop') { throw 'Log in as the FishHomeDesktop standard account.' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Provide a Python executable readable by this account.' }
$state = Join-Path $env:LOCALAPPDATA 'FishHome\desktop'
$null = New-Item -Path $state -ItemType Directory -Force
$tokenFile = Join-Path $state 'broker-token'
if (-not (Test-Path -LiteralPath $tokenFile)) {
    $bytes = [byte[]]::new(32)
    $crypto = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $crypto.GetBytes($bytes) } finally { $crypto.Dispose() }
    $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
    [IO.File]::WriteAllText($tokenFile, $token, [Text.Encoding]::ASCII)
}
Write-Host 'Pairing token file (copy its contents in private):' $tokenFile
Write-Host 'Keep this window running while testing the dedicated desktop.'
& $Python -X utf8 -B (Join-Path $PSScriptRoot 'windows_desktop.py') --workspace (Join-Path $state 'workspace') --token-file $tokenFile
exit $LASTEXITCODE
