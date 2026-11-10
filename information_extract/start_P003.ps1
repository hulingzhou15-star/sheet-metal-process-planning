$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start.ps1') --config configs/P003.json @args
