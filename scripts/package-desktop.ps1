param(
    [string]$DesktopRoot,
    [string]$OutputDirectory,
    [string]$ArchiveName
)

$ErrorActionPreference = 'Stop'

$repoRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot '..\..'))
if ([string]::IsNullOrWhiteSpace($DesktopRoot)) {
    $DesktopRoot = Join-Path $workspaceRoot 'outputs\DeepSeekHarnessDesktop'
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $workspaceRoot 'outputs'
}
$DesktopRoot = [IO.Path]::GetFullPath($DesktopRoot)
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)

$manifestPath = Join-Path $DesktopRoot 'DeepPulse\deeppulse.manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "Desktop runtime manifest was not found: $manifestPath"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$version = [string]$manifest.version
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Invalid DeepPulse version: $version"
}
if ([string]::IsNullOrWhiteSpace($ArchiveName)) {
    $ArchiveName = "DeepSeekHarnessDesktop-v$version-FullSync-$((Get-Date).ToString('yyyyMMdd')).zip"
}
if ([IO.Path]::GetFileName($ArchiveName) -ne $ArchiveName -or $ArchiveName -notmatch '\.zip$') {
    throw 'ArchiveName must be a plain .zip filename.'
}

$desktopExecutable = Join-Path $DesktopRoot 'DeepSeekHarnessDesktop.exe'
$requiredFiles = @(
    $desktopExecutable,
    (Join-Path $DesktopRoot 'README-zh.txt'),
    (Join-Path $DesktopRoot 'sync-report.json'),
    $manifestPath
)
foreach ($required in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required desktop package file is missing: $required"
    }
}

$runningExecutable = Get-CimInstance Win32_Process -Filter "Name='DeepSeekHarnessDesktop.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.ExecutablePath -and
        [IO.Path]::GetFullPath($_.ExecutablePath) -eq [IO.Path]::GetFullPath($desktopExecutable)
    } |
    Select-Object -First 1
if ($runningExecutable) {
    throw 'Close DeepSeekHarnessDesktop.exe before packaging so the executable can be copied safely.'
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$destination = [IO.Path]::GetFullPath((Join-Path $OutputDirectory $ArchiveName))
if (-not $destination.StartsWith($OutputDirectory + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Resolved archive destination is outside OutputDirectory.'
}
if (Test-Path -LiteralPath $destination) {
    throw "Archive already exists: $destination"
}

$tempRoot = [IO.Path]::Combine(
    [IO.Path]::GetTempPath(),
    'deeppulse-package-' + [Guid]::NewGuid().ToString('N'))
$stageRoot = Join-Path $tempRoot 'DeepSeekHarnessDesktop'
$tempArchive = Join-Path $tempRoot $ArchiveName
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

function Copy-One([string]$Source, [string]$Destination) {
    $parent = Split-Path $Destination -Parent
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

try {
    foreach ($file in Get-ChildItem -LiteralPath $DesktopRoot -Recurse -File) {
        $relative = $file.FullName.Substring($DesktopRoot.Length).TrimStart('\')
        if ($relative -match '(^|\\)(data|__pycache__)(\\|$)' -or $relative -match '\.pyc$') {
            continue
        }
        Copy-One $file.FullName (Join-Path $stageRoot $relative)
    }

    $stagedRequired = @(
        (Join-Path $stageRoot 'DeepSeekHarnessDesktop.exe'),
        (Join-Path $stageRoot 'README-zh.txt'),
        (Join-Path $stageRoot 'sync-report.json'),
        (Join-Path $stageRoot 'DeepPulse\deeppulse.manifest.json')
    )
    foreach ($required in $stagedRequired) {
        if (-not (Test-Path -LiteralPath $required)) {
            throw "Required file was not staged: $required"
        }
    }

    $textFiles = Get-ChildItem -LiteralPath $stageRoot -Recurse -File |
        Where-Object { $_.Length -lt 10MB -and $_.Extension -match '^\.(txt|md|json|js|css|html|py|ps1|bat)$' }
    $secretMatches = $textFiles | Select-String -Pattern (
        '(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{12,}' +
        '|authorization\s*[:=]\s*bearer\s+[A-Za-z0-9_.-]{12,}'
    ) -ErrorAction SilentlyContinue
    if (@($secretMatches).Count -gt 0) {
        throw 'Potential credential material was found in the staged desktop package.'
    }

    Compress-Archive -LiteralPath $stageRoot -DestinationPath $tempArchive -CompressionLevel Optimal

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($tempArchive)
    try {
        $entryNames = @($archive.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
        $expectedEntries = @(
            'DeepSeekHarnessDesktop/DeepSeekHarnessDesktop.exe',
            'DeepSeekHarnessDesktop/README-zh.txt',
            'DeepSeekHarnessDesktop/sync-report.json',
            'DeepSeekHarnessDesktop/DeepPulse/deeppulse.manifest.json'
        )
        foreach ($expected in $expectedEntries) {
            if ($entryNames -notcontains $expected) {
                throw "Required archive entry is missing: $expected"
            }
        }
        if (@($entryNames | Where-Object { $_ -match '(^|/)data/' }).Count -gt 0) {
            throw 'Runtime data unexpectedly entered the desktop archive.'
        }
    }
    finally {
        $archive.Dispose()
    }

    Move-Item -LiteralPath $tempArchive -Destination $destination
    $result = [ordered]@{
        ok = $true
        version = $version
        archive = $destination
        bytes = (Get-Item -LiteralPath $destination).Length
        sha256 = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
        runtimeDataIncluded = $false
        secretMatches = 0
    }
    $result | ConvertTo-Json -Depth 3
}
finally {
    $resolvedTempRoot = [IO.Path]::GetFullPath($tempRoot)
    $systemTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (($resolvedTempRoot.StartsWith($systemTemp, [StringComparison]::OrdinalIgnoreCase)) -and
        ((Split-Path $resolvedTempRoot -Leaf).StartsWith('deeppulse-package-'))) {
        Remove-Item -LiteralPath $resolvedTempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
