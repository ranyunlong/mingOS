# Desktop Private Data Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit Linux desktop path that uninstalls an app and permanently deletes only its private data, while preserving the existing keep-data uninstall flow.

**Architecture:** Extend the trusted desktop prompt state with a destructive purge mode and add a third Dock hit target. The session calls the existing install-registry data-policy API with the already established package storage authority; a purge failure leaves the app removed and exposes a retry prompt without reactivating the package.

**Tech Stack:** Rust 1.99, `ming-app-host`, CPU shell rasterizer, WGSL trusted shell shader, Linux `PackageStorageAuthority` and `PackageInstallRegistry`.

**Spec:** `docs/superpowers/specs/2026-10-30-desktop-private-data-delete.md`

## Global Constraints

- Ordinary `U` uninstall continues to preserve private data.
- Public files and public authorization are never removed by the purge path.
- Input decisions require a matching presented frame and pointer target.
- Existing package identity, install registry, and no-follow deletion boundaries remain authoritative.
- `finalSystemAccepted=false` remains unchanged until the full system acceptance gates are met.

---

### Task 1: Add failing prompt and Dock geometry tests

**Files:**
- Modify: `crates/ming-app-host/src/graphics/desktop_shell.rs`
- Modify: `crates/ming-app-host/tests/desktop_shell.rs`

**Interfaces:**
- Produce `StoragePrompt::purge`, `StoragePrompt::purge_blocked`, and `StoragePrompt::purge_retry` constructors plus `dock_purge_cell`.

- [ ] **Step 1: Write the failing tests**

Add assertions for `CANCEL` / `DELETE`, `PRIVATE DATA WILL BE DELETED`, `PRIVATE DATA NOT DELETED`, blocked close-only state, three non-overlapping Dock cells, and visible CPU pixels for the D/U/S glyphs.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `cargo test --offline --locked -p ming-app-host --test desktop_shell -- --nocapture`

Expected: compile failures because the purge constructors and Dock geometry do not exist.

- [ ] **Step 3: Implement the minimal prompt and geometry API**

Add bounded validation, mode values above the existing uninstall modes, and a 16x16 D cell between the existing U and S cells. Keep the prompt geometry and `has_allow` contract unchanged.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run the same command and require zero failures.

### Task 2: Add failing session purge tests

**Files:**
- Modify: `crates/ming-app-host/src/ming_desktop.rs`

**Interfaces:**
- Produce a Linux-only `begin_uninstall_with_purge` decision and a purge pending state that can retry after a failed deletion.

- [ ] **Step 1: Write the failing tests**

Cover successful purge after a presented DELETE response, active-app blocking, and a failed purge that removes the catalog entry while preserving a retryable prompt and allowing a later successful retry.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `cargo test --offline --locked -p ming-app-host ming_desktop::input_backpressure_tests::desktop_purge -- --nocapture`

Expected: compile failures because the purge decision and session entry point do not exist.

- [ ] **Step 3: Implement the minimal session flow**

Add a purge decision variant, initialize or reuse the private storage authority, call `uninstall_with_data_policy(PurgePrivateData(...))`, remove catalog/autostart entries once the registry is removed, and keep a retry prompt only when data deletion fails.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run the same command and require zero failures.

### Task 3: Add failing native input and GPU shader coverage

**Files:**
- Modify: `crates/ming-app-host/src/desktop_native.rs`
- Modify: `crates/ming-app-host/src/graphics/desktop_shell.wgsl`
- Modify: `crates/ming-app-host/src/graphics/desktop_shell_gpu.rs`

**Interfaces:**
- Produce `purge_dock_press` state, matching `purge_dock_release`, D glyph packing, and shader labels for DELETE/RETRY.

- [ ] **Step 1: Write the failing tests**

Cover D Dock press/release matching the presented frame and opening the purge confirmation; extend the shader validation test to exercise the changed metadata path.

- [ ] **Step 2: Run focused native/GPU tests and verify they fail**

Run: `cargo test --offline --locked -p ming-app-host desktop_native::input_tests::purge_dock --features gpu -- --nocapture`

Expected: compile failures because the native D state and geometry do not exist.

- [ ] **Step 3: Implement input and shader support**

Route D before U/S, clear it on focus loss and mouse release, require the same presented frame, and update WGSL button selection without changing existing modes.

- [ ] **Step 4: Run focused tests and shader validation**

Run the native test and `graphics::desktop_shell_gpu::tests::trusted_shell_shader_parses_and_validates`; require zero failures.

### Task 4: Document, run full verification, commit and push

**Files:**
- Modify: `docs/continuous-development.md`

- [ ] **Step 1: Record the completed phase and explicit remaining gaps**

Add the test evidence and keep `finalSystemAccepted=false`.

- [ ] **Step 2: Run final checks**

Run `git diff --check`, the full fixed-environment `cargo test --offline --locked -p ming-app-host --all-targets -j1`, and the GPU shader test.

- [ ] **Step 3: Commit and push**

Use commit message `feat: add desktop private data deletion` and push `main` to `origin/main`.
