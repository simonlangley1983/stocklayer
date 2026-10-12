param(
    [Parameter(Mandatory)][ValidatePattern('^[0-9]+$')][string]$RunId,
    [ValidatePattern('^[a-f0-9]{40}$')][string]$CommitSha
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$exportRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'outputs'))
$importRoot = Join-Path $exportRoot ('.company-wordmarks-import-' + $RunId + '-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $importRoot | Out-Null
$manifestFile = Join-Path $importRoot 'logos/hero-manifest.json'
if ($CommitSha) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $manifestFile) | Out-Null
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/simonlangley1983/stocklayer/$CommitSha/logos/hero-manifest.json" -OutFile $manifestFile
} else {
    & gh run download $RunId --repo simonlangley1983/stocklayer --name transparent-company-wordmarks --dir $importRoot
    if ($LASTEXITCODE -ne 0) { throw 'Artifact download failed. Import retained for inspection.' }
}
$manifest = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json -AsHashtable
if ($manifest.version -ne 1 -or $manifest.provider -ne 'logo.dev') { throw 'Unexpected logo manifest.' }
$imports = foreach ($slug in $manifest.logos.Keys) {
    $artwork = $manifest.logos[$slug]
    $expected = "images/company-logos/$slug-hero.png"
    if ($slug -notmatch '^[a-z0-9-]+$' -or $artwork.path -ne $expected -or $artwork.sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid asset path or digest.' }
    $sourceFile = Join-Path $importRoot $expected
    $destinationFile = Join-Path $projectRoot $expected
    if ($CommitSha) {
        if ((Test-Path -LiteralPath $destinationFile) -and (Get-FileHash -LiteralPath $destinationFile -Algorithm SHA256).Hash.ToLowerInvariant() -eq $artwork.sha256) {
            $sourceFile = $destinationFile
        } else {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $sourceFile) | Out-Null
            Invoke-WebRequest -Uri "https://raw.githubusercontent.com/simonlangley1983/stocklayer/$CommitSha/$expected" -OutFile $sourceFile
        }
    }
    if ((Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $artwork.sha256) { throw 'Downloaded image digest does not match.' }
    @{ Source = $sourceFile; Destination = $destinationFile }
}
# Validate every image before replacing the manifest or copying any artwork.
foreach ($item in $imports) {
    if ($item.Source -eq $item.Destination) { continue }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $item.Destination) | Out-Null
    Copy-Item -LiteralPath $item.Source -Destination $item.Destination -Force
}
[IO.File]::Move($manifestFile, (Join-Path $projectRoot 'logos/hero-manifest.json'), $true)
$resolvedImport = (Resolve-Path -LiteralPath $importRoot).Path
if (-not $resolvedImport.StartsWith($exportRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
    (Split-Path -Leaf $resolvedImport) -notmatch '^\.company-wordmarks-import-[0-9]+-[a-f0-9]{32}$') { throw 'Unsafe cleanup path.' }
# Only the disposable import is removed; verified assets remain in the project.
Remove-Item -LiteralPath $resolvedImport -Recurse
Write-Output "Imported $($manifest.logos.Count) verified logos; disposable import removed."
$manifest.lastRun | ConvertTo-Json -Compress
