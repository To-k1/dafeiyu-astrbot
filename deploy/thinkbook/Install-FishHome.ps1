param([switch]$Plan, [switch]$SkipEmbedding)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = $PSScriptRoot
$repo = Join-Path $root 'repo'
$extras = Join-Path $root 'extras'
$dev = Join-Path $root 'dev'
$tools = Join-Path $dev 'tools'
$cache = Join-Path $root 'downloads'
$astro = Join-Path $dev 'astrbot-4.29.4'
$phone = Join-Path $dev 'PhoneMCP'
$dsh = Join-Path $dev 'dsh-0.2.2-rc.1'
$ollama = Join-Path $dev 'ollama-0.40.15'
$napcat = Join-Path $dev 'napcat-4.18.38'
$ownedInstall = Join-Path $dev '.thinkbook-setup'
$pythonProbe = 'import ensurepip, sys, venv; sys.exit(not (sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32))'

$assets = [ordered]@{
    'python-3.12.10-windows-x64.tar.gz' = 'https://github.com/astral-sh/python-build-standalone/releases/download/20250409/cpython-3.12.10%2B20250409-x86_64-pc-windows-msvc-install_only.tar.gz'
    'MinGit-2.54.0-64-bit.zip' = 'https://github.com/git-for-windows/git/releases/download/v2.54.0.windows.1/MinGit-2.54.0-64-bit.zip'
    'node-v24.21.0-win-x64.zip' = 'https://nodejs.org/dist/v24.21.0/node-v24.21.0-win-x64.zip'
    'NapCat.Shell.Windows.Node.zip' = 'https://github.com/NapNeko/NapCatQQ/releases/download/v4.18.38/NapCat.Shell.Windows.Node.zip'
    'ollama-windows-amd64.zip' = 'https://github.com/ollama/ollama/releases/download/v0.40.15/ollama-windows-amd64.zip'
}

function Require-File([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Required file missing: $Path" }
}

function Run([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed (exit $LASTEXITCODE)." }
}

function Get-Asset([string]$Name, [switch]$Refresh) {
    $file = Join-Path $cache $Name
    if ($Refresh -or -not (Test-Path -LiteralPath $file -PathType Leaf)) {
        Write-Host "Downloading $Name"
        $partial = "$file.$([guid]::NewGuid().ToString('N')).partial"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $assets[$Name] -OutFile $partial -TimeoutSec 1800
            Move-Item -LiteralPath $partial -Destination $file -Force
        } finally {
            if (Test-Path -LiteralPath $partial -PathType Leaf) { Remove-Item -LiteralPath $partial }
        }
    }
    return $file
}

function Move-Incomplete([string]$Directory) {
    if (-not (Test-Path -LiteralPath $Directory)) { return }
    $item = Get-Item -LiteralPath $Directory -Force
    $devPrefix = [IO.Path]::GetFullPath($dev).TrimEnd('\') + '\'
    if (-not $item.FullName.StartsWith($devPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Cannot repair this directory automatically: $Directory"
    }
    Rename-Item -LiteralPath $item.FullName -NewName ($item.Name + '.incomplete-' + [guid]::NewGuid().ToString('N'))
}

function Unpack([string]$Archive, [string]$Target, [string]$Marker, [string]$Wrapper = '') {
    if (Test-Path -LiteralPath (Join-Path $Target $Marker) -PathType Leaf) { return }
    $staging = Join-Path (Split-Path -Parent $Target) ('.unpack-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    if ($Archive.EndsWith('.tar.gz')) {
        Run (Join-Path $env:SystemRoot 'System32\tar.exe') @('-xzf', $Archive, '-C', $staging)
    } else {
        Expand-Archive -LiteralPath $Archive -DestinationPath $staging
    }
    $source = $staging
    if ($Wrapper) { $source = Join-Path $staging $Wrapper }
    if (-not (Test-Path -LiteralPath (Join-Path $source $Marker) -PathType Leaf) -and -not $Wrapper) {
        $entries = @(Get-ChildItem -LiteralPath $staging -Directory)
        if ($entries.Count -eq 1) { $source = $entries[0].FullName }
    }
    Require-File (Join-Path $source $Marker)
    Move-Incomplete $Target
    Move-Item -LiteralPath $source -Destination $Target
    if ($source -ne $staging -and -not (Get-ChildItem -LiteralPath $staging -Force)) {
        Remove-Item -LiteralPath $staging
    }
}

function Install-Asset([string]$Name, [string]$Target, [string]$Marker, [string]$Wrapper = '') {
    if (Test-Path -LiteralPath (Join-Path $Target $Marker) -PathType Leaf) { return }
    $bundled = Join-Path $extras $Name
    if (Test-Path -LiteralPath $bundled -PathType Leaf) {
        Write-Host "Using bundled $Name"
        Unpack $bundled $Target $Marker $Wrapper
        return
    }
    try {
        $archive = Get-Asset $Name
        Unpack $archive $Target $Marker $Wrapper
    } catch {
        Write-Warning "Could not use $Name. Downloading a fresh copy once."
        $archive = Get-Asset $Name -Refresh
        Unpack $archive $Target $Marker $Wrapper
    }
}

function Test-Python([string]$Exe) {
    if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) { return $false }
    try {
        $null = & $Exe -c $pythonProbe 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Prepare-Venv([string]$Directory) {
    $exe = Join-Path $Directory 'Scripts\python.exe'
    $marker = Join-Path $Directory '.fishhome-ready'
    if ((Test-Path -LiteralPath $marker -PathType Leaf) -and (Test-Path -LiteralPath $exe -PathType Leaf)) {
        try {
            $null = & $exe -m pip --version 2>$null
            if ($LASTEXITCODE -eq 0) { return }
        } catch {
            Write-Host "Repairing $Directory"
        }
    }
    Move-Incomplete $Directory
    Run $basePython @('-m', 'venv', $Directory)
}

function Apply-Patch([string]$Directory, [string]$Patch) {
    Require-File $Patch
    $parameters = @{
        FilePath = $git; ArgumentList = @('apply','--reverse','--check',('"' + $Patch + '"'))
        WorkingDirectory = $Directory; WindowStyle = 'Hidden'; PassThru = $true; Wait = $true
    }
    if ((Start-Process @parameters).ExitCode -eq 0) { return }
    Run $git @('-C', $Directory, 'apply', '--check', $Patch)
    Run $git @('-C', $Directory, 'apply', $Patch)
}

foreach ($relative in @('repo\deploy\desktop_manager\install.ps1','repo\persona\fish-home-v2.3.1.txt',
        'extras\PhoneMCP-v0.5.0-source.zip','extras\PhoneMCP-Windows.patch','extras\LivingMemory-2.9.1.zip',
        'extras\DSH-Bridge-1.0.0.zip','extras\Start-DSH-M3.cmd','extras\m5-file-only.yml',
        'extras\dsh\package.json','extras\dsh\package-lock.json')) {
    Require-File (Join-Path $root $relative)
}
if ($Plan) {
    [pscustomobject]@{ root = $root; downloads = @($assets.Keys); memory_migration = 'none'
        starts_qq = $false; starts_services = $false; pulls_embedding = -not $SkipEmbedding } | ConvertTo-Json
    exit 0
}
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'Use 64-bit PowerShell on Windows x64.' }
if (Test-Path -LiteralPath (Join-Path $astro 'data\cmd_config.json')) {
    throw 'This installation already has AstrBot settings. Keep it, or unpack into a fresh folder.'
}
if ((Test-Path -LiteralPath $dev) -and -not (Test-Path -LiteralPath $ownedInstall)) {
    throw 'This folder already contains a dev installation. Unpack into a fresh folder.'
}
if (Get-NetTCPConnection -State Listen -LocalPort 6185,6199 -ErrorAction SilentlyContinue) {
    throw 'An AstrBot service is using port 6185 or 6199 on this computer.'
}
New-Item -ItemType Directory -Force -Path $dev, $tools, $cache | Out-Null
Set-Content -LiteralPath $ownedInstall -Value 'ThinkBook migration setup'

Write-Host '[1/6] Python, Git and Node.js'
$pythonRoot = Join-Path $tools 'python-3.12.10-portable'
$basePython = Join-Path $pythonRoot 'python.exe'
if (-not (Test-Python $basePython)) {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $existing = & $launcher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $existing -and (Test-Python $existing)) {
                $basePython = $existing
            }
        } catch {
            Write-Host 'Using the bundled Python installation step.'
        }
    }
}
if (-not (Test-Python $basePython)) {
    Move-Incomplete $pythonRoot
    Install-Asset 'python-3.12.10-windows-x64.tar.gz' $pythonRoot 'python.exe' 'python'
}
if (-not (Test-Python $basePython)) {
    Write-Host "Python failed its startup check at $basePython"
    Require-File $basePython
    & $basePython -c $pythonProbe
    throw "The Python runtime could not start or is missing venv/ensurepip (exit $LASTEXITCODE)."
}
$gitRoot = Join-Path $tools 'git'
$git = Join-Path $gitRoot 'cmd\git.exe'
Install-Asset 'MinGit-2.54.0-64-bit.zip' $gitRoot 'cmd\git.exe'
$node = Join-Path $dsh 'runtime'
Install-Asset 'node-v24.21.0-win-x64.zip' $node 'node.exe' 'node-v24.21.0-win-x64'
$env:Path = (Join-Path $gitRoot 'cmd') + ';' + $node + ';' + $env:Path
$env:PYTHONUTF8 = '1'
$env:PIP_INDEX_URL = 'https://pypi.org/simple'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PIP_NO_CACHE_DIR = '1'

Write-Host '[2/6] AstrBot and plugins'
$py = Join-Path $astro '.venv\Scripts\python.exe'
Prepare-Venv (Join-Path $astro '.venv')
Run $py @('-m','pip','install','astrbot==4.29.4','aiohttp==3.14.5','dashscope==2.0.4','mcp==1.30.0',
    'pillow==12.3.0','faiss-cpu==1.15.1','psutil==7.1.3','pywin32==312','qrcode==8.2','tzdata','imageio-ffmpeg')
$plugins = Join-Path $astro 'data\plugins'
New-Item -ItemType Directory -Force -Path $plugins | Out-Null
$memory = Join-Path $plugins 'astrbot_plugin_livingmemory'
Unpack (Join-Path $extras 'LivingMemory-2.9.1.zip') $memory 'main.py'
Run $py @('-m','pip','install','-r',(Join-Path $memory 'requirements.txt'))
Run $py @('-m','pip','check')
Unpack (Join-Path $extras 'DSH-Bridge-1.0.0.zip') (Join-Path $plugins 'astrbot_plugin_dsh_bridge') 'dsh_client.py'
$remoteAdapter = Join-Path $plugins 'astrbot_plugin_dsh_bridge\dsh_remote.py'
if (-not (Test-Path -LiteralPath $remoteAdapter)) {
    Copy-Item -LiteralPath (Join-Path $repo 'deploy\dsh_bridge\dsh_remote.py') -Destination $remoteAdapter
}
foreach ($name in @('astrbot_plugin_face_convert','astrbot_plugin_schedule_awareness')) {
    $target = Join-Path $plugins $name
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $repo "plugin\$name") -File | Copy-Item -Destination $target -Force
}
$patches = Join-Path $repo 'deploy\patches'
$site = Join-Path $astro '.venv\Lib\site-packages'
foreach ($name in @('qq_admin_ingress','qq_message_id','chat_media')) {
    Apply-Patch $site (Join-Path $patches "astrbot_4.29.4_$name.patch")
}
Copy-Item -LiteralPath (Join-Path $patches 'chat_layout.py') -Destination (Join-Path $site 'astrbot\core\pipeline') -Force

Write-Host '[3/6] NapCat, Ollama and PhoneMCP'
Install-Asset 'NapCat.Shell.Windows.Node.zip' $napcat 'index.js'
if (-not (Test-Path -LiteralPath (Join-Path $napcat 'node.exe'))) {
    Copy-Item -LiteralPath (Join-Path $node 'node.exe') -Destination $napcat
}
Install-Asset 'ollama-windows-amd64.zip' $ollama 'ollama.exe'
$phoneSource = Join-Path $phone 'src'
Unpack (Join-Path $extras 'PhoneMCP-v0.5.0-source.zip') $phoneSource 'main.py'
Apply-Patch $phoneSource (Join-Path $extras 'PhoneMCP-Windows.patch')
$phonePython = Join-Path $phone 'venv\Scripts\python.exe'
Prepare-Venv (Join-Path $phone 'venv')
Run $phonePython @('-m','pip','install','-r',(Join-Path $phoneSource 'requirements.txt'),'fastmcp==4.1.1')
Run $phonePython @('-m','pip','check')
Set-Content -LiteralPath (Join-Path $phone 'venv\.fishhome-ready') -Value 'ready'

Write-Host '[4/6] DeepSeek Harness and FishHome console'
foreach ($name in @('package.json','package-lock.json')) {
    Copy-Item -LiteralPath (Join-Path $extras "dsh\$name") -Destination $dsh -Force
}
Push-Location -LiteralPath $dsh
try { Run (Join-Path $node 'npm.cmd') @('ci','--no-audit','--no-fund','--prefer-online','--cache',(Join-Path $cache 'npm')) }
finally { Pop-Location }
foreach ($name in @('Start-DSH-M3.cmd','m5-file-only.yml')) {
    Copy-Item -LiteralPath (Join-Path $extras $name) -Destination $dsh -Force
}
& (Join-Path $repo 'deploy\desktop_manager\install.ps1') -InstallOwnerWork -InstallAffectiveBridge
foreach ($name in @('Start-DSH-M3.cmd','Start-DSH-Commission.cmd')) {
    $launcher = Join-Path $dsh $name
    $body = [IO.File]::ReadAllText($launcher).Replace('C:\Program Files\nodejs\node.exe', '%~dp0runtime\node.exe')
    [IO.File]::WriteAllText($launcher, $body, [Text.Encoding]::ASCII)
}
Require-File (Join-Path $dev 'FishHome.exe')
Set-Content -LiteralPath (Join-Path $astro '.venv\.fishhome-ready') -Value 'ready'

Write-Host '[5/6] Local embedding model'
if (-not $SkipEmbedding) {
    $env:OLLAMA_HOST = '127.0.0.1:11435'
    $env:OLLAMA_MODELS = Join-Path $dev 'ollama-models'
    $env:OLLAMA_NO_CLOUD = '1'
    if (Get-NetTCPConnection -State Listen -LocalPort 11435 -ErrorAction SilentlyContinue) {
        throw 'Port 11435 is already in use. Free it before downloading bge-m3.'
    }
    $ollamaExe = Join-Path $ollama 'ollama.exe'
    $server = Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden -PassThru
    try {
        $ready = $false
        for ($i = 0; $i -lt 30 -and -not $ready; $i++) {
            try { $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/version' -TimeoutSec 2; $ready = $true }
            catch { Start-Sleep -Seconds 1 }
        }
        if (-not $ready) { throw 'Ollama did not start for model download.' }
        Run $ollamaExe @('pull','bge-m3')
    } finally {
        if (-not $server.HasExited) { Stop-Process -Id $server.Id }
    }
}

Write-Host '[6/6] Initialize AstrBot'
Push-Location -LiteralPath $astro
try {
    Run $py @('-B','-m','astrbot.cli','init','--yes')
    @'
from pathlib import Path
from astrbot.core.config.astrbot_config import AstrBotConfig

config = AstrBotConfig(config_path=str(Path.cwd() / "data" / "cmd_config.json"))
config["dashboard"]["host"] = "127.0.0.1"
config.save_config()
'@ | & $py -B -
    if ($LASTEXITCODE -ne 0) { throw 'AstrBot dashboard setup failed.' }
} finally { Pop-Location }
Write-Host 'Installation complete. Read the quick start before starting services.'
