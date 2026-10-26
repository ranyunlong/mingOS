# GPU Render Contexts Implementation Plan

> **For agentic workers:** Use test-first with actual hardware verification and independent review. No Git repository or worktree initialization. ComputerUse stays stopped.

**Goal:** Let the future Ming desktop reuse a GPU device without mixing application render caches or accounting.
**Architecture:** GpuSession.clone remains an alias; explicitfork creates independent Gpu resource state over cloned same-device handles/pipeline. Shared driver mutex serializes device error scopes and gives device identity.
**Tech Stack:** Rust1.99.1/wgpu27; no new dependencies.
**Spec:** docs/superpowers/specs/2026-10-25-minimal-desktop-design.md

## Constraints

Preserve SDK1..5 import/graphics behavior, frame quotas, integer CPU/GPU oracle and direct no-readback path. Oldframes keep their owning context alive. Driver lock precedes app-state lock for every device/scope operation; no nested driver lock. Different devices remain incompatible.

## Task1: Forked state

Files: graphics/gpu.rs,gpu_session.rs; focused hardwaretests. Produces public fork_render_context()->Result<GpuSession> and stable same-device identity for existing presenter.

- [ ] RED using red/blue same imageHandle and desired fork API/isolation behavior.
- [ ] Clone Device/Queue/adapter/pipeline; initialize fresh lazyatlas/cache/stats and share backend failure status.
- [ ] Actual hardware image/close/warm/stat isolation and old direct frame references.

## Task2: Device serialization and presentation

Files: same session methods/presenter compatibility andtests.

- [ ] Shared driver guard; validate lock order/error scopes, metadata/stat polling and fatal device failure propagation.
- [ ] Same-device fork frame accepted by presenter; differentdevice rejected.
- [ ] Preserve legacy aliases and no implicit readbacks.

## Task3: Regression and continuation

- [ ] Targeted/default/minimal/allfeature checks, Clippy and relevant explicithardware tests.
- [ ] Independent review and source/evidence snapshot; close7A only.
- [ ] Continue purewindow state and actual compositor surfaces; do not claim full desktop or OS fromfork feature.
