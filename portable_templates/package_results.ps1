[CmdletBinding()]
param(
    [string]$DestinationZip,
    [switch]$AllowPausedCheckpoint
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

function Remove-VerifiedStagingDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    $target = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $leaf = Split-Path -Leaf $target
    if (
        -not $target.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not $leaf.StartsWith('isef-results-', [StringComparison]::Ordinal)
    ) {
        throw "Refusing to remove an unverified result staging directory: $target"
    }
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
}

function Copy-Tree {
    param([Parameter(Mandatory = $true)][string]$Source, [Parameter(Mandatory = $true)][string]$Target)
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        return
    }
    Get-ChildItem -LiteralPath $Source -File -Recurse -Force | ForEach-Object {
        if ($_.Name -match '\.(sqlite3-wal|sqlite3-shm)$' -or $_.Name -match '\.lock$') {
            return
        }
        $relative = Get-SafeRelativePath -Root $Source -Path $_.FullName
        $destination = Join-Path $Target $relative
        [IO.Directory]::CreateDirectory((Split-Path -Parent $destination)) | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $destination
    }
}

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$authorityPath = Join-Path $projectRoot 'transfer_authority.json'
$authority = Get-Content -LiteralPath $authorityPath -Raw -Encoding UTF8 | ConvertFrom-Json
$machineFingerprint = Get-MachineFingerprint
if ($authority.state -ne 'target_active' -or [string]$authority.target_machine_sha256 -ne $machineFingerprint) {
    throw 'This machine does not own the active portable checkpoint.'
}
$statusPath = Join-Path $projectRoot 'data\interim\model_runs\status.json'
$status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$status.counts.running -ne 0 -or [int]$status.active -ne 0) {
    throw 'Active model tasks remain. Request safe pause and wait for running/active to reach zero.'
}
if (-not $AllowPausedCheckpoint -and $status.state -ne 'complete') {
    throw 'Final result packaging requires state=complete. Use -AllowPausedCheckpoint only for a paused handoff.'
}
if ($AllowPausedCheckpoint -and $status.state -notin @('paused', 'complete', 'blocked')) {
    throw "Checkpoint state is not packageable: $($status.state)"
}
if ($status.state -eq 'paused' -and $status.desired_state -ne 'paused') {
    throw 'Paused checkpoint status does not persist desired_state=paused.'
}

if (-not $DestinationZip) {
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    $DestinationZip = Join-Path (Split-Path -Parent $projectRoot) "ISEF_model_results_$stamp.zip"
}
$zipPath = [IO.Path]::GetFullPath($DestinationZip)
if (Test-Path -LiteralPath $zipPath) {
    throw "Destination zip already exists: $zipPath"
}
$staging = Join-Path ([IO.Path]::GetTempPath()) ("isef-results-" + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($staging) | Out-Null
try {
    $resultRoot = Join-Path $staging 'ISEF_model_results'
    [IO.Directory]::CreateDirectory($resultRoot) | Out-Null
    $runSource = Join-Path $projectRoot 'data\interim\model_runs'
    $runTarget = Join-Path $resultRoot 'data\interim\model_runs'
    Copy-Tree -Source (Join-Path $runSource 'runs') -Target (Join-Path $runTarget 'runs')
    foreach ($name in @('status.json', 'calibration.json', 'dashboard_control.json')) {
        $source = Join-Path $runSource $name
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            [IO.Directory]::CreateDirectory($runTarget) | Out-Null
            Copy-Item -LiteralPath $source -Destination (Join-Path $runTarget $name)
        }
    }
    $venvPython = Join-Path $projectRoot '.venv-portable\Scripts\python.exe'
    $helper = Join-Path $projectRoot 'scripts\transfer_queue_snapshot.py'
    $queueSource = Join-Path $runSource 'model_tasks.sqlite3'
    $queueTarget = Join-Path $runTarget 'model_tasks.sqlite3'
    $snapshotArguments = @($helper, $queueSource, $queueTarget)
    if ($status.state -in @('complete', 'blocked')) {
        $snapshotArguments += '--allow-nonpaused-terminal'
    }
    & $venvPython @snapshotArguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Consistent result-queue backup failed.'
    }
    Copy-Tree -Source (Join-Path $projectRoot 'data\processed\model_evaluation') -Target (Join-Path $resultRoot 'data\processed\model_evaluation')
    Copy-Item -LiteralPath $authorityPath -Destination (Join-Path $resultRoot 'transfer_authority.json')
    foreach ($auditName in @(
        'portable_bundle_manifest.json',
        'portable_relocation.json',
        'portable_requirements_lock.txt',
        'portable_python_version.txt'
    )) {
        $auditSource = Join-Path $projectRoot $auditName
        if (-not (Test-Path -LiteralPath $auditSource -PathType Leaf)) {
            throw "Required portable audit root is missing: $auditName"
        }
        Copy-Item -LiteralPath $auditSource -Destination (Join-Path $resultRoot $auditName)
    }

    $records = @(Get-ChildItem -LiteralPath $resultRoot -File -Recurse | Sort-Object FullName | ForEach-Object {
        [ordered]@{
            path = (Get-SafeRelativePath -Root $resultRoot -Path $_.FullName).Replace('\', '/')
            bytes = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    })
    $bundleManifestPath = Join-Path $projectRoot 'portable_bundle_manifest.json'
    $relocationManifestPath = Join-Path $projectRoot 'portable_relocation.json'
    $relocation = Get-Content -LiteralPath $relocationManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $relocationCommit = $null
    if ($relocation.PSObject.Properties['commit_sha256']) {
        $relocationCommit = [string]$relocation.commit_sha256
    }
    $resultManifest = [ordered]@{
        schema_version = 1
        transfer_id = [string]$authority.transfer_id
        packaged_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        source_state = [string]$status.state
        run_id = [string]$status.run_id
        completed = [int]$status.completed
        total = [int]$status.total
        bundle_manifest_sha256 = (Get-FileHash -LiteralPath $bundleManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
        authority_bundle_manifest_sha256 = [string]$authority.bundle_manifest_sha256
        relocation_manifest_sha256 = (Get-FileHash -LiteralPath $relocationManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
        relocation_commit_sha256 = $relocationCommit
        immutable_file_records_embedded_in = 'portable_bundle_manifest.json'
        files = $records
    }
    $resultJson = ($resultManifest | ConvertTo-Json -Depth 8) + "`n"
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $resultRoot 'result_manifest.json'), $resultJson, $encoding)
    [IO.Directory]::CreateDirectory((Split-Path -Parent $zipPath)) | Out-Null
    Compress-Archive -LiteralPath $resultRoot -DestinationPath $zipPath -CompressionLevel Optimal
}
finally {
    Remove-VerifiedStagingDirectory -Path $staging
}

Write-Host "Result package created: $zipPath"
Write-Host 'Do not resume the source copy until this package has been returned and verified.'
