# Oracle gateway for Windows (Lappy).
# Run: powershell -ExecutionPolicy Bypass -File run.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$host = if ($env:ORACLE_HOST) { $env:ORACLE_HOST } else { "0.0.0.0" }
$port = if ($env:ORACLE_PORT) { $env:ORACLE_PORT } else { "8003" }
python -m uvicorn app.main:app --host $host --port $port
