[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8769,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$bundledPython = Join-Path $projectRoot 'runtime\python\python.exe'
$wheelhouse = Join-Path $projectRoot 'runtime\wheelhouse'
$requirements = Join-Path $projectRoot 'portable_requirements_lock.txt'
$dashboard = Join-Path $projectRoot 'scripts\run_portable_sentinel_dashboard.py'
$engine = Join-Path $projectRoot 'scripts\build_portable_sentinel_features.py'
$venvRoot = Join-Path $projectRoot '.venv-sentinel'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'
$markerPath = Join-Path $venvRoot 'portable_environment.json'

foreach ($requiredFile in @($bundledPython, $requirements, $dashboard, $engine)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required program file is missing: $requiredFile"
    }
}
if (-not (Test-Path -LiteralPath $wheelhouse -PathType Container)) {
    throw "Offline wheelhouse is missing: $wheelhouse"
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Host 'First launch: creating the local Python environment...'
    & $bundledPython -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not create .venv-sentinel.'
    }
}

$requirementsSha = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash.ToLowerInvariant()
$environmentReady = $false
if (Test-Path -LiteralPath $markerPath -PathType Leaf) {
    try {
        $marker = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $environmentReady = ([string]$marker.requirements_sha256 -eq $requirementsSha)
    }
    catch {
        $environmentReady = $false
    }
}

if (-not $environmentReady) {
    Write-Host 'First launch: installing bundled dependencies (offline, usually several minutes)...'
    & $venvPython -m pip install --disable-pip-version-check --no-index --find-links $wheelhouse --requirement $requirements
    if ($LASTEXITCODE -ne 0) {
        throw 'Offline dependency installation failed.'
    }
    $marker = [ordered]@{
        requirements_sha256 = $requirementsSha
        python_version = (& $venvPython -c 'import platform; print(platform.python_version())').Trim()
        prepared_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    }
    $json = ($marker | ConvertTo-Json -Depth 3) + "`n"
    [IO.File]::WriteAllText($markerPath, $json, (New-Object Text.UTF8Encoding($false)))
}

$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:GDAL_DISABLE_READDIR_ON_OPEN = 'EMPTY_DIR'

& $venvPython -c 'import geopandas, numpy, pandas, planetary_computer, pyarrow, rasterio, shapely; print("Environment ready.")'
if ($LASTEXITCODE -ne 0) {
    throw 'The local Python environment is incomplete.'
}

$arguments = @($dashboard, '--port', [string]$Port)
if ($NoBrowser) {
    $arguments += '--no-browser'
}

Write-Host "Opening progress page at http://127.0.0.1:$Port/"
Write-Host 'Choose 6 or 8 download threads in the page, then click Start / Continue.'
Write-Host 'Before closing this window, click Safe Pause and wait for running=0.'
Push-Location $projectRoot
try {
    & $venvPython @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Dashboard exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
