# GPU surface and image cache implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Track steps with checkboxes.

**Goal:** Present native SDK frames directly on GPU and reuse image uploads while preserving SDK behavior.
**Architecture:** Per-app shared and serialized GpuSession serves compute and Surface. Frames explicitly carry CPU pixels or GPU buffers; snapshots read back on demand. Separate bounded image atlas caches handle pixels.
**Tech Stack:** Existing Rust1.99.1, wgpu27.0.1, winit0.30.13, Kotlin/Wasm SDK2.
**Spec:** docs/superpowers/specs/2026-10-24-gpu-surface-design.md

## Global constraints

- Preserve SDK/application artifacts and CPU default; no silent GPU fallback.
- Keep 32M raster,64M candidate,32MiB image,4M logicalpixel and physicalsurface limits.
- GPU work/error scopes serialized per session; preserve terminal failure reporting and finite waits.
- Interactive GPU readbackBytes=0 unless explicit snapshot; count image uploads and surface presents.
- No Git repo/worktree creation or commits; existing workspace and purpose-specific Docker resources only.

## Task1: Bounded cache layout

Files: graphics/image_cache.rs (independent worker).
- [x] Test first: retained handles, release/reuse, compaction, invalid/overbudget atomic failure.
- [x] Implement specified CacheLayout/CacheUpdate interface; report tests.

## Task2: Shared GPU session and frame resources

Files: graphics/{mod.rs,gpu.rs,gpu_session.rs,scene.wgsl}, broker.rs, supervisor.rs, main.rs and existing tests.
- [x] Add failing tests for native GPU frames without CPU pixels, explicit readback and upload reuse.
- [x] Split image atlas binding, integrate CacheLayout and counters; retain checked allocations.
- [x] Share device/session and serialized error scopes; represent GPU frames explicitly, read back only by requested mode/snapshot.
- [x] Update existing CPU/GPU tests to explicit pixel access, run differential and error regressions.

## Task3: Surface presentation

Files: graphics/{presenter.rs,present.wgsl}, desktop.rs, tests GPU.
- [x] Implement compatible adapter selection and bounded surface recovery with pure policy tests.
- [x] Direct fragment pass from GPU output buffer, format handling and nearest-neighbor scaling.
- [x] Update native lifecycle, input gate, surface cleanup and accurate final stats after snapshots.
- [x] Hardware offscreen shader validation and real bounded native sessions with/without snapshots.

## Task4: Integration and delivery

Files: scripts/Linux verifier if required, docs/README, verification and ADR.
- [x] Full Rust feature-matrix tests, hardware specials, Clippy, formatting, existing Node regressions.
- [x] Scoped review and fixes before final source freeze.
- [x] Fresh Linux CPU/Wayland and all-feature compilation; preserve raw evidence.
- [x] Document exact counters, rendering/timing boundaries, remaining limitations and runnable commands.

## Execution record

Completed2026-10-24: Windows default42/no-default24/all-features56 pass; five hardware tests explicitly passed; Clippy/format/Node pass. Native Surface zero readback vs one explicit snapshot verified, one image upload each. Linux42 Rust tests/60fixtures/CPUWayland2present/all-featurescheck passed; parentrevalidationpassed. Evidence docs/gpu-surface-verification.md and artifacts/gpu-surface/acceptance.json. Proceed automatically to typography under user authorization.
