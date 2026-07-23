[CmdletBinding()]
param(
    [ValidateSet(6, 8)]
    [int]$Workers = 6,
    [string]$PythonExecutable,
    [switch]$NoPrompt,
    [switch]$NoBrowser,
    [switch]$VerifyOnly
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

function Test-FileRecords {
    param(
        [Parameter(Mandatory = $true)][object[]]$Records,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Label
    )
    foreach ($record in $Records) {
        $relative = ([string]$record.path).Replace('/', [IO.Path]::DirectorySeparatorChar)
        $path = [IO.Path]::GetFullPath((Join-Path $Root $relative))
        if (-not $path.StartsWith("$Root\", [StringComparison]::OrdinalIgnoreCase)) {
            throw "$Label manifest path escapes the project root: $relative"
        }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "$Label file is missing: $relative"
        }
        $file = Get-Item -LiteralPath $path
        if ($file.Length -ne [long]$record.bytes) {
            throw "$Label byte-size mismatch: $relative"
        }
        $observed = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($observed -ne [string]$record.sha256) {
            throw "$Label SHA-256 mismatch: $relative"
        }
    }
}

function Write-AtomicJson {
    param([Parameter(Mandatory = $true)]$Payload, [Parameter(Mandatory = $true)][string]$Path)
    $temporary = "$Path.$PID.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        $json = ($Payload | ConvertTo-Json -Depth 8) + "`n"
        $encoding = New-Object System.Text.UTF8Encoding($false)
        [IO.File]::WriteAllText($temporary, $json, $encoding)
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Remove-VerifiedTemporaryDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    $target = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $leaf = Split-Path -Leaf $target
    if (
        -not $target.StartsWith($temporaryRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not $leaf.StartsWith('isef-portable-verify-', [StringComparison]::Ordinal)
    ) {
        throw "Refusing to remove an unverified temporary directory: $target"
    }
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
}

function Get-ExactPythonLauncher {
    param(
        [Parameter(Mandatory = $true)][string]$RequiredVersion,
        [Parameter(Mandatory = $true)][string]$BundledInstaller,
        [Parameter(Mandatory = $true)][string]$BundledPython,
        [string]$PreferredPython
    )
    $parts = $RequiredVersion.Split('.')
    $majorMinor = "$($parts[0]).$($parts[1])"
    $localPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$($parts[0])$($parts[1])\python.exe"
    $installManagerPython = Join-Path $env:LOCALAPPDATA "Python\pythoncore-$majorMinor-64\python.exe"
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    $candidates = @($PreferredPython, $BundledPython, $installManagerPython, $localPython)
    if ($pythonCommand) {
        $candidates += $pythonCommand.Source
    }
    $seenCandidates = @{}
    foreach ($candidate in $candidates) {
        if (-not $candidate) {
            continue
        }
        $candidatePath = [IO.Path]::GetFullPath([string]$candidate)
        if ($seenCandidates.ContainsKey($candidatePath)) {
            continue
        }
        $seenCandidates[$candidatePath] = $true
        if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
            try {
                $observed = (& $candidatePath -c 'import platform; print(platform.python_version())' 2>$null).Trim()
                if ($observed -eq $RequiredVersion) {
                    return [pscustomobject]@{ File = $candidatePath; Prefix = @() }
                }
            }
            catch {
                # Continue to the next exact-version candidate.
            }
        }
    }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $observed = (& $launcher.Source "-$majorMinor" -c 'import platform; print(platform.python_version())' 2>$null).Trim()
            if ($observed -eq $RequiredVersion) {
                return [pscustomobject]@{ File = $launcher.Source; Prefix = @("-$majorMinor") }
            }
        }
        catch {
            # Installation is attempted below.
        }
    }
    if (Test-Path -LiteralPath $BundledInstaller -PathType Leaf) {
        $requiredInstallerSha = 'b571567bd11ea98fd7a2cf85791d2c8557a63b1e04e9d1dae665a275cac87f1b'
        $observedInstallerSha = (Get-FileHash -LiteralPath $BundledInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($observedInstallerSha -ne $requiredInstallerSha) {
            throw 'Bundled Python installer failed its frozen SHA-256 check.'
        }
        $installerArguments = @(
            '/quiet',
            'InstallAllUsers=0',
            'PrependPath=0',
            'Include_test=0',
            'Include_launcher=1'
        )
        $installerProcess = Start-Process -FilePath $BundledInstaller -ArgumentList $installerArguments -Wait -WindowStyle Hidden -PassThru
        if ($installerProcess.ExitCode -ne 0) {
            throw "Bundled Python installer exited with code $($installerProcess.ExitCode)."
        }
        if (Test-Path -LiteralPath $localPython -PathType Leaf) {
            $observed = (& $localPython -c 'import platform; print(platform.python_version())').Trim()
            if ($observed -eq $RequiredVersion) {
                return [pscustomobject]@{ File = $localPython; Prefix = @() }
            }
        }
    }
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python $RequiredVersion is required and winget is unavailable. Install that exact version, then rerun."
    }
    $packageId = "Python.Python.$majorMinor"
    & $winget.Source install --id $packageId --exact --version $RequiredVersion --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget could not install exact Python $RequiredVersion."
    }
    if (Test-Path -LiteralPath $localPython -PathType Leaf) {
        $observed = (& $localPython -c 'import platform; print(platform.python_version())').Trim()
        if ($observed -eq $RequiredVersion) {
            return [pscustomobject]@{ File = $localPython; Prefix = @() }
        }
    }
    throw "Exact Python $RequiredVersion was not found after installation."
}

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$disabledMarker = Join-Path $projectRoot 'RUN_DISABLED_TRANSFERRED_OUT.txt'
if (Test-Path -LiteralPath $disabledMarker -PathType Leaf) {
    throw 'This is the disabled source copy, not the authorized portable bundle. Launch refused.'
}
$manifestPath = Join-Path $projectRoot 'portable_bundle_manifest.json'
$authorityPath = Join-Path $projectRoot 'transfer_authority.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or -not (Test-Path -LiteralPath $authorityPath -PathType Leaf)) {
    throw 'This is not a prepared portable bundle (manifest/authority missing).'
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$authority = Get-Content -LiteralPath $authorityPath -Raw -Encoding UTF8 | ConvertFrom-Json
$observedBundleManifestSha = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ([string]$authority.bundle_manifest_sha256 -ne $observedBundleManifestSha) {
    throw 'Transfer authority does not authenticate this immutable bundle manifest.'
}
if ([string]$authority.transfer_id -ne [string]$manifest.transfer_id) {
    throw 'Transfer authority does not match the portable manifest.'
}
if ($authority.state -notin @('target_authorized', 'target_active')) {
    throw "Transfer authority state does not permit launch: $($authority.state)"
}

Test-FileRecords -Records @($manifest.immutable_files) -Root $projectRoot -Label 'Immutable bundle'
$machineFingerprint = Get-MachineFingerprint
if (-not $VerifyOnly -and $machineFingerprint -eq [string]$authority.source_machine_sha256) {
    throw 'Portable launch is forbidden on the source machine; ownership was transferred to one laptop.'
}
if ($authority.state -eq 'target_authorized') {
}
elseif (-not $VerifyOnly -and [string]$authority.target_machine_sha256 -ne $machineFingerprint) {
    throw 'This checkpoint is already active on a different target machine. Two-machine execution is forbidden.'
}

$runtimePath = Join-Path $projectRoot 'portable_runtime.json'
$controlPath = Join-Path $projectRoot 'data\interim\model_runs\dashboard_control.json'
$savedWorkers = 6
if (Test-Path -LiteralPath $controlPath -PathType Leaf) {
    try {
        $savedControl = Get-Content -LiteralPath $controlPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([int]$savedControl.workers -in @(6, 8)) {
            $savedWorkers = [int]$savedControl.workers
        }
    }
    catch {
        $savedWorkers = 6
    }
}
elseif (Test-Path -LiteralPath $runtimePath -PathType Leaf) {
    try {
        $runtime = Get-Content -LiteralPath $runtimePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([int]$runtime.workers -in @(6, 8)) {
            $savedWorkers = [int]$runtime.workers
        }
    }
    catch {
        $savedWorkers = 6
    }
}
if ($VerifyOnly) {
    $NoPrompt = $true
}
if (-not $PSBoundParameters.ContainsKey('Workers')) {
    $Workers = $savedWorkers
    if (-not $NoPrompt) {
        $choice = Read-Host "Workers (6 or 8; Enter keeps $savedWorkers)"
        if ($choice) {
            if ($choice -notin @('6', '8')) {
                throw 'Workers must be either 6 or 8.'
            }
            $Workers = [int]$choice
        }
    }
}

$requiredPythonVersion = (Get-Content -LiteralPath (Join-Path $projectRoot 'portable_python_version.txt') -Raw -Encoding UTF8).Trim()
$bundledInstaller = Join-Path $projectRoot 'runtime\python-3.14.4-amd64.exe'
$bundledPython = Join-Path $projectRoot 'runtime\python\python.exe'
$launcher = Get-ExactPythonLauncher `
    -RequiredVersion $requiredPythonVersion `
    -BundledInstaller $bundledInstaller `
    -BundledPython $bundledPython `
    -PreferredPython $PythonExecutable
$verificationVenv = $null
if ($VerifyOnly) {
    $verificationVenv = Join-Path ([IO.Path]::GetTempPath()) ("isef-portable-verify-" + [guid]::NewGuid().ToString('N'))
    $venvRoot = $verificationVenv
}
else {
    $venvRoot = Join-Path $projectRoot '.venv-portable'
}
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    & $launcher.File @($launcher.Prefix) -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
        throw 'Virtual-environment creation failed.'
    }
}
$venvVersion = (& $venvPython -c 'import platform; print(platform.python_version())').Trim()
if ($venvVersion -ne $requiredPythonVersion) {
    throw "Existing .venv-portable uses Python $venvVersion, expected $requiredPythonVersion. Remove only that venv and rerun."
}

$requirementsPath = Join-Path $projectRoot 'portable_requirements_lock.txt'
$requirementsSha = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
$environmentMarkerPath = Join-Path $venvRoot 'portable_environment.json'
$environmentReady = $false
if (Test-Path -LiteralPath $environmentMarkerPath -PathType Leaf) {
    try {
        $environmentMarker = Get-Content -LiteralPath $environmentMarkerPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $environmentReady = (
            [string]$environmentMarker.python_version -eq $requiredPythonVersion -and
            [string]$environmentMarker.requirements_sha256 -eq $requirementsSha
        )
    }
    catch {
        $environmentReady = $false
    }
}
if (-not $environmentReady) {
    $wheelhouse = Join-Path $projectRoot 'runtime\wheelhouse'
    if (-not (Test-Path -LiteralPath $wheelhouse -PathType Container)) {
        throw 'Offline wheelhouse is missing.'
    }
    & $venvPython -m pip install --no-index --find-links $wheelhouse --requirement $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Locked dependency installation failed.'
    }
    Write-AtomicJson -Payload ([ordered]@{
        python_version = $requiredPythonVersion
        requirements_sha256 = $requirementsSha
        installed_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    }) -Path $environmentMarkerPath
}
$env:PYTHONPATH = Join-Path $projectRoot 'src'

$relocationPath = Join-Path $projectRoot 'portable_relocation.json'
$relocationVerifier = Join-Path $projectRoot 'scripts\verify_portable_relocation.py'
if (-not (Test-Path -LiteralPath $relocationPath -PathType Leaf) -or -not (Test-Path -LiteralPath $relocationVerifier -PathType Leaf)) {
    throw 'Signed relocation manifest or its validator is missing from this bundle.'
}
& $venvPython $relocationVerifier --project-root $projectRoot --manifest $relocationPath
if ($LASTEXITCODE -ne 0) {
    throw 'Portable relocation manifest validation failed.'
}
$env:LA_HEAT_PORTABLE_RELOCATION = $relocationPath

$smokeTests = @(
    (Join-Path $projectRoot 'tests\test_portable_relocation.py'),
    (Join-Path $projectRoot 'tests\test_model_dashboard.py'),
    (Join-Path $projectRoot 'tests\test_model_run_queue.py')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
if ($smokeTests.Count -eq 0) {
    throw 'Portable smoke tests are missing from the bundle.'
}
& $venvPython -m pytest @smokeTests -q
if ($LASTEXITCODE -ne 0) {
    throw 'Target portability/dashboard/queue smoke tests failed.'
}
$contextSmoke = "import os,sys; from pathlib import Path; from la_heat.model_run_context import load_model_run_context; from la_heat.model_task_engine import build_task_plan; root=Path(sys.argv[1]); context=load_model_run_context(portable_manifest_path=os.environ['LA_HEAT_PORTABLE_RELOCATION'], portable_root=root); plan=build_task_plan(context.fold_definitions, context.model_selection); assert len(plan.inner_tasks)==55645 and len(plan.outer_tasks)==2155; print(context.run_id)"
& $venvPython -c $contextSmoke $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Relocated frozen model context failed authentication.'
}
if ($VerifyOnly) {
    if ($verificationVenv) {
        Remove-VerifiedTemporaryDirectory -Path $verificationVenv
    }
    Write-Host 'VERIFY-ONLY PASS: immutable files, offline runtime, relocation, context, and 57,800-task plan are valid.'
    exit 0
}

$queuePath = Join-Path $projectRoot 'data\interim\model_runs\model_tasks.sqlite3'
if (-not (Test-Path -LiteralPath $queuePath -PathType Leaf)) {
    & $venvPython (Join-Path $projectRoot 'scripts\run_grouped_models.py') --workers $Workers --prepare-only
    if ($LASTEXITCODE -ne 0) {
        throw 'Fresh target queue preparation failed.'
    }
    $statusPath = Join-Path $projectRoot 'data\interim\model_runs\status.json'
    $pauseCode = "import json,sys; from pathlib import Path; from la_heat.model_run_queue import ModelRunQueue; status=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')); ModelRunQueue(sys.argv[1]).set_desired_state(status['run_id'],'paused')"
    & $venvPython -c $pauseCode $queuePath $statusPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Fresh target queue could not be placed into initial paused state.'
    }
    & $venvPython (Join-Path $projectRoot 'scripts\run_grouped_models.py') --workers $Workers --prepare-only
    if ($LASTEXITCODE -ne 0) {
        throw 'Paused queue status publication failed.'
    }
}

if ($authority.state -eq 'target_authorized') {
    $authority.state = 'target_active'
    $authority.target_machine_sha256 = $machineFingerprint
    $authority | Add-Member -NotePropertyName activated_at_utc -NotePropertyValue ([DateTimeOffset]::UtcNow.ToString('o')) -Force
    Write-AtomicJson -Payload $authority -Path $authorityPath
}

$desiredState = 'paused'
if (Test-Path -LiteralPath $controlPath -PathType Leaf) {
    try {
        $savedControl = Get-Content -LiteralPath $controlPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$savedControl.desired_state -in @('running', 'paused')) {
            $desiredState = [string]$savedControl.desired_state
        }
    }
    catch {
        $desiredState = 'paused'
    }
}
Write-AtomicJson -Payload ([ordered]@{
    schema_version = 1
    desired_state = $desiredState
    workers = $Workers
    updated_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
}) -Path $controlPath
Write-AtomicJson -Payload ([ordered]@{
    schema_version = 1
    workers = $Workers
    port = 8766
    target_machine_sha256 = $machineFingerprint
    relocation_manifest = $relocationPath
    updated_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
}) -Path $runtimePath

$dashboardUrl = 'http://127.0.0.1:8766/'
$alreadyRunning = $false
try {
    $null = Invoke-RestMethod -Uri "${dashboardUrl}api/status" -TimeoutSec 2
    $alreadyRunning = $true
}
catch {
    $alreadyRunning = $false
}
if (-not $alreadyRunning) {
    $listeners = @(Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -ne 0) {
        throw 'Port 8766 is occupied by another application.'
    }
    $arguments = @(
        '-m', 'la_heat.model_dashboard_watchdog',
        '--workers', [string]$Workers,
        '--host', '127.0.0.1',
        '--port', '8766',
        '--run-directory', 'data/interim/model_runs',
        '--no-browser'
    )
    $process = Start-Process -FilePath $venvPython -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $null = Invoke-RestMethod -Uri "${dashboardUrl}api/status" -TimeoutSec 2
            $alreadyRunning = $true
            break
        }
        catch {
            if ($process.HasExited) {
                throw "Dashboard watchdog exited with code $($process.ExitCode)."
            }
        }
    }
    if (-not $alreadyRunning) {
        throw 'Dashboard did not become ready within 30 seconds.'
    }
}
if (-not $NoBrowser) {
    Start-Process $dashboardUrl
}
Write-Host "Dashboard: $dashboardUrl"
Write-Host "Workers: $Workers (the persisted choice can be 6 or 8)"
Write-Host 'The queue remains paused. Confirm 6/8 workers in the page, then click Start/Resume.'
Write-Host 'Start/pause intent and SQLite checkpoints are persistent.'
