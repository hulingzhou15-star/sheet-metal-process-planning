$ErrorActionPreference = 'Stop'
$extractRoot = $PSScriptRoot
$extractPython = Join-Path (Split-Path $extractRoot -Parent) '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $extractPython)) {
    throw 'Python runtime not found. See README.md for the manual launch command.'
}
& $extractPython -B -X utf8 (Join-Path $extractRoot 'scripts/serve.py') @args
