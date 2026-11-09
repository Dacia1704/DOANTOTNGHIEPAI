param([int]$Users = 1000)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $pythonPath = Join-Path (Get-Location) '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        py -3.11 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Cannot create Python environment' }
    }
    & $pythonPath -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    & $pythonPath -m scripts.prepare_database
    if ($LASTEXITCODE -ne 0) { throw 'Database/grants setup failed' }
    & $pythonPath -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'AI migration failed' }
    & $pythonPath -m scripts.seed_demo_ai --users $Users
    if ($LASTEXITCODE -ne 0) { throw 'AI demo seed failed' }
    & $pythonPath -m scripts.build_embeddings
    if ($LASTEXITCODE -ne 0) { throw 'Embedding build failed' }
    Write-Host 'rental_ai is ready. Start Uvicorn from the AIService terminal.'
} finally {
    Pop-Location
}
