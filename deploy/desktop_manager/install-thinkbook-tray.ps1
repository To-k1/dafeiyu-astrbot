$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$dev = Join-Path (Split-Path -Parent $root) 'dev'
$python = Join-Path $dev 'astrbot-4.29.4\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'FishHome desktop Python environment not found.' }
if (Get-Process 'FishHome-ThinkBook' -ErrorAction SilentlyContinue) {
    throw 'Exit FishHome-ThinkBook from the system tray before installing an update.'
}
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$target = Join-Path $dev 'FishHome-ThinkBook.exe'
& $compiler /nologo /target:winexe /optimize+ /codepage:65001 /r:System.Drawing.dll /r:System.Windows.Forms.dll "/out:$target" (Join-Path $PSScriptRoot 'ThinkBookTray.cs')
if ($LASTEXITCODE -ne 0) { throw 'ThinkBook tray compilation failed.' }
Write-Output $target
