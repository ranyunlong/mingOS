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

- [x] **Step 3: Implement the minimal prompt and geometry API**

Add bounded validation, mode values above the existing uninstall modes, and a 16x16 D cell between the existing U and S cells. Keep the prompt geometry and `has_allow` contract unchanged.

- [x] **Step 4: Run the focused tests and verify they pass**

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

- [x] **Step 3: Implement the minimal session flow**

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

- [x] **Step 3: Implement input and shader support**

Route D before U/S, clear it on focus loss and mouse release, require the same presented frame, and update WGSL button selection without changing existing modes.

- [ ] **Step 4: Run focused tests and shader validation**

Run the native test and `graphics::desktop_shell_gpu::tests::trusted_shell_shader_parses_and_validates`; require zero failures.

### Task 4: Document, run full verification, commit and push

**Files:**
- Modify: `docs/continuous-development.md`

- [x] **Step 1: Record the completed phase and explicit remaining gaps**

Add the test evidence and keep `finalSystemAccepted=false`.

- [x] **Step 2: Run final checks**

Run `git diff --check`, the full fixed-environment `cargo test --offline --locked -p ming-app-host --all-targets -j1`, and the GPU shader test.

- [ ] **Step 3: Commit and push**

Use commit message `feat: add desktop private data deletion` and push `main` to `origin/main`.

## 2026-10-31 当前验证边界

当前工作树已经包含 Task 1--3 的实现和对应单元测试。Windows 固定 Rust 1.99.1 环境已完成以下可执行检查：

- `cargo test --offline --locked --package ming-app-host --tests -j1`：44 个测试目标组，`350 passed, 0 failed`。
- GPU library：当前 shader 修复后 `240 passed, 0 failed, 17 ignored`；trusted Shell parser/label 定向测试均通过。
- Boot Python：`80/80`；Node：`21 passed, 3 skipped, 0 failed`。
- `cargo check --offline --locked --package ming-app-host --lib`：通过。
- `git diff --check`：通过。

purge session、Linux 原生 `D` Dock 和缺少 `--data-root` 的回归均受 `target_os = linux` 限制，Windows 不能把这些断言记为通过。当前工作树已复跑 `cargo test --offline --locked --package ming-app-host --all-targets -j1` 并退出码 `0`；Linux-only `isolation_probe_v2` 的非 Linux 编译占位入口已可通过 Windows all-targets 编译。该结果仍不等价于 Linux runtime qualification。Docker Server、Linux Rust target 和 QEMU 尚不可用，因此 Task 1/2/3 的 Linux focused tests、当前源码 Host/bundle 重建和 QEMU purge acceptance 仍待环境恢复。历史 RED 和冻结产物保持不变，`finalSystemAccepted=false`。

最新 all-targets 证据：`artifacts/desktop-private-data-delete-20261031/windows-all-targets-green-v3.json`；旧 `windows-all-targets-red.log` 保留为修复前历史证据。

最新 GPU library 证据：`artifacts/desktop-private-data-delete-20261031/windows-gpu-lib-green-v2.json`。本轮修复了 GPU Shell 在 `UNINSTALL`/`DELETE` 模式下残留通用 `ALLOW` 字形的问题；硬件 GPU runtime 仍未验证。

CPU Shell focused evidence: `artifacts/desktop-private-data-delete-20261031/windows-desktop-shell-focused-v2.json`（16 passed, 0 failed）。该结果只覆盖 Windows CPU shell 目标，Linux native D input 仍未执行。

`cargo fmt --all -- --check` 可以执行但返回 `1`，差异分布在仓库既有的多处文件；本轮未运行全仓格式化，也未把无关格式变更混入工作树。
