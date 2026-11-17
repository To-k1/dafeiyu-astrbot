param([Parameter(Mandatory=$true)][string]$InstallRoot)
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this update from an elevated PowerShell.'
}
$resolvedRoot = (Resolve-Path -LiteralPath $InstallRoot).Path
$source = Join-Path $resolvedRoot 'repo\deploy\dsh_bridge\windows_desktop.py'
$directory = Join-Path $env:ProgramData 'FishHomeDesktop'
$target = Join-Path $directory 'windows_desktop.py'
if (-not (Test-Path -LiteralPath $source -PathType Leaf) -or
    -not (Test-Path -LiteralPath $target -PathType Leaf) -or
    ((Get-Item -LiteralPath $directory).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Choose the existing prepared FishHome installation.'
}
$backup = Join-Path $directory ('windows_desktop.before-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.py')
Copy-Item -LiteralPath $target -Destination $backup
Copy-Item -LiteralPath $source -Destination $target -Force
Write-Output 'Protected desktop broker code updated.'
