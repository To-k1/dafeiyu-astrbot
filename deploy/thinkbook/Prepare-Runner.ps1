$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $arguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'))
    $child = Start-Process -FilePath powershell.exe -ArgumentList $arguments -Verb RunAs -PassThru -Wait
    exit $child.ExitCode
}
$dsh = Join-Path $PSScriptRoot 'dev\dsh-0.2.2-rc.1'
if (-not (Test-Path -LiteralPath (Join-Path $dsh 'runtime\node.exe') -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'dev\.thinkbook-setup') -PathType Leaf)) {
    throw 'Run Install-FishHome.cmd first.'
}
$name = 'FishHomeRunner'
$account = Get-LocalUser -Name $name -ErrorAction SilentlyContinue
$password = Read-Host 'Enter the current FishHomeRunner password, or choose one for the new local account' -AsSecureString
if (-not $account) {
    $account = New-LocalUser -Name $name -Password $password -Description 'FishHome restricted file task runner'
    if ($account.SID -notin @(Get-LocalGroupMember -SID 'S-1-5-32-545').SID) {
        Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $account
    }
}
$admins = @(Get-LocalGroupMember -SID 'S-1-5-32-544')
if ($admins.SID -contains $account.SID) { throw 'FishHomeRunner must be a standard user.' }
$access = '*' + $account.SID.Value + ':(OI)(CI)(RX)'
& icacls.exe $dsh '/grant:r' $access
if ($LASTEXITCODE -ne 0) { throw 'Could not grant the runner read and execute access to DSH.' }
$blocked = '*' + $account.SID.Value + ':(OI)(CI)(F)'
foreach ($relative in @('repo','dev\astrbot-4.29.4','dev\napcat-4.18.38','dev\tools','downloads')) {
    $folder = Join-Path $PSScriptRoot $relative
    if (Test-Path -LiteralPath $folder -PathType Container) {
        if ((Get-Item -LiteralPath $folder).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Install folders must be local directories: $folder"
        }
        & icacls.exe $folder '/deny' $blocked
        if ($LASTEXITCODE -ne 0) { throw "Could not restrict runner access to $relative." }
    }
}
$credential = [pscredential]::new("$env:COMPUTERNAME\$name", $password)
$bootstrap = @'
$ErrorActionPreference = 'Stop'
foreach ($relative in @('m3-file-check', 'commission-files')) {
    $work = Join-Path $env:LOCALAPPDATA "FishHome\$relative"
    New-Item -ItemType Directory -Force -Path (Join-Path $work 'output') | Out-Null
    $sample = Join-Path $work 'sample.csv'
    if (-not (Test-Path -LiteralPath $sample)) {
        Set-Content -LiteralPath $sample -Value @('item,count','notebook,2','pen,3') -Encoding Ascii
        (Get-Item -LiteralPath $sample).IsReadOnly = $true
    }
    if ($relative -eq 'm3-file-check' -and -not (Test-Path -LiteralPath (Join-Path $work '.m3-root'))) {
        New-Item -ItemType File -Path (Join-Path $work '.m3-root') | Out-Null
    }
}
'@
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
$parameters = @{
    FilePath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    ArgumentList = @('-NoProfile','-NonInteractive','-EncodedCommand',$encoded)
    Credential = $credential; LoadUserProfile = $true; WindowStyle = 'Hidden'
    WorkingDirectory = $env:SystemRoot; PassThru = $true; Wait = $true
}
$child = Start-Process @parameters
if ($child.ExitCode -ne 0) { throw 'Could not initialize the FishHomeRunner workspace.' }
Write-Host 'Runner prepared. Save the same password in the FishHome console.'
