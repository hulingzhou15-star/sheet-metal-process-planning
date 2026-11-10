$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start.ps1') --config configs/P002.json @args
