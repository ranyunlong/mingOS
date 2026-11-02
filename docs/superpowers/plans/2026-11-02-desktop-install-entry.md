# Desktop Install Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Expose a trusted parent-selected package source through the native desktop header so install/update uses the existing signed registry, confirmation prompt, catalog transaction, and retry path without relying on a reviewed event script.

**Architecture:** Add an optional absolute `--install-source` parent option. When present, the Linux native desktop renders a bounded `INSTALL` action in the header; a matching presented-frame press/release calls `Session::begin_install` with that source. The existing parent-side signature, identity, registry, catalog persistence, and retry logic remain authoritative.

**Tech Stack:** Rust 1.99, `ming-app-host`, CPU shell rasterizer, trusted WGSL shell, Linux `PackageInstallRegistry`.

**Spec:** `docs/superpowers/specs/2026-10-30-publisher-signing-design.md`, `docs/superpowers/specs/2026-10-30-package-install-registry-design.md`, current install flow in `crates/ming-app-host/src/ming_desktop.rs`.

## Global Constraints

- The source path is supplied by the trusted parent and must be absolute; guest applications cannot choose it.
- Installation and update must re-load and verify publisher signature, package identity, version, and storage fingerprint before committing.
- Input activation requires the same presented frame for press and release.
- Failed catalog or registry writes keep the previous catalog/registry state and expose the existing retry prompt.
- Existing U uninstall, D purge, and S public-storage controls retain their geometry and behavior.
- `finalSystemAccepted=false` remains unchanged until all overall gates pass.

---

### Task 1: Parent install-source option and parser tests

**Files:**
- Modify: `crates/ming-app-host/src/desktop_catalog.rs`
- Modify: `crates/ming-app-host/tests/desktop_catalog.rs`

**Interfaces:**
- Add `DesktopOptions::install_source: Option<PathBuf>`.
- Parse `--install-source <absolute-path>` with duplicate-option and absolute-path validation.

- [ ] **Step 1: Write failing parser tests**

Add tests that accept an absolute source, reject a relative source, and reject duplicate `--install-source`.

- [ ] **Step 2: Run the focused parser tests and confirm failure**

Run `cargo test --offline --locked -p ming-app-host --test desktop_catalog install_source -- --nocapture`.

- [ ] **Step 3: Implement the option and validation**

Store the path in `DesktopOptions`, keep it parent-only, and enforce the existing path length bound.

- [ ] **Step 4: Re-run the focused parser tests**

Require all new tests to pass.

### Task 2: Header install target and native frame-bound input

**Files:**
- Modify: `crates/ming-app-host/src/graphics/desktop_shell.rs`
- Modify: `crates/ming-app-host/src/graphics/desktop_shell.wgsl`
- Modify: `crates/ming-app-host/src/graphics/desktop_shell_gpu.rs`
- Modify: `crates/ming-app-host/src/desktop_native.rs`

**Interfaces:**
- Add `install_cell(width, height) -> Rect` in the existing header control band.
- Render a bounded `INSTALL` label only when an install source is configured.
- Add native `install_press` state and `install_release` matching the presented frame before calling `Session::begin_install`.

- [ ] **Step 1: Add failing geometry/input tests**

Cover the non-overlapping header install and power cells, visible CPU glyph output, and native press/release rejection when the frame sequence changes.

- [ ] **Step 2: Run focused tests and confirm failure**

Run `cargo test --offline --locked -p ming-app-host graphics::desktop_shell desktop_native::input_tests -- --nocapture`.

- [ ] **Step 3: Implement the header target**

Use the existing header control band, update CPU/GPU shell labels, and route release only after the same presented sequence remains current.

- [ ] **Step 4: Run focused CPU and trusted-shader tests**

Run the focused native tests and `cargo test --offline --locked -p ming-app-host graphics::desktop_shell_gpu::tests::trusted_shell_shader_parses_and_validates --features gpu -- --nocapture`.

### Task 3: Wire the source into the native session and evidence

**Files:**
- Modify: `crates/ming-app-host/src/ming_desktop.rs`
- Modify: `scripts/boot/guest.py`
- Modify: `scripts/boot/payload_profiles.py`
- Modify: `scripts/boot/test_profiles.py`
- Add: `artifacts/install-lifecycle-20261102-v9/`

**Interfaces:**
- Add `Session::begin_install_from_dock()` that consumes `options.install_source` and delegates to the existing `begin_install` path.
- Pass `--install-source` from the reviewed guest session configuration.
- Extend the install lifecycle profile to activate the I target, confirm the prompt, launch the new app, and power off.

- [ ] **Step 1: Add failing session/profile tests**

Require the Dock action to produce mode 16/17 prompt state and reject the action when no trusted source is configured.

- [ ] **Step 2: Implement the session and profile wiring**

Keep source identity and signature checks in `begin_install`; only add the trusted source handoff and input path.

- [ ] **Step 3: Run Windows source tests and Docker Linux release checks**

Run the focused Rust tests, then the fixed Docker release all-targets suite.

- [ ] **Step 4: Build a fresh bundle and run two QEMU cold boots**

Use a new artifact directory and verify install confirmation, catalog commit, new-app readiness, worker reap, sync/unmount, PowerDown, and marker `1 -> 2`.

### Task 4: Update gate evidence and report

**Files:**
- Modify: `docs/continuous-development.md`
- Add: `artifacts/overall-gate-audit-20261102-v2.json`

- [ ] **Step 1: Record the verified install-entry scope and exact limitations**

State that the native install entry is verified in QEMU, while physical input, Linux hardware GPU, multi-user identity, cross-platform services, and full daily acceptance remain open.

- [ ] **Step 2: Run `git diff --check` and capture final status**

Do not mark `finalSystemAccepted` true unless every overall gate has independent evidence.
