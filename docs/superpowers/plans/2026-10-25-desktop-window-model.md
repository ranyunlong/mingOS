# Desktop Window Model Implementation Plan

**Goal:** A bounded, deterministic host-owned Ming window state machine, ready for the real GPU compositor.
**Spec:** docs/superpowers/specs/2026-10-25-minimal-desktop-design.md
**Scope:** new desktop_model.rs and focused tests/artifacts only; no GPU/worker/GUI side effects.

- [ ] Test-first screen/workarea bounds, monotonic IDs/capacity4 and atomic invalid operations.
- [ ] z-order/focus, minimize/restore, maximize/restore geometry, resize clamp and close effects.
- [ ] Reuse ViewportGate: changed geometry invalidates input until matching source-frame presentation; stale frame sequence cannot unlock it.
- [ ] Content pointer capture binds one WindowId, projects half-open coords and is canceled on focus/geometry/hide/close; no wrong-recipient release.
- [ ] Standalone deterministic invariants and reviewed transitions before runtime integration.

Constants: title32/topbar24/Dock64. Screen320x240..4096 each,<=4194304pixels. Workarea excludes topbar/Dock. Geometry always stays insideworkarea; initial window<=720x480. Model emits effects only; no app paths/process execution or physical mouse control. A pure model is not a working desktop.
