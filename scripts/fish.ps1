[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('doctor', 'status', 'backup', 'verify', 'restore')]
    [string]$Command,
    [ValidateSet('local-dev')]
    [string]$Target = 'local-dev',
    [string]$Python = 'python',
    [string]$MockDatabase,
    [string]$Archive,
    [string]$Destination,
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$backupTool = Join-Path $PSScriptRoot 'mock_backup.py'

switch ($Command) {
    'doctor' {
        & $Python -B (Join-Path $repo 'deploy-kit\preflight.py') --with-bili --json
        exit $LASTEXITCODE
    }
    'status' {
        $ports = [ordered]@{ astrbot = 6185; napcat = 6099 }
        $status = foreach ($name in $ports.Keys) {
            $client = [Net.Sockets.TcpClient]::new()
            try {
                $connected = $client.ConnectAsync('127.0.0.1', $ports[$name]).Wait(750)
            } catch {
                $connected = $false
            } finally {
                $client.Dispose()
            }
            [pscustomobject]@{
                component = $name
                address = '127.0.0.1'
                port = $ports[$name]
                reachable = $connected
            }
        }
        ConvertTo-Json -InputObject @($status)
        return
    }
    'backup' {
        if (-not $MockDatabase) { throw 'backup requires -MockDatabase in M1.' }
        if (-not $Archive) {
            if (-not $env:FISH_HOME) { throw 'Set FISH_HOME or provide -Archive.' }
            $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
            $Archive = Join-Path $env:FISH_HOME "backups\m1-mock-$stamp"
        }
        & $Python -B $backupTool snapshot $MockDatabase $Archive
    }
    'verify' {
        if (-not $Archive) { throw 'verify requires -Archive.' }
        & $Python -B $backupTool verify $Archive
    }
    'restore' {
        if (-not $Archive -or -not $Destination) {
            throw 'restore requires -Archive and -Destination.'
        }
        $arguments = @('restore', $Archive, $Destination)
        if ($Apply) { $arguments += '--apply' }
        & $Python -B $backupTool @arguments
    }
}
exit $LASTEXITCODE
