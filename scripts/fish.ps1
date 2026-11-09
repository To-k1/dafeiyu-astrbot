[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('doctor', 'status', 'backup', 'verify', 'restore', 'export-memory', 'room-sync', 'migration-preview', 'deploy')]
    [string]$Command,
    [ValidateSet('local-dev', 'fishbook')]
    [string]$Target = 'local-dev',
    [string]$Python = 'python',
    [string]$MockDatabase,
    [string]$Archive,
    [string]$Destination,
    [string]$AstrbotRoot,
    [string]$RemoteRepo,
    [string]$RemotePython,
    [string]$Ref,
    [ValidateSet('dev', 'prod')][string]$Environment = 'dev',
    [ValidateSet('runtime', 'memory')][string]$Profile = 'runtime',
    [ValidateSet('none', 'selective', 'full')][string]$Mode = 'none',
    [string[]]$Record,
    [string]$Workspace,
    [string[]]$WorkFile,
    [switch]$IncludePrivateChat,
    [switch]$WritersStopped,
    [switch]$DryRun,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$backupTool = Join-Path $PSScriptRoot 'mock_backup.py'

if ($DryRun -and $Apply) { throw 'Choose -DryRun or -Apply.' }
if ($Target -eq 'local-dev') {
    if ($Command -eq 'doctor') {
        & $Python -B (Join-Path $repo 'deploy-kit\preflight.py') --with-bili --json
        exit $LASTEXITCODE
    }
    if ($Command -eq 'backup' -and $MockDatabase) {
        if (-not $Archive) {
            if (-not $env:FISH_HOME) { throw 'Set FISH_HOME or provide -Archive.' }
            $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
            $Archive = Join-Path $env:FISH_HOME "backups\m1-mock-$stamp"
        }
        & $Python -B $backupTool snapshot $MockDatabase $Archive
        exit $LASTEXITCODE
    }
    if ($Command -in @('verify','restore') -and $Archive -and (Test-Path -LiteralPath $Archive -PathType Container)) {
        $arguments = @($Command, $Archive)
        if ($Command -eq 'restore') {
            if (-not $Destination) { throw 'restore requires -Destination.' }
            $arguments += $Destination
            if ($Apply) { $arguments += '--apply' }
        }
        & $Python -B $backupTool @arguments
        exit $LASTEXITCODE
    }
    if (-not $AstrbotRoot) { $AstrbotRoot = Join-Path (Split-Path -Parent $repo) 'dev\astrbot-4.29.4' }
    $AstrbotRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($AstrbotRoot)
}

if ($Archive) { $Archive = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Archive) }
if ($Destination) { $Destination = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Destination) }
if ($Workspace) { $Workspace = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Workspace) }
if ($Python -match '[\\/]') { $Python = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Python) }

$arguments = @($Command, '--target', $Target, '--environment', $Environment, '--profile', $Profile, '--mode', $Mode)
$paths = [ordered]@{ 'astrbot-root' = $AstrbotRoot; 'remote-repo' = $RemoteRepo; 'remote-python' = $RemotePython;
                     archive = $Archive; destination = $Destination; ref = $Ref; workspace = $Workspace }
foreach ($item in $paths.GetEnumerator()) {
    if ($item.Value) { $arguments += @(('--' + $item.Key), $item.Value) }
}
foreach ($item in $Record) { $arguments += @('--record', $item) }
foreach ($item in $WorkFile) { $arguments += @('--work-file', $item) }
if ($IncludePrivateChat) { $arguments += '--include-private-chat' }
if ($WritersStopped) { $arguments += '--writers-stopped' }
if ($Apply) { $arguments += '--apply' }
Push-Location -LiteralPath $repo
try {
    & $Python -B -m scripts.fish_m6 @arguments
    $result = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $result
