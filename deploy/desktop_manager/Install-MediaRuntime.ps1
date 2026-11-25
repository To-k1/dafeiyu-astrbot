param([string]$InstallRoot = (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))),
      [string]$Target = (Join-Path $env:LOCALAPPDATA 'FishHome/manager/media-runtime'))

$ErrorActionPreference = 'Stop'
$python = Join-Path $InstallRoot 'dev/astrbot-4.29.4/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'InstallRoot must point to an existing FishHome Windows installation.'
}

New-Item -Path $Target -ItemType Directory -Force | Out-Null
& $python -m pip install --disable-pip-version-check --target $Target rapidocr==3.9.0 onnxruntime==1.23.2
if ($LASTEXITCODE -ne 0) { throw 'Could not install RapidOCR/ONNX Runtime.' }
& $python -m pip install --disable-pip-version-check --target $Target --no-deps silero-vad==6.2.0
if ($LASTEXITCODE -ne 0) { throw 'Could not install the Silero VAD model.' }

$env:PYTHONPATH = $Target
@'
from importlib.metadata import distribution
from rapidocr import RapidOCR, OCRVersion, ModelType

RapidOCR(params={"Global.use_cls": False, "Det.ocr_version": OCRVersion.PPOCRV5,
                 "Det.model_type": ModelType.MOBILE, "Rec.ocr_version": OCRVersion.PPOCRV5,
                 "Rec.model_type": ModelType.MOBILE})
assert distribution("silero-vad").locate_file("silero_vad/data/silero_vad.onnx").is_file()
'@ | & $python -B -
if ($LASTEXITCODE -ne 0) { throw 'Media runtime or public model weights did not load.' }
Write-Output "Media runtime ready: $Target"
