param([switch]$InstallBudgetBridge, [switch]$InstallActivity)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$dev = (Resolve-Path -LiteralPath (Join-Path (Split-Path -Parent $repo) 'dev')).Path
$astro = Join-Path $dev 'astrbot-4.29.4'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath (Join-Path $astro '.venv\Scripts\pythonw.exe'))) {
    throw 'FishHome AstrBot Python environment not found.'
}
$state = Join-Path $env:LOCALAPPDATA 'FishHome\manager'
New-Item -ItemType Directory -Force -Path $state | Out-Null
$launcher = Join-Path $dev 'Start-FishHome.cmd'
$original = Join-Path $dev 'Start-FishHome.before-tray.cmd'
$settings = Join-Path $state 'settings.json'
if ((Test-Path -LiteralPath $launcher) -and -not (Test-Path -LiteralPath $original)) {
    Copy-Item -LiteralPath $launcher -Destination $original
}
if (-not (Test-Path -LiteralPath $settings) -and (Test-Path -LiteralPath $original)) {
    $body = Get-Content -LiteralPath $original -Raw
    if ($body -match '\s-q\s+(\d+)') {
        @{ napcat_qq = $Matches[1] } | ConvertTo-Json | Set-Content -LiteralPath $settings -Encoding utf8
    }
}

$output = Join-Path $dev 'FishHome.exe'
& $csc /nologo /target:winexe /optimize+ /codepage:65001 /r:System.Drawing.dll /r:System.Windows.Forms.dll `
    "/out:$output" (Join-Path $PSScriptRoot 'Tray.cs')
if ($LASTEXITCODE -ne 0) { throw 'FishHome tray compilation failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Start-FishHome.cmd') -Destination $launcher -Force

if ($InstallBudgetBridge) {
    $plugin = Join-Path $astro 'data\plugins\astrbot_plugin_dsh_bridge'
    if (-not (Test-Path -LiteralPath (Join-Path $plugin 'dsh_client.py'))) {
        throw 'The installed DSH Bridge dependency is missing.'
    }
    $backup = Join-Path $state 'bridge-before-budget'
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    foreach ($file in @('main.py', 'dsh_remote.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $backup $file))) {
            Copy-Item -LiteralPath (Join-Path $plugin $file) -Destination $backup
        }
    }
    Copy-Item -LiteralPath (Join-Path $repo 'deploy\dsh_bridge\m3_qq.py') -Destination (Join-Path $plugin 'main.py') -Force
    Copy-Item -LiteralPath (Join-Path $repo 'deploy\dsh_bridge\dsh_remote.py') -Destination $plugin -Force
}
if ($InstallActivity) {
    if (Get-NetTCPConnection -State Listen -LocalPort 6185,6199 -ErrorAction SilentlyContinue) {
        throw 'Stop AstrBot before updating the activity plugin.'
    }
    $name = 'astrbot_plugin_bili_activity'
    $source = Join-Path $repo "plugin\$name"
    $plugin = Join-Path $astro "data\plugins\$name"
    $backup = Join-Path $state ('activity-before-trial-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssffffZ'))
    if (Test-Path -LiteralPath $plugin) {
        Copy-Item -LiteralPath $plugin -Destination $backup -Recurse
    } else {
        New-Item -ItemType Directory -Path $plugin | Out-Null
    }
    Get-ChildItem -LiteralPath $source -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $plugin -Force
    }
    if (Test-Path -LiteralPath $backup) { Write-Output $backup }
}
Write-Output $output
