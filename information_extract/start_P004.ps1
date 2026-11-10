$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start.ps1') --config configs/P004.json @args
