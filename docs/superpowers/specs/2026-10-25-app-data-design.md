# MingOS platform app data — stage 8 independent module

Date: 2026-10-25. Scope: the independent Parent storage capability and optional Kotlin/Wasm SDK. Main owns engine/catalog/supervisor/protocol/manager wiring and VM data disk provisioning. This document does not claim a boot-system acceptance run.

## Frozen integration contract

The authority is a parent-owned AppDataGrant; cloning shares revocation. Guest paths, guest app IDs and manifest strings never grant storage. The new source is crates/ming-app-host/src/app_data.rs. Main may add the module export after handoff; this task leaves lib.rs untouched.

Authority API:

~~~rust
pub const MAX_SLOT_BYTES: usize = 65_536;
pub const MAX_SLOTS: usize = 8;
pub const MAX_TOTAL_BYTES: usize = 524_288;
pub enum AppDataError { Denied, InvalidArgument, NotFound, Io }
impl AppDataError { pub const fn code(self) -> i32; }
pub struct AppDataWrite { pub length: usize, pub directory_synced: bool }
#[derive(Clone)]
pub struct AppDataGrant { /* private state */ }
impl AppDataGrant {
    pub fn open(root: impl AsRef<Path>, data_key: &str, slots: &[u8]) -> Result<Self, AppDataError>;
    pub fn read(&self, slot: i32, offset: i32, capacity: i32) -> Result<Vec<u8>, AppDataError>;
    pub fn write(&self, slot: i32, bytes: &[u8]) -> Result<AppDataWrite, AppDataError>;
    pub fn size(&self, slot: i32) -> Result<usize, AppDataError>;
    pub fn delete(&self, slot: i32) -> Result<bool, AppDataError>;
    pub fn revoke(&self);
}
pub fn validate_read(slot: i32, offset: i32, outptr: i32, capacity: i32, memory_length: usize)
    -> Result<std::ops::Range<usize>, AppDataError>;
pub fn validate_write(slot: i32, pointer: i32, length: i32, memory_length: usize)
    -> Result<std::ops::Range<usize>, AppDataError>;
~~~

The private Result alias expands to std::result::Result<T, AppDataError>. There is no new dependency in the host crate; Linux uses existing libc and std, Windows uses std plus narrow kernel32 FFI. Unsupported operating systems fail compilation rather than silently using a weaker path backend.

| Error | ABI |
| --- | ---: |
| Denied / no grant / revoked / valid but ungranted slot | -1 |
| InvalidArgument / slot outside 1..8 / invalid bounds or memory range | -2 |
| NotFound / missing stored value | -3 |
| Io / unsafe filesystem object / detected change / I/O failure | -4 |

A successful Rust write returns a receipt. Every Err is before the atomic rename and leaves the old slot value intact under the trusted-directory ownership assumption. Once rename succeeds the new value is committed: a failed directory sync cannot truthfully be represented as a failed, rolled-back write. directory_synced=false records this durability uncertainty. Main maps a committed write to length for the ABI and can surface the durability receipt through Parent diagnostics. delete returns Ok(directory_synced); main maps either true or false to ABI success 0. Missing delete is -3. Windows reports directory_synced=false because this prototype does not confirm directory flush.

## Authority, key and lifetime

root is an existing absolute Parent-provisioned directory. It is never created from a guest path. data_key has 1..64 ASCII bytes matching [A-Za-z0-9][A-Za-z0-9_-]*: dots, separators, colon, NUL, spaces and non-ASCII are rejected before filesystem operations. slots is a nonempty explicit unique list of 1..8; a manifest SDK version or application ID cannot create the grant. Reopen with the same trusted data_key intentionally sees persistent values. The internal directory name is key- followed by the lowercase hexadecimal encoding of the validated ASCII key (maximum 132 bytes). This mapping is injective even on case-insensitive filesystems and avoids Windows reserved device names; Key, key and NUL remain separate ordinary capabilities. No caller may infer storage paths from a manifest identifier. Different keys are independent namespaces; multiple opens of the same key are the same storage, with independently revocable capabilities.

A Mutex serializes clone I/O with revoke. When revoke returns, already-running I/O has completed, and every old clone rejects subsequent reads, writes, sizes and deletes before filesystem access. New independent open calls do not inherit revocation; only the trusted Parent may create those capabilities. Poisoned state fails closed. Revoke is not file erasure.

The quota is a logical persistent payload limit: at most eight fixed slot names, at most 65536 bytes each, total 512 KiB. No guest filename is accepted. Atomic replacement temporarily needs one extra bounded file; this is not a strict 512 KiB physical-disk quota. Existing slots under a key count even if a later grant authorizes only a subset. Crash-orphan temporary files are not silently swept by this module, to avoid deleting another operation's data; lifecycle/disk budgeting belongs to Parent provisioning. A returned precommit error cleans the invocation's own temporary name when filesystem permissions still permit unlink.

Linux checks the root and key directories are owned by the Parent effective UID and have no group/other permission bits (0700 or more restrictive). Slot files must be single-link regular files owned by that UID, no group/other bits (0600 or more restrictive), size <=65536. These checks refer to Parent authority, never an app ownerUID. Sealed workers and app UIDs must not receive the grant or OS access to root; UID/mount/kernel isolation is another module.

## Linux filesystem implementation

Open the absolute root one component at a time from / using directory descriptors and O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC. No root component is canonicalized through a symlink; parent components '..' are rejected. Pin the root descriptor, mkdirat the fixed trusted key with 0700, openat the key with O_NOFOLLOW, verify owner/mode, and sync the parent directory for a newly created namespace. The key descriptor is held for the grant lifetime, so pathname replacement does not retarget the capability.

All slot operations use this descriptor and generated slot-N names. Read opens with O_NOFOLLOW | O_NONBLOCK before checking regular-file type, ownership, hardlink count and size; directory, FIFO, symlink and oversized data are refused without an unbounded read or FIFO wait. Read copies the whole <=64KiB value twice and compares content, then compares fstat identity/size/mode/mtime/ctime and reopens the relative name to confirm identity. This detects same-length overwrite even on the tested filesystem where repeated writes sometimes kept identical mtime/ctime. The requested offset/capacity slice is taken only from the accepted detached snapshot; offset past EOF yields empty. size verifies metadata and identity without reading contents.

The two content copies plus metadata checks detect observed changes; no userspace read algorithm can promise to detect every adversarial ABA overwrite by another process with the same OS authority. Permission isolation and all normal writers using atomic replace are part of the boundary. This module does not protect against a malicious Parent, writable mounts, or kernel-level manipulation.

Write validates all inputs before file access, rejects an unsafe existing target, creates its own same-directory O_CREAT | O_EXCL temporary file with 0600, writes all bytes, sync_all, rechecks the target and renameat-replaces it. Rename uses the same pinned directory descriptor. Temporary-name collisions never authorize deleting an existing file. On precommit failure only the name successfully created by this invocation is removed. After commit, directory sync is attempted and recorded. Delete similarly verifies a safe regular target, unlinkat-deletes and attempts directory sync. The clone mutex does not provide cross-process transactions between independently opened grants; Parent should own the key and coordinate such writers.

## Windows prototype boundary

This is an explicit local trusted-directory prototype. Every root ancestor and every key/slot operation checks reparse attributes; roots are absolute drive paths (UNC/device namespace prefixes are rejected). Regular slot handles are opened with FILE_FLAG_OPEN_REPARSE_POINT, verified by GetFileType/GetFileInformationByHandle, and compared by volume/file index/size/creation/write metadata. The same bounded double-content read is used. Writes use create_new + sync_all and same-directory MoveFileExW(REPLACE_EXISTING), without COPY_ALLOWED. No recursive path cleanup is used by the implementation.

Verified here: real local Windows filesystem roundtrip, precommit failure preservation/temp cleanup, sharing-denied failure preservation, and actual junction root/ancestor/key/slot refusal. Not claimed: production ACL/owner verification, NT relative handle namespace protection against ancestor-swap races, guarantees on arbitrary mapped/network filesystems, Windows directory durability, malicious concurrent reparse replacement, or crash/power-loss testing. Main must preserve the trusted-private-directory assumption on Windows or disable persistent grants there pending a production backend. No claim that an ownerUID/sealed worker is prevented from directly opening the path is made by this module.

## Optional SDK6 Wasm ABI

The namespace ming_data_v1 is independent and optional. The host should admit it only for manifest SDK >=6, and register its imports even when a grant is absent so valid calls return DENIED=-1. Kotlin dead-code elimination removes unused imports: consumers of old SDK APIs receive no ming_data_v1 imports; a size-only consumer receives only size.

| Import | Signature | Success |
| --- | --- | --- |
| read | (slot:i32, offset:i32, outptr:i32, capacity:i32)->i32 | bytes copied, 0 at EOF or zero capacity |
| write | (slot:i32, ptr:i32, length:i32)->i32 | length, including 0 for an empty value |
| size | (slot:i32)->i32 | stored length, including 0 |
| delete | (slot:i32)->i32 | 0 |

slot is 1..8. offset and capacity are each 0..65536; a read may cross EOF and is truncated. write length is 0..65536. A pointer is an unsigned wasm32 address encoded as i32. Validate the full requested guest range (even when the slot is shorter) using checked arithmetic and actual linear-memory length. For zero length, pointer must still be <= memory length. Negative length/capacity, invalid slot/offset and invalid guest memory never invoke storage or Parent RPC. The pure Rust validate_read/validate_write helpers are supplied for this boundary; main must call them before copying guest bytes or making a host request.

ming-sdk/src/wasmWasiMain/kotlin/ming/sdk/AppData.kt exposes MingAppData.size, read, write, delete; read returns AppDataReadResult(code, bytes:ByteArray?). code=0 means bytes owns a detached array; any negative error has bytes=null. No host path, app identity, handle, close or platform-specific buffer is public. Invalid Kotlin arguments return -2 before import invocation. Read/write response lengths are validated; partial positive write, positive delete, size >65536 or read >capacity become -2. Negative errors, including unknown future codes, are propagated. Empty read/write still makes a host call to enforce capability revocation/denial.

Main integration steps: add only the module export; bind optional grants from trusted catalog configuration; marshal bulk read output and write input through parent/child RPC with bounded frame sizes; revalidate grant/slot and memory at the appropriate trust boundary; map receipts as described; revoke cloned capabilities on uninstall/authority removal; provision and attach the private persistent VM data root. Keep fuel replenishment exclusively in the wait mechanism: data imports do not replenish execution. Do not allow an untrusted worker to construct/open grants from a supplied appID or path. The namespace registration/manifest policy/RPC/fuel behavior are not implemented or tested by this independent task.

## Verification and evidence

All evidence is under artifacts/app-data. Tests use a small standalone manifest under probes/app-data-sdk/rust or direct rustc with the cached libc rlib. No root target tree is cloned, no all-feature host Cargo build is run, and no GUI/physical input is used. CARGO_INCREMENTAL=0 is set for Rust commands. No new external dependency or host system configuration is installed.

- rust-red-windows.log: 9 assertion failures against the deliberately unimplemented seam. rust-red-linux/receipt.json and logs: 14 failures in an isolated existing Linux image.
- rust-green-windows.log / rust-green-linux/receipt.json: initial 9/14 passes.
- rust-review-linux: later same-length overwrite regression failed, preserved rather than overwritten. same-length-stat-repeat-diagnostic.json: 191 of 200 observed overwrites kept the same metadata timestamps, motivating bounded content verification.
- rust-review-fixed-windows.log: pre-key-encoding 11 tests pass with -Dwarnings, including actual junction/sharing checks.
- rust-review-fixed-linux/receipt.json: pre-key-encoding 15 tests pass with -Dwarnings, including real EACCES preservation, FIFO nonblocking refusal, symlink/pinned-dirfd behavior, hardlink rejection, size quota, atomic failpoints and content-change detection.
- key-case-red-windows.log and key-case-red/: an actual Windows case-insensitive alias failure and its source snapshot, before introducing portable key encoding.
- rust-acceptance-windows/receipt.json and rust-acceptance-linux/receipt.json: final production cfg compile and 12/16 passing tests respectively, with exact current source hashes. Case-distinct keys and Windows reserved names are included.
- sdk-red is an environment spawn-EPERM failure and is not counted as RED. sdk-red-compile/build-receipt.json is the actual expected Kotlin compilation RED: unresolved MingAppData before AppData.kt existed.
- sdk-green/build-receipt.json and contract-result.json: real Kotlin 2.3.20 Wasm consumer compiled and executed; 51 parent capability calls, invalid local arguments invoked none, no-grant errors, byte roundtrip up to 64KiB, offsets/EOF, zero buffers, returned error codes, malicious positive result validation, pointer bounds and missing-namespace LinkError.
- sdk-legacy: old SDK consumer compiled and ran with zero app-data imports/calls. sdk-size-only: only size is retained and run once, with all other data imports eliminated.

Node doubles only the capability implementation; the Kotlin wrapper is compiled and executed unchanged. These are standalone SDK and storage checks, not engine/RPC catalog policy or VM persistence acceptance. Runtime guest-memory bounds are checked by the Node double for actual Kotlin pointers and directly by Rust helper tests for malformed ranges; native engine callers must still integrate those helpers. No crash/power-loss test, adversarial same-UID ABA race proof, or syscall-level cleanup failure simulation is claimed. Review consists of local source inspection and the targeted regressions above.

Reproduction commands, precise hashes, image/cache identity and file scope are saved in artifacts/app-data/summary.json and commands.md. Input snapshots preserve each Linux test phase and each Kotlin build. The Linux image and old cache volume are read-only, and only artifacts/app-data is bind-mounted writable; /tmp writes belong to the disposable container. The existing cache is not copied into a full target.
