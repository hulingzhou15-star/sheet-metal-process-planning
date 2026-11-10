param([switch]$SkipPythonDownload)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot '.local/python'
$env:UV_CACHE_DIR = Join-Path $projectRoot '.local/uv-cache'
$uvPath = Join-Path $projectRoot '.local/tools/uv.exe'
if (-not (Test-Path -LiteralPath $uvPath)) {
    New-Item -ItemType Directory -Path (Split-Path $uvPath -Parent) -Force | Out-Null
    $uvArchive = Join-Path $projectRoot '.local/tools/uv.zip'
    Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/download/0.13.7/uv-x86_64-pc-windows-msvc.zip' -OutFile $uvArchive
    Expand-Archive -LiteralPath $uvArchive -DestinationPath (Split-Path $uvPath -Parent) -Force
}
if (-not $SkipPythonDownload) {
    & $uvPath python install 3.12.12 --no-bin
    if ($LASTEXITCODE -ne 0) { throw 'Python installation failed' }
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.venv/Scripts/python.exe'))) {
    & $uvPath venv .venv --python 3.12.12
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
}
& $uvPath pip sync requirements/windows-py312.lock --python .venv/Scripts/python.exe
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& npm ci --ignore-scripts
if ($LASTEXITCODE -ne 0) { throw 'Node.js/npm must be installed; npm ci failed' }
& .venv/Scripts/python.exe -B -X utf8 scripts/project.py doctor
if ($LASTEXITCODE -ne 0) { throw 'Project doctor failed' }
