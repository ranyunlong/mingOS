# Notes

A development Compose application with one single-line note, an explicit **保存**
button, and startup loading from the package's private `note.txt`. Merely editing,
closing, or restarting the app never saves. An empty saved note is valid.

The text field accepts at most 512 UTF-8 bytes, matching `TextEditState`. Loading
reads one extra byte to detect oversized existing files instead of truncating them.
Malformed UTF-8, unsupported text, denied access, or I/O failure locks saving.
**重新读取（替换编辑内容）** retries after authorization and replaces unsaved edits.
An absent file is an editable new note. A failed write leaves edits in the field.

Successful atomic replacement is followed by exact byte readback. The status
distinguishes confirmed directory sync, unconfirmed directory sync, write failure,
and failed readback. Failed readback locks saving until a successful reload. The
application does not promise protection against another instance editing the same
note concurrently. Use one instance for this development demonstration.

`MingPrivateFiles` is typed SDK6 API; the sample never constructs raw RPC or grants
itself access. With a data root configured, the Linux desktop selects the trusted
catalog package `dev.ming.notes-demo` and provides its package-bound private-file
capability. Private files need no user permission dialog. Public shared storage
has a separate consent flow, which this sample does not request. SDK6 identifies
the protocol; the parent still controls the actual capability and package binding.
Without a usable capability startup displays a read error and **保存不可用**.
After the underlying problem is resolved, explicitly reload. `MingAppData` slots
are unnecessary here: private files support a named atomic replacement with
directory-sync feedback.

## Build integration

The standalone settings resolve SDK, Compose and the two included port projects
relative to the repository root. They work in a full restored checkout with the
same layout. With the repository's offline toolchain/cache configured:

```text
./gradlew -p samples/notes-demo --offline compileProductionExecutableKotlinWasmWasi
./gradlew -p samples/notes-demo --offline -PnotesContract compileProductionExecutableKotlinWasmWasi
```

The contract property selects `tests/kotlin/Contract.kt` and excludes UI `Main.kt`.
On Windows use `gradlew.bat`. The shared root build registers `:notes-demo` with
project directory `samples/notes-demo`; public profile `developer-workflow`
builds `notes-demo` and `animated-demo` in order.
Build from a read-only restored checkout using the public runner and its external
work directories; do not put writable Gradle state into the frozen checkout.
Adapt the compiler Wasm using `scripts/wasm-sdk-adapter.mjs` before packaging.

```text
node samples/notes-demo/tests/contract.mjs <adapted-contract.wasm> <new-report.json>
node samples/notes-demo/tests/app.mjs <adapted-app.wasm> <new-report.json>
```

These execute real Kotlin/Wasm, with deterministic capability/input doubles.
They do not claim a real filesystem grant, physical input, guest restart, or formal
qualification. Those require the real development host workflow separately.

## Development diagnostics

**This demo explicitly enables plaintext diagnostic byte logging. Do not enter
sensitive notes.** `NotesDocument` itself defaults this option off; `Main.kt`
enables it for the bounded development replay. Only successfully validated notes
of at most 512 bytes are logged; failed/oversized/corrupt data is never dumped.
No host private directory or unrelated note is copied or exported by this sample.

Events are ordered per operation. Each load starts with `9700=read code`, then
`9701=validated UTF-8 length` (or -1), `9703=save allowed`, and `9704=one unsigned
UTF-8 byte` repeated for that length when diagnostics are enabled. Missing is
`read code=-3`, length=0 and save allowed=1. Each attempted write emits
`9710=write code`, `9711=directory synced` (1/0/-1). Readback emits `9712=read code`,
`9713=exact match` (1/0), `9715=validated matching byte length` (or -1), then
`9714=one unsigned byte` for a matching readback only. No hashCode is content proof.
`9720=1` means startup load completed and the UI is entering its run loop;
`9730` is the UI exit code and `9731` is its frame count.

Default column padding is 24 with a 12-pixel gap: title, text field, guarded Save,
Reload, status and a short hint. Save remains in the same layout position while
locked; its label changes to **保存不可用** and its callback cannot write.
