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
        Ok(AppDataGrant {
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
        })
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
        let public = self.public_authorized.get_mut().unwrap_or_else(|poison| poison.into_inner());
        for entry in public.values_mut() {
            for session in entry.sessions.drain(..).filter_map(|session| session.upgrade()) {
                *session.lock().unwrap_or_else(|poison| poison.into_inner()) = false;
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
            private_directory(&self.0)?;
            let file = open_at(&self.0, ".policy-lock", libc::O_RDWR | libc::O_CREAT, 0o600)?;
            stamp(&file)?;
            if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
                return Err(AppDataError::Io);
            }
            Ok(file)
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
}

#[cfg(windows)]
mod platform {
    use super::*;
    use std::{
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
            check_chain(path)?;
            let handle = OpenOptions::new().read(true).share_mode(7).custom_flags(0x02000000 | OPEN_REPARSE).open(path).map_err(io_error)?;
            let m = handle.metadata().map_err(io_error)?;
            if !m.is_dir() || m.file_attributes() & REPARSE != 0 { return Err(AppDataError::Io); }
            Ok(Self { path: path.to_owned(), _handle: handle })
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
