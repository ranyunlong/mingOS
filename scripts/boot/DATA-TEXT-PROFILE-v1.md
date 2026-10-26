# Closed data-text payload profile v1

Main explicitly approved this additive profile on 2026-10-25. An absent profile remains legacy desktop (Compose/Motion); unknown profiles fail. Review field `profile: "data-text"` selects this verifier. Neither original package nor receipt is rewritten or recompiled by Boot.

## Catalog and session

Exactly app1/app2, both autostart once:

- app1: dev.ming.app-data-desktop, SDK6, trusted dataKey counter-main, slots exactly [1].
- app2: dev.ming.text-entry-demo, SDK6, no dataKey or slots field (including null).

Both manifest entries are app.json with main.wasm. Reject escaping paths or any payload symlink. Exact reviewed long-lived argv:

```text
--duration-ms 0 --isolation strict --data-root /data/mingos --state-file /run/evidence/desktop-state.json
```

All existing reviewed=true/reviewer/hostSha256/catalogSha256/budgetEvidence checks remain required. Native CPU presentation is fixed by guest. No events injection, watchdog/fuel changes or fallback profile. Bundle manifest records payloadProfile and payload_profiles.py hash.

## Package evidence

`review.packageEvidenceSha256` must have exactly keys "1" and "2": app1 boot-receipt.json SHA, app2 original build-receipt.json SHA.

App1 keeps original probe-schema build-receipt.json: status passed, mode appdata, stage package, compilerExitCode integer0. Its package.rawSha256 and package.packagedSha256 must match compiler.wasm and main.wasm.

App1 additionally carries this main-reviewed derived envelope (no fabricated build-info):

```json
{
  "schemaVersion": 1,
  "kind": "derived-evidence-envelope",
  "provenance": "original-receipt-no-recompile",
  "packageId": "dev.ming.app-data-desktop",
  "manifestSha256": "ACTUAL_app.json_SHA",
  "originalReceiptSha256": "ACTUAL_original_build-receipt.json_SHA",
  "rawSha256": "ACTUAL_compiler.wasm_SHA",
  "packagedSha256": "ACTUAL_main.wasm_SHA"
}
```

All derived values must match original bytes and nested receipt. The envelope is assembly provenance, not a claim of recompilation. The entire envelope is pinned by main review.

App2 requires original flat schemaVersion1 receipt, status passed/exitCode integer0 and manifest/raw/packaged hashes; compiler.wasm and build-info.json raw/packaged hashes must match. Windows rawPath in build-info remains provenance and is not dereferenced in guest.

## Guest and input evidence

Guest mounts only the expected UUID/serial virtio disk and verifies UID0/mode0700 after chmod; data-marker reports rootUid/rootGid/rootMode. Guest never formats the disk. Infrastructure marker counters and SDK counter values are distinct evidence.

The actual Host emits root:null before first presentation. Accept it for non-ready states, reject ready/null, and never dispatch input before ready/presented root and target app handshake. This fixes the failed v2 boot consumer without weakening input readiness.

QMP captures an actual scanout after a step's presentation expectation is confirmed and before the next input step is dispatched. Periodic captures remain. Input expectations only prove what they explicitly require: a single sourceAdvance does not by itself prove every character in a batched key step. Compare the complete guest Text hashes/events and actual scanout. POWER is the trusted persistent desktop exit; Esc is not an exit assumption.

## Recorded evidence

artifacts/boot-data-text-20261025 contains profile/scanout/null-root RED and GREEN tests, real package/ELF file checks, the failed v2 run and separate v3 recovery run. Each run keeps new bundle/output directories; original disk and previous markers remain. No reset or formatting after failure. Host source and main compilation cache are never written by Boot.

Actual kernel/cold-pair success must be established from run results, strict cleanup/resource markers, normal Kotlin exit/reap, data sync/unmount and kernel Power down. finalSystemAccepted remains false pending main review.
