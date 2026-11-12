param([Parameter(Mandatory)][string]$FixtureRoot, [Parameter(Mandatory)][string]$Python)
$ErrorActionPreference = 'Stop'
$tokens = $null
$errorsFound = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PSScriptRoot 'Install-FishHome.ps1'), [ref]$tokens, [ref]$errorsFound)
if ($errorsFound.Count) { throw ($errorsFound | Out-String) }
foreach ($definition in $ast.EndBlock.Statements) {
    if ($definition -is [System.Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($definition.Extent.Text))
    }
}
$probeAssignment = $ast.EndBlock.Statements | Where-Object {
    $_ -is [System.Management.Automation.Language.AssignmentStatementAst] -and
    $_.Left.VariablePath.UserPath -eq 'pythonProbe'
}
. ([scriptblock]::Create($probeAssignment.Extent.Text))
if (-not (Test-Python $Python)) { throw 'Python probe rejected a working 64-bit Python 3.12.' }

$dev = Join-Path $FixtureRoot 'dev'
$cache = Join-Path $FixtureRoot 'downloads'
$payload = Join-Path $FixtureRoot 'payload'
$extras = Join-Path $FixtureRoot 'extras'
New-Item -ItemType Directory -Path $dev,$cache,$payload,$extras | Out-Null
Set-Content -LiteralPath (Join-Path $payload 'node.exe') -Value 'fixture'
$script:valid = Join-Path $FixtureRoot 'valid.zip'
Compress-Archive -Path (Join-Path $payload 'node.exe') -DestinationPath $script:valid
$assets = @{ 'node.zip' = 'https://example.invalid/node.zip' }
$script:downloads = 0
$script:failOnce = $false
function Invoke-WebRequest {
    param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile, [int]$TimeoutSec)
    $script:downloads++
    if ($script:failOnce) {
        $script:failOnce = $false
        Set-Content -LiteralPath $OutFile -Value 'interrupted'
        throw 'simulated interrupted download'
    }
    Copy-Item -LiteralPath $script:valid -Destination $OutFile
}

$cached = Join-Path $cache 'node.zip'
Set-Content -LiteralPath $cached -Value 'corrupted archive'
Set-Content -LiteralPath "$cached.old.partial" -Value 'interrupted previous run'
$node = Join-Path $dev 'node'
New-Item -ItemType Directory -Path $node | Out-Null
Set-Content -LiteralPath (Join-Path $node 'old.txt') -Value 'keep'
Install-Asset 'node.zip' $node 'node.exe'
if ($script:downloads -ne 1 -or -not (Test-Path -LiteralPath (Join-Path $node 'node.exe'))) {
    throw 'Corrupt cache did not recover.'
}
$saved = @(Get-ChildItem -LiteralPath $dev -Directory -Filter 'node.incomplete-*')
if ($saved.Count -ne 1 -or -not (Test-Path -LiteralPath (Join-Path $saved[0].FullName 'old.txt'))) {
    throw 'Partial install was not preserved.'
}
Remove-Item -LiteralPath $cached
Install-Asset 'node.zip' $node 'node.exe'
if ($script:downloads -ne 1) { throw 'Installed component downloaded again.' }

$script:failOnce = $true
$second = Join-Path $dev 'other'
Install-Asset 'node.zip' $second 'node.exe'
if ($script:downloads -ne 3 -or -not (Test-Path -LiteralPath (Join-Path $second 'node.exe')) -or
    @(Get-ChildItem -LiteralPath $cache -Filter '*.partial').Count -ne 1) {
    throw 'Missing cache or interrupted download did not recover.'
}
Copy-Item -LiteralPath $script:valid -Destination (Join-Path $extras 'node.zip')
$third = Join-Path $dev 'bundled'
Install-Asset 'node.zip' $third 'node.exe'
if ($script:downloads -ne 3 -or -not (Test-Path -LiteralPath (Join-Path $third 'node.exe'))) {
    throw 'Bundled asset did not bypass the network.'
}

$basePython = 'fake-python'
function Run([string]$Exe, [string[]]$Arguments) {
    if ($Exe -ne 'fake-python' -or ($Arguments[0..1] -join ' ') -ne '-m venv') { throw 'Unexpected launch' }
    $scripts = Join-Path $Arguments[2] 'Scripts'
    New-Item -ItemType Directory -Path $scripts -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $scripts 'python.exe') -Value 'recreated'
}
$venv = Join-Path $dev 'venv'
$scripts = Join-Path $venv 'Scripts'
New-Item -ItemType Directory -Force -Path $scripts | Out-Null
Set-Content -LiteralPath (Join-Path $scripts 'python.exe') -Value 'partial environment'
Prepare-Venv $venv
if ((Get-Content -LiteralPath (Join-Path $scripts 'python.exe')) -ne 'recreated') {
    throw 'Incomplete environment was not recreated.'
}
Write-Output 'Recovery checks passed.'
