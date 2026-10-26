# Compose Ming Bridge Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or executing-plans with disjoint write scopes, test-first and evidence before completion. No Git repository exists; do not initialize one.

**Goal:** Run true Compose stateful Ming components as a Kotlin/Wasm application in the existing native host.
**Architecture:** Official-source WASI Runtime included build; synchronous Snapshot/ControlledComposition bridge; bounded Ming node tree mapped to SDK4.
**Tech Stack:** Kotlin/Compose Compiler2.3.20, official Runtime1.10.0 source port, Wasmtime50, existing Rust/wgpu27 SDK4.
**Spec:** docs/superpowers/specs/2026-10-24-compose-ming-bridge-design.md

## Global constraints

Wasm/WASI and Ming-only imports; no JVM/browser fallback or fake Runtime. Keep all existing SDK/frame quotas; committednodes<=32 includingroot, boundedtransientnodes<=64, committedtextlayouts<=16 plusatmost16stagedlayouts(total<=32) in this initial bridge. Preserve source provenance and experimental port limitations. Do not claim asynchronous effects or frame clock support from synchronous counter tests. Implement after6B acceptance.

## Task1: Automatic state/composition bridge

Files: new ming-compose/build.gradle.kts, ming-compose/src/wasmWasiMain/kotlin/ming/compose/Composition.kt, Node.kt; tests within new sample contract source. Root settings.gradle.kts and build.gradle.kts link Compose compiler and included build.

Consumes: actual Runtime classes from probes/compose-wasi/port and existing Ming SDK.
Produces: a session-owned composition that receives Snapshot apply changes automatically, composes/recomposes/applies nodes and disposes its observer and composition.

- [x] Write realWasm contract tests for initial state, automatic state update, remembered identity, node remove/move and idempotent disposal.
- [x] Observe RED against missing bridge.
- [x] Implement minimal observer/snapshot lifecycle and bounded Applier; no application-supplied modified-state set.
- [x] Compile via actual Compose Compiler, execute in Wasmtime, verify exact events/import types and source hashes.

Ruling: retain up to16 committed labels and at most16 staged replacements (total32, belowhost64), then close superseded handles only after a successful frame. This preserves old resources/input targets on allocation/submit failure without giving up atomic text replacement.

Ruling: evaluate the32-node committed-tree quota after both normal/late apply phases, retaining a64-node transient guard. Reviewer reproduced a legal full-capacity disjoint-key replacement that inserts before removing; per-insert32 incorrectly rejects it. Keep true over-limit disposal and add disjoint/subtree regressions.

## Task2: Ming components and SDK rendering

Files: new ming-compose/.../Components.kt, Layout.kt, Renderer.kt; new samples/compose-demo/**. Keep Rust ABI unchanged.
Produces: MingColumn/MingText/MingButton, integer vertical layout and half-open hit testing; SDK4 render with resource ownership and rollback.

- [x] Failing behavior tests for text replacement/cleanup, clicks, duplicate resize, limits and viewport preservation.
- [x] Implement minimal components and immutable event/render view of committed tree.
- [x] Real Kotlin/Wasm CPU/GPU screenshot comparison, including remember/state and child removal driven by actual input events.

## Task3: Acceptance and continuation

Files: root build-kotlin sample registration; dedicated Compose acceptance scripts/tests; Linux verifier paths; docs.

- [x] Freeze host/runtime/sources/package/fixtures before first case; validate raw results and exact imports. Current Windows evidence ui-compose-reviewed binds400 source files.
- [x] Automated native presentation, final presentedFrame identity, ordinaryzero pixelreadback, explicitsnapshot and cache counters.
- [ ] Physical mouse click/maximize test: Computer Use was explicitly stopped by user Escape on2026-10-25; remains deferred, not claimed passing.
- [x] Linux/Wayland real execution and old SDK regression; independent review and focused fixes. Release11paths/Rust155/fixtures162 passed; coalescing identity and PSsequence-anchor findings fixed/closed.
- [x] Record measured startup/host-render timing limits; preserve native API vs physical scanout distinction.
- [ ] Full6C physical-input gate remains deferred by userEscape. Implementation/automated evidence is complete; independent6D timing qualification can proceed without mouse control.
