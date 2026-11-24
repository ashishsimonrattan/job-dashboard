$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
Start-Process 'http://127.0.0.1:8765'
python -u server.py *>> (Join-Path $PSScriptRoot 'data\server.log')
