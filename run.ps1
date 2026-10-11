$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created optional .env. Configure models and connections in the settings window.'
}
uv sync --frozen --cache-dir .cache/uv
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) {
    npm.cmd --prefix frontend ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    npm.cmd --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}
New-Item -ItemType Directory -Path data -Force | Out-Null
$ragPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
Write-Host 'Shelf: http://127.0.0.1:8000 (API automatically manages the ingestion worker)'
& $ragPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
