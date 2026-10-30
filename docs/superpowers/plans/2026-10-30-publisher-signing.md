# Publisher Signing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a parent-owned Ed25519 publisher trust root and enforce signed package verification in the verified installation path.

**Architecture:** Keep the existing unsigned `PackageInstallRegistry::open()` as a development-compatible API. Add a persistent, atomically-written `PublisherTrustStore`; `open_verified()` loads it once and requires every installed, resolved, and verified package to carry a valid signature from an active trusted key.

**Tech Stack:** Rust 1.99.1, `ed25519-dalek`, `sha2`, `base64`, `serde`, existing atomic filesystem and Linux `flock` patterns.

**Spec:** `docs/superpowers/specs/2026-10-30-publisher-signing-design.md`

## Global Constraints

- Preserve unsigned development behavior through `PackageInstallRegistry::open()`.
- Signed verification uses Ed25519; do not replace it with a hash or shared-secret construction.
- Keyring updates use private-owner checks, `O_NOFOLLOW`, `sync_all`, atomic rename, and directory sync.
- Signature input is the fixed `ming-package-signature-v1` binary envelope from the spec.
- Every implementation task has a focused failing test, a green test run, and its own commit/push checkpoint.

### Task 1: Add the signed package model and keyring parser

**Files:**
- Modify: `crates/ming-app-host/Cargo.toml`
- Modify: `Cargo.lock`
- Modify: `crates/ming-app-host/src/package.rs`
- Create: `crates/ming-app-host/src/publisher.rs`
- Test: `crates/ming-app-host/tests/publisher.rs`

- [ ] Add dependency and a failing test for valid/invalid manifest signature metadata.
- [ ] Add `publisherKeyId` and `publisherSignature` optional fields with strict validation.
- [ ] Implement the canonical signature envelope and Ed25519 verification.
- [ ] Implement keyring parsing, active/revoked state validation, and key ID validation.
- [ ] Run focused tests and commit/push.

### Task 2: Add atomic keyring provisioning and rotation

**Files:**
- Modify: `crates/ming-app-host/src/publisher.rs`
- Test: `crates/ming-app-host/tests/publisher.rs`

- [ ] Add failing tests for provisioning, rotation, revocation, malformed records, and private permissions.
- [ ] Implement locked atomic keyring writes and reload behavior.
- [ ] Verify revoked keys fail without changing installed content.
- [ ] Run focused tests and commit/push.

### Task 3: Enforce verified installs and catalog resolution

**Files:**
- Modify: `crates/ming-app-host/src/package_install.rs`
- Modify: `crates/ming-app-host/src/desktop_catalog.rs`
- Modify: `crates/ming-app-host/src/lib.rs`
- Test: `crates/ming-app-host/tests/package_install.rs`
- Test: `crates/ming-app-host/src/desktop_catalog.rs`

- [ ] Add failing tests for `open_verified()` rejecting unsigned, unknown-key, revoked-key, and tampered packages.
- [ ] Enforce verification on install, active manifest resolution, and package verification.
- [ ] Keep `open()` unsigned compatibility tests green.
- [ ] Run all package/catalog tests and commit/push.

### Task 4: Connect desktop production mode and verify repository state

**Files:**
- Modify: `crates/ming-app-host/src/ming_desktop.rs` or current desktop option initialization path.
- Modify: `docs/continuous-development.md`

- [ ] Add a failing CLI/desktop test showing `--install-root` uses verified mode.
- [ ] Wire the production flag without changing unconfigured development paths.
- [ ] Run fixed Docker `--all-targets`, `git diff --check`, and update the stage record.
- [ ] Commit and push the completed stage; record remaining non-goals explicitly.
