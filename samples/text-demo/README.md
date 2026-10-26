# Text demo (SDK3)

Build only this application with Node 24 and the cached Kotlin toolchain:

```powershell
$env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
node scripts/build-kotlin.mjs text-demo
node --test samples/text-demo/tests/demo.test.mjs
```

The package is `artifacts/text-demo/app.json` plus `main.wasm`. It needs the parent
native host's SDK3 text support and builtin font pack, with no file/image grant.
`node scripts/build-kotlin.mjs all` also includes the two existing applications.

The four fixed specimens are `MingOS 中文，标点。AV fi é Á` at 12, 16, 24 and 48 px.
Each row shows the host's advance, ascent, descent, gap, line height, ink bounds,
glyph count, status flags and font revision. Q6 values are exact; the advance also
has a pixel convenience value. The shaded ink rectangle and baseline/advance
line use the measured values rather than character counts.

F or the FIT button recenters the measured sheet and resets scroll offsets.
Font sizes remain fixed; a small window clips the sheet instead of scaling glyphs.
Scroll and drag reveal overflow, 1 aligns left, and Escape/window close ends the
session through the existing `MingInput` API. The final panel reuses the 24 px
layout on either side of a translucent rectangle in command order, inside an
explicit clip. Idle events do not redraw. The input loop stops after 16,384 events
(including idle); every exit attempts to close all owned layouts.

The Node tests instantiate the **real compiled Kotlin Wasm** with an ABI test
double. They verify imports, metrics transfer, frame encoding, layout reuse,
resize/input bounds and cleanup after errors. They do not render glyphs or prove
CPU/GPU/native presentation. Run those acceptance checks with the parent host
when its corresponding renderer is ready; this sample makes no GPU-run claim.

For the isolated SDK boundary probe (no demo/package replacement):

```powershell
$env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
$env:GRADLE_USER_HOME = "$PWD\.cache\gradle"
$env:JAVA_TOOL_OPTIONS = "-Djdk.net.unixdomain.tmpdir=`"$PWD\.cache\java-tmp`""
& "$env:JAVA_HOME\bin\java.exe" -classpath gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain --no-daemon -PtextDemoContract :text-demo:stageSdkContract
node --test samples/text-demo/tests/sdk.test.mjs
```

This probe checks exact metric words and pixel conveniences, empty and 512-byte
UTF8 inputs, quarter-pixel em sizes, nonfinite/out-of-range coordinates, the
256-command boundary, all supported command encodings, and closed handles.

The staging task preserves the probe in `build/sdk-contract-artifact/sdk-contract.wasm`.
A subsequent normal demo build may clean the probe compilation directory; the
staged test artifact and the packaged real demo remain independent.
