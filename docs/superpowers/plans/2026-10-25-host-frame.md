# HostFrame and Async Compose Implementation Plan

> **For agentic workers:** Use disjoint tasks and test-first under superpowers:subagent-driven-development. No Git repository; no worktree/init/commit. Computer Use remains stopped by user Escape.

**Goal:** Provide controlled real host time/frame opportunities and run true Compose coroutine effects through Ming SDK.
**Architecture:** SDK5 inherits stableSDK1..4; ming_frame_v1 has fixed8B clock read and16B request/48B event bounded wait. Real host Instant and single-thread owned coroutine dispatcher; preserve synchronous legacy entry.
**Tech Stack:** Rust1.99.1/Wasmtime50, Kotlin/Compiler2.3.20, official Runtime1.10.0 source and coroutines1.9 qualification variant.
**Spec:** docs/superpowers/specs/2026-10-25-host-frame-design.md

## Constraints

No widened WASI, generalFDpolling, wall clock or native pointer. run/view/fuel/request/memory limits unchanged. 32committed/64transient nodes,16committed+16staged labels, existing graphics quotas retained. Frame opportunity is not vsync/presentation. Keep6C physicalmouse deferred and original evidence.

## Task1: Packet and clock state core

Files: new crates/ming-app-host/src/frame_clock.rs and focused unit tests; no SDK/worker change yet.
Interfaces: Request::decode(&[u8])->Result<Request,ClockError>; FrameClock app-owned monotonic epoch/state, bounded poll/wait event encoded48. Pure clock decisions tested with explicit nanoseconds before real Instant integration.

- [x] RED packet/version/flags/deadline/byteorder/error and scheduling tests. Core10failed/2passed RED retained.
- [x] Implement checked counters, frame/timer distinction, nonblocking and bounded wait fairness. Core12+service5 passed.
- [x] Verify deterministic boundary/overflow and no busy wait. Bounded sleep, nonblocking, burst8 and close priority contracts passed. Private deferral was removed after a real cross-API FIFO regression; due time is delivered before consuming the next input.

## Task2: SDK5 host import and RPC

Files: package.rs,engine.rs,supervisor.rs,broker.rs and new integration tests; reuse oldgraphics decoder semantics.
Produces: two exact functions in ming_frame_v1, SDK5 gated andmemorychecked, response8/48/0/negative, live/scripted input integration and clock stats.

- [x] RealWasm RED for SDK5/signatures/guestmemory and oldSDK rejection. UnsupportedSDK RED retained.
- [x] Implement parent-owned clock/RPC, inheritSDK1..4 without acceptingnewopcodes througholdsubmit. Six actualWasm tests passed including overlap/unaligned/oldsubmit rejection.
- [x] Full low-level regression/review completion: six default host ABI tests and five engine-only tests passed; SDK5 clock-only feature gate corrected while oldSDK3/4 gating is preserved. Full regression first exposed the old package fixture treating newly allocatedSDK5 as unsupported; updated accepted1..5/rejected0/6 coverage. Full rerun passed236 tests (12hardware ignored) before focused review fixes. Review found mixed-API FIFO and remaining-budget issues; realWasm7/7 and clock/service21/21 pass after fixes, FIFO/deadline independent scoped closure passed; refreshed Clippy/all-features/release passed.

## Task3: Kotlin SDK and real async model

Files: new ming-sdk FrameClock.kt, source-coroutines integration, new framed classes in ming-compose, standalone/sample contracts.
Consumes: clock8/request16/event48, official source-qualified coroutine actuals and parentclock pattern fromprobes/compose-frame.

- [x] Preserve source/license/provenance; verify KLIB identity and truecompiler lowering. Source-coroutine composite+SDK4 compatibility verified.
- [x] Low-level SDK source/boundary GREEN (16clock/58wait/20checks) delivered; realKotlin SDK5 Host time integration passed; deterministic driver53 checks passed. Deterministic async dispatcher qualification28checks passed; production FramedComposition/MingFramedApplication Host integration passed.
- [x] True Recomposer runner loop with LaunchedEffect/frames/delay/timeout, no handrolledRuntime.
- [x] Legacy syncSDK4 imports remain unchanged; avoid implicit SDK5 default-platform imports.

## Task4: Real animated UI and continuation

- [x] Sourcefreeze realKotlin sample, headless terminal CPU/GPU equality and liveclock behavior.
- [x] Automated native readback0/presentedFrame identity/clock events and Linux13paths/Rust183/fixtures177 passed; keep physicalmouse gate pending.
- [ ] Final framed Kotlin independent review incomplete (tool task terminated); local source audit and deterministic/live evidence retained. Low-level findings closed.
- [x] Continue desktop prerequisite design/GPU context correctness, retaining deferred review/input items; no fullOS completion claim.
