Add-Type -AssemblyName System.IO.Compression.FileSystem
$composeEvidence = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../artifacts/compose-qualification'))
$composeRuntimeKlib = Join-Path $composeEvidence 'downloads/dl.google.com/dl/android/maven2/androidx/compose/runtime/runtime-wasm-js/1.10.0/runtime-wasm-js-1.10.0.klib'
$composeZip = [IO.Compression.ZipFile]::OpenRead($composeRuntimeKlib)
try {
    foreach ($composeEntry in $composeZip.Entries) {
        if ($composeEntry.FullName -match '(^|/)manifest$') {
            $composeReader = [IO.StreamReader]::new($composeEntry.Open())
            try { $composeManifest = $composeReader.ReadToEnd() } finally { $composeReader.Dispose() }
            [IO.File]::WriteAllText((Join-Path $composeEvidence 'runtime-wasm-js-1.10.0.klib-manifest.txt'), $composeManifest)
            Write-Output ('Entry: ' + $composeEntry.FullName)
            Write-Output $composeManifest
        }
    }
} finally { $composeZip.Dispose() }
