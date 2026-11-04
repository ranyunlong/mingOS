//! Parent-owned, bounded slot storage. Catalog authority creates grants; guest IDs do not.
//!
//! Linux uses pinned directory descriptors and no-follow relative operations. Windows is a
//! trusted-private-directory prototype: reparse points are checked, but ancestor replacement
//! races and ACL verification require a separate production implementation. No worker should
//! receive this grant or the root path. Revoke is linearized with I/O across all clones.
use std::{
    collections::BTreeMap,
    fs::File,
    io::{Read, Seek, SeekFrom, Write},
    ops::Range,
    path::Path,
    sync::{
        Arc, Mutex, MutexGuard, Weak,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
};
#[cfg(target_os = "linux")]
use crate::package::{DataMigration, MigrationStep};

pub const MAX_SLOT_BYTES: usize = 65_536;
pub const MAX_SLOTS: usize = 8;
pub const MAX_TOTAL_BYTES: usize = MAX_SLOTS * MAX_SLOT_BYTES;
#[cfg(target_os = "linux")]
pub const MAX_PRIVATE_FILE_BYTES: usize = 8 * 1024 * 1024;
#[cfg(target_os = "linux")]
pub const MAX_PRIVATE_TOTAL_BYTES: usize = 8 * 1024 * 1024;
#[cfg(target_os = "linux")]
const MAX_PRIVATE_ENTRIES: usize = 1024;
const MAX_PRIVATE_PATH_BYTES: usize = 1024;
const MAX_PRIVATE_COMPONENT_BYTES: usize = 255;
const MAX_PRIVATE_PATH_DEPTH: usize = 16;
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AppDataError {
    Denied,
    InvalidArgument,
    NotFound,
    Io,
}
impl AppDataError {
    pub const fn code(self) -> i32 {
        match self {
            Self::Denied => -1,
            Self::InvalidArgument => -2,
            Self::NotFound => -3,
            Self::Io => -4,
        }
    }
}
impl std::fmt::Display for AppDataError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Denied => "app data denied",
            Self::InvalidArgument => "invalid app data argument",
            Self::NotFound => "app data not found",
            Self::Io => "unsafe or unavailable app data",
        })
    }
}
impl std::error::Error for AppDataError {}
fn io_error(error: std::io::Error) -> AppDataError {
    if error.kind() == std::io::ErrorKind::NotFound {
        AppDataError::NotFound
    } else {
        AppDataError::Io
    }
}
type Result<T> = std::result::Result<T, AppDataError>;

/// Validates guest paths before any component-wise, no-follow filesystem traversal.
pub(crate) fn validate_private_path(path: &str) -> Result<Vec<&str>> {
    if path.len() > MAX_PRIVATE_PATH_BYTES || path.contains(['\0', '\\']) {
        return Err(AppDataError::InvalidArgument);
    }
    let components: Vec<_> = path.split('/').collect();
    if components.len() > MAX_PRIVATE_PATH_DEPTH || components.iter().any(|component| {
        component.is_empty()
            || *component == "."
            || *component == ".."
            || component.len() > MAX_PRIVATE_COMPONENT_BYTES
            || component.starts_with(".ming-data-")
    }) {
        return Err(AppDataError::InvalidArgument);
    }
    Ok(components)
}

pub(crate) fn valid_migration_backup_name(name: &str) -> bool {
    name.len() <= 128 && name.strip_prefix(".ming-update-backup-").is_some_and(|suffix| {
        !suffix.is_empty() && suffix.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_')
    })
}

/// A committed write. False means directory durability was not confirmed; the value is committed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AppDataWrite {
    pub length: usize,
    pub directory_synced: bool,
}

#[cfg(target_os = "linux")]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PrivateFileEntry {
    pub name: String,
    pub is_directory: bool,
}

/// A bounded, read-only observation of existing private file content.
#[cfg(target_os = "linux")]
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PrivateContentFile { pub path: String, pub size: u64, pub sha256: String }
#[cfg(target_os = "linux")]
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PrivateContentSnapshot { pub package_id: String, pub files: Vec<PrivateContentFile> }

/// Versioned digest of a complete bounded tree, including empty directories.
#[cfg(target_os = "linux")]
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TreeDigest { pub entries: u32, pub bytes: u64, pub sha256: String }
#[cfg(target_os = "linux")]
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PrivateUpdateBaseline {
    pub schema_version: u32,
    pub package_id: String,
    pub namespace_exists: bool,
    #[serde(deserialize_with = "required_optional_tree")]
    pub files: Option<TreeDigest>,
    pub non_files_sha256: String,
}
#[cfg(target_os = "linux")]
fn required_optional_tree<'de, D: serde::Deserializer<'de>>(deserializer: D) -> std::result::Result<Option<TreeDigest>, D::Error> {
    serde::Deserialize::deserialize(deserializer)
}

/// Observes existing storage without creating directories, policies, locks, or grants.
/// A missing namespace or files tree is a valid, distinct baseline.
/// Callers must stop workers or serialize writers for this observation. This does not
/// provide a globally atomic snapshot against arbitrary external concurrent writes.
#[cfg(target_os = "linux")]
pub fn observe_private_update_read_only(root: &Path, package_id: &str) -> Result<PrivateUpdateBaseline> {
    if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
    platform::observe_private_update_read_only_checked(root, package_id, || {})
}

/// Never initializes storage, grants, policies, or locks. Only single-level regular files
/// are accepted; every directory entry counts toward the bound before validation.
#[cfg(target_os = "linux")]
pub fn snapshot_private_read_only(root: &Path, package_id: &str) -> Result<PrivateContentSnapshot> {
    if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
    platform::snapshot_private_read_only(root, package_id)
}

/// Parent-held root capability. Linux reopens generations relative to the retained directory fd.
#[derive(Clone)]
pub struct AppDataRoot { root: Arc<platform::Root> }
impl std::fmt::Debug for AppDataRoot {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { f.write_str("AppDataRoot(private)") }
}
impl AppDataRoot {
    pub fn open(root: impl AsRef<Path>) -> Result<Self> { Ok(Self { root: Arc::new(platform::Root::open(root.as_ref())?) }) }
    pub fn grant(&self, data_key: &str, slots: &[u8]) -> Result<AppDataGrant> {
        let mask = grant_slots(data_key, slots)?;
        self.grant_directory(&key_directory_name(data_key), mask)
    }
    /// The trusted parent must check user authorization and pass the installed package identity.
    /// A guest-provided identity is never authority to create a grant.
    pub fn grant_package(&self, package_id: &str, slots: &[u8]) -> Result<AppDataGrant> {
        if !valid_package_id(package_id) {
            return Err(AppDataError::InvalidArgument);
        }
        let mask = grant_slots("package", slots)?;
        self.grant_directory(&package_directory_name(package_id), mask)
    }
    fn grant_directory(&self, name: &str, mask: u8) -> Result<AppDataGrant> {
        let directory = platform::Directory::open(&self.root, name)?;
        Ok(self.grant_opened_directory(directory, mask))
    }
    #[cfg(target_os = "linux")]
    fn grant_existing_directory(&self, name: &str, mask: u8) -> Result<AppDataGrant> {
        Ok(self.grant_opened_directory(platform::Directory::open_existing(&self.root, name)?, mask))
    }
    fn grant_opened_directory(&self, directory: platform::Directory, mask: u8) -> AppDataGrant {
        AppDataGrant {
            shared: Arc::new(Mutex::new(State {
                directory,
                slots: mask,
                revoked: false,
                #[cfg(test)]
                fail_commit: false,
                #[cfg(test)]
                fail_directory_sync: false,
                #[cfg(test)]
                change_read: None,
            })),
        }
    }
}
/// Matches the package manifest identity contract.
pub fn valid_package_id(id: &str) -> bool {
    id.len() <= 128
        && id.as_bytes().first().is_some_and(u8::is_ascii_lowercase)
        && id.contains(['.', '-'])
        && id.split(['.', '-']).all(|part| {
            !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
        })
}

/// Authorization coordinator owned by the trusted parent. Decisions must come from trusted user
/// interaction. Persistent mode is available on Linux and must be the sole authority for its root.
pub struct PackageStorageAuthority {
    root: AppDataRoot,
    authorized: Mutex<BTreeMap<String, PackageAuthorization>>,
    #[cfg(target_os = "linux")]
    private: Mutex<BTreeMap<String, PackageAuthorization>>,
    public_authorized: Mutex<BTreeMap<String, PackageAuthorization>>,
    policy: Option<PersistentPolicy>,
    public_policy: Option<PersistentPolicy>,
    #[cfg(target_os = "linux")]
    _private_only_lock: Option<PersistentPolicy>,
}
struct PackageAuthorization {
    fingerprint: Option<[u8; 32]>,
    grants: Vec<Weak<Mutex<State>>>,
    generation: Arc<AtomicBool>,
    #[cfg(target_os = "linux")]
    sessions: Vec<Weak<Mutex<bool>>>,
}
#[cfg(target_os = "linux")]
pub(crate) struct AuthorizationSnapshot {
    pub(crate) fingerprint: Option<[u8; 32]>,
    generation: Arc<AtomicBool>,
}
#[cfg(target_os = "linux")]
#[derive(Debug)]
pub struct PrivateMigrationTransaction {
    pub(crate) package_id: String,
    pub(crate) backup_name: String,
}
#[cfg(target_os = "linux")]
impl AuthorizationSnapshot {
    pub(crate) fn revocation_pending(&self) -> bool { !self.generation.load(Ordering::Acquire) }
}
impl Default for PackageAuthorization {
    fn default() -> Self {
        Self {
            fingerprint: None,
            grants: Vec::new(),
            generation: Arc::new(AtomicBool::new(true)),
            #[cfg(target_os = "linux")]
            sessions: Vec::new(),
        }
    }
}
struct PersistentPolicy { store: AppDataGrant, _lock: File }
#[cfg(target_os = "linux")]
impl Drop for PersistentPolicy {
    fn drop(&mut self) {
        use std::os::fd::AsRawFd;
        // A forked child can retain the open file description until exec, even with CLOEXEC.
        // Unlock explicitly after PackageStorageAuthority has revoked its active grants.
        unsafe { libc::flock(self._lock.as_raw_fd(), libc::LOCK_UN); }
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AuthorizationReceipt {
    pub persisted: bool,
    pub directory_synced: bool,
}
#[cfg(target_os = "linux")]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum PackageBinding { Missing, Matched, Conflict }
const POLICY_HEADER: &str = "ming-private-storage-policy-v1\n";
const POLICY_HEADER_V2: &str = "ming-private-storage-policy-v2\n";
const PUBLIC_POLICY_HEADER: &str = "ming-public-storage-policy-v1\n";
const PUBLIC_POLICY_HEADER_V2: &str = "ming-public-storage-policy-v2\n";
#[cfg(target_os = "linux")]
fn load_persistent_policy(
    store: &AppDataGrant,
    legacy_header: &str,
    bound_header: &str,
) -> Result<BTreeMap<String, PackageAuthorization>> {
    let bytes = match store.read(1, 0, MAX_SLOT_BYTES as i32) {
        Ok(bytes) => bytes,
        Err(AppDataError::NotFound) => Vec::new(),
        Err(error) => return Err(error),
    };
    if bytes.is_empty() {
        if store.size(1).is_ok() { return Err(AppDataError::Io); }
        return Ok(BTreeMap::new());
    }
    let text = std::str::from_utf8(&bytes).map_err(|_| AppDataError::Io)?;
    let (lines, v2) = if let Some(lines) = text.strip_prefix(bound_header) {
        (lines, true)
    } else { (text.strip_prefix(legacy_header).ok_or(AppDataError::Io)?, false) };
    if !lines.is_empty() && !lines.ends_with('\n') { return Err(AppDataError::Io); }
    let mut authorized = BTreeMap::new();
    let mut previous: Option<&str> = None;
    for line in lines.lines() {
        let (id, fingerprint) = if v2 {
            let (id, value) = line.split_once('\t').ok_or(AppDataError::Io)?;
            let fingerprint = if value == "-" { None } else {
                if value.len() != 64 || !value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) { return Err(AppDataError::Io); }
                let mut result = [0u8; 32];
                for (index, item) in result.iter_mut().enumerate() {
                    *item = u8::from_str_radix(&value[index * 2..index * 2 + 2], 16).map_err(|_| AppDataError::Io)?;
                }
                Some(result)
            };
            (id, fingerprint)
        } else { (line, None) };
        if !valid_package_id(id) || previous.is_some_and(|value| value >= id) { return Err(AppDataError::Io); }
        authorized.insert(id.to_owned(), PackageAuthorization { fingerprint, ..Default::default() });
        previous = Some(id);
    }
    Ok(authorized)
}
impl PackageStorageAuthority {
    pub fn new(root: AppDataRoot) -> Self {
        Self { root, authorized: Mutex::new(BTreeMap::new()), #[cfg(target_os = "linux")] private: Mutex::new(BTreeMap::new()), public_authorized: Mutex::new(BTreeMap::new()), policy: None, public_policy: None, #[cfg(target_os = "linux")] _private_only_lock: None }
    }
    /// The registry lock must already be held. No absent policy directory or lock is created.
    /// As with default private mode, an absent public slot means first-use empty authorization.
    #[cfg(target_os = "linux")]
    pub(crate) fn open_update_recovery_existing(path: &Path) -> Result<Self> {
        Self::open_update_recovery_existing_checked(path, || {})
    }
    #[cfg(target_os = "linux")]
    fn open_update_recovery_existing_checked(path: &Path, after_policy_read: impl FnOnce()) -> Result<Self> {
        let root = AppDataRoot::open(path)?;
        let before = platform::stamp_root(&root.root)?;
        let store = root.grant_existing_directory("system-private-storage-policy-v1", 1)?;
        let lock = store.shared.lock().map_err(|_| AppDataError::Denied)?.directory.lock_policy_existing()?;
        let private_binding = store.shared.lock().map_err(|_| AppDataError::Denied)?.directory.policy_binding(&lock)?;
        let public_store = root.grant_existing_directory("system-public-storage-policy-v1", 1)?;
        let public_lock = public_store.shared.lock().map_err(|_| AppDataError::Denied)?.directory.lock_policy_existing()?;
        let public_binding = public_store.shared.lock().map_err(|_| AppDataError::Denied)?.directory.policy_binding(&public_lock)?;
        let public_authorized = load_persistent_policy(&public_store, PUBLIC_POLICY_HEADER, PUBLIC_POLICY_HEADER_V2)?;
        after_policy_read();
        store.shared.lock().map_err(|_| AppDataError::Denied)?.directory.verify_policy_binding(
            &root.root, "system-private-storage-policy-v1", &lock, &private_binding)?;
        public_store.shared.lock().map_err(|_| AppDataError::Denied)?.directory.verify_policy_binding(
            &root.root, "system-public-storage-policy-v1", &public_lock, &public_binding)?;
        if before != platform::stamp_root(&root.root)? || before != platform::stamp_root(&platform::Root::open(path)?)? {
            return Err(AppDataError::Io);
        }
        Ok(Self {
            root,
            authorized: Mutex::new(BTreeMap::new()),
            private: Mutex::new(BTreeMap::new()),
            public_authorized: Mutex::new(public_authorized),
            policy: None,
            public_policy: Some(PersistentPolicy { store: public_store, _lock: public_lock }),
            _private_only_lock: Some(PersistentPolicy { store, _lock: lock }),
        })
    }
    /// Reuses the authority's pinned root and serializes with its private-file operations.
    #[cfg(target_os = "linux")]
    pub(crate) fn observe_update(&self, package_id: &str) -> Result<PrivateUpdateBaseline> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let _authorized = self.authorized.lock().map_err(|_| AppDataError::Denied)?;
        let _private = self.private.lock().map_err(|_| AppDataError::Denied)?;
        platform::observe_update_checked(&self.root.root, package_id, || {})
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn open_default_private(root: AppDataRoot) -> Result<Self> {
        let store = root.grant_directory("system-private-storage-policy-v1", 1)?;
        let lock = store.shared.lock().map_err(|_| AppDataError::Denied)?.directory.lock_policy()?;
        let public_store = root.grant_directory("system-public-storage-policy-v1", 1)?;
        let public_lock = public_store.shared.lock().map_err(|_| AppDataError::Denied)?.directory.lock_policy()?;
        let public_authorized = load_persistent_policy(&public_store, PUBLIC_POLICY_HEADER, PUBLIC_POLICY_HEADER_V2)?;
        Ok(Self {
            root,
            authorized: Mutex::new(BTreeMap::new()),
            private: Mutex::new(BTreeMap::new()),
            public_authorized: Mutex::new(public_authorized),
            policy: None,
            public_policy: Some(PersistentPolicy { store: public_store, _lock: public_lock }),
            _private_only_lock: Some(PersistentPolicy { store, _lock: lock }),
        })
    }
    /// The caller must ensure that only this trusted coordinator issues package grants.
    #[cfg(target_os = "linux")]
    pub fn open_persistent(root: AppDataRoot) -> Result<Self> {
        let store = root.grant_directory("system-private-storage-policy-v1", 1)?;
        let lock = store.shared.lock().map_err(|_| AppDataError::Denied)?.directory.lock_policy()?;
        let public_store = root.grant_directory("system-public-storage-policy-v1", 1)?;
        let public_lock = public_store.shared.lock().map_err(|_| AppDataError::Denied)?.directory.lock_policy()?;
        let authorized = load_persistent_policy(&store, POLICY_HEADER, POLICY_HEADER_V2)?;
        let public_authorized = load_persistent_policy(&public_store, PUBLIC_POLICY_HEADER, PUBLIC_POLICY_HEADER_V2)?;
        Ok(Self { root, authorized: Mutex::new(authorized), private: Mutex::new(BTreeMap::new()), public_authorized: Mutex::new(public_authorized), policy: Some(PersistentPolicy { store, _lock: lock }), public_policy: Some(PersistentPolicy { store: public_store, _lock: public_lock }), _private_only_lock: None })
    }
    fn persist(&self, authorized: &BTreeMap<String, PackageAuthorization>) -> Result<AuthorizationReceipt> {
        let Some(policy) = &self.policy else {
            return Ok(AuthorizationReceipt { persisted: false, directory_synced: false });
        };
        let v2 = authorized.values().any(|entry| entry.generation.load(Ordering::Acquire) && entry.fingerprint.is_some());
        let mut text = String::from(if v2 { POLICY_HEADER_V2 } else { POLICY_HEADER });
        for (id, entry) in authorized {
            if !entry.generation.load(Ordering::Acquire) { continue; }
            text.push_str(id);
            if v2 {
                use std::fmt::Write as _;
                text.push('\t');
                if let Some(fingerprint) = entry.fingerprint {
                    for byte in fingerprint { write!(&mut text, "{byte:02x}").map_err(|_| AppDataError::Io)?; }
                } else { text.push('-'); }
            }
            text.push('\n');
            if text.len() > MAX_SLOT_BYTES { return Err(AppDataError::Io); }
        }
        let receipt = policy.store.write(1, text.as_bytes())?;
        Ok(AuthorizationReceipt { persisted: true, directory_synced: receipt.directory_synced })
    }
    fn persist_public(&self, authorized: &BTreeMap<String, PackageAuthorization>) -> Result<AuthorizationReceipt> {
        let Some(policy) = &self.public_policy else {
            return Ok(AuthorizationReceipt { persisted: false, directory_synced: false });
        };
        let v2 = authorized.values().any(|entry| entry.generation.load(Ordering::Acquire) && entry.fingerprint.is_some());
        let mut text = String::from(if v2 { PUBLIC_POLICY_HEADER_V2 } else { PUBLIC_POLICY_HEADER });
        for (id, entry) in authorized {
            if !entry.generation.load(Ordering::Acquire) { continue; }
            text.push_str(id);
            if v2 {
                use std::fmt::Write as _;
                text.push('\t');
                if let Some(fingerprint) = entry.fingerprint {
                    for byte in fingerprint { write!(&mut text, "{byte:02x}").map_err(|_| AppDataError::Io)?; }
                } else { text.push('-'); }
            }
            text.push('\n');
            if text.len() > MAX_SLOT_BYTES { return Err(AppDataError::Io); }
        }
        let receipt = policy.store.write(1, text.as_bytes())?;
        Ok(AuthorizationReceipt { persisted: true, directory_synced: receipt.directory_synced })
    }
    /// Called by the trusted parent after the user has approved this installed identity.
    pub fn authorize(&self, package_id: &str) -> Result<AuthorizationReceipt> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let mut authorized = self.authorized.lock().map_err(|_| AppDataError::Denied)?;
        if authorized.get(package_id).is_some_and(|entry| entry.fingerprint.is_some() || !entry.generation.load(Ordering::Acquire)) { return Err(AppDataError::Denied); }
        let inserted = !authorized.contains_key(package_id);
        authorized.entry(package_id.to_owned()).or_default();
        match self.persist(&authorized) {
            Ok(receipt) => Ok(receipt),
            Err(error) => {
                if inserted { authorized.remove(package_id); }
                Err(error)
            }
        }
    }
    /// Grants access to the shared public directory. This is independent from private storage.
    pub fn authorize_public(&self, package_id: &str) -> Result<AuthorizationReceipt> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let mut authorized = self.public_authorized.lock().map_err(|_| AppDataError::Denied)?;
        if authorized.get(package_id).is_some_and(|entry| !entry.generation.load(Ordering::Acquire)) {
            return Err(AppDataError::Denied);
        }
        let inserted = !authorized.contains_key(package_id);
        authorized.entry(package_id.to_owned()).or_default();
        match self.persist_public(&authorized) {
            Ok(receipt) => Ok(receipt),
            Err(error) => { if inserted { authorized.remove(package_id); } Err(error) }
        }
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn authorize_public_bound(&self, package_id: &str, fingerprint: [u8; 32]) -> Result<AuthorizationReceipt> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let mut authorized = self.public_authorized.lock().map_err(|_| AppDataError::Denied)?;
        if let Some(entry) = authorized.get(package_id) {
            if entry.generation.load(Ordering::Acquire) && entry.fingerprint == Some(fingerprint) {
                return self.persist_public(&authorized);
            }
        }
        if let Some(entry) = authorized.get_mut(package_id) {
            entry.generation.store(false, Ordering::Release);
            for session in entry.sessions.drain(..).filter_map(|session| session.upgrade()) {
                *session.lock().unwrap_or_else(|poison| poison.into_inner()) = false;
            }
        }
        authorized.insert(package_id.to_owned(), PackageAuthorization { fingerprint: Some(fingerprint), ..Default::default() });
        match self.persist_public(&authorized) {
            Ok(receipt) => Ok(receipt),
            Err(error) => { authorized.remove(package_id); Err(error) }
        }
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn public_binding_status(&self, package_id: &str, fingerprint: [u8; 32]) -> Result<PackageBinding> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let authorized = self.public_authorized.lock().map_err(|_| AppDataError::Denied)?;
        Ok(match authorized.get(package_id) {
            None => PackageBinding::Missing,
            Some(entry) if entry.generation.load(Ordering::Acquire) && entry.fingerprint == Some(fingerprint) => PackageBinding::Matched,
            Some(_) => PackageBinding::Conflict,
        })
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn supports_public_persistence(&self) -> bool { self.public_policy.is_some() }
    #[cfg(target_os = "linux")]
    pub(crate) fn authorize_bound(&self, package_id: &str, fingerprint: [u8; 32]) -> Result<AuthorizationReceipt> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let mut authorized = self.authorized.lock().map_err(|_| AppDataError::Denied)?;
        if let Some(entry) = authorized.get(package_id) {
            if !entry.generation.load(Ordering::Acquire) || entry.fingerprint != Some(fingerprint) { return Err(AppDataError::Denied); }
            return self.persist(&authorized);
        }
        authorized.insert(package_id.to_owned(), PackageAuthorization { fingerprint: Some(fingerprint), ..Default::default() });
        match self.persist(&authorized) {
            Ok(receipt) => Ok(receipt),
            Err(error) => { authorized.remove(package_id); Err(error) }
        }
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn binding_status(&self, package_id: &str, fingerprint: [u8; 32]) -> Result<PackageBinding> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let authorized = self.authorized.lock().map_err(|_| AppDataError::Denied)?;
        Ok(match authorized.get(package_id) {
            None => PackageBinding::Missing,
            Some(entry) if entry.generation.load(Ordering::Acquire) && entry.fingerprint == Some(fingerprint) => PackageBinding::Matched,
            Some(_) => PackageBinding::Conflict,
        })
    }
    pub fn grant(&self, package_id: &str, slots: &[u8]) -> Result<AppDataGrant> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let mut authorized = self.authorized.lock().map_err(|_| AppDataError::Denied)?;
        let entry = authorized.get_mut(package_id).ok_or(AppDataError::Denied)?;
        if !entry.generation.load(Ordering::Acquire) || entry.fingerprint.is_some() { return Err(AppDataError::Denied); }
        let grants = &mut entry.grants;
        grants.retain(|grant| grant.strong_count() != 0);
        if grants.len() >= 64 { return Err(AppDataError::Io); }
        let grant = self.root.grant_package(package_id, slots)?;
        grants.push(Arc::downgrade(&grant.shared));
        Ok(grant)
    }
    /// Creates a parent-owned capability bound to an already authorized package identity.
    #[cfg(target_os = "linux")]
    pub fn file_session(self: &Arc<Self>, package_id: &str) -> Result<PrivateFileSession> {
        self.file_session_checked(package_id, None)
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn file_session_bound(self: &Arc<Self>, package_id: &str, fingerprint: [u8; 32]) -> Result<PrivateFileSession> {
        self.file_session_checked(package_id, Some(fingerprint))
    }
    /// Creates the application's private capability without a user permission decision.
    /// The package must be selected by the trusted parent; package ID is not an installer identity.
    #[cfg(target_os = "linux")]
    pub(crate) fn default_private_session(self: &Arc<Self>, package_id: &str, fingerprint: [u8; 32]) -> Result<PrivateFileSession> {
        if !valid_package_id(package_id) { return Err(AppDataError::Denied); }
        let mut private = self.private.lock().map_err(|_| AppDataError::Denied)?;
        let entry = private.entry(package_id.to_owned()).or_default();
        entry.sessions.retain(|session| session.strong_count() != 0);
        if entry.sessions.len() >= 64 { return Err(AppDataError::Io); }
        let active = Arc::new(Mutex::new(true));
        entry.sessions.push(Arc::downgrade(&active));
        Ok(PrivateFileSession { authority: self.clone(), package_id: package_id.to_owned(), generation: entry.generation.clone(), fingerprint: Some(fingerprint), active })
    }
    /// Permanently removes one application's private directory after all private sessions close.
    /// Shared public files and their authorization are intentionally unaffected.
    #[cfg(target_os = "linux")]
    pub fn purge_private_data(&self, package_id: &str) -> Result<bool> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let authorized = self.authorized.lock().map_err(|_| AppDataError::Denied)?;
        if authorized.contains_key(package_id) { return Err(AppDataError::Denied); }
        let mut private = self.private.lock().map_err(|_| AppDataError::Denied)?;
        if let Some(entry) = private.get_mut(package_id) {
            entry.sessions.retain(|session| session.strong_count() != 0);
            if entry.sessions.iter().any(|session| session.strong_count() != 0) {
                return Err(AppDataError::Denied);
            }
        }
        let removed = self.root.root.remove_tree(&package_directory_name(package_id))?;
        if let Some(entry) = private.remove(package_id) {
            entry.generation.store(false, Ordering::Release);
        }
        Ok(removed)
    }
    /// Applies a validated, declarative update plan to one package's private files.
    /// The package must have no live private session. The old `files` directory remains
    /// authoritative until the staged tree has been fully validated and atomically swapped.
    #[cfg(target_os = "linux")]
    pub fn begin_private_data_migration(&self, package_id: &str, migration: &DataMigration) -> Result<PrivateMigrationTransaction> {
        let backup_name = format!(".ming-update-backup-{}-{}", std::process::id(), TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed));
        self.begin_private_data_migration_with_backup(package_id, migration, &backup_name)
    }
    #[cfg(target_os = "linux")]
    pub fn begin_private_data_migration_with_backup(&self, package_id: &str, migration: &DataMigration, backup_name: &str) -> Result<PrivateMigrationTransaction> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        if !valid_migration_backup_name(backup_name) {
            return Err(AppDataError::InvalidArgument);
        }
        let authorized = self.authorized.lock().map_err(|_| AppDataError::Denied)?;
        if authorized.contains_key(package_id) { return Err(AppDataError::Denied); }
        let mut private = self.private.lock().map_err(|_| AppDataError::Denied)?;
        if let Some(entry) = private.get_mut(package_id) {
            entry.sessions.retain(|session| session.strong_count() != 0);
            if entry.sessions.iter().any(|session| session.strong_count() != 0) {
                return Err(AppDataError::Denied);
            }
        }
        let package = platform::Directory::open(&self.root.root, &package_directory_name(package_id))?;
        let files = package.child("files", true)?;
        files.usage()?;
        let staging_name = format!("{backup_name}.staging");
        let staging = package.new_child(&staging_name)?;
        let result = (|| {
            platform::copy_private_tree(&files, &staging)?;
            for step in &migration.steps {
                platform::apply_migration_step(&staging, step)?;
                staging.usage()?;
            }
            staging.usage()?;
            if !staging.sync() { return Err(AppDataError::Io); }
            package.rename_no_replace("files", &package, backup_name)?;
            if let Err(error) = package.rename_no_replace(&staging_name, &package, "files") {
                let _ = package.rename_no_replace(&backup_name, &package, "files");
                return Err(error);
            }
            if !package.sync() { return Err(AppDataError::Io); }
            Ok(PrivateMigrationTransaction { package_id: package_id.to_owned(), backup_name: backup_name.to_owned() })
        })();
        if result.is_err() { package.remove_tree(&staging_name).ok(); }
        if result.is_ok() {
            if let Some(entry) = private.remove(package_id) { entry.generation.store(false, Ordering::Release); }
        }
        result
    }
    #[cfg(target_os = "linux")]
    pub fn commit_private_data_migration(&self, transaction: &PrivateMigrationTransaction) -> Result<bool> {
        if !valid_package_id(&transaction.package_id) || !valid_migration_backup_name(&transaction.backup_name) {
            return Err(AppDataError::InvalidArgument);
        }
        let package = platform::Directory::open(&self.root.root, &package_directory_name(&transaction.package_id))?;
        let removed = package.remove_tree(&transaction.backup_name)?;
        if !package.sync() { return Err(AppDataError::Io); }
        Ok(removed)
    }
    #[cfg(target_os = "linux")]
    pub fn rollback_private_data_migration(&self, transaction: &PrivateMigrationTransaction) -> Result<()> {
        if !valid_package_id(&transaction.package_id) || !valid_migration_backup_name(&transaction.backup_name) {
            return Err(AppDataError::InvalidArgument);
        }
        let authorized = self.authorized.lock().map_err(|_| AppDataError::Denied)?;
        if authorized.contains_key(&transaction.package_id) { return Err(AppDataError::Denied); }
        let mut private = self.private.lock().map_err(|_| AppDataError::Denied)?;
        if let Some(entry) = private.get_mut(&transaction.package_id) {
            entry.sessions.retain(|session| session.strong_count() != 0);
            if !entry.sessions.is_empty() { return Err(AppDataError::Denied); }
        }
        let package = platform::Directory::open(&self.root.root, &package_directory_name(&transaction.package_id))?;
        let staging_name = format!("{}.staging", transaction.backup_name);
        let current_name = format!("{}.discard", transaction.backup_name);
        // A crash after the old tree was restored but before the journal was
        // cleared must be harmless on the next open. In that state the backup
        // is gone and the restored files directory is already authoritative.
        match package.child(&transaction.backup_name, false) {
            Ok(_) => {}
            Err(AppDataError::NotFound) => {
                package.child("files", true)?;
                package.remove_tree(&staging_name)?;
                package.remove_tree(&current_name)?;
                if !package.sync() { return Err(AppDataError::Io); }
                return Ok(());
            }
            Err(error) => return Err(error),
        }
        match package.child("files", false) {
            Ok(_) => package.rename_no_replace("files", &package, &current_name)?,
            Err(AppDataError::NotFound) => {},
            Err(error) => return Err(error),
        }
        package.rename_no_replace(&transaction.backup_name, &package, "files")?;
        if !package.sync() { return Err(AppDataError::Io); }
        package.remove_tree(&current_name)?;
        package.remove_tree(&staging_name)?;
        if !package.sync() { return Err(AppDataError::Io); }
        if let Some(entry) = private.remove(&transaction.package_id) { entry.generation.store(false, Ordering::Release); }
        Ok(())
    }
    #[cfg(target_os = "linux")]
    pub fn migrate_private_data(&self, package_id: &str, migration: &DataMigration) -> Result<bool> {
        let transaction = self.begin_private_data_migration(package_id, migration)?;
        self.commit_private_data_migration(&transaction)
    }
    /// Creates a parent-owned capability for the shared public directory.
    #[cfg(target_os = "linux")]
    pub fn public_session(self: &Arc<Self>, package_id: &str) -> Result<PublicFileSession> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let mut authorized = self.public_authorized.lock().map_err(|_| AppDataError::Denied)?;
        let entry = authorized.get_mut(package_id).ok_or(AppDataError::Denied)?;
        if !entry.generation.load(Ordering::Acquire) { return Err(AppDataError::Denied); }
        entry.sessions.retain(|session| session.strong_count() != 0);
        if entry.sessions.len() >= 64 { return Err(AppDataError::Io); }
        let active = Arc::new(Mutex::new(true));
        entry.sessions.push(Arc::downgrade(&active));
        Ok(PublicFileSession { authority: self.clone(), package_id: package_id.to_owned(), generation: entry.generation.clone(), fingerprint: None, active })
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn public_session_bound(self: &Arc<Self>, package_id: &str, fingerprint: [u8; 32]) -> Result<PublicFileSession> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let mut authorized = self.public_authorized.lock().map_err(|_| AppDataError::Denied)?;
        let entry = authorized.get_mut(package_id).ok_or(AppDataError::Denied)?;
        if !entry.generation.load(Ordering::Acquire) || entry.fingerprint != Some(fingerprint) { return Err(AppDataError::Denied); }
        entry.sessions.retain(|session| session.strong_count() != 0);
        if entry.sessions.len() >= 64 { return Err(AppDataError::Io); }
        let active = Arc::new(Mutex::new(true));
        entry.sessions.push(Arc::downgrade(&active));
        Ok(PublicFileSession { authority: self.clone(), package_id: package_id.to_owned(), generation: entry.generation.clone(), fingerprint: Some(fingerprint), active })
    }
    #[cfg(target_os = "linux")]
    fn file_session_checked(self: &Arc<Self>, package_id: &str, fingerprint: Option<[u8; 32]>) -> Result<PrivateFileSession> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let mut authorized = self.authorized.lock().map_err(|_| AppDataError::Denied)?;
        let entry = authorized.get_mut(package_id).ok_or(AppDataError::Denied)?;
        if !entry.generation.load(Ordering::Acquire) || entry.fingerprint != fingerprint { return Err(AppDataError::Denied); }
        entry.sessions.retain(|session| session.strong_count() != 0);
        if entry.sessions.len() >= 64 { return Err(AppDataError::Io); }
        let active = Arc::new(Mutex::new(true));
        entry.sessions.push(Arc::downgrade(&active));
        Ok(PrivateFileSession { authority: self.clone(), package_id: package_id.to_owned(), generation: entry.generation.clone(), fingerprint, active })
    }
    /// These parent-only methods require a trusted installed identity, never a guest-supplied ID.
    /// All operations share the authority lock so quota checks and revocation are serialized.
    #[cfg(target_os = "linux")]
    fn with_files<T>(&self, package_id: &str, generation: Option<&AtomicBool>, operation: impl FnOnce(&platform::Directory) -> Result<T>) -> Result<T> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let authorized = self.authorized.lock().map_err(|_| AppDataError::Denied)?;
        let private = self.private.lock().map_err(|_| AppDataError::Denied)?;
        let active = |entry: &PackageAuthorization| entry.generation.load(Ordering::Acquire)
            && generation.is_none_or(|g| std::ptr::eq(entry.generation.as_ref(), g));
        if !authorized.get(package_id).is_some_and(active)
            && !(generation.is_some() && private.get(package_id).is_some_and(active)) { return Err(AppDataError::Denied); }
        let package = platform::Directory::open(&self.root.root, &package_directory_name(package_id))?;
        let files = package.child("files", true)?;
        operation(&files)
    }
    #[cfg(target_os = "linux")]
    pub fn create_file_directory(&self, package_id: &str, path: &str) -> Result<bool> {
        self.create_file_directory_checked(package_id, path, None)
    }
    #[cfg(target_os = "linux")]
    fn create_file_directory_checked(&self, package_id: &str, path: &str, generation: Option<&AtomicBool>) -> Result<bool> {
        self.with_files(package_id, generation, |files| {
            let components = validate_private_path(path)?;
            let (parent, name) = files.parent(&components)?;
            let (_, entries) = files.usage()?;
            if entries >= MAX_PRIVATE_ENTRIES { return Err(AppDataError::Io); }
            parent.child(name, true)?;
            Ok(parent.sync())
        })
    }
    /// An empty path lists the app's private root; all other paths are relative directories.
    #[cfg(target_os = "linux")]
    pub fn list_file_directory(&self, package_id: &str, path: &str) -> Result<Vec<PrivateFileEntry>> {
        self.list_file_directory_checked(package_id, path, None)
    }
    #[cfg(target_os = "linux")]
    fn list_file_directory_checked(&self, package_id: &str, path: &str, generation: Option<&AtomicBool>) -> Result<Vec<PrivateFileEntry>> {
        self.with_files(package_id, generation, |files| {
            if path.is_empty() { return files.list(); }
            let components = validate_private_path(path)?;
            let (parent, name) = files.parent(&components)?;
            parent.child(name, false)?.list()
        })
    }
    #[cfg(target_os = "linux")]
    pub fn delete_file_directory(&self, package_id: &str, path: &str) -> Result<bool> {
        self.delete_file_directory_checked(package_id, path, None)
    }
    #[cfg(target_os = "linux")]
    fn delete_file_directory_checked(&self, package_id: &str, path: &str, generation: Option<&AtomicBool>) -> Result<bool> {
        self.with_files(package_id, generation, |files| {
            let components = validate_private_path(path)?;
            let (parent, name) = files.parent(&components)?;
            parent.remove_directory(name)?;
            Ok(parent.sync())
        })
    }
    #[cfg(target_os = "linux")]
    pub fn read_file(&self, package_id: &str, path: &str) -> Result<Vec<u8>> {
        self.read_file_checked(package_id, path, None)
    }
    #[cfg(target_os = "linux")]
    fn read_file_checked(&self, package_id: &str, path: &str, generation: Option<&AtomicBool>) -> Result<Vec<u8>> {
        self.with_files(package_id, generation, |files| {
            let components = validate_private_path(path)?;
            let (parent, name) = files.parent(&components)?;
            let mut file = parent.read_private_file(name)?;
            let before = platform::stamp_private(&file)?;
            let mut bytes = vec![0; before.length() as usize];
            file.read_exact(&mut bytes).map_err(io_error)?;
            file.seek(SeekFrom::Start(0)).map_err(io_error)?;
            let mut verify = vec![0; bytes.len()];
            file.read_exact(&mut verify).map_err(io_error)?;
            if bytes != verify { return Err(AppDataError::Io); }
            parent.unchanged_private(name, &file, &before)?;
            Ok(bytes)
        })
    }
    #[cfg(target_os = "linux")]
    pub fn write_file(&self, package_id: &str, path: &str, bytes: &[u8]) -> Result<AppDataWrite> {
        self.write_file_checked(package_id, path, bytes, None)
    }
    #[cfg(target_os = "linux")]
    fn write_file_checked(&self, package_id: &str, path: &str, bytes: &[u8], generation: Option<&AtomicBool>) -> Result<AppDataWrite> {
        self.with_files(package_id, generation, |files| {
            if bytes.len() > MAX_PRIVATE_FILE_BYTES { return Err(AppDataError::InvalidArgument); }
            let components = validate_private_path(path)?;
            let (parent, name) = files.parent(&components)?;
            let previous = match parent.read_private_file(name) {
                Ok(file) => Some(platform::stamp_private(&file)?.length() as usize),
                Err(AppDataError::NotFound) => None,
                Err(error) => return Err(error),
            };
            let (used, entries) = files.usage()?;
            if used.saturating_sub(previous.unwrap_or(0)).saturating_add(bytes.len()) > MAX_PRIVATE_TOTAL_BYTES
                || (previous.is_none() && entries >= MAX_PRIVATE_ENTRIES) { return Err(AppDataError::Io); }
            let (temporary, mut file) = parent.new_temp()?;
            let result = (|| {
                file.write_all(bytes).map_err(io_error)?;
                file.sync_all().map_err(io_error)?;
                parent.check_private_target(name)?;
                parent.replace(&temporary, name)
            })();
            drop(file);
            if let Err(error) = result { parent.remove_temp(&temporary); return Err(error); }
            Ok(AppDataWrite { length: bytes.len(), directory_synced: parent.sync() })
        })
    }
    #[cfg(target_os = "linux")]
    pub fn delete_file(&self, package_id: &str, path: &str) -> Result<bool> {
        self.delete_file_checked(package_id, path, None)
    }
    #[cfg(target_os = "linux")]
    fn delete_file_checked(&self, package_id: &str, path: &str, generation: Option<&AtomicBool>) -> Result<bool> {
        self.with_files(package_id, generation, |files| {
            let components = validate_private_path(path)?;
            let (parent, name) = files.parent(&components)?;
            let file = parent.read_private_file(name)?;
            let before = platform::stamp_private(&file)?;
            parent.unchanged_private(name, &file, &before)?;
            parent.delete(name)?;
            Ok(parent.sync())
        })
    }
    /// Atomically moves a regular file without replacing an existing destination.
    /// The returned flag reports whether both affected directories were synced.
    #[cfg(target_os = "linux")]
    pub fn rename_file(&self, package_id: &str, from: &str, to: &str) -> Result<bool> {
        self.rename_file_checked(package_id, from, to, None)
    }
    #[cfg(target_os = "linux")]
    fn with_public_files<T>(&self, package_id: &str, generation: Option<&AtomicBool>, operation: impl FnOnce(&platform::Directory) -> Result<T>) -> Result<T> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let authorized = self.public_authorized.lock().map_err(|_| AppDataError::Denied)?;
        let active = |entry: &PackageAuthorization| entry.generation.load(Ordering::Acquire)
            && generation.is_none_or(|g| std::ptr::eq(entry.generation.as_ref(), g));
        if !authorized.get(package_id).is_some_and(active) { return Err(AppDataError::Denied); }
        let files = platform::Directory::open(&self.root.root, public_directory_name())?;
        operation(&files)
    }
    #[cfg(target_os = "linux")]
    fn public_create_directory_checked(&self, package_id: &str, path: &str, generation: Option<&AtomicBool>) -> Result<bool> {
        self.with_public_files(package_id, generation, |files| {
            let components = validate_private_path(path)?;
            let (parent, name) = files.parent(&components)?;
            let (_, entries) = files.usage()?;
            if entries >= MAX_PRIVATE_ENTRIES { return Err(AppDataError::Io); }
            parent.child(name, true)?;
            Ok(parent.sync())
        })
    }
    #[cfg(target_os = "linux")]
    fn public_list_directory_checked(&self, package_id: &str, path: &str, generation: Option<&AtomicBool>) -> Result<Vec<PrivateFileEntry>> {
        self.with_public_files(package_id, generation, |files| {
            if path.is_empty() { return files.list(); }
            let components = validate_private_path(path)?;
            let (parent, name) = files.parent(&components)?;
            parent.child(name, false)?.list()
        })
    }
    #[cfg(target_os = "linux")]
    fn public_delete_directory_checked(&self, package_id: &str, path: &str, generation: Option<&AtomicBool>) -> Result<bool> {
        self.with_public_files(package_id, generation, |files| {
            let components = validate_private_path(path)?;
            let (parent, name) = files.parent(&components)?;
            parent.remove_directory(name)?;
            Ok(parent.sync())
        })
    }
    #[cfg(target_os = "linux")]
    fn public_read_file_checked(&self, package_id: &str, path: &str, generation: Option<&AtomicBool>) -> Result<Vec<u8>> {
        self.with_public_files(package_id, generation, |files| {
            let components = validate_private_path(path)?;
            let (parent, name) = files.parent(&components)?;
            let mut file = parent.read_private_file(name)?;
            let before = platform::stamp_private(&file)?;
            let mut bytes = vec![0; before.length() as usize];
            file.read_exact(&mut bytes).map_err(io_error)?;
            file.seek(SeekFrom::Start(0)).map_err(io_error)?;
            let mut verify = vec![0; bytes.len()];
            file.read_exact(&mut verify).map_err(io_error)?;
            if bytes != verify { return Err(AppDataError::Io); }
            parent.unchanged_private(name, &file, &before)?;
            Ok(bytes)
        })
    }
    #[cfg(target_os = "linux")]
    fn public_write_file_checked(&self, package_id: &str, path: &str, bytes: &[u8], generation: Option<&AtomicBool>) -> Result<AppDataWrite> {
        self.with_public_files(package_id, generation, |files| {
            if bytes.len() > MAX_PRIVATE_FILE_BYTES { return Err(AppDataError::InvalidArgument); }
            let components = validate_private_path(path)?;
            let (parent, name) = files.parent(&components)?;
            let previous = match parent.read_private_file(name) {
                Ok(file) => Some(platform::stamp_private(&file)?.length() as usize),
                Err(AppDataError::NotFound) => None,
                Err(error) => return Err(error),
            };
            let (used, entries) = files.usage()?;
            if used.saturating_sub(previous.unwrap_or(0)).saturating_add(bytes.len()) > MAX_PRIVATE_TOTAL_BYTES
                || (previous.is_none() && entries >= MAX_PRIVATE_ENTRIES) { return Err(AppDataError::Io); }
            let (temporary, mut file) = parent.new_temp()?;
            let result = (|| {
                file.write_all(bytes).map_err(io_error)?;
                file.sync_all().map_err(io_error)?;
                parent.check_private_target(name)?;
                parent.replace(&temporary, name)
            })();
            drop(file);
            if let Err(error) = result { parent.remove_temp(&temporary); return Err(error); }
            Ok(AppDataWrite { length: bytes.len(), directory_synced: parent.sync() })
        })
    }
    #[cfg(target_os = "linux")]
    fn public_delete_file_checked(&self, package_id: &str, path: &str, generation: Option<&AtomicBool>) -> Result<bool> {
        self.with_public_files(package_id, generation, |files| {
            let components = validate_private_path(path)?;
            let (parent, name) = files.parent(&components)?;
            let file = parent.read_private_file(name)?;
            let before = platform::stamp_private(&file)?;
            parent.unchanged_private(name, &file, &before)?;
            parent.delete(name)?;
            Ok(parent.sync())
        })
    }
    #[cfg(target_os = "linux")]
    fn public_rename_file_checked(&self, package_id: &str, from: &str, to: &str, generation: Option<&AtomicBool>) -> Result<bool> {
        self.with_public_files(package_id, generation, |files| {
            let source = validate_private_path(from)?;
            let destination = validate_private_path(to)?;
            let (from_dir, from_name) = files.parent(&source)?;
            let (to_dir, to_name) = files.parent(&destination)?;
            let file = from_dir.read_private_file(from_name)?;
            let before = platform::stamp_private(&file)?;
            from_dir.unchanged_private(from_name, &file, &before)?;
            from_dir.rename_no_replace(from_name, &to_dir, to_name)?;
            Ok(from_dir.sync() && to_dir.sync())
        })
    }
    #[cfg(target_os = "linux")]
    fn rename_file_checked(&self, package_id: &str, from: &str, to: &str, generation: Option<&AtomicBool>) -> Result<bool> {
        self.with_files(package_id, generation, |files| {
            let source = validate_private_path(from)?;
            let destination = validate_private_path(to)?;
            let (from_dir, from_name) = files.parent(&source)?;
            let (to_dir, to_name) = files.parent(&destination)?;
            let file = from_dir.read_private_file(from_name)?;
            let before = platform::stamp_private(&file)?;
            from_dir.unchanged_private(from_name, &file, &before)?;
            from_dir.rename_no_replace(from_name, &to_dir, to_name)?;
            let source_synced = from_dir.sync();
            let destination_synced = to_dir.sync();
            Ok(source_synced && destination_synced)
        })
    }
    /// Denies new grants and waits for in-flight I/O on every existing grant.
    pub fn revoke(&self, package_id: &str) -> Result<AuthorizationReceipt> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let mut authorized = self.authorized.lock().map_err(|_| AppDataError::Denied)?;
        self.revoke_locked(package_id, &mut authorized)
    }
    /// Revokes only public-directory access; private storage is unaffected.
    #[cfg(target_os = "linux")]
    pub fn revoke_public(&self, package_id: &str) -> Result<AuthorizationReceipt> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let mut authorized = self.public_authorized.lock().map_err(|_| AppDataError::Denied)?;
        self.revoke_public_locked(package_id, &mut authorized)
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn public_authorization_snapshot(&self, package_id: &str) -> Result<Option<AuthorizationSnapshot>> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let authorized = self.public_authorized.lock().map_err(|_| AppDataError::Denied)?;
        Ok(authorized.get(package_id).map(|entry| AuthorizationSnapshot {
            fingerprint: entry.fingerprint, generation: entry.generation.clone(),
        }))
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn revoke_public_snapshot(&self, package_id: &str, snapshot: &AuthorizationSnapshot) -> Result<AuthorizationReceipt> {
        let mut authorized = self.public_authorized.lock().map_err(|_| AppDataError::Denied)?;
        if !authorized.get(package_id).is_some_and(|entry| Arc::ptr_eq(&entry.generation, &snapshot.generation)) {
            return Err(AppDataError::Denied);
        }
        self.revoke_public_locked(package_id, &mut authorized)
    }
    #[cfg(target_os = "linux")]
    fn revoke_public_locked(&self, package_id: &str, authorized: &mut BTreeMap<String, PackageAuthorization>) -> Result<AuthorizationReceipt> {
        if let Some(entry) = authorized.get_mut(package_id) {
            entry.generation.store(false, Ordering::Release);
            for session in entry.sessions.drain(..).filter_map(|session| session.upgrade()) {
                *session.lock().unwrap_or_else(|poison| poison.into_inner()) = false;
            }
        }
        let receipt = self.persist_public(&authorized)?;
        if self.public_policy.is_none() || receipt.directory_synced { authorized.remove(package_id); }
        Ok(receipt)
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn authorization_snapshot(&self, package_id: &str) -> Result<Option<AuthorizationSnapshot>> {
        if !valid_package_id(package_id) { return Err(AppDataError::InvalidArgument); }
        let authorized = self.authorized.lock().map_err(|_| AppDataError::Denied)?;
        Ok(authorized.get(package_id).map(|entry| AuthorizationSnapshot {
            fingerprint: entry.fingerprint, generation: entry.generation.clone(),
        }))
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn revoke_snapshot(&self, package_id: &str, snapshot: &AuthorizationSnapshot) -> Result<AuthorizationReceipt> {
        let mut authorized = self.authorized.lock().map_err(|_| AppDataError::Denied)?;
        if !authorized.get(package_id).is_some_and(|entry| Arc::ptr_eq(&entry.generation, &snapshot.generation)) {
            return Err(AppDataError::Denied);
        }
        self.revoke_locked(package_id, &mut authorized)
    }
    fn revoke_locked(&self, package_id: &str, authorized: &mut BTreeMap<String, PackageAuthorization>) -> Result<AuthorizationReceipt> {
        if let Some(entry) = authorized.get_mut(package_id) {
            entry.generation.store(false, Ordering::Release);
            for shared in entry.grants.drain(..).filter_map(|grant| grant.upgrade()) {
                AppDataGrant { shared }.revoke();
            }
        }
        let receipt = self.persist(authorized)?;
        if self.policy.is_none() || receipt.directory_synced { authorized.remove(package_id); }
        Ok(receipt)
    }
    #[cfg(test)]
    pub fn fail_next_policy_commit(&self) { self.policy.as_ref().unwrap().store.fail_next_commit(); }
    #[cfg(test)]
    pub fn fail_next_policy_directory_sync(&self) { self.policy.as_ref().unwrap().store.fail_next_directory_sync(); }
    #[cfg(test)]
    pub fn fail_next_public_policy_commit(&self) { self.public_policy.as_ref().unwrap().store.fail_next_commit(); }
    #[cfg(test)]
    pub fn fail_next_public_policy_directory_sync(&self) { self.public_policy.as_ref().unwrap().store.fail_next_directory_sync(); }
}
impl Drop for PackageStorageAuthority {
    fn drop(&mut self) {
        #[cfg(target_os = "linux")]
        for entry in self.private.get_mut().unwrap_or_else(|poison| poison.into_inner()).values() { entry.generation.store(false, Ordering::Release); }
        let authorized = self.authorized.get_mut().unwrap_or_else(|poison| poison.into_inner());
        for entry in authorized.values() { entry.generation.store(false, Ordering::Release); }
        for entry in self.public_authorized.get_mut().unwrap_or_else(|poison| poison.into_inner()).values() { entry.generation.store(false, Ordering::Release); }
        for shared in authorized.values_mut().flat_map(|entry| entry.grants.drain(..)).filter_map(|grant| grant.upgrade()) {
            AppDataGrant { shared }.revoke();
        }
        #[cfg(target_os = "linux")]
        {
            let public = self.public_authorized.get_mut().unwrap_or_else(|poison| poison.into_inner());
            for entry in public.values_mut() {
                for session in entry.sessions.drain(..).filter_map(|session| session.upgrade()) {
                    *session.lock().unwrap_or_else(|poison| poison.into_inner()) = false;
                }
            }
        }
    }
}
/// A parent-owned capability for one running application generation.
#[cfg(target_os = "linux")]
#[derive(Clone)]
pub struct PrivateFileSession {
    authority: Arc<PackageStorageAuthority>,
    package_id: String,
    generation: Arc<AtomicBool>,
    fingerprint: Option<[u8; 32]>,
    active: Arc<Mutex<bool>>,
}
#[cfg(target_os = "linux")]
impl PrivateFileSession {
    pub(crate) fn belongs_to(&self, package_id: &str) -> bool { self.package_id == package_id }
    pub(crate) fn matches_fingerprint(&self, fingerprint: [u8; 32]) -> bool { self.fingerprint == Some(fingerprint) }
    pub(crate) fn check(&self) -> Result<()> {
        self.run(|a, id, g| a.with_files(id, Some(g), |_| Ok(())))
    }
    fn run<T>(&self, operation: impl FnOnce(&PackageStorageAuthority, &str, &AtomicBool) -> Result<T>) -> Result<T> {
        let active = self.active.lock().map_err(|_| AppDataError::Denied)?;
        if !*active { return Err(AppDataError::Denied); }
        operation(&self.authority, &self.package_id, &self.generation)
    }
    /// Waits for in-flight operations and invalidates every clone of this session.
    pub fn close(&self) { *self.active.lock().unwrap_or_else(|poison| poison.into_inner()) = false; }
    pub fn create_directory(&self, path: &str) -> Result<bool> {
        self.run(|a, id, g| a.create_file_directory_checked(id, path, Some(g)))
    }
    pub fn list_directory(&self, path: &str) -> Result<Vec<PrivateFileEntry>> {
        self.run(|a, id, g| a.list_file_directory_checked(id, path, Some(g)))
    }
    pub fn delete_directory(&self, path: &str) -> Result<bool> {
        self.run(|a, id, g| a.delete_file_directory_checked(id, path, Some(g)))
    }
    pub fn read_file(&self, path: &str) -> Result<Vec<u8>> {
        self.run(|a, id, g| a.read_file_checked(id, path, Some(g)))
    }
    pub fn write_file(&self, path: &str, bytes: &[u8]) -> Result<AppDataWrite> {
        self.run(|a, id, g| a.write_file_checked(id, path, bytes, Some(g)))
    }
    pub fn delete_file(&self, path: &str) -> Result<bool> {
        self.run(|a, id, g| a.delete_file_checked(id, path, Some(g)))
    }
    pub fn rename_file(&self, from: &str, to: &str) -> Result<bool> {
        self.run(|a, id, g| a.rename_file_checked(id, from, to, Some(g)))
    }
}
/// A parent-owned capability for the shared public directory.
#[cfg(target_os = "linux")]
#[derive(Clone)]
pub struct PublicFileSession {
    authority: Arc<PackageStorageAuthority>,
    package_id: String,
    generation: Arc<AtomicBool>,
    fingerprint: Option<[u8; 32]>,
    active: Arc<Mutex<bool>>,
}
#[cfg(target_os = "linux")]
impl PublicFileSession {
    pub(crate) fn belongs_to(&self, package_id: &str) -> bool { self.package_id == package_id }
    pub(crate) fn matches_fingerprint(&self, fingerprint: [u8; 32]) -> bool { self.fingerprint == Some(fingerprint) }
    pub(crate) fn check(&self) -> Result<()> {
        self.run(|a, id, g| a.with_public_files(id, Some(g), |_| Ok(())))
    }
    fn run<T>(&self, operation: impl FnOnce(&PackageStorageAuthority, &str, &AtomicBool) -> Result<T>) -> Result<T> {
        let active = self.active.lock().map_err(|_| AppDataError::Denied)?;
        if !*active { return Err(AppDataError::Denied); }
        operation(&self.authority, &self.package_id, &self.generation)
    }
    pub fn close(&self) { *self.active.lock().unwrap_or_else(|poison| poison.into_inner()) = false; }
    pub fn create_directory(&self, path: &str) -> Result<bool> {
        self.run(|a, id, g| a.public_create_directory_checked(id, path, Some(g)))
    }
    pub fn list_directory(&self, path: &str) -> Result<Vec<PrivateFileEntry>> {
        self.run(|a, id, g| a.public_list_directory_checked(id, path, Some(g)))
    }
    pub fn delete_directory(&self, path: &str) -> Result<bool> {
        self.run(|a, id, g| a.public_delete_directory_checked(id, path, Some(g)))
    }
    pub fn read_file(&self, path: &str) -> Result<Vec<u8>> {
        self.run(|a, id, g| a.public_read_file_checked(id, path, Some(g)))
    }
    pub fn write_file(&self, path: &str, bytes: &[u8]) -> Result<AppDataWrite> {
        self.run(|a, id, g| a.public_write_file_checked(id, path, bytes, Some(g)))
    }
    pub fn delete_file(&self, path: &str) -> Result<bool> {
        self.run(|a, id, g| a.public_delete_file_checked(id, path, Some(g)))
    }
    pub fn rename_file(&self, from: &str, to: &str) -> Result<bool> {
        self.run(|a, id, g| a.public_rename_file_checked(id, from, to, Some(g)))
    }
}
/// Structural trusted-catalog validation; performs no I/O and grants no authority.
pub fn validate_grant(data_key: &str, slots: &[u8]) -> Result<()> { grant_slots(data_key, slots).map(|_| ()) }
fn grant_slots(data_key: &str, slots: &[u8]) -> Result<u8> {
        let key = data_key.as_bytes();
        if key.is_empty()
            || key.len() > 64
            || !key[0].is_ascii_alphanumeric()
            || !key
                .iter()
                .all(|b| b.is_ascii_alphanumeric() || *b == b'_' || *b == b'-')
            || slots.is_empty()
            || slots.len() > MAX_SLOTS
        {
            return Err(AppDataError::InvalidArgument);
        }
        let mut mask = 0u8;
        for &slot in slots {
            if !(1..=8).contains(&slot) || mask & (1 << (slot - 1)) != 0 {
                return Err(AppDataError::InvalidArgument);
            }
            mask |= 1 << (slot - 1);
        }
        Ok(mask)
}
#[derive(Clone)]
pub struct AppDataGrant {
    shared: Arc<Mutex<State>>,
}
struct State {
    directory: platform::Directory,
    slots: u8,
    revoked: bool,
    #[cfg(test)]
    fail_commit: bool,
    #[cfg(test)]
    fail_directory_sync: bool,
    #[cfg(test)]
    change_read: Option<bool>,
}
impl AppDataGrant {
    /// root must already exist, be absolute and be provisioned by the trusted parent.
    /// data_key: 1..=64 ASCII bytes, [A-Za-z0-9][A-Za-z0-9_-]*; slots are explicit and unique.
    /// Separate open calls have independent revocation, even when reopening the same data key.
    pub fn open(root: impl AsRef<Path>, data_key: &str, slots: &[u8]) -> Result<Self> {
        validate_grant(data_key, slots)?;
        AppDataRoot::open(root)?.grant(data_key, slots)
    }
    fn lock(&self, slot: i32) -> Result<MutexGuard<'_, State>> {
        let state = self.shared.lock().map_err(|_| AppDataError::Denied)?;
        if state.revoked {
            return Err(AppDataError::Denied);
        }
        valid_slot(slot)?;
        if state.slots & (1 << (slot - 1)) == 0 {
            return Err(AppDataError::Denied);
        }
        Ok(state)
    }
    /// After return, no clone can start further I/O. Already-running I/O completes before return.
    pub fn revoke(&self) {
        let mut state = self
            .shared
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        state.revoked = true;
    }
    pub fn size(&self, slot: i32) -> Result<usize> {
        let state = self.lock(slot)?;
        let file = state.directory.read_file(&slot_name(slot))?;
        let before = platform::stamp(&file)?;
        state
            .directory
            .unchanged(&slot_name(slot), &file, &before)?;
        Ok(before.length() as usize)
    }
    /// Copies a stable snapshot. offset beyond EOF yields an empty buffer, up to 65536.
    pub fn read(&self, slot: i32, offset: i32, capacity: i32) -> Result<Vec<u8>> {
        #[allow(unused_mut)]
        let mut state = self.lock(slot)?;
        valid_read(offset, capacity)?;
        let name = slot_name(slot);
        let mut file = state.directory.read_file(&name)?;
        let before = platform::stamp(&file)?;
        // Read the entire bounded value twice: coarse filesystem timestamps alone cannot
        // detect a same-length in-place overwrite, including outside the requested slice.
        let mut snapshot = vec![0u8; before.length() as usize];
        file.read_exact(&mut snapshot).map_err(io_error)?;
        #[cfg(test)]
        if let Some(grow) = state.change_read.take() {
            let mut writer = state.directory.test_open_writer(&name)?;
            writer
                .seek(if grow {
                    SeekFrom::End(0)
                } else {
                    SeekFrom::Start(0)
                })
                .map_err(io_error)?;
            writer
                .write_all(if grow { b"changed" } else { b"new" })
                .map_err(io_error)?;
            writer.sync_all().map_err(io_error)?;
        }
        file.seek(SeekFrom::Start(0)).map_err(io_error)?;
        let mut verification = vec![0u8; snapshot.len()];
        file.read_exact(&mut verification).map_err(io_error)?;
        if snapshot != verification {
            return Err(AppDataError::Io);
        }
        state.directory.unchanged(&name, &file, &before)?;
        let start = (offset as usize).min(snapshot.len());
        let end = (start + capacity as usize).min(snapshot.len());
        Ok(snapshot[start..end].to_vec())
    }
    /// All Err outcomes precede atomic replacement and preserve the old slot value.
    /// Directory sync happens after the commit; its result is reported without pretending rollback.
    pub fn write(&self, slot: i32, bytes: &[u8]) -> Result<AppDataWrite> {
        #[allow(unused_mut)]
        let mut state = self.lock(slot)?;
        if bytes.len() > MAX_SLOT_BYTES {
            return Err(AppDataError::InvalidArgument);
        }
        let name = slot_name(slot);
        state.directory.check_target(&name)?;
        // create_new/O_EXCL means only this invocation owns the temporary name it removes.
        let (temporary, mut file) = state.directory.new_temp()?;
        let result = (|| {
            file.write_all(bytes).map_err(io_error)?;
            file.sync_all().map_err(io_error)?;
            state.directory.check_target(&name)?;
            #[cfg(test)]
            if std::mem::take(&mut state.fail_commit) {
                return Err(AppDataError::Io);
            }
            state.directory.replace(&temporary, &name)
        })();
        drop(file);
        if let Err(error) = result {
            state.directory.remove_temp(&temporary);
            return Err(error);
        }
        #[cfg(test)]
        if std::mem::take(&mut state.fail_directory_sync) {
            return Ok(AppDataWrite {
                length: bytes.len(),
                directory_synced: false,
            });
        }
        Ok(AppDataWrite {
            length: bytes.len(),
            directory_synced: state.directory.sync(),
        })
    }
    /// Ok(bool) means committed deletion; bool reports directory sync. Missing is NOT_FOUND.
    pub fn delete(&self, slot: i32) -> Result<bool> {
        let state = self.lock(slot)?;
        let name = slot_name(slot);
        let file = state.directory.read_file(&name)?;
        let before = platform::stamp(&file)?;
        state.directory.unchanged(&name, &file, &before)?;
        state.directory.delete(&name)?;
        Ok(state.directory.sync())
    }
    #[cfg(test)]
    pub fn fail_next_commit(&self) {
        self.shared.lock().unwrap().fail_commit = true;
    }
    #[cfg(test)]
    pub fn fail_next_directory_sync(&self) {
        self.shared.lock().unwrap().fail_directory_sync = true;
    }
    #[cfg(all(test, target_os = "linux"))]
    pub fn change_during_next_read_for_test(&self, grow: bool) {
        self.shared.lock().unwrap().change_read = Some(grow);
    }
}
fn valid_slot(slot: i32) -> Result<()> {
    if (1..=8).contains(&slot) {
        Ok(())
    } else {
        Err(AppDataError::InvalidArgument)
    }
}
fn valid_read(offset: i32, capacity: i32) -> Result<()> {
    if (0..=MAX_SLOT_BYTES as i32).contains(&offset)
        && (0..=MAX_SLOT_BYTES as i32).contains(&capacity)
    {
        Ok(())
    } else {
        Err(AppDataError::InvalidArgument)
    }
}
fn slot_name(slot: i32) -> String {
    format!("slot-{slot}")
}
fn memory_range(pointer: i32, length: i32, memory_length: usize) -> Result<Range<usize>> {
    if !(0..=MAX_SLOT_BYTES as i32).contains(&length) {
        return Err(AppDataError::InvalidArgument);
    }
    let start = pointer as u32 as usize;
    let end = start
        .checked_add(length as usize)
        .ok_or(AppDataError::InvalidArgument)?;
    if end > memory_length {
        return Err(AppDataError::InvalidArgument);
    }
    Ok(start..end)
}
/// Call in the parent/engine before any host/RPC invocation or guest memory access.
pub fn validate_read(
    slot: i32,
    offset: i32,
    outptr: i32,
    capacity: i32,
    memory_length: usize,
) -> Result<Range<usize>> {
    valid_slot(slot)?;
    valid_read(offset, capacity)?;
    memory_range(outptr, capacity, memory_length)
}
/// Pointer is a wasm32 unsigned address encoded as i32. Zero length still requires a valid range.
pub fn validate_write(
    slot: i32,
    pointer: i32,
    length: i32,
    memory_length: usize,
) -> Result<Range<usize>> {
    valid_slot(slot)?;
    memory_range(pointer, length, memory_length)
}
// Encoding is injective even on case-insensitive filesystems and avoids DOS device names.
// The prefix and lowercase hex keep every filesystem component portable and below 255 bytes.
fn key_directory_name(key: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut name = String::with_capacity(4 + key.len() * 2);
    name.push_str("key-");
    for byte in key.bytes() {
        name.push(HEX[(byte >> 4) as usize] as char);
        name.push(HEX[(byte & 15) as usize] as char);
    }
    name
}
// Lowercase base32 is injective and portable on case-insensitive filesystems. The separate
// prefix prevents collisions with legacy key directories, even for attacker-chosen identities.
fn package_directory_name(id: &str) -> String {
    const ALPHABET: &[u8; 32] = b"abcdefghijklmnopqrstuvwxyz234567";
    let mut name = String::with_capacity(4 + (id.len() * 8).div_ceil(5));
    name.push_str("app-");
    let mut bits = 0u32;
    let mut count = 0u32;
    for byte in id.bytes() {
        bits = (bits << 8) | u32::from(byte);
        count += 8;
        while count >= 5 {
            count -= 5;
            name.push(ALPHABET[((bits >> count) & 31) as usize] as char);
        }
        bits &= (1 << count) - 1;
    }
    if count != 0 {
        name.push(ALPHABET[(bits << (5 - count)) as usize] as char);
    }
    name
}
fn public_directory_name() -> &'static str { "public-files-v1" }
fn temp_name() -> String {
    format!(
        ".ming-data-{}-{}",
        std::process::id(),
        TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed)
    )
}

#[cfg(target_os = "linux")]
mod platform {
    use super::*;
    use std::{
        ffi::{CStr, CString},
        fs::Metadata,
        os::{
            fd::{AsRawFd, FromRawFd, IntoRawFd},
            unix::{ffi::OsStrExt, fs::MetadataExt},
        },
        path::Component,
    };
    pub struct Root(File);
    pub struct Directory(File);
    #[derive(PartialEq, Eq)]
    pub struct Stamp {
        dev: u64,
        ino: u64,
        size: u64,
        mtime: (i64, i64),
        ctime: (i64, i64),
        mode: u32,
        uid: u32,
        nlink: u64,
    }
    impl Stamp {
        pub fn length(&self) -> u64 {
            self.size
        }
    }
    fn cstring(value: &std::ffi::OsStr) -> Result<CString> {
        CString::new(value.as_bytes()).map_err(|_| AppDataError::InvalidArgument)
    }
    fn open_at(dir: &File, name: &str, flags: i32, mode: u32) -> Result<File> {
        let name = CString::new(name).map_err(|_| AppDataError::InvalidArgument)?;
        // SAFETY: parent fd and terminated name are live; a successful fd is uniquely owned.
        let fd = unsafe {
            libc::openat(
                dir.as_raw_fd(),
                name.as_ptr(),
                flags | libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_NONBLOCK,
                mode,
            )
        };
        if fd < 0 {
            Err(io_error(std::io::Error::last_os_error()))
        } else {
            Ok(unsafe { File::from_raw_fd(fd) })
        }
    }
    fn private_directory(file: &File) -> Result<()> {
        let m = file.metadata().map_err(io_error)?;
        if !m.is_dir() || m.uid() != unsafe { libc::geteuid() } || m.mode() & 0o077 != 0 {
            return Err(AppDataError::Io);
        }
        Ok(())
    }
    fn file_stamp_with_limit(m: Metadata, max_bytes: usize) -> Result<Stamp> {
        if !m.is_file()
            || m.len() > max_bytes as u64
            || m.nlink() != 1
            || m.uid() != unsafe { libc::geteuid() }
            || m.mode() & 0o077 != 0
        {
            return Err(AppDataError::Io);
        }
        Ok(Stamp {
            dev: m.dev(),
            ino: m.ino(),
            size: m.len(),
            mtime: (m.mtime(), m.mtime_nsec()),
            ctime: (m.ctime(), m.ctime_nsec()),
            mode: m.mode(),
            uid: m.uid(),
            nlink: m.nlink(),
        })
    }
    fn file_stamp(m: Metadata) -> Result<Stamp> {
        file_stamp_with_limit(m, MAX_SLOT_BYTES)
    }
    pub fn stamp(file: &File) -> Result<Stamp> {
        file_stamp(file.metadata().map_err(io_error)?)
    }
    pub fn stamp_private(file: &File) -> Result<Stamp> {
        file_stamp_with_limit(file.metadata().map_err(io_error)?, MAX_PRIVATE_FILE_BYTES)
    }
    fn update_directory_stamp(file: &File) -> Result<Stamp> {
        private_directory(file)?;
        let m = file.metadata().map_err(io_error)?;
        Ok(Stamp { dev: m.dev(), ino: m.ino(), size: m.len(), mtime: (m.mtime(), m.mtime_nsec()),
            ctime: (m.ctime(), m.ctime_nsec()), mode: m.mode(), uid: m.uid(), nlink: m.nlink() })
    }
    pub fn stamp_root(root: &Root) -> Result<Stamp> { update_directory_stamp(&root.0) }
    fn update_names(dir: &File, limit: usize) -> Result<Vec<String>> {
        let raw = open_at(dir, ".", libc::O_RDONLY | libc::O_DIRECTORY, 0)?.into_raw_fd();
        let stream = unsafe { libc::fdopendir(raw) };
        if stream.is_null() { unsafe { libc::close(raw); } return Err(AppDataError::Io); }
        let result = (|| {
            let mut names = Vec::new();
            loop {
                unsafe { *libc::__errno_location() = 0; }
                let entry = unsafe { libc::readdir(stream) };
                if entry.is_null() {
                    if unsafe { *libc::__errno_location() } != 0 { return Err(AppDataError::Io); }
                    break;
                }
                let name = unsafe { CStr::from_ptr((*entry).d_name.as_ptr()) }.to_str().map_err(|_| AppDataError::Io)?;
                if name == "." || name == ".." { continue; }
                names.push(name.to_owned());
                if names.len() > limit { return Err(AppDataError::Io); }
            }
            names.sort_by(|a, b| a.as_bytes().cmp(b.as_bytes()));
            Ok(names)
        })();
        unsafe { libc::closedir(stream); }
        result
    }
    fn update_read(mut file: &File) -> Result<Vec<u8>> {
        file.seek(SeekFrom::Start(0)).map_err(io_error)?;
        let mut bytes = Vec::new();
        Read::by_ref(&mut file).take(MAX_PRIVATE_FILE_BYTES as u64 + 1).read_to_end(&mut bytes).map_err(io_error)?;
        if bytes.len() > MAX_PRIVATE_FILE_BYTES { return Err(AppDataError::Io); }
        Ok(bytes)
    }
    // Field order, explicit directory records and UTF-8 path ordering form the v1 encoding.
    #[derive(serde::Serialize)]
    struct UpdateTreeEntry {
        path: String,
        #[serde(rename = "type")]
        kind: &'static str,
        length: u64,
        sha256: Option<String>,
    }
    fn update_digest(mut entries: Vec<UpdateTreeEntry>, domain: &[u8]) -> Result<TreeDigest> {
        use sha2::{Digest, Sha256};
        entries.sort_by(|a, b| a.path.as_bytes().cmp(b.path.as_bytes()));
        let bytes = entries.iter().map(|entry| entry.length).sum();
        let encoded = serde_json::to_vec(&entries).map_err(|_| AppDataError::Io)?;
        let mut hash = Sha256::new(); hash.update(domain); hash.update(encoded);
        Ok(TreeDigest { entries: entries.len() as u32, bytes, sha256: format!("{:x}", hash.finalize()) })
    }
    // Retain content and identity, keeping open descriptors bounded by tree depth.
    // The authority serializes mutation; the read-only helper requires stopped workers.
    enum UpdateNode {
        Directory(UpdateDirectory),
        File { stamp: Stamp, bytes: Vec<u8> },
    }
    struct UpdateDirectory {
        stamp: Stamp,
        names: Vec<String>,
        children: Vec<(String, UpdateNode)>,
    }
    #[derive(Default)]
    struct UpdateBudget { entries: usize, bytes: usize }
    impl UpdateDirectory {
        fn capture(file: File, prefix: &str, skip_files: bool, entries: &mut Vec<UpdateTreeEntry>, budget: &mut UpdateBudget) -> Result<Self> {
            use sha2::{Digest, Sha256};
            let stamp = update_directory_stamp(&file)?;
            let names = update_names(&file, MAX_PRIVATE_ENTRIES + usize::from(skip_files))?;
            let mut children = Vec::new();
            for name in &names {
                if skip_files && name == "files" { continue; }
                let path = if prefix.is_empty() { name.clone() } else { format!("{prefix}/{name}") };
                validate_private_path(&path).map_err(|_| AppDataError::Io)?;
                budget.entries += 1;
                if budget.entries > MAX_PRIVATE_ENTRIES { return Err(AppDataError::Io); }
                let child = open_at(&file, name, libc::O_RDONLY, 0)?;
                let metadata = child.metadata().map_err(io_error)?;
                let node = if metadata.is_dir() {
                    entries.push(UpdateTreeEntry { path: path.clone(), kind: "directory", length: 0, sha256: None });
                    Self::capture(child, &path, false, entries, budget).map(UpdateNode::Directory)?
                } else {
                    let stamp = file_stamp_with_limit(metadata, MAX_PRIVATE_FILE_BYTES)?;
                    budget.bytes = budget.bytes.checked_add(stamp.size as usize).ok_or(AppDataError::Io)?;
                    if budget.bytes > MAX_PRIVATE_TOTAL_BYTES { return Err(AppDataError::Io); }
                    let bytes = update_read(&child)?;
                    if bytes.len() as u64 != stamp.size || stamp_private(&child)? != stamp
                        || update_read(&child)? != bytes || stamp_private(&child)? != stamp { return Err(AppDataError::Io); }
                    entries.push(UpdateTreeEntry { path, kind: "file", length: stamp.size, sha256: Some(format!("{:x}", Sha256::digest(&bytes))) });
                    UpdateNode::File { stamp, bytes }
                };
                children.push((name.clone(), node));
            }
            if update_directory_stamp(&file)? != stamp || update_names(&file, MAX_PRIVATE_ENTRIES + 1)? != names { return Err(AppDataError::Io); }
            Ok(Self { stamp, names, children })
        }
        fn verify(&self, reopened: &File) -> Result<()> {
            if update_directory_stamp(reopened)? != self.stamp
                || update_names(reopened, MAX_PRIVATE_ENTRIES + 1)? != self.names { return Err(AppDataError::Io); }
            for (name, node) in &self.children {
                let current = open_at(reopened, name, libc::O_RDONLY, 0)?;
                match node {
                    UpdateNode::Directory(directory) => directory.verify(&current)?,
                    UpdateNode::File { stamp, bytes } => {
                        if stamp_private(&current)? != *stamp || update_read(&current)? != *bytes
                            || stamp_private(&current)? != *stamp || update_read(&current)? != *bytes
                            || stamp_private(&current)? != *stamp { return Err(AppDataError::Io); }
                    }
                }
            }
            if update_directory_stamp(reopened)? != self.stamp
                || update_names(reopened, MAX_PRIVATE_ENTRIES + 1)? != self.names { return Err(AppDataError::Io); }
            Ok(())
        }
    }
    pub(super) fn observe_private_update_read_only_checked(path: &Path, package_id: &str, after_read: impl FnOnce()) -> Result<PrivateUpdateBaseline> {
        let root = Root::open(path)?;
        let before = stamp_root(&root)?;
        let baseline = observe_update_checked(&root, package_id, after_read)?;
        if stamp_root(&root)? != before || stamp_root(&Root::open(path)?)? != before { return Err(AppDataError::Io); }
        Ok(baseline)
    }
    pub(super) fn observe_update_checked(root: &Root, package_id: &str, after_read: impl FnOnce()) -> Result<PrivateUpdateBaseline> {
        let root_before = stamp_root(root)?;
        let name = package_directory_name(package_id);
        let package = match Directory::open_existing(root, &name) {
            Ok(package) => Some(package),
            Err(AppDataError::NotFound) => None,
            Err(error) => return Err(error),
        };
        let mut non_files_entries = Vec::new();
        let mut files_entries = Vec::new();
        let non_files = package.as_ref().map(|package| {
            UpdateDirectory::capture(open_at(&package.0, ".", libc::O_RDONLY | libc::O_DIRECTORY, 0)?, "", true, &mut non_files_entries, &mut UpdateBudget::default())
        }).transpose()?;
        let files_directory = if let Some(package) = &package {
            match package.child("files", false) {
                Ok(files) => Some(files),
                Err(AppDataError::NotFound) => None,
                Err(error) => return Err(error),
            }
        } else { None };
        let files = files_directory.as_ref().map(|files| {
            UpdateDirectory::capture(open_at(&files.0, ".", libc::O_RDONLY | libc::O_DIRECTORY, 0)?, "", false, &mut files_entries, &mut UpdateBudget::default())
        }).transpose()?;
        after_read();
        match (&package, &non_files) {
            (Some(package), Some(non_files)) => {
                let reopened = Directory::open_existing(root, &name)?;
                if let (Some(files), Some(pinned)) = (&files, &files_directory) {
                    files.verify(&reopened.child("files", false)?.0)?;
                    if update_directory_stamp(&pinned.0)? != files.stamp { return Err(AppDataError::Io); }
                }
                non_files.verify(&reopened.0)?;
                if update_directory_stamp(&package.0)? != non_files.stamp { return Err(AppDataError::Io); }
            }
            (None, None) => {
                if !matches!(Directory::open_existing(root, &name), Err(AppDataError::NotFound)) { return Err(AppDataError::Io); }
            }
            _ => return Err(AppDataError::Io),
        }
        if stamp_root(root)? != root_before { return Err(AppDataError::Io); }
        Ok(PrivateUpdateBaseline {
            schema_version: 1, package_id: package_id.to_owned(), namespace_exists: package.is_some(),
            files: files.map(|_| update_digest(files_entries, b"ming-private-update-tree-v1\0")).transpose()?,
            non_files_sha256: update_digest(non_files_entries, b"ming-private-update-non-files-v1\0")?.sha256,
        })
    }
    pub fn snapshot_private_read_only(path: &Path, package_id: &str) -> Result<PrivateContentSnapshot> {
        snapshot_private_read_only_checked(path, package_id, || {})
    }
    pub(super) fn snapshot_private_read_only_checked(path: &Path, package_id: &str, after_read: impl FnOnce()) -> Result<PrivateContentSnapshot> {
        use sha2::{Digest, Sha256};
        const MAX_ENTRIES: usize = 32;
        const MAX_BYTES: usize = 65_536;
        fn directory_stamp(file: &File) -> Result<(u64, u64, i64, i64, i64, i64)> {
            private_directory(file)?;
            let m = file.metadata().map_err(io_error)?;
            Ok((m.dev(), m.ino(), m.mtime(), m.mtime_nsec(), m.ctime(), m.ctime_nsec()))
        }
        fn names(dir: &File) -> Result<Vec<String>> {
            let raw = open_at(dir, ".", libc::O_RDONLY | libc::O_DIRECTORY, 0)?.into_raw_fd();
            let stream = unsafe { libc::fdopendir(raw) };
            if stream.is_null() { unsafe { libc::close(raw); } return Err(AppDataError::Io); }
            let result = (|| {
                let mut names = Vec::new();
                loop {
                    unsafe { *libc::__errno_location() = 0; }
                    let entry = unsafe { libc::readdir(stream) };
                    if entry.is_null() {
                        if unsafe { *libc::__errno_location() } != 0 { return Err(AppDataError::Io); }
                        break;
                    }
                    let name = unsafe { CStr::from_ptr((*entry).d_name.as_ptr()) }.to_str().map_err(|_| AppDataError::Io)?;
                    if name == "." || name == ".." { continue; }
                    names.push(name.to_owned());
                    if names.len() > MAX_ENTRIES { return Err(AppDataError::Io); }
                }
                names.sort();
                Ok(names)
            })();
            unsafe { libc::closedir(stream); }
            result
        }
        fn read(file: &mut File, limit: usize) -> Result<Vec<u8>> {
            file.seek(SeekFrom::Start(0)).map_err(io_error)?;
            let mut bytes = Vec::new();
            Read::by_ref(file).take(limit as u64 + 1).read_to_end(&mut bytes).map_err(io_error)?;
            if bytes.len() > limit { return Err(AppDataError::Io); }
            Ok(bytes)
        }
        // All opens are no-follow and relative to pinned, already existing descriptors.
        let root = Root::open(path)?;
        let package_name = package_directory_name(package_id);
        let package = Directory(open_at(&root.0, &package_name, libc::O_RDONLY | libc::O_DIRECTORY, 0)?);
        let files = package.child("files", false)?;
        let before = (directory_stamp(&root.0)?, directory_stamp(&package.0)?, directory_stamp(&files.0)?);
        let entries = names(&files.0)?;
        if entries.is_empty() { return Err(AppDataError::NotFound); }
        let mut total = 0usize;
        let mut observed = Vec::new();
        let mut content = Vec::new();
        for name in &entries {
            if name.starts_with('.') { return Err(AppDataError::Io); }
            validate_private_path(name).map_err(|_| AppDataError::Io)?;
            let mut file = open_at(&files.0, name, libc::O_RDONLY, 0)?;
            let stamp = file_stamp_with_limit(file.metadata().map_err(io_error)?, MAX_BYTES - total)?;
            let bytes = read(&mut file, MAX_BYTES - total)?;
            if bytes.len() as u64 != stamp.size { return Err(AppDataError::Io); }
            total += bytes.len();
            content.push(PrivateContentFile { path: name.clone(), size: stamp.size, sha256: format!("{:x}", Sha256::digest(&bytes)) });
            observed.push((file, stamp, bytes));
        }
        after_read();
        // Re-read each pinned file and reopen each name to reject changes or replacement.
        for (name, (mut file, stamp, bytes)) in entries.iter().zip(observed) {
            if read(&mut file, MAX_BYTES)? != bytes { return Err(AppDataError::Io); }
            files.unchanged_private(name, &file, &stamp)?;
        }
        if names(&files.0)? != entries { return Err(AppDataError::Io); }
        let reopened = Root::open(path)?;
        let reopened_package = Directory(open_at(&reopened.0, &package_name, libc::O_RDONLY | libc::O_DIRECTORY, 0)?);
        let reopened_files = reopened_package.child("files", false)?;
        let after = (directory_stamp(&reopened.0)?, directory_stamp(&reopened_package.0)?, directory_stamp(&reopened_files.0)?);
        if before != after || before != (directory_stamp(&root.0)?, directory_stamp(&package.0)?, directory_stamp(&files.0)?) { return Err(AppDataError::Io); }
        Ok(PrivateContentSnapshot { package_id: package_id.to_owned(), files: content })
    }
    impl Root {
        pub fn open(root: &Path) -> Result<Self> {
            if !root.is_absolute() {
                return Err(AppDataError::InvalidArgument);
            }
            let start = CString::new("/").unwrap();
            let fd = unsafe {
                libc::open(
                    start.as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
                )
            };
            if fd < 0 {
                return Err(AppDataError::Io);
            }
            let mut dir = unsafe { File::from_raw_fd(fd) };
            for part in root.components() {
                match part {
                    Component::RootDir => (),
                    Component::Normal(name) => {
                        let name = cstring(name)?;
                        let fd = unsafe {
                            libc::openat(
                                dir.as_raw_fd(),
                                name.as_ptr(),
                                libc::O_RDONLY
                                    | libc::O_DIRECTORY
                                    | libc::O_CLOEXEC
                                    | libc::O_NOFOLLOW
                                    | libc::O_NONBLOCK,
                            )
                        };
                        if fd < 0 {
                            return Err(io_error(std::io::Error::last_os_error()));
                        }
                        dir = unsafe { File::from_raw_fd(fd) };
                    }
                    _ => return Err(AppDataError::InvalidArgument),
                }
            }
            private_directory(&dir)?;
            Ok(Self(dir))
        }
        pub fn remove_tree(&self, name: &str) -> Result<bool> {
            private_directory(&self.0)?;
            let directory = match open_at(&self.0, name, libc::O_RDONLY | libc::O_DIRECTORY, 0) {
                Ok(file) => Directory(file),
                Err(AppDataError::NotFound) => return Ok(false),
                Err(error) => return Err(error),
            };
            directory.remove_contents()?;
            let name = CString::new(name).map_err(|_| AppDataError::InvalidArgument)?;
            if unsafe { libc::unlinkat(self.0.as_raw_fd(), name.as_ptr(), libc::AT_REMOVEDIR) } != 0 {
                return Err(io_error(std::io::Error::last_os_error()));
            }
            self.0.sync_all().map_err(io_error)?;
            Ok(true)
        }
    }
    impl Directory {
        pub fn new_child(&self, name: &str) -> Result<Self> {
            private_directory(&self.0)?;
            let name_c = CString::new(name).map_err(|_| AppDataError::InvalidArgument)?;
            if unsafe { libc::mkdirat(self.0.as_raw_fd(), name_c.as_ptr(), 0o700) } != 0 {
                return Err(io_error(std::io::Error::last_os_error()));
            }
            self.0.sync_all().map_err(io_error)?;
            self.child(name, false)
        }
        pub fn child(&self, name: &str, create: bool) -> Result<Self> {
            private_directory(&self.0)?;
            let name_c = CString::new(name).map_err(|_| AppDataError::InvalidArgument)?;
            let created = if create {
                let status = unsafe { libc::mkdirat(self.0.as_raw_fd(), name_c.as_ptr(), 0o700) };
                if status != 0 && std::io::Error::last_os_error().raw_os_error() != Some(libc::EEXIST) {
                    return Err(io_error(std::io::Error::last_os_error()));
                }
                status == 0
            } else { false };
            let file = open_at(&self.0, name, libc::O_RDONLY | libc::O_DIRECTORY, 0)?;
            private_directory(&file)?;
            if created { self.0.sync_all().map_err(io_error)?; }
            Ok(Self(file))
        }
        pub fn parent<'a>(&self, components: &[&'a str]) -> Result<(Self, &'a str)> {
            let (name, ancestors) = components.split_last().ok_or(AppDataError::InvalidArgument)?;
            let mut current = Self(open_at(&self.0, ".", libc::O_RDONLY | libc::O_DIRECTORY, 0)?);
            for component in ancestors { current = current.child(component, false)?; }
            Ok((current, *name))
        }
        pub fn list(&self) -> Result<Vec<PrivateFileEntry>> {
            private_directory(&self.0)?;
            let file = open_at(&self.0, ".", libc::O_RDONLY | libc::O_DIRECTORY, 0)?;
            let raw = file.into_raw_fd();
            let stream = unsafe { libc::fdopendir(raw) };
            if stream.is_null() { unsafe { libc::close(raw); } return Err(AppDataError::Io); }
            let result = (|| {
                let mut entries = Vec::new();
                let mut count = 0;
                loop {
                    unsafe { *libc::__errno_location() = 0; }
                    let entry = unsafe { libc::readdir(stream) };
                    if entry.is_null() {
                        if unsafe { *libc::__errno_location() } != 0 { return Err(AppDataError::Io); }
                        break;
                    }
                    let name = unsafe { CStr::from_ptr((*entry).d_name.as_ptr()) }.to_str().map_err(|_| AppDataError::Io)?;
                    if name == "." || name == ".." { continue; }
                    count += 1;
                    if count > MAX_PRIVATE_ENTRIES { return Err(AppDataError::Io); }
                    let child = open_at(&self.0, name, libc::O_RDONLY, 0)?;
                    let metadata = child.metadata().map_err(io_error)?;
                    let is_directory = metadata.is_dir();
                    if is_directory { private_directory(&child)?; } else { file_stamp_with_limit(metadata, MAX_PRIVATE_FILE_BYTES)?; }
                    if name.starts_with(".ming-data-") { continue; }
                    validate_private_path(name).map_err(|_| AppDataError::Io)?;
                    entries.push(PrivateFileEntry { name: name.to_owned(), is_directory });
                }
                entries.sort_by(|a, b| a.name.cmp(&b.name));
                Ok(entries)
            })();
            unsafe { libc::closedir(stream); }
            result
        }
        pub fn remove_directory(&self, name: &str) -> Result<()> {
            let _directory = self.child(name, false)?;
            let name = CString::new(name).map_err(|_| AppDataError::InvalidArgument)?;
            if unsafe { libc::unlinkat(self.0.as_raw_fd(), name.as_ptr(), libc::AT_REMOVEDIR) } == 0 {
                Ok(())
            } else {
                Err(io_error(std::io::Error::last_os_error()))
            }
        }
        pub fn remove_tree(&self, name: &str) -> Result<bool> {
            let child = match self.child(name, false) {
                Ok(child) => child,
                Err(AppDataError::NotFound) => return Ok(false),
                Err(error) => return Err(error),
            };
            child.remove_contents()?;
            self.remove_directory(name)?;
            Ok(true)
        }
        fn remove_contents(&self) -> Result<()> {
            private_directory(&self.0)?;
            let file = open_at(&self.0, ".", libc::O_RDONLY | libc::O_DIRECTORY, 0)?;
            let raw = file.into_raw_fd();
            let stream = unsafe { libc::fdopendir(raw) };
            if stream.is_null() {
                unsafe { libc::close(raw); }
                return Err(AppDataError::Io);
            }
            let result = (|| {
                let mut count = 0;
                loop {
                    unsafe { *libc::__errno_location() = 0; }
                    let entry = unsafe { libc::readdir(stream) };
                    if entry.is_null() {
                        if unsafe { *libc::__errno_location() } != 0 { return Err(AppDataError::Io); }
                        break;
                    }
                    let name = unsafe { CStr::from_ptr((*entry).d_name.as_ptr()) }.to_str().map_err(|_| AppDataError::Io)?;
                    if name == "." || name == ".." { continue; }
                    count += 1;
                    if count > MAX_PRIVATE_ENTRIES { return Err(AppDataError::Io); }
                    let child = open_at(&self.0, name, libc::O_RDONLY, 0)?;
                    let metadata = child.metadata().map_err(io_error)?;
                    let name_c = CString::new(name).map_err(|_| AppDataError::InvalidArgument)?;
                    if metadata.is_dir() {
                        private_directory(&child)?;
                        Directory(child).remove_contents()?;
                        if unsafe { libc::unlinkat(self.0.as_raw_fd(), name_c.as_ptr(), libc::AT_REMOVEDIR) } != 0 {
                            return Err(io_error(std::io::Error::last_os_error()));
                        }
                    } else {
                        file_stamp_with_limit(metadata, MAX_PRIVATE_FILE_BYTES)?;
                        if unsafe { libc::unlinkat(self.0.as_raw_fd(), name_c.as_ptr(), 0) } != 0 {
                            return Err(io_error(std::io::Error::last_os_error()));
                        }
                    }
                }
                self.0.sync_all().map_err(io_error)
            })();
            unsafe { libc::closedir(stream); }
            result
        }
        pub fn rename_no_replace(&self, from: &str, destination: &Directory, to: &str) -> Result<()> {
            private_directory(&self.0)?;
            private_directory(&destination.0)?;
            let from = CString::new(from).map_err(|_| AppDataError::InvalidArgument)?;
            let to = CString::new(to).map_err(|_| AppDataError::InvalidArgument)?;
            let result = unsafe {
                libc::syscall(libc::SYS_renameat2, self.0.as_raw_fd(), from.as_ptr(),
                    destination.0.as_raw_fd(), to.as_ptr(), libc::RENAME_NOREPLACE)
            };
            if result == 0 { Ok(()) } else { Err(io_error(std::io::Error::last_os_error())) }
        }
        pub fn usage(&self) -> Result<(usize, usize)> {
            fn walk(dir: &Directory, depth: usize, bytes: &mut usize, entries: &mut usize) -> Result<()> {
                if depth > MAX_PRIVATE_PATH_DEPTH { return Err(AppDataError::Io); }
                private_directory(&dir.0)?;
                let file = open_at(&dir.0, ".", libc::O_RDONLY | libc::O_DIRECTORY, 0)?;
                let raw = file.into_raw_fd();
                let stream = unsafe { libc::fdopendir(raw) };
                if stream.is_null() { unsafe { libc::close(raw); } return Err(AppDataError::Io); }
                let result = (|| {
                    loop {
                        unsafe { *libc::__errno_location() = 0; }
                        let entry = unsafe { libc::readdir(stream) };
                        if entry.is_null() {
                            if unsafe { *libc::__errno_location() } != 0 { return Err(AppDataError::Io); }
                            break;
                        }
                        let name = unsafe { CStr::from_ptr((*entry).d_name.as_ptr()) }.to_str().map_err(|_| AppDataError::Io)?;
                        if name == "." || name == ".." { continue; }
                        *entries = entries.checked_add(1).ok_or(AppDataError::Io)?;
                        if *entries > MAX_PRIVATE_ENTRIES { return Err(AppDataError::Io); }
                        let child = open_at(&dir.0, name, libc::O_RDONLY, 0)?;
                        let metadata = child.metadata().map_err(io_error)?;
                        if metadata.is_dir() {
                            private_directory(&child)?;
                            walk(&Directory(child), depth + 1, bytes, entries)?;
                        } else {
                            let stamp = file_stamp_with_limit(metadata, MAX_PRIVATE_FILE_BYTES)?;
                            *bytes = bytes.checked_add(stamp.length() as usize).ok_or(AppDataError::Io)?;
                            if *bytes > MAX_PRIVATE_TOTAL_BYTES { return Err(AppDataError::Io); }
                        }
                    }
                    Ok(())
                })();
                unsafe { libc::closedir(stream); }
                result
            }
            let (mut bytes, mut entries) = (0, 0);
            walk(self, 0, &mut bytes, &mut entries)?;
            Ok((bytes, entries))
        }
        pub fn lock_policy(&self) -> Result<File> {
            self.lock_policy_open(true)
        }
        pub fn lock_policy_existing(&self) -> Result<File> {
            self.lock_policy_open(false)
        }
        pub fn policy_binding(&self, lock: &File) -> Result<(Stamp, Stamp)> {
            Ok((update_directory_stamp(&self.0)?, stamp(lock)?))
        }
        pub fn verify_policy_binding(&self, root: &Root, name: &str, lock: &File, before: &(Stamp, Stamp)) -> Result<()> {
            let reopened = Self::open_existing(root, name)?;
            if update_directory_stamp(&self.0)? != before.0 || update_directory_stamp(&reopened.0)? != before.0 {
                return Err(AppDataError::Io);
            }
            self.unchanged(".policy-lock", lock, &before.1)?;
            reopened.unchanged(".policy-lock", lock, &before.1)?;
            if update_directory_stamp(&self.0)? != before.0 || update_directory_stamp(&reopened.0)? != before.0 {
                return Err(AppDataError::Io);
            }
            Ok(())
        }
        fn lock_policy_open(&self, create: bool) -> Result<File> {
            private_directory(&self.0)?;
            let file = open_at(&self.0, ".policy-lock", libc::O_RDWR | if create { libc::O_CREAT } else { 0 }, 0o600)?;
            let before = stamp(&file)?;
            if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
                return Err(AppDataError::Io);
            }
            self.unchanged(".policy-lock", &file, &before)?;
            Ok(file)
        }
        pub fn open_existing(root: &Root, key: &str) -> Result<Self> {
            private_directory(&root.0)?;
            let file = open_at(&root.0, key, libc::O_RDONLY | libc::O_DIRECTORY, 0)?;
            private_directory(&file)?;
            Ok(Self(file))
        }
        pub fn open(root: &Root, key: &str) -> Result<Self> {
            let dir = &root.0;
            private_directory(dir)?;
            let key_c = CString::new(key).unwrap();
            let status = unsafe { libc::mkdirat(dir.as_raw_fd(), key_c.as_ptr(), 0o700) };
            if status != 0 && std::io::Error::last_os_error().raw_os_error() != Some(libc::EEXIST) {
                return Err(AppDataError::Io);
            }
            let data = open_at(&dir, key, libc::O_RDONLY | libc::O_DIRECTORY, 0)?;
            private_directory(&data)?;
            // Persist a newly created namespace before allowing any slot write.
            if status == 0 {
                dir.sync_all().map_err(io_error)?;
            }
            Ok(Self(data))
        }
        pub fn read_file(&self, name: &str) -> Result<File> {
            private_directory(&self.0)?;
            let file = open_at(&self.0, name, libc::O_RDONLY, 0)?;
            stamp(&file)?;
            Ok(file)
        }
        pub fn read_private_file(&self, name: &str) -> Result<File> {
            private_directory(&self.0)?;
            let file = open_at(&self.0, name, libc::O_RDONLY, 0)?;
            stamp_private(&file)?;
            Ok(file)
        }
        pub fn unchanged_private(&self, name: &str, file: &File, before: &Stamp) -> Result<()> {
            if stamp_private(file)? != *before { return Err(AppDataError::Io); }
            let current = self.read_private_file(name)?;
            if stamp_private(&current)? != *before { return Err(AppDataError::Io); }
            Ok(())
        }
        pub fn check_private_target(&self, name: &str) -> Result<()> {
            match self.read_private_file(name) {
                Ok(file) => {
                    let before = stamp_private(&file)?;
                    self.unchanged_private(name, &file, &before)
                }
                Err(AppDataError::NotFound) => Ok(()),
                Err(error) => Err(error),
            }
        }
        pub fn unchanged(&self, name: &str, file: &File, before: &Stamp) -> Result<()> {
            if stamp(file)? != *before {
                return Err(AppDataError::Io);
            }
            let current = self.read_file(name)?;
            if stamp(&current)? != *before {
                return Err(AppDataError::Io);
            }
            Ok(())
        }
        pub fn check_target(&self, name: &str) -> Result<()> {
            match self.read_file(name) {
                Ok(file) => {
                    let before = stamp(&file)?;
                    self.unchanged(name, &file, &before)
                }
                Err(AppDataError::NotFound) => Ok(()),
                Err(e) => Err(e),
            }
        }
        pub fn new_temp(&self) -> Result<(String, File)> {
            private_directory(&self.0)?;
            for _ in 0..32 {
                let name = temp_name();
                match open_at(
                    &self.0,
                    &name,
                    libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL,
                    0o600,
                ) {
                    Ok(file) => return Ok((name, file)),
                    Err(_) => continue,
                }
            }
            Err(AppDataError::Io)
        }
        pub fn replace(&self, from: &str, to: &str) -> Result<()> {
            private_directory(&self.0)?;
            let from = CString::new(from).unwrap();
            let to = CString::new(to).unwrap();
            if unsafe {
                libc::renameat(
                    self.0.as_raw_fd(),
                    from.as_ptr(),
                    self.0.as_raw_fd(),
                    to.as_ptr(),
                )
            } == 0
            {
                Ok(())
            } else {
                Err(AppDataError::Io)
            }
        }
        pub fn delete(&self, name: &str) -> Result<()> {
            private_directory(&self.0)?;
            let name = CString::new(name).unwrap();
            if unsafe { libc::unlinkat(self.0.as_raw_fd(), name.as_ptr(), 0) } == 0 {
                Ok(())
            } else {
                Err(io_error(std::io::Error::last_os_error()))
            }
        }
        pub fn remove_temp(&self, name: &str) {
            let _ = self.delete(name);
        }
        pub fn sync(&self) -> bool {
            self.0.sync_all().is_ok()
        }
        #[cfg(test)]
        pub fn test_open_writer(&self, name: &str) -> Result<File> {
            open_at(&self.0, name, libc::O_WRONLY, 0)
        }
    }

    pub fn copy_private_tree(source: &Directory, destination: &Directory) -> Result<()> {
        for entry in source.list()? {
            if entry.is_directory {
                let child = destination.child(&entry.name, true)?;
                let source_child = source.child(&entry.name, false)?;
                copy_private_tree(&source_child, &child)?;
                if !child.sync() { return Err(AppDataError::Io); }
            } else {
                let mut input = source.read_private_file(&entry.name)?;
                let before = stamp_private(&input)?;
                let mut bytes = vec![0; before.length() as usize];
                input.read_exact(&mut bytes).map_err(io_error)?;
                source.unchanged_private(&entry.name, &input, &before)?;
                let (temporary, mut output) = destination.new_temp()?;
                let result = (|| {
                    output.write_all(&bytes).map_err(io_error)?;
                    output.sync_all().map_err(io_error)?;
                    destination.rename_no_replace(&temporary, destination, &entry.name)
                })();
                drop(output);
                if let Err(error) = result {
                    destination.remove_temp(&temporary);
                    return Err(error);
                }
            }
        }
        Ok(())
    }

    fn directory_for_path(base: &Directory, components: &[&str], create: bool) -> Result<Directory> {
        let mut current = Directory(open_at(&base.0, ".", libc::O_RDONLY | libc::O_DIRECTORY, 0)?);
        for component in components {
            current = current.child(component, create)?;
        }
        Ok(current)
    }

    fn file_parent<'a>(base: &Directory, path: &'a str) -> Result<(Directory, &'a str)> {
        let components = validate_private_path(path)?;
        let (name, ancestors) = components.split_last().ok_or(AppDataError::InvalidArgument)?;
        Ok((directory_for_path(base, ancestors, false)?, *name))
    }

    fn ensure_directory(base: &Directory, path: &str) -> Result<()> {
        let components = validate_private_path(path)?;
        directory_for_path(base, &components, true)?.sync().then_some(()).ok_or(AppDataError::Io)
    }

    pub fn apply_migration_step(base: &Directory, step: &MigrationStep) -> Result<()> {
        match step {
            MigrationStep::EnsureDir { path } => ensure_directory(base, path),
            MigrationStep::DeleteFile { path } => {
                let (parent, name) = file_parent(base, path)?;
                match parent.read_private_file(name) {
                    Ok(_) => {},
                    Err(AppDataError::NotFound) => return Ok(()),
                    Err(error) => return Err(error),
                }
                match parent.delete(name) {
                    Ok(()) | Err(AppDataError::NotFound) => parent.sync().then_some(()).ok_or(AppDataError::Io),
                    Err(error) => Err(error),
                }
            }
            MigrationStep::RenameFile { from, to } => {
                let (destination_parent, destination_name) = file_parent(base, to)?;
                let (source_parent, source_name) = file_parent(base, from)?;
                let source = match source_parent.read_private_file(source_name) {
                    Ok(source) => source,
                    Err(AppDataError::NotFound) => {
                        return match destination_parent.read_private_file(destination_name) {
                            Ok(_) => Ok(()),
                            Err(error) => Err(error),
                        };
                    }
                    Err(error) => return Err(error),
                };
                let stamp = stamp_private(&source)?;
                source_parent.unchanged_private(source_name, &source, &stamp)?;
                destination_parent.check_private_target(destination_name)?;
                source_parent.rename_no_replace(source_name, &destination_parent, destination_name)?;
                if !source_parent.sync() || !destination_parent.sync() { return Err(AppDataError::Io); }
                Ok(())
            }
            MigrationStep::CopyFile { from, to } => {
                let (source_parent, source_name) = file_parent(base, from)?;
                let (destination_parent, destination_name) = file_parent(base, to)?;
                let mut source = source_parent.read_private_file(source_name)?;
                let stamp = stamp_private(&source)?;
                let mut bytes = vec![0; stamp.length() as usize];
                source.read_exact(&mut bytes).map_err(io_error)?;
                source_parent.unchanged_private(source_name, &source, &stamp)?;
                if let Ok(mut existing) = destination_parent.read_private_file(destination_name) {
                    let mut existing_bytes = Vec::new();
                    existing.read_to_end(&mut existing_bytes).map_err(io_error)?;
                    if existing_bytes == bytes { return Ok(()); }
                    return Err(AppDataError::Io);
                }
                let (temporary, mut output) = destination_parent.new_temp()?;
                let result = (|| {
                    output.write_all(&bytes).map_err(io_error)?;
                    output.sync_all().map_err(io_error)?;
                    destination_parent.rename_no_replace(&temporary, &destination_parent, destination_name)
                })();
                drop(output);
                if let Err(error) = result { destination_parent.remove_temp(&temporary); return Err(error); }
                if !destination_parent.sync() { return Err(AppDataError::Io); }
                Ok(())
            }
        }
    }
}

#[cfg(windows)]
mod platform {
    use super::*;
    use std::{
        ffi::OsString,
        fs::{self, OpenOptions},
        os::windows::{
            ffi::OsStrExt,
            fs::{MetadataExt, OpenOptionsExt},
            io::AsRawHandle,
        },
        path::{Component, PathBuf, Prefix},
    };
    const REPARSE: u32 = 0x400;
    const OPEN_REPARSE: u32 = 0x00200000;
    #[repr(C)]
    #[derive(Default)]
    struct Info {
        attributes: u32,
        creation: [u32; 2],
        access: [u32; 2],
        write: [u32; 2],
        volume: u32,
        size_high: u32,
        size_low: u32,
        links: u32,
        index_high: u32,
        index_low: u32,
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn MoveFileExW(from: *const u16, to: *const u16, flags: u32) -> i32;
        fn GetFileInformationByHandle(file: *mut std::ffi::c_void, info: *mut Info) -> i32;
        fn GetFileType(file: *mut std::ffi::c_void) -> u32;
    }
    pub struct Root { path: PathBuf, _handle: File }
    impl Root {
        pub fn open(path: &Path) -> Result<Self> {
            let path = verbatim_path(path);
            check_chain(&path)?;
            let handle = OpenOptions::new().read(true).share_mode(7).custom_flags(0x02000000 | OPEN_REPARSE).open(&path).map_err(io_error)?;
            let m = handle.metadata().map_err(io_error)?;
            if !m.is_dir() || m.file_attributes() & REPARSE != 0 { return Err(AppDataError::Io); }
            Ok(Self { path, _handle: handle })
        }
    }
    pub struct Directory(PathBuf);
    #[derive(PartialEq, Eq)]
    pub struct Stamp {
        volume: u32,
        index: (u32, u32),
        size: u64,
        creation: [u32; 2],
        write: [u32; 2],
        attributes: u32,
    }
    impl Stamp {
        pub fn length(&self) -> u64 {
            self.size
        }
    }
    fn check_chain(path: &Path) -> Result<()> {
        if !path.is_absolute() {
            return Err(AppDataError::InvalidArgument);
        }
        let mut prefix = PathBuf::new();
        for part in path.components() {
            match part {
                Component::Prefix(p)
                    if matches!(p.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_)) =>
                {
                    prefix.push(part.as_os_str());
                    continue;
                }
                Component::RootDir => {
                    prefix.push(part.as_os_str());
                }
                Component::Normal(_) => {
                    prefix.push(part.as_os_str());
                }
                _ => return Err(AppDataError::InvalidArgument),
            }
            let m = fs::symlink_metadata(&prefix).map_err(io_error)?;
            if !m.is_dir() || m.file_attributes() & REPARSE != 0 {
                return Err(AppDataError::Io);
            }
        }
        Ok(())
    }
    fn verbatim_path(path: &Path) -> PathBuf {
        match path.components().next() {
            Some(Component::Prefix(prefix)) if matches!(prefix.kind(), Prefix::Disk(_)) => {
                let mut value = OsString::from(r"\\?\");
                value.push(path.as_os_str());
                PathBuf::from(value)
            }
            _ => path.to_owned(),
        }
    }
    pub fn stamp(file: &File) -> Result<Stamp> {
        let m = file.metadata().map_err(io_error)?;
        let mut info = Info::default();
        if !m.is_file()
            || m.len() > MAX_SLOT_BYTES as u64
            || m.file_attributes() & REPARSE != 0
            || unsafe { GetFileType(file.as_raw_handle()) } != 1
            || unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
            || info.links != 1
        {
            return Err(AppDataError::Io);
        }
        Ok(Stamp {
            volume: info.volume,
            index: (info.index_high, info.index_low),
            size: m.len(),
            creation: info.creation,
            write: info.write,
            attributes: info.attributes,
        })
    }
    impl Directory {
        pub fn open(root: &Root, key: &str) -> Result<Self> {
            check_chain(&root.path)?;
            let data = root.path.join(key);
            match fs::create_dir(&data) {
                Ok(()) => (),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
                Err(e) => return Err(io_error(e)),
            }
            check_chain(&data)?;
            Ok(Self(data))
        }
        pub fn read_file(&self, name: &str) -> Result<File> {
            check_chain(&self.0)?;
            let p = self.0.join(name);
            let m = fs::symlink_metadata(&p).map_err(io_error)?;
            if !m.is_file() || m.file_attributes() & REPARSE != 0 || m.len() > MAX_SLOT_BYTES as u64
            {
                return Err(AppDataError::Io);
            }
            let file = OpenOptions::new()
                .read(true)
                .share_mode(7)
                .custom_flags(OPEN_REPARSE)
                .open(p)
                .map_err(io_error)?;
            stamp(&file)?;
            Ok(file)
        }
        pub fn unchanged(&self, name: &str, file: &File, before: &Stamp) -> Result<()> {
            if stamp(file)? != *before || stamp(&self.read_file(name)?)? != *before {
                return Err(AppDataError::Io);
            }
            Ok(())
        }
        pub fn check_target(&self, name: &str) -> Result<()> {
            match self.read_file(name) {
                Ok(file) => {
                    let before = stamp(&file)?;
                    self.unchanged(name, &file, &before)
                }
                Err(AppDataError::NotFound) => Ok(()),
                Err(e) => Err(e),
            }
        }
        pub fn new_temp(&self) -> Result<(String, File)> {
            check_chain(&self.0)?;
            for _ in 0..32 {
                let name = temp_name();
                match OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .share_mode(7)
                    .custom_flags(OPEN_REPARSE)
                    .open(self.0.join(&name))
                {
                    Ok(file) => return Ok((name, file)),
                    Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                    Err(e) => return Err(io_error(e)),
                }
            }
            Err(AppDataError::Io)
        }
        pub fn replace(&self, from: &str, to: &str) -> Result<()> {
            check_chain(&self.0)?;
            let wide = |p: PathBuf| {
                p.as_os_str()
                    .encode_wide()
                    .chain(Some(0))
                    .collect::<Vec<u16>>()
            };
            let from = wide(self.0.join(from));
            let to = wide(self.0.join(to));
            // Same-directory local-volume rename only; COPY_ALLOWED is deliberately absent.
            if unsafe { MoveFileExW(from.as_ptr(), to.as_ptr(), 1) } != 0 {
                Ok(())
            } else {
                Err(AppDataError::Io)
            }
        }
        pub fn delete(&self, name: &str) -> Result<()> {
            check_chain(&self.0)?;
            fs::remove_file(self.0.join(name)).map_err(io_error)
        }
        pub fn remove_temp(&self, name: &str) {
            let _ = self.delete(name);
        }
        pub fn sync(&self) -> bool {
            false
        }
        #[cfg(test)]
        pub fn test_open_writer(&self, name: &str) -> Result<File> {
            OpenOptions::new()
                .write(true)
                .open(self.0.join(name))
                .map_err(io_error)
        }
    }
}

#[cfg(not(any(target_os = "linux", windows)))]
compile_error!("App data storage supports Linux and the documented Windows prototype only");


#[cfg(all(test, target_os = "linux"))]
mod snapshot_tests {
    use super::*;
    use std::{fs, os::unix::fs::{symlink, PermissionsExt}, path::PathBuf};
    const ID: &str = "dev.ming.install-demo";
    fn directory(path: &Path) { fs::create_dir(path).unwrap(); fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap(); }
    fn fixture() -> (tempfile::TempDir, PathBuf, PathBuf) {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("data"); directory(&root);
        let package = root.join(package_directory_name(ID)); directory(&package);
        let files = package.join("files"); directory(&files);
        file(&files.join("note.txt"), b"alpha");
        (temp, root, files)
    }
    fn file(path: &Path, bytes: &[u8]) { fs::write(path, bytes).unwrap(); fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap(); }
    fn tree(path: &Path) -> Vec<(PathBuf, u64, Vec<u8>)> {
        use std::os::unix::fs::MetadataExt;
        fn walk(path: &Path, out: &mut Vec<(PathBuf,u64,Vec<u8>)>) {
            let m = fs::symlink_metadata(path).unwrap();
            let bytes = if m.is_file() { fs::read(path).unwrap() } else if m.file_type().is_symlink() { fs::read_link(path).unwrap().as_os_str().as_encoded_bytes().to_vec() } else { Vec::new() };
            let mut evidence = format!("{}:{}:{}:{}:{}:{}:{}:{}:",m.mode(),m.uid(),m.nlink(),m.len(),m.mtime(),m.mtime_nsec(),m.ctime(),m.ctime_nsec()).into_bytes();
            evidence.extend(bytes);
            out.push((path.to_owned(), m.ino(), evidence));
            if m.is_dir() { let mut children: Vec<_> = fs::read_dir(path).unwrap().map(|e|e.unwrap().path()).collect(); children.sort(); for child in children { walk(&child,out); } }
        }
        let mut out=Vec::new(); walk(path,&mut out); out
    }
    #[test]
    fn update_observation_preserves_missing_namespace() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("data");
        assert!(observe_private_update_read_only(&root, ID).is_err());
        assert!(!root.exists());
        directory(&root);
        let before = tree(&root);
        let baseline = observe_private_update_read_only(&root, ID).unwrap();
        assert_eq!(baseline.schema_version, 1);
        assert_eq!(baseline.package_id, ID);
        assert!(!baseline.namespace_exists);
        assert_eq!(baseline.files, None);
        assert_eq!(tree(&root), before);
    }
    #[test]
    fn update_observation_distinguishes_missing_and_empty_files() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("data"); directory(&root);
        let package = root.join(package_directory_name(ID)); directory(&package);
        let before = tree(&root);
        let missing = observe_private_update_read_only(&root, ID).unwrap();
        assert!(missing.namespace_exists);
        assert_eq!(missing.files, None);
        assert_eq!(tree(&root), before);
        directory(&package.join("files"));
        let before = tree(&root);
        let empty = observe_private_update_read_only(&root, ID).unwrap();
        let digest = empty.files.unwrap();
        assert_eq!((digest.entries, digest.bytes), (0, 0));
        assert_eq!(empty.non_files_sha256, missing.non_files_sha256);
        assert_eq!(tree(&root), before);
    }
    #[test]
    fn update_observation_includes_nested_empty_directories() {
        let (_temp, root, files) = fixture();
        let plain = observe_private_update_read_only(&root, ID).unwrap();
        directory(&files.join("中文\"目录"));
        directory(&files.join("中文\"目录/empty"));
        file(&files.join("中文\"目录/note"), b"beta");
        let before = tree(&root);
        let nested = observe_private_update_read_only(&root, ID).unwrap();
        let digest = nested.files.as_ref().unwrap();
        assert_eq!((digest.entries, digest.bytes), (4, 9));
        assert_ne!(nested.files, plain.files);
        assert_eq!(nested.non_files_sha256, plain.non_files_sha256);
        assert_eq!(tree(&root), before);
        fs::remove_dir(files.join("中文\"目录/empty")).unwrap();
        assert_ne!(observe_private_update_read_only(&root, ID).unwrap().files, nested.files);
    }
    #[test]
    fn update_existing_authority_never_creates_policy_or_lock() {
        for missing in 0..6 {
            let temp = tempfile::tempdir().unwrap();
            let root = temp.path().join("data");
            if missing != 0 { directory(&root); }
            if missing == 2 { directory(&root.join("system-private-storage-policy-v1")); }
            if missing == 3 {
                directory(&root.join("system-private-storage-policy-v1"));
                directory(&root.join("system-public-storage-policy-v1"));
            }
            if missing >= 4 {
                drop(PackageStorageAuthority::open_default_private(AppDataRoot::open(&root).unwrap()).unwrap());
                let policy = if missing == 4 { "system-private-storage-policy-v1" } else { "system-public-storage-policy-v1" };
                fs::remove_file(root.join(policy).join(".policy-lock")).unwrap();
            }
            let before = root.exists().then(|| tree(&root));
            assert!(PackageStorageAuthority::open_update_recovery_existing(&root).is_err(), "missing case {missing}");
            assert_eq!(root.exists().then(|| tree(&root)), before);
        }
    }
    #[test]
    fn update_observation_has_canonical_hashes_and_strict_fields() {
        let (_temp, root, files) = fixture();
        fs::rename(files.join("note.txt"), files.join("a")).unwrap();
        directory(&files.join("中文\"目录"));
        let baseline = observe_private_update_read_only(&root, ID).unwrap();
        assert_eq!(baseline.files.as_ref().unwrap().sha256, "695cbca5446af29000369fd84a61836a0835224aa8b1bba5b03312d8573f7d33");
        assert_eq!(baseline.non_files_sha256, "714c462ccccbf70a67fa4851e4a64832c2acd042d1716b41f2c24b42abf271a1");
        let encoded = serde_json::to_value(&baseline).unwrap();
        assert_eq!(encoded["namespaceExists"], true);
        assert_eq!(serde_json::from_value::<PrivateUpdateBaseline>(encoded.clone()).unwrap(), baseline);
        for field in ["files", "schemaVersion", "packageId", "namespaceExists", "nonFilesSha256"] {
            let mut missing = encoded.clone(); missing.as_object_mut().unwrap().remove(field);
            assert!(serde_json::from_value::<PrivateUpdateBaseline>(missing).is_err(), "missing {field}");
        }
        let mut unknown = encoded.clone(); unknown["unknown"] = true.into();
        assert!(serde_json::from_value::<PrivateUpdateBaseline>(unknown).is_err());
        let raw = serde_json::to_string(&baseline).unwrap();
        assert!(serde_json::from_str::<PrivateUpdateBaseline>(&raw.replacen('{', "{\"schemaVersion\":1,", 1)).is_err());
        assert!(serde_json::from_str::<TreeDigest>(r#"{"entries":0,"entries":1,"bytes":0,"sha256":"x"}"#).is_err());
        assert!(serde_json::from_str::<TreeDigest>(r#"{"entries":0,"bytes":0,"sha256":"x","other":0}"#).is_err());
        assert_eq!(observe_private_update_read_only(&root, "../invalid"), Err(AppDataError::InvalidArgument));
    }
    #[test]
    fn update_observation_keeps_non_files_content_and_empty_directories() {
        let (_temp, root, _files) = fixture();
        let package = root.join(package_directory_name(ID));
        let initial = observe_private_update_read_only(&root, ID).unwrap();
        file(&package.join("slot-1"), b"metadata");
        directory(&package.join("empty"));
        let before = tree(&root);
        let changed = observe_private_update_read_only(&root, ID).unwrap();
        assert_eq!(changed.files, initial.files);
        assert_ne!(changed.non_files_sha256, initial.non_files_sha256);
        assert_eq!(tree(&root), before);
        file(&package.join(".ming-update-backup-other"), b"unexpected");
        assert_ne!(observe_private_update_read_only(&root, ID).unwrap().non_files_sha256, changed.non_files_sha256);
        file(&package.join(".ming-data-unknown"), b"temporary");
        assert!(observe_private_update_read_only(&root, ID).is_err());
    }
    #[test]
    fn update_observation_enforces_entries_bytes_depth_and_path_bounds() {
        let (_temp, root, files) = fixture();
        fs::remove_file(files.join("note.txt")).unwrap();
        for index in 0..1024 { file(&files.join(format!("f{index}")), b""); }
        assert_eq!(observe_private_update_read_only(&root, ID).unwrap().files.unwrap().entries, 1024);
        file(&files.join("overflow"), b"");
        assert!(observe_private_update_read_only(&root, ID).is_err());

        let (_temp, root, files) = fixture();
        file(&files.join("note.txt"), &vec![0; 8 * 1024 * 1024]);
        assert_eq!(observe_private_update_read_only(&root, ID).unwrap().files.unwrap().bytes, 8 * 1024 * 1024);
        file(&files.join("extra"), b"x");
        assert!(observe_private_update_read_only(&root, ID).is_err());
        fs::remove_file(files.join("extra")).unwrap();
        file(&files.join("note.txt"), &vec![0; 8 * 1024 * 1024 + 1]);
        assert!(observe_private_update_read_only(&root, ID).is_err());

        let (_temp, root, files) = fixture();
        let mut deepest = files.clone();
        for _ in 0..16 { deepest.push("a"); directory(&deepest); }
        assert!(observe_private_update_read_only(&root, ID).is_ok());
        directory(&deepest.join("a"));
        assert!(observe_private_update_read_only(&root, ID).is_err());

        let (_temp, root, files) = fixture();
        let mut deepest = files;
        for _ in 0..4 { deepest.push("a".repeat(255)); directory(&deepest); }
        assert!(observe_private_update_read_only(&root, ID).is_ok());
        directory(&deepest.join("aa"));
        assert!(observe_private_update_read_only(&root, ID).is_err());
    }
    #[test]
    fn update_observation_low_fd_child() {
        let Some(root) = std::env::var_os("MING_UPDATE_OBSERVER_FD_ROOT") else { return; };
        let mut limit = libc::rlimit { rlim_cur: 0, rlim_max: 0 };
        assert_eq!(unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, &mut limit) }, 0);
        limit.rlim_cur = 1024;
        assert_eq!(unsafe { libc::setrlimit(libc::RLIMIT_NOFILE, &limit) }, 0);
        let digest = observe_private_update_read_only(Path::new(&root), ID).unwrap().files.unwrap();
        assert_eq!(digest.entries, 1024);
    }
    #[test]
    fn update_observation_accepts_1024_entries_with_1024_open_file_limit() {
        let (_temp, root, files) = fixture();
        fs::remove_file(files.join("note.txt")).unwrap();
        for index in 0..1024 { file(&files.join(format!("f{index}")), b""); }
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "app_data::snapshot_tests::update_observation_low_fd_child", "--nocapture"])
            .env("MING_UPDATE_OBSERVER_FD_ROOT", &root).output().unwrap();
        assert!(output.status.success(), "1024-entry observation under RLIMIT_NOFILE=1024 failed:\n{}\n{}",
            String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        assert!(String::from_utf8_lossy(&output.stdout).contains("1 passed; 0 failed"));
    }
    #[test]
    fn update_observation_rejects_links_special_files_and_unsafe_modes_without_writes() {
        for case in 0..7 {
            let (_temp, root, files) = fixture();
            let package = root.join(package_directory_name(ID));
            match case {
                0 => symlink("note.txt", files.join("link")).unwrap(),
                1 => fs::hard_link(files.join("note.txt"), files.join("hard")).unwrap(),
                2 => {
                    use std::os::unix::ffi::OsStrExt;
                    let path = std::ffi::CString::new(files.join("fifo").as_os_str().as_bytes()).unwrap();
                    assert_eq!(unsafe { libc::mkfifo(path.as_ptr(), 0o600) }, 0);
                }
                3 => fs::set_permissions(files.join("note.txt"), fs::Permissions::from_mode(0o644)).unwrap(),
                4 => fs::set_permissions(&files, fs::Permissions::from_mode(0o755)).unwrap(),
                5 => symlink(&files, package.join("non-files-link")).unwrap(),
                _ => { fs::rename(&files, package.join("actual")).unwrap(); symlink("actual", &files).unwrap(); }
            }
            let before = tree(&root);
            assert!(observe_private_update_read_only(&root, ID).is_err(), "unsafe case {case}");
            assert_eq!(tree(&root), before);
        }
    }
    #[test]
    fn update_observation_rejects_replacement_addition_and_deletion_during_read() {
        for case in 0..9 {
            let (_temp, root, files) = fixture();
            directory(&files.join("empty"));
            let result = platform::observe_private_update_read_only_checked(&root, ID, || {
                match case {
                    0 => file(&files.join("note.txt"), b"bravo"),
                    1 => { fs::remove_file(files.join("note.txt")).unwrap(); file(&files.join("note.txt"), b"alpha"); }
                    2 => file(&files.join("extra"), b"x"),
                    3 => fs::remove_file(files.join("note.txt")).unwrap(),
                    4 => { fs::remove_dir(files.join("empty")).unwrap(); directory(&files.join("empty")); }
                    5 => { fs::rename(&files, files.with_file_name("previous")).unwrap(); directory(&files); }
                    6 => file(&files.parent().unwrap().join("metadata"), b"x"),
                    7 => {
                        let package = files.parent().unwrap();
                        fs::rename(package, package.with_file_name("replaced")).unwrap(); directory(package); directory(&files);
                    }
                    _ => { fs::rename(&root, root.with_file_name("replaced-root")).unwrap(); directory(&root); }
                }
            });
            assert!(result.is_err(), "unstable case {case}");
        }
        for namespace_exists in [false, true] {
            let temp = tempfile::tempdir().unwrap(); let root = temp.path().join("data"); directory(&root);
            let package = root.join(package_directory_name(ID));
            if namespace_exists { directory(&package); }
            assert!(platform::observe_private_update_read_only_checked(&root, ID, || {
                if !namespace_exists { directory(&package); }
                directory(&package.join("files"));
            }).is_err());
        }
    }
    #[test]
    fn update_existing_authority_reuses_locks_and_reads_existing_public_policy() {
        for public_slot in 0..3 {
            let temp = tempfile::tempdir().unwrap(); let root = temp.path().join("data"); directory(&root);
            let authority = PackageStorageAuthority::open_default_private(AppDataRoot::open(&root).unwrap()).unwrap();
            let before = tree(&root);
            assert!(PackageStorageAuthority::open_update_recovery_existing(&root).is_err());
            assert_eq!(tree(&root), before);
            drop(authority);
            let slot = root.join("system-public-storage-policy-v1/slot-1");
            match public_slot { 1 => file(&slot, format!("{PUBLIC_POLICY_HEADER}{ID}\n").as_bytes()), 2 => file(&slot, b"damaged\n"), _ => {} }
            let before = tree(&root);
            let recovery = PackageStorageAuthority::open_update_recovery_existing(&root);
            if public_slot == 2 { assert!(recovery.is_err()); }
            else {
                let recovery = recovery.unwrap();
                assert_eq!(recovery.public_authorized.lock().unwrap().contains_key(ID), public_slot == 1);
                assert_eq!(recovery.observe_update(ID).unwrap(), observe_private_update_read_only(&root, ID).unwrap());
                assert!(PackageStorageAuthority::open_update_recovery_existing(&root).is_err());
                drop(recovery);
                assert!(PackageStorageAuthority::open_update_recovery_existing(&root).is_ok());
            }
            assert_eq!(tree(&root), before);
        }
    }
    #[test]
    fn update_existing_authority_rejects_replaced_policy_locks_after_acquisition() {
        for policy in ["system-private-storage-policy-v1", "system-public-storage-policy-v1"] {
            let temp = tempfile::tempdir().unwrap(); let root = temp.path().join("data"); directory(&root);
            drop(PackageStorageAuthority::open_default_private(AppDataRoot::open(&root).unwrap()).unwrap());
            let mut after_mutation = None;
            let result = PackageStorageAuthority::open_update_recovery_existing_checked(&root, || {
                let lock = root.join(policy).join(".policy-lock");
                fs::remove_file(&lock).unwrap(); file(&lock, b"");
                after_mutation = Some(tree(&root));
            });
            assert!(matches!(result, Err(AppDataError::Io)), "replacement accepted for {policy}");
            assert_eq!(tree(&root), after_mutation.unwrap());
        }
    }
    #[test]
    fn update_existing_authority_rejects_policy_directory_replacement() {
        let temp = tempfile::tempdir().unwrap(); let root = temp.path().join("data"); directory(&root);
        drop(PackageStorageAuthority::open_default_private(AppDataRoot::open(&root).unwrap()).unwrap());
        let result = PackageStorageAuthority::open_update_recovery_existing_checked(&root, || {
            let policy = root.join("system-private-storage-policy-v1");
            fs::rename(&policy, root.join("previous-policy")).unwrap();
            directory(&policy); file(&policy.join(".policy-lock"), b"");
        });
        assert!(matches!(result, Err(AppDataError::Io)));
    }
    #[test]
    fn missing_components_are_not_created() {
        let temp=tempfile::tempdir().unwrap(); let root=temp.path().join("data");
        assert!(snapshot_private_read_only(&root,ID).is_err()); assert!(!root.exists());
        directory(&root); let before=tree(&root); assert!(snapshot_private_read_only(&root,ID).is_err()); assert_eq!(tree(&root),before);
        directory(&root.join(package_directory_name(ID))); let before=tree(&root); assert!(snapshot_private_read_only(&root,ID).is_err()); assert_eq!(tree(&root),before);
    }
    #[test]
    fn existing_snapshot_is_sorted_and_does_not_write() {
        let (_temp,root,files)=fixture(); file(&files.join("a.txt"),b"beta"); let before=tree(&root);
        let snapshot=snapshot_private_read_only(&root,ID).unwrap();
        assert_eq!(snapshot.package_id,ID); assert_eq!(snapshot.files.iter().map(|f|f.path.as_str()).collect::<Vec<_>>(),["a.txt","note.txt"]);
        assert_eq!(snapshot.files[1].size,5); assert_eq!(snapshot.files[1].sha256,"8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8"); assert_eq!(tree(&root),before);
        let encoded=serde_json::to_value(&snapshot).unwrap(); assert!(serde_json::from_value::<PrivateContentSnapshot>(serde_json::json!({"packageId":ID,"files":[],"unknown":true})).is_err()); assert_eq!(encoded["packageId"],ID);
    }
    #[test]
    fn links_hidden_temporary_and_directories_are_rejected_without_writes() {
        for case in 0..7 {
            let (_temp,root,files)=fixture();
            match case {
                0=>symlink("note.txt",files.join("link")).unwrap(),
                1=>fs::hard_link(files.join("note.txt"),files.join("hard")).unwrap(),
                2=>file(&files.join(".hidden"),b"x"),
                3=>file(&files.join(".ming-data-tmp"),b"x"),
                4=>directory(&files.join("empty")),
                5=>{fs::remove_file(files.join("note.txt")).unwrap();},
                _=>{fs::rename(&files,root.join("actual")).unwrap();symlink(root.join("actual"),&files).unwrap();}
            }
            let before=tree(&root); assert!(snapshot_private_read_only(&root,ID).is_err(),"case {case}"); assert_eq!(tree(&root),before);
        }
    }
    #[test]
    fn changed_content_or_name_during_observation_is_rejected() {
        for case in 0..3 {
            let (_temp,root,files)=fixture();
            let result=platform::snapshot_private_read_only_checked(&root,ID,|| {
                match case {
                    0=>file(&files.join("note.txt"),b"bravo"),
                    1=>{fs::remove_file(files.join("note.txt")).unwrap();file(&files.join("note.txt"),b"alpha");},
                    _=>file(&files.join("extra.txt"),b"x"),
                }
            });
            assert!(result.is_err(),"unstable case {case}");
        }
    }
    #[test]
    fn bounded_entries_and_total_content_are_enforced() {
        for case in 0..3 {
            let (_temp,root,files)=fixture();
            match case {
                0=>for i in 0..32 {file(&files.join(format!("f{i}")),b"");},
                1=>for i in 0..32 {directory(&files.join(format!("d{i}")));},
                _=>file(&files.join("large"),&vec![0;65_536]),
            }
            let before=tree(&root); assert!(snapshot_private_read_only(&root,ID).is_err()); assert_eq!(tree(&root),before);
        }
        let (_temp,root,files)=fixture(); file(&files.join("note.txt"),&vec![0;65_536]); assert_eq!(snapshot_private_read_only(&root,ID).unwrap().files[0].size,65_536);
    }
}
