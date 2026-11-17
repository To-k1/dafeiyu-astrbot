param([Parameter(Mandatory=$true)][string]$InstallRoot)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $InstallRoot).Path
$repo = Join-Path $root 'repo\deploy\dsh_bridge'
$python = Join-Path $root 'dev\tools\python-3.12.10-portable'
if (-not (Test-Path -LiteralPath (Join-Path $root 'dev\FishHome.exe')) -or
    -not (Test-Path -LiteralPath (Join-Path $python 'python.exe'))) {
    throw 'Choose the existing ThinkBook FishHome installation root.'
}
$account = Get-LocalUser -Name FishHomeDesktop
$admins = Get-LocalGroup -SID 'S-1-5-32-544'
if (Get-LocalGroupMember -Group $admins.Name | Where-Object { $_.SID -eq $account.SID }) {
    throw 'FishHomeDesktop must be a standard user.'
}
$directory = Join-Path $env:ProgramData 'FishHomeDesktop'
if (Test-Path -LiteralPath $directory) {
    if ((Get-Item -LiteralPath $directory).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Desktop broker destination cannot be a reparse point.'
    }
} else {
    $null = New-Item -Path $directory -ItemType Directory
}
$sid = "*$($account.SID.Value)"
& icacls.exe $directory /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "$($sid):(OI)(CI)RX"
if ($LASTEXITCODE) { throw 'Could not protect the desktop broker directory.' }
$packages = Join-Path $directory 'packages'
if (Test-Path -LiteralPath $packages) {
    if ((Get-Item -LiteralPath $packages).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Desktop packages directory cannot be a reparse point.'
    }
} else {
    $null = New-Item -Path $packages -ItemType Directory
}
$null = & icacls.exe $packages /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "$($sid):(OI)(CI)RX"
if ($LASTEXITCODE) { throw 'Could not protect the desktop packages directory.' }
& icacls.exe $root /deny "$($sid):(OI)(CI)F"
if ($LASTEXITCODE) { throw 'Could not protect the existing FishHome installation.' }
foreach ($name in @('Start-DesktopBroker.ps1', 'windows_desktop.py')) {
    Copy-Item -LiteralPath (Join-Path $repo $name) -Destination $directory -Force
}
& robocopy.exe $python (Join-Path $directory 'python') /E /COPY:DAT /R:1 /W:1 /NFL /NDL /NP /NJH /NJS
if ($LASTEXITCODE -ge 8) { throw 'Could not copy the independent Python runtime.' }
Write-Host "Desktop broker prepared at $directory"
