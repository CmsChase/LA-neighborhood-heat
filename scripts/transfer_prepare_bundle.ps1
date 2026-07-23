[CmdletBinding()]
param(
    [string]$Destination
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-MachineFingerprint {
    $machineGuid = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Cryptography').MachineGuid
    $material = "$env:COMPUTERNAME|$machineGuid"
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($material)))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-SafeRelativePath {
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][string]$Path)
    $rootPrefix = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $fullPath = [IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes its declared root: $fullPath"
    }
    return $fullPath.Substring($rootPrefix.Length)
}

function Write-Utf8NoBom {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Text)
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Copy-FilteredTree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Required source directory is missing: $Source"
    }
    Get-ChildItem -LiteralPath $Source -File -Recurse -Force | ForEach-Object {
        $relative = Get-SafeRelativePath -Root $Source -Path $_.FullName
        $segments = $relative -split '[\\/]'
        if ($segments -contains '__pycache__' -or $_.Extension -in @('.pyc', '.pyo')) {
            return
        }
        $destinationFile = Join-Path $Target $relative
        $destinationDirectory = Split-Path -Parent $destinationFile
        [IO.Directory]::CreateDirectory($destinationDirectory) | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $destinationFile
    }
}

function Get-FileRecord {
    param([Parameter(Mandatory = $true)][IO.FileInfo]$File, [Parameter(Mandatory = $true)][string]$Root)
    $relative = (Get-SafeRelativePath -Root $Root -Path $File.FullName).Replace('\', '/')
    [ordered]@{
        path = $relative
        bytes = $File.Length
        sha256 = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

function Copy-PortablePythonRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePython,
        [Parameter(Mandatory = $true)][string]$Target
    )
    $basePrefix = (& $SourcePython -c 'import sys; print(sys.base_prefix)').Trim()
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $basePrefix -PathType Container)) {
        throw 'Could not locate the exact source Python base runtime.'
    }
    [IO.Directory]::CreateDirectory($Target) | Out-Null
    Get-ChildItem -LiteralPath $basePrefix -File -Force | Copy-Item -Destination $Target
    Copy-FilteredTree -Source (Join-Path $basePrefix 'DLLs') -Target (Join-Path $Target 'DLLs')
    $libraryRoot = Join-Path $basePrefix 'Lib'
    $sitePackagesPrefix = [IO.Path]::GetFullPath((Join-Path $libraryRoot 'site-packages')).TrimEnd('\') + '\'
    Get-ChildItem -LiteralPath $libraryRoot -File -Recurse -Force | Where-Object {
        -not $_.FullName.StartsWith($sitePackagesPrefix, [StringComparison]::OrdinalIgnoreCase) -and
        $_.FullName -notlike '*\__pycache__\*' -and
        $_.Extension -notin @('.pyc', '.pyo')
    } | ForEach-Object {
        $relative = Get-SafeRelativePath -Root $libraryRoot -Path $_.FullName
        $destinationFile = Join-Path (Join-Path $Target 'Lib') $relative
        [IO.Directory]::CreateDirectory((Split-Path -Parent $destinationFile)) | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $destinationFile
    }
    $portablePython = Join-Path $Target 'python.exe'
    $sourceVersion = (& $SourcePython -c 'import platform; print(platform.python_version())').Trim()
    $portableVersion = (& $portablePython -c 'import platform; print(platform.python_version())').Trim()
    if ($LASTEXITCODE -ne 0 -or $portableVersion -ne $sourceVersion) {
        throw 'The copied portable Python runtime failed its exact-version check.'
    }
}

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
if (-not $Destination) {
    $Destination = Join-Path $projectRoot 'exports\ISEF_MODEL_RUNNER_8845H'
}
$destinationRoot = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
$exportsPrefix = [IO.Path]::GetFullPath((Join-Path $projectRoot 'exports')).TrimEnd('\') + '\'
if (
    $destinationRoot.StartsWith("$projectRoot\", [StringComparison]::OrdinalIgnoreCase) -and
    -not $destinationRoot.StartsWith($exportsPrefix, [StringComparison]::OrdinalIgnoreCase)
) {
    throw 'A destination inside the project is allowed only below the excluded exports directory.'
}
if (Test-Path -LiteralPath $destinationRoot) {
    if ((Get-ChildItem -LiteralPath $destinationRoot -Force | Measure-Object).Count -ne 0) {
        throw "Destination already exists and is not empty: $destinationRoot"
    }
}
else {
    [IO.Directory]::CreateDirectory($destinationRoot) | Out-Null
}

$statusPath = Join-Path $projectRoot 'data\interim\model_runs\status.json'
if (-not (Test-Path -LiteralPath $statusPath -PathType Leaf)) {
    throw 'Grouped-model status.json is missing; prepare/pause the run before transfer.'
}
$status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($status.desired_state -ne 'paused' -or [int]$status.counts.running -ne 0 -or [int]$status.active -ne 0) {
    throw 'Run is not safely drained. Pause it and wait until active/running are both zero.'
}

$processPattern = 'run_grouped_models\.py|model_dashboard(?:_watchdog)?|la_heat\.model_dashboard'
$modelProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" | Where-Object {
    $_.CommandLine -and $_.CommandLine -match $processPattern
})
if ($modelProcesses.Count -ne 0) {
    throw 'A grouped-model coordinator/dashboard process is still alive. Close it before transfer.'
}

$sourceOwnershipPath = Join-Path $projectRoot 'data\interim\model_runs\transfer_ownership.json'
if (Test-Path -LiteralPath $sourceOwnershipPath) {
    $existingOwnership = Get-Content -LiteralPath $sourceOwnershipPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($existingOwnership.state -eq 'transferred_out') {
        throw 'This source checkpoint is already marked transferred_out; do not authorize a second laptop.'
    }
}

$sourcePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $sourcePython -PathType Leaf)) {
    throw 'Source .venv Python is required to lock dependencies and audit the bundle.'
}
& $sourcePython -m pytest
if ($LASTEXITCODE -ne 0) {
    throw 'Full source test suite failed; portable bundle creation is refused.'
}
& $sourcePython -m ruff check . --exclude exports
if ($LASTEXITCODE -ne 0) {
    throw 'Full source Ruff audit failed; portable bundle creation is refused.'
}

$copyDirectories = @(
    'src',
    'scripts',
    'tests',
    'configs',
    'docs',
    'tools',
    'portable_templates',
    'data\processed\model_dataset',
    'manifests\validation_splits',
    'manifests\model_selection'
)
foreach ($relativeDirectory in $copyDirectories) {
    Copy-FilteredTree -Source (Join-Path $projectRoot $relativeDirectory) -Target (Join-Path $destinationRoot $relativeDirectory)
}
foreach ($relativeFile in @('pyproject.toml', 'README.md')) {
    $sourceFile = Join-Path $projectRoot $relativeFile
    if (-not (Test-Path -LiteralPath $sourceFile -PathType Leaf)) {
        throw "Required project file is missing: $relativeFile"
    }
    Copy-Item -LiteralPath $sourceFile -Destination (Join-Path $destinationRoot $relativeFile)
}

$pythonVersion = (& $sourcePython -c 'import platform; print(platform.python_version())').Trim()
if ($LASTEXITCODE -ne 0 -or -not $pythonVersion) {
    throw 'Could not read the source Python version.'
}
Write-Utf8NoBom -Path (Join-Path $destinationRoot 'portable_python_version.txt') -Text ($pythonVersion + "`n")
Copy-PortablePythonRuntime `
    -SourcePython $sourcePython `
    -Target (Join-Path $destinationRoot 'runtime\python')
$pipPackages = (& $sourcePython -m pip list --format=json | ConvertFrom-Json) | Where-Object {
    $_.name -notin @('la-neighborhood-heat', 'pip')
} | Sort-Object { $_.name.ToLowerInvariant() }
$requirements = @($pipPackages | ForEach-Object { "$($_.name)==$($_.version)" })
if ($requirements.Count -eq 0) {
    throw 'Dependency lock generation returned no installed packages.'
}
Write-Utf8NoBom -Path (Join-Path $destinationRoot 'portable_requirements_lock.txt') -Text (($requirements -join "`n") + "`n")

$runtimeRoot = Join-Path $destinationRoot 'runtime'
[IO.Directory]::CreateDirectory($runtimeRoot) | Out-Null
$pythonInstaller = Join-Path $runtimeRoot 'python-3.14.4-amd64.exe'
$pythonInstallerUrl = 'https://www.python.org/ftp/python/3.14.4/python-3.14.4-amd64.exe'
$pythonInstallerSha256 = 'b571567bd11ea98fd7a2cf85791d2c8557a63b1e04e9d1dae665a275cac87f1b'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $pythonInstallerUrl -OutFile $pythonInstaller -UseBasicParsing
$observedInstallerSha = (Get-FileHash -LiteralPath $pythonInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
if ($observedInstallerSha -ne $pythonInstallerSha256) {
    throw 'Bundled official Python installer SHA-256 verification failed.'
}
$wheelhouse = Join-Path $runtimeRoot 'wheelhouse'
[IO.Directory]::CreateDirectory($wheelhouse) | Out-Null
& $sourcePython -m pip download --only-binary=:all: --requirement (Join-Path $destinationRoot 'portable_requirements_lock.txt') --dest $wheelhouse
if ($LASTEXITCODE -ne 0) {
    throw 'An exact locked binary wheel is unavailable; portable bundle creation failed closed.'
}

$relocationBuilder = Join-Path $destinationRoot 'scripts\create_portable_relocation.py'
$relocationPath = Join-Path $destinationRoot 'portable_relocation.json'
if (-not (Test-Path -LiteralPath $relocationBuilder -PathType Leaf)) {
    throw 'Copied bundle lacks scripts/create_portable_relocation.py.'
}
& $sourcePython $relocationBuilder --source-root $projectRoot --bundle-root $destinationRoot --output $relocationPath
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $relocationPath -PathType Leaf)) {
    throw 'Audited portable relocation manifest creation failed.'
}

$startCommand = @'
@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0portable_templates\setup_and_launch.ps1"
if errorlevel 1 pause
'@
$packageCommand = @'
@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0portable_templates\package_results.ps1"
if errorlevel 1 pause
'@
Write-Utf8NoBom -Path (Join-Path $destinationRoot 'START_HERE.cmd') -Text ($startCommand + "`r`n")
Write-Utf8NoBom -Path (Join-Path $destinationRoot 'PACKAGE_RESULTS.cmd') -Text ($packageCommand + "`r`n")

$textExtensions = @('.py', '.ps1', '.toml', '.json', '.csv', '.md', '.html', '.txt')
$secretNamePattern = '(?i)(^|[._-])(token|secret|credential|cookie|password|\.env|netrc|id_rsa)([._-]|$)|\.(pem|key|pfx|p12)$'
$jwtPattern = 'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}'
foreach ($file in Get-ChildItem -LiteralPath $destinationRoot -File -Recurse -Force) {
    if ($file.Name -match $secretNamePattern) {
        throw "Credential-like filename was copied; bundle rejected: $($file.Name)"
    }
    if ($file.Extension -in $textExtensions -and $file.Length -le 5MB) {
        $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
        if ($content -match $jwtPattern) {
            throw "JWT-like credential text was detected; bundle rejected: $($file.Name)"
        }
    }
}

$transferId = [guid]::NewGuid().ToString('N')
$createdAt = [DateTimeOffset]::UtcNow.ToString('o')
$sourceMachine = Get-MachineFingerprint
$authority = [ordered]@{
    schema_version = 1
    transfer_id = $transferId
    state = 'target_authorized'
    source_machine_sha256 = $sourceMachine
    target_machine_sha256 = $null
    source_project_root = $projectRoot
    created_at_utc = $createdAt
}
$authorityJson = $authority | ConvertTo-Json -Depth 5
Write-Utf8NoBom -Path (Join-Path $destinationRoot 'transfer_authority.json') -Text ($authorityJson + "`n")

$allFiles = @(Get-ChildItem -LiteralPath $destinationRoot -File -Recurse -Force | Where-Object {
    $_.Name -notin @('portable_bundle_manifest.json', 'transfer_authority.json')
})
$immutableRecords = @()
foreach ($file in $allFiles) {
    $record = Get-FileRecord -File $file -Root $destinationRoot
    $immutableRecords += $record
}
$manifest = [ordered]@{
    schema_version = 1
    bundle_revision = 2
    transfer_id = $transferId
    created_at_utc = $createdAt
    source_project_root = $projectRoot
    relocation_manifest = 'portable_relocation.json'
    queue_policy = 'fresh_queue_required_on_target'
    runtime_policy = 'bundled_minimal_python_exact_with_offline_wheelhouse'
    python_version = $pythonVersion
    source_status = [ordered]@{
        run_id = [string]$status.run_id
        state = [string]$status.state
        desired_state = [string]$status.desired_state
        completed = [int]$status.completed
        total = [int]$status.total
        running = [int]$status.counts.running
    }
    exclusions = @('.venv', '.venv-portable', '.git', '__pycache__', '*.pyc', 'credentials', 'tokens', 'dashboard locks')
    immutable_files = $immutableRecords
}
$manifestJson = $manifest | ConvertTo-Json -Depth 8
$bundleManifestPath = Join-Path $destinationRoot 'portable_bundle_manifest.json'
Write-Utf8NoBom -Path $bundleManifestPath -Text ($manifestJson + "`n")
$bundleManifestSha256 = (Get-FileHash -LiteralPath $bundleManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
$authority['bundle_manifest_sha256'] = $bundleManifestSha256
$authorityJson = $authority | ConvertTo-Json -Depth 5
Write-Utf8NoBom -Path (Join-Path $destinationRoot 'transfer_authority.json') -Text ($authorityJson + "`n")

$sourceOwnership = [ordered]@{
    schema_version = 1
    transfer_id = $transferId
    state = 'transferred_out'
    source_machine_sha256 = $sourceMachine
    bundle_path = $destinationRoot
    bundle_manifest_sha256 = $bundleManifestSha256
    created_at_utc = $createdAt
    warning = 'Do not run grouped models on this source while the target owns the checkpoint.'
}
$sourceOwnershipJson = $sourceOwnership | ConvertTo-Json -Depth 5
Write-Utf8NoBom -Path $sourceOwnershipPath -Text ($sourceOwnershipJson + "`n")
$disabledMarker = Join-Path $projectRoot 'RUN_DISABLED_TRANSFERRED_OUT.txt'
$disabledText = @"
GROUPED MODEL EXECUTION IS DISABLED ON THIS SOURCE COPY.
Transfer ID: $transferId
Created UTC: $createdAt
Authorized bundle: $destinationRoot

Do not start run_grouped_models.py, the model dashboard, or its watchdog here.
Return and verify the target result package before removing this marker.
"@
Write-Utf8NoBom -Path $disabledMarker -Text $disabledText

Write-Host "Portable folder created: $destinationRoot"
Write-Host 'Copy this one folder to any local folder on the laptop; setup will verify the audited relocation manifest.'
Write-Host 'The target intentionally starts a fresh queue; the source 42-task checkpoint is not copied.'
Write-Host "The original machine is now marked transferred_out. Do not run both copies."
