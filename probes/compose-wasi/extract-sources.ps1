Add-Type -AssemblyName System.IO.Compression.FileSystem
$composeEvidenceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../artifacts/compose-qualification'))
$composeArchives = @(
    @('dl.google.com/dl/android/maven2/androidx/compose/runtime/runtime/1.10.0/runtime-1.10.0-sources.jar', 'androidx-runtime-1.10.0-common'),
    @('dl.google.com/dl/android/maven2/androidx/compose/runtime/runtime-wasm-js/1.10.0/runtime-wasm-js-1.10.0-sources.jar', 'androidx-runtime-1.10.0-wasm-js'),
    @('dl.google.com/dl/android/maven2/androidx/collection/collection/1.5.0/collection-1.5.0-sources.jar', 'androidx-collection-1.5.0-common'),
    @('dl.google.com/dl/android/maven2/androidx/compose/runtime/runtime-annotation/1.10.0/runtime-annotation-1.10.0-sources.jar', 'androidx-runtime-annotation-1.10.0-common')
)
foreach ($composePair in $composeArchives) {
    $composeArchive = Join-Path $composeEvidenceRoot ('downloads/' + $composePair[0])
    $composeDestination = Join-Path $composeEvidenceRoot ('official-sources/' + $composePair[1])
    if (-not [IO.Directory]::Exists($composeDestination)) {
        $composeZip = [IO.Compression.ZipFile]::OpenRead($composeArchive)
        try {
            foreach ($composeEntry in $composeZip.Entries) {
                $composeEntryPath = [IO.Path]::GetFullPath((Join-Path $composeDestination $composeEntry.FullName))
                if (-not $composeEntryPath.StartsWith($composeDestination + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                    throw 'Archive entry leaves its qualification evidence directory'
                }
            }
        } finally { $composeZip.Dispose() }
        [IO.Compression.ZipFile]::ExtractToDirectory($composeArchive, $composeDestination)
    }
    Write-Output ('Official sources retained: ' + $composeDestination)
}
