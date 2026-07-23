[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Bundle,
    [Parameter(Mandatory = $true)][string]$SourceOwnership
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-AtomicUtf8Json {
    param(
        [Parameter(Mandatory = $true)]$Payload,
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$Depth = 10
    )
    $temporary = "$Path.$PID.$([guid]::NewGuid().ToString('N')).tmp"
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText(
        $temporary,
        (($Payload | ConvertTo-Json -Depth $Depth) + "`n"),
        $encoding
    )
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$bundleRoot = [IO.Path]::GetFullPath($Bundle).TrimEnd('\')
$ownershipPath = [IO.Path]::GetFullPath($SourceOwnership)
$manifestPath = Join-Path $bundleRoot 'portable_bundle_manifest.json'
$authorityPath = Join-Path $bundleRoot 'transfer_authority.json'
foreach ($requiredPath in @($manifestPath, $authorityPath, $ownershipPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required transfer record is missing: $requiredPath"
    }
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$authority = Get-Content -LiteralPath $authorityPath -Raw -Encoding UTF8 | ConvertFrom-Json
$ownership = Get-Content -LiteralPath $ownershipPath -Raw -Encoding UTF8 | ConvertFrom-Json
$oldManifestSha = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ([string]$authority.bundle_manifest_sha256 -ne $oldManifestSha) {
    throw 'Existing transfer authority does not authenticate the pre-repair manifest.'
}
if ($authority.state -ne 'target_authorized' -or $authority.target_machine_sha256) {
    throw 'Only an unactivated target_authorized bundle may be repaired.'
}
if ($ownership.state -ne 'transferred_out') {
    throw 'The source ownership record is not transferred_out.'
}
if (
    [string]$manifest.transfer_id -ne [string]$authority.transfer_id -or
    [string]$ownership.transfer_id -ne [string]$authority.transfer_id
) {
    throw 'Manifest, authority, and source ownership transfer IDs do not agree.'
}
if (
    [IO.Path]::GetFullPath([string]$ownership.bundle_path).TrimEnd('\') -ne
    $bundleRoot
) {
    throw 'Source ownership points to a different bundle.'
}

$bundlePrefix = $bundleRoot + '\'
$records = @(
    Get-ChildItem -LiteralPath $bundleRoot -File -Recurse -Force |
        Where-Object {
            $_.FullName -ne $manifestPath -and $_.FullName -ne $authorityPath
        } |
        ForEach-Object {
            $fullPath = [IO.Path]::GetFullPath($_.FullName)
            if (-not $fullPath.StartsWith($bundlePrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "File escaped bundle root: $fullPath"
            }
            [pscustomobject][ordered]@{
                path = $fullPath.Substring($bundlePrefix.Length).Replace('\', '/')
                bytes = $_.Length
                sha256 = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        } |
        Sort-Object path
)

$repairTime = [DateTimeOffset]::UtcNow.ToString('o')
$manifest.immutable_files = $records
$manifest | Add-Member -NotePropertyName bundle_revision -NotePropertyValue 2 -Force
$manifest | Add-Member `
    -NotePropertyName runtime_policy `
    -NotePropertyValue 'bundled_minimal_python_exact_with_offline_wheelhouse' `
    -Force
$manifest | Add-Member -NotePropertyName repaired_at_utc -NotePropertyValue $repairTime -Force
$manifest | Add-Member `
    -NotePropertyName repair_reason `
    -NotePropertyValue 'Pre-activation exact-Python launcher repair for installer conflict 1638.' `
    -Force
Write-AtomicUtf8Json -Payload $manifest -Path $manifestPath -Depth 10

$manifestSha = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
$authority.bundle_manifest_sha256 = $manifestSha
$authority | Add-Member -NotePropertyName bundle_revision -NotePropertyValue 2 -Force
$authority | Add-Member -NotePropertyName repaired_at_utc -NotePropertyValue $repairTime -Force
Write-AtomicUtf8Json -Payload $authority -Path $authorityPath -Depth 6

$ownership.bundle_manifest_sha256 = $manifestSha
$ownership | Add-Member -NotePropertyName bundle_revision -NotePropertyValue 2 -Force
$ownership | Add-Member -NotePropertyName repaired_at_utc -NotePropertyValue $repairTime -Force
Write-AtomicUtf8Json -Payload $ownership -Path $ownershipPath -Depth 6

Write-Host "Transfer ID: $($authority.transfer_id)"
Write-Host "Immutable files: $($records.Count)"
Write-Host "Bundle manifest SHA-256: $manifestSha"
Write-Host 'Authority remains target_authorized and unactivated.'
