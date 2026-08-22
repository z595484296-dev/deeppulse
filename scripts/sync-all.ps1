param(
    [string]$HarnessRoot,
    [string]$InstalledRoot,
    [string]$DesktopOutput,
    [switch]$VerifyOnly,
    [switch]$SkipHarnessBuild,
    [switch]$SkipDesktopBuild
)

$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot '..\..'))
if ([string]::IsNullOrWhiteSpace($HarnessRoot)) {
    $HarnessRoot = Join-Path $workspaceRoot 'deepseek-harness'
}
if ([string]::IsNullOrWhiteSpace($InstalledRoot)) {
    $installedFolderName = 'deepseek' + [char]0x8EAB + [char]0x4F53
    $InstalledRoot = Join-Path ([Environment]::GetFolderPath('Desktop')) $installedFolderName
}
if ([string]::IsNullOrWhiteSpace($DesktopOutput)) {
    $DesktopOutput = Join-Path $workspaceRoot 'outputs\DeepSeekHarnessDesktop'
}
$HarnessRoot = [IO.Path]::GetFullPath($HarnessRoot)
$InstalledRoot = [IO.Path]::GetFullPath($InstalledRoot)
$DesktopOutput = [IO.Path]::GetFullPath($DesktopOutput)

$manifest = Get-Content -LiteralPath (Join-Path $repoRoot 'deeppulse.manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$version = [string]$manifest.version
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Invalid version in deeppulse.manifest.json: $version"
}
if (-not (Test-Path -LiteralPath (Join-Path $HarnessRoot 'apps\web\package.json'))) {
    throw "Invalid DeepSeek Harness path: $HarnessRoot"
}

$methodologyFile = -join @([char]0x60C5, [char]0x7EEA, [char]0x5468, [char]0x671F, [char]0x65B9, [char]0x6CD5, [char]0x8BBA) + '.md'
$runtimeFiles = @(
    'server.py',
    'emotion.py',
    'event_impact.py',
    'attention_triage.py',
    'observation_rules.py',
    'research_hypothesis.py',
    'research_memory.py',
    'research_workflow.py',
    'research_watch.py',
    'ai_research_duty.py',
    'ai_provider.py',
    'ai_service_management.py',
    'research_suggestions.py',
    'akshare_research.py',
    'hypothesis_evidence.py',
    'tdx_local.py',
    'deeppulse.manifest.json',
    'README.md',
    'README-zh.txt',
    'PRODUCT_ROADMAP.md',
    $methodologyFile,
    'LICENSE',
    'SECURITY.md',
    'CONTRIBUTING.md',
    'start-deeppulse.bat'
)
$harnessOverlaySource = Join-Path $repoRoot 'integrations\deepseek-harness\app\DeepPulseOverlay.tsx'
$harnessOverlayTarget = Join-Path $HarnessRoot 'packages\client\web\src\DeepPulseOverlay.tsx'
$harnessTestSource = Join-Path $repoRoot 'integrations\deepseek-harness\app\tests\deeppulse-overlay.client.spec.tsx'
$harnessTestTarget = Join-Path $HarnessRoot 'packages\client\web\tests\deeppulse-overlay.client.spec.tsx'
$harnessWebTarget = Join-Path $HarnessRoot 'apps\web\public\deeppulse'
$installedWebTarget = Join-Path $InstalledRoot 'web'
$portableRuntime = Join-Path $DesktopOutput 'DeepPulse'

function Copy-One([string]$Source, [string]$Destination) {
    $parent = Split-Path $Destination -Parent
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

function Copy-Tree([string]$SourceRoot, [string]$DestinationRoot) {
    foreach ($file in Get-ChildItem -LiteralPath $SourceRoot -Recurse -File) {
        $relative = $file.FullName.Substring($SourceRoot.Length).TrimStart('\')
        Copy-One $file.FullName (Join-Path $DestinationRoot $relative)
    }
}

function Assert-Same([string]$Source, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Destination)) {
        throw "Missing synchronized file: $Destination"
    }
    $sourceHash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
    $destinationHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
    if ($sourceHash -ne $destinationHash) {
        throw "Synchronized file mismatch: $Destination"
    }
}

function Assert-Tree([string]$SourceRoot, [string]$DestinationRoot) {
    foreach ($file in Get-ChildItem -LiteralPath $SourceRoot -Recurse -File) {
        $relative = $file.FullName.Substring($SourceRoot.Length).TrimStart('\')
        Assert-Same $file.FullName (Join-Path $DestinationRoot $relative)
    }
}

if (-not $VerifyOnly) {
    New-Item -ItemType Directory -Path $InstalledRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $DesktopOutput -Force | Out-Null

    foreach ($relative in $runtimeFiles) {
        $source = Join-Path $repoRoot $relative
        Copy-One $source (Join-Path $InstalledRoot $relative)
        Copy-One $source (Join-Path $portableRuntime $relative)
    }
    Copy-Tree (Join-Path $repoRoot 'web') $installedWebTarget
    Copy-Tree (Join-Path $repoRoot 'web') (Join-Path $portableRuntime 'web')
    Copy-Tree (Join-Path $repoRoot 'web') $harnessWebTarget
    Copy-Tree (Join-Path $repoRoot 'hardware') (Join-Path $InstalledRoot 'hardware')
    Copy-Tree (Join-Path $repoRoot 'hardware') (Join-Path $portableRuntime 'hardware')
    Copy-One $harnessOverlaySource $harnessOverlayTarget
    Copy-One $harnessTestSource $harnessTestTarget

    if (-not $SkipHarnessBuild) {
        $pnpm = Get-ChildItem -LiteralPath (Join-Path $workspaceRoot 'work\runtime') -Recurse -Filter 'pnpm.cmd' -File |
            Where-Object { $_.FullName -match 'node-v[^\\]+-win-x64\\pnpm\.cmd$' } |
            Sort-Object FullName -Descending |
            Select-Object -First 1 -ExpandProperty FullName
        if (-not $pnpm) {
            throw 'Portable pnpm.cmd was not found.'
        }
        $runtimeBin = Split-Path $pnpm -Parent
        $corepack = Join-Path $runtimeBin 'corepack.cmd'
        $packageManager = [string](Get-Content -LiteralPath (Join-Path $HarnessRoot 'package.json') -Raw -Encoding UTF8 | ConvertFrom-Json).packageManager
        if (-not (Test-Path -LiteralPath $corepack) -or $packageManager -notmatch '^pnpm@\d+\.\d+\.\d+$') {
            throw 'Compatible Corepack or packageManager metadata was not found.'
        }
        $previousPath = $env:PATH
        try {
            $env:PATH = "$runtimeBin$([IO.Path]::PathSeparator)$previousPath"
            & $corepack $packageManager --dir $HarnessRoot run build:lib:client
            if ($LASTEXITCODE -ne 0) { throw 'Harness client build failed.' }
            & $corepack $packageManager --dir $HarnessRoot run build:web
            if ($LASTEXITCODE -ne 0) { throw 'Harness web build failed.' }
        }
        finally {
            $env:PATH = $previousPath
        }
    }

    if (-not $SkipDesktopBuild) {
        $publishDirectory = Join-Path $repoRoot 'out\desktop-publish'
        dotnet publish (Join-Path $repoRoot 'desktop\DeepSeekHarnessDesktop.csproj') `
            -c Release -r win-x64 --self-contained true `
            -p:PublishSingleFile=true -o $publishDirectory
        if ($LASTEXITCODE -ne 0) { throw 'Desktop app build failed.' }
        Copy-One (Join-Path $publishDirectory 'DeepSeekHarnessDesktop.exe') `
            (Join-Path $DesktopOutput 'DeepSeekHarnessDesktop.exe')
    }
    Copy-One (Join-Path $repoRoot 'desktop\README-zh.txt') (Join-Path $DesktopOutput 'README-zh.txt')
}

foreach ($relative in $runtimeFiles) {
    $source = Join-Path $repoRoot $relative
    Assert-Same $source (Join-Path $InstalledRoot $relative)
    Assert-Same $source (Join-Path $portableRuntime $relative)
}
Assert-Tree (Join-Path $repoRoot 'web') $installedWebTarget
Assert-Tree (Join-Path $repoRoot 'web') (Join-Path $portableRuntime 'web')
Assert-Tree (Join-Path $repoRoot 'web') $harnessWebTarget
Assert-Tree (Join-Path $repoRoot 'hardware') (Join-Path $InstalledRoot 'hardware')
Assert-Tree (Join-Path $repoRoot 'hardware') (Join-Path $portableRuntime 'hardware')
Assert-Same $harnessOverlaySource $harnessOverlayTarget
Assert-Same $harnessTestSource $harnessTestTarget

if (-not $SkipHarnessBuild) {
    Assert-Tree (Join-Path $repoRoot 'web') (Join-Path $HarnessRoot 'apps\web\dist\deeppulse')
}
if (-not $SkipDesktopBuild -and -not (Test-Path -LiteralPath (Join-Path $DesktopOutput 'DeepSeekHarnessDesktop.exe'))) {
    throw 'Desktop app artifact is missing.'
}

$report = [ordered]@{
    ok = $true
    version = $version
    syncedAt = [DateTimeOffset]::Now.ToString('o')
    source = $repoRoot
    harness = $HarnessRoot
    installed = $InstalledRoot
    desktopOutput = $DesktopOutput
    surfaces = @('standalone', 'installed-runtime', 'harness-embedded', 'desktop-app')
}
if (-not $VerifyOnly) {
    $report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $DesktopOutput 'sync-report.json') -Encoding UTF8
}
$report | ConvertTo-Json -Depth 4
