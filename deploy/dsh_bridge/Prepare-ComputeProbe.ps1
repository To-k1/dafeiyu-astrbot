param(
    [Parameter(Mandatory)][string]$InstallRoot,
    [switch]$ProtectInstall
)

$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an Administrator Windows PowerShell on ThinkBook.'
}

$InstallRoot = (Resolve-Path -LiteralPath $InstallRoot).Path
if ((Get-Item -LiteralPath $InstallRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'The install root must be a local directory, not a link.'
}
$archive = Join-Path $InstallRoot 'downloads\python-3.12.10-windows-x64.tar.gz'
$verifier = Join-Path $PSScriptRoot 'verify_python_workspace.py'
foreach ($path in @($archive, $verifier, (Join-Path $InstallRoot 'dev\FishHome.exe'))) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing file: $path" }
}
$runner = Get-LocalUser -Name FishHomeRunner
$name = 'FishHomeCompute'
$password = Read-Host 'Password for the separate FishHomeCompute standard account' -AsSecureString
$account = Get-LocalUser -Name $name -ErrorAction SilentlyContinue
if (-not $account) {
    $account = New-LocalUser -Name $name -Password $password -Description 'FishHome isolated compute'
    if ($account.SID -notin @((Get-LocalGroupMember -SID 'S-1-5-32-545').SID)) {
        Add-LocalGroupMember -SID 'S-1-5-32-545' -Member $account
    }
}
if ($account.SID -in @((Get-LocalGroupMember -SID 'S-1-5-32-544').SID)) {
    throw 'FishHomeCompute must be a standard account.'
}

$root = Join-Path $env:ProgramData ('FishHomeComputeProbe-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
try {
    & icacls.exe $root '/inheritance:r' '/grant:r' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' ('*' + $account.SID.Value + ':(OI)(CI)RX') | Out-Null
    if ($LASTEXITCODE) { throw 'Could not set probe root permissions.' }
    $work = Join-Path $root 'work'
    New-Item -ItemType Directory -Path $work | Out-Null
    & icacls.exe $work '/grant:r' ('*' + $account.SID.Value + ':(OI)(CI)M') | Out-Null
    if ($LASTEXITCODE) { throw 'Could not set workspace permissions.' }
    $fixture = Join-Path $root 'outside-fixture.txt'
    Set-Content -LiteralPath $fixture -Value 'FishHome synthetic boundary probe' -Encoding Ascii
    & icacls.exe $fixture '/inheritance:r' '/grant:r' '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null
    if ($LASTEXITCODE) { throw 'Could not protect outside fixture.' }
    & tar.exe -xzf $archive -C $root
    if ($LASTEXITCODE) { throw 'Could not unpack the portable runtime.' }
    $python = Join-Path $root 'python\python.exe'
    $script = Join-Path $root 'verify_python_workspace.py'
    Copy-Item -LiteralPath $verifier -Destination $script
    $receipt = Join-Path $work 'receipt.json'
    $runnerProfile = (Get-ItemProperty -LiteralPath "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$($runner.SID)").ProfileImagePath
    $runnerState = Join-Path $runnerProfile 'AppData\Local\FishHome'
    $protected = [ordered]@{
        astrbot_config = Join-Path $InstallRoot 'dev\astrbot-4.29.4\data\cmd_config.json'
        autonomous_dsh_log = Join-Path $runnerState 'DSH\web-start.log'
        commissioned_dsh_log = Join-Path $runnerState 'DSH-Commission\web-start.log'
    }
    foreach ($target in $protected.Values) {
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw 'A protected control file or DSH log is missing.' }
    }
    if ($ProtectInstall) {
        # Only Compute is affected. Keep the install separate from its writable workspace.
        & icacls.exe $InstallRoot '/deny' ('*' + $account.SID.Value + ':(OI)(CI)F') | Out-Null
        if ($LASTEXITCODE) { throw 'Could not protect the install root from FishHomeCompute.' }
    }
    $argsForPython = @('-B', ('"' + $script + '"'), ('"' + $work + '"'), ('"' + $fixture + '"'),
                       '--receipt', ('"' + $receipt + '"'))
    foreach ($label in $protected.Keys) {
        $argsForPython += @('--probe-file', $label, ('"' + $protected[$label] + '"'))
    }
    $credential = [pscredential]::new("$env:COMPUTERNAME\$name", $password)
    $child = Start-Process -FilePath $python -ArgumentList $argsForPython -WorkingDirectory $work `
        -Credential $credential -LoadUserProfile -WindowStyle Hidden -Wait -PassThru
    if (Test-Path -LiteralPath $receipt -PathType Leaf) { Get-Content -LiteralPath $receipt -Raw }
    if ($child.ExitCode) { throw "Compute boundary probe failed (exit $($child.ExitCode))." }
} finally {
    $fullRoot = [IO.Path]::GetFullPath($root)
    $prefix = [IO.Path]::GetFullPath($env:ProgramData).TrimEnd('\') + '\FishHomeComputeProbe-'
    if ($fullRoot.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $fullRoot -Recurse -Force
    }
}
