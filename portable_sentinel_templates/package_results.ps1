[CmdletBinding()]
param(
    [string]$DestinationDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-RelativePath {
    param([string]$Root, [string]$Path)
    # Windows PowerShell 5.1 runs on .NET Framework, where
    # System.IO.Path.GetRelativePath() does not exist.  The portable bundle is
    # intentionally launched with powershell.exe, so keep this implementation
    # compatible with both Windows PowerShell 5.1 and newer PowerShell versions.
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd(
        [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    )
    $pathFull = [IO.Path]::GetFullPath($Path)
    if ([string]::Equals($rootFull, $pathFull, [StringComparison]::OrdinalIgnoreCase)) {
        return '.'
    }
    $rootPrefix = $rootFull + [IO.Path]::DirectorySeparatorChar
    if (-not $pathFull.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside the requested root: root='$rootFull', path='$pathFull'"
    }
    return $pathFull.Substring($rootPrefix.Length)
}

function Test-ExcludedResultPath {
    param([string]$RelativePath)
    $normalized = $RelativePath.Replace('\', '/').ToLowerInvariant()
    $segments = $normalized.Split('/')
    if ($segments -contains 'tmp' -or $segments -contains 'locks') {
        return $true
    }
    $name = $segments[-1]
    return (
        $name.EndsWith('.lock') -or
        $name.EndsWith('.tmp') -or
        $name -match '(token|credential|cookie|secret)'
    )
}

function Copy-ResultTree {
    param([string]$Source, [string]$Target)
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        return
    }
    foreach ($file in Get-ChildItem -LiteralPath $Source -File -Recurse -Force) {
        $relative = Get-RelativePath -Root $Source -Path $file.FullName
        if (Test-ExcludedResultPath -RelativePath $relative) {
            continue
        }
        $destination = Join-Path $Target $relative
        [IO.Directory]::CreateDirectory((Split-Path -Parent $destination)) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }
}

function Copy-ResultFile {
    param([string]$Source, [string]$Target)
    if (Test-Path -LiteralPath $Source -PathType Leaf) {
        [IO.Directory]::CreateDirectory((Split-Path -Parent $Target)) | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Target -Force
    }
}

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$runtimeRelative = 'data\interim\multicity\portable_predictors\runtime\sentinel'
$runtimeRoot = Join-Path $projectRoot $runtimeRelative
$statusPath = Join-Path $runtimeRoot 'status.json'
if (-not (Test-Path -LiteralPath $statusPath -PathType Leaf)) {
    throw 'No Sentinel status.json exists. Start the task before packaging results.'
}
$status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
$state = ([string]$status.state).ToLowerInvariant()
$running = 0
if ($status.PSObject.Properties['running']) {
    $running = [int]$status.running
}
elseif ($status.PSObject.Properties['counts'] -and $status.counts.PSObject.Properties['running']) {
    $running = [int]$status.counts.running
}
if ($running -ne 0) {
    throw 'Tasks are still running. Click Safe Pause and wait for running=0, then package again.'
}
if ($state -notin @('complete', 'completed', 'paused', 'failed', 'blocked')) {
    throw "State '$state' is not ready to package. Complete the run or use Safe Pause first."
}

if (-not $DestinationDirectory) {
    $DestinationDirectory = Split-Path -Parent $projectRoot
}
$destinationRoot = [IO.Path]::GetFullPath($DestinationDirectory)
[IO.Directory]::CreateDirectory($destinationRoot) | Out-Null
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$baseName = "GAMING_LAPTOP_SENTINEL_RESULTS_$stamp"
$stagingRoot = Join-Path ([IO.Path]::GetTempPath()) ("sentinel-results-" + [guid]::NewGuid().ToString('N'))
$resultRoot = Join-Path $stagingRoot 'GAMING_LAPTOP_SENTINEL_RESULTS'
$zipPath = Join-Path $destinationRoot "$baseName.zip"
if (Test-Path -LiteralPath $zipPath) {
    throw "Destination already exists: $zipPath"
}

[IO.Directory]::CreateDirectory($resultRoot) | Out-Null
try {
    $treePaths = @(
        $runtimeRelative,
        'data\raw\multicity\portable_predictors',
        'data\raw\sentinel\product_metadata',
        'data\processed\multicity\portable_predictors',
        'manifests\multicity',
        'manifests\sentinel_inventory'
    )
    foreach ($relative in $treePaths) {
        Copy-ResultTree `
            -Source (Join-Path $projectRoot $relative) `
            -Target (Join-Path $resultRoot $relative)
    }
    foreach ($name in @('README_CN.txt', 'PORTABLE_BUNDLE_MANIFEST.json')) {
        Copy-ResultFile `
            -Source (Join-Path $projectRoot $name) `
            -Target (Join-Path $resultRoot $name)
    }

    $records = @(
        Get-ChildItem -LiteralPath $resultRoot -File -Recurse -Force |
            Sort-Object FullName |
            ForEach-Object {
                [ordered]@{
                    path = (Get-RelativePath -Root $resultRoot -Path $_.FullName).Replace('\', '/')
                    bytes = $_.Length
                    sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                }
            }
    )
    $manifest = [ordered]@{
        schema_version = 1
        packaged_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        source_state = $state
        source_status = $runtimeRelative.Replace('\', '/') + '/status.json'
        files = $records
    }
    $manifestJson = ($manifest | ConvertTo-Json -Depth 7) + "`n"
    [IO.File]::WriteAllText(
        (Join-Path $resultRoot 'RESULT_MANIFEST.json'),
        $manifestJson,
        (New-Object Text.UTF8Encoding($false))
    )

    Compress-Archive -LiteralPath $resultRoot -DestinationPath $zipPath -CompressionLevel Optimal
}
finally {
    $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    $resolvedStaging = [IO.Path]::GetFullPath($stagingRoot)
    if (
        $resolvedStaging.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedStaging).StartsWith('sentinel-results-')
    ) {
        Remove-Item -LiteralPath $resolvedStaging -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$zipSha = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$shaPath = "$zipPath.sha256"
[IO.File]::WriteAllText(
    $shaPath,
    "$zipSha *$(Split-Path -Leaf $zipPath)`n",
    (New-Object Text.UTF8Encoding($false))
)
Write-Host "Result package: $zipPath"
Write-Host "ZIP SHA-256:   $zipSha"
Write-Host "Checksum file: $shaPath"
