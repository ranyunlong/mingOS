//! Parent-owned publisher keys and Ed25519 package signature verification.

use crate::package::Package;
use anyhow::{Context, Result, bail};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

const HEADER: &str = "ming-trusted-publishers-v1\n";
const KEYRING_NAME: &str = "trusted-publishers-v1";
const LOCK_NAME: &str = ".ming-publisher-keyring.lock";
const MAX_KEYRING_BYTES: u64 = 64 * 1024;
const SIGNATURE_DOMAIN: &[u8] = b"ming-package-signature-v1\0";
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PublisherState {
    Active,
    Revoked,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PublisherRecord {
    public_key: [u8; 32],
    state: PublisherState,
}

#[derive(Debug)]
struct PublisherLock { file: Option<File> }

impl PublisherLock {
    fn into_file(mut self) -> File { self.file.take().unwrap() }
}

impl Drop for PublisherLock {
    fn drop(&mut self) {
        #[cfg(target_os = "linux")]
        if let Some(file) = &self.file {
            use std::os::fd::AsRawFd;
            // Closing alone leaves the flock alive in a forked child until exec.
            unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_UN); }
        }
    }
}

/// A keyring loaded once for one trusted parent process lifetime.
pub struct PublisherTrustStore {
    root: PathBuf,
    records: BTreeMap<String, PublisherRecord>,
    lock: File,
}

impl std::fmt::Debug for PublisherTrustStore {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PublisherTrustStore")
            .field("root", &self.root)
            .field("key_count", &self.records.len())
            .finish_non_exhaustive()
    }
}

impl PublisherTrustStore {
    pub fn open(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref();
        verify_root(root)?;
        let lock = open_lock(root, true)?;
        let records = load_records(&root.join(KEYRING_NAME), false)?;
        Ok(Self { root: root.to_path_buf(), records, lock: lock.into_file() })
    }

    /// Strict reopen: acquire an existing lock without repairing missing files.
    pub(crate) fn open_existing(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref();
        verify_root(root)?;
        let lock = open_lock(root, false)?;
        let records = load_records(&root.join(KEYRING_NAME), false)?;
        Ok(Self { root: root.to_path_buf(), records, lock: lock.into_file() })
    }

    pub fn provision(root: impl AsRef<Path>, key_id: &str, public_key: &[u8; 32]) -> Result<()> {
        validate_key_id(key_id)?;
        let root = root.as_ref();
        verify_root(root)?;
        let _lock = open_lock(root, true)?;
        let path = root.join(KEYRING_NAME);
        let mut records = load_records(&path, true)?;
        if records.contains_key(key_id) {
            bail!("PUBLISHER_KEY_EXISTS");
        }
        VerifyingKey::from_bytes(public_key).map_err(|_| anyhow::anyhow!("PUBLISHER_KEY_INVALID"))?;
        records.insert(key_id.to_owned(), PublisherRecord { public_key: *public_key, state: PublisherState::Active });
        persist_records(root, &records)
    }

    pub fn revoke(root: impl AsRef<Path>, key_id: &str) -> Result<()> {
        validate_key_id(key_id)?;
        let root = root.as_ref();
        verify_root(root)?;
        let _lock = open_lock(root, true)?;
        let path = root.join(KEYRING_NAME);
        let mut records = load_records(&path, false)?;
        let record = records.get_mut(key_id).context("PUBLISHER_KEY_UNKNOWN")?;
        record.state = PublisherState::Revoked;
        persist_records(root, &records)
    }

    pub fn verify_package(&self, package: &Package) -> Result<()> {
        let key_id = package.manifest.publisher_key_id.as_deref();
        let encoded = package.manifest.publisher_signature.as_deref();
        let (Some(key_id), Some(encoded)) = (key_id, encoded) else {
            if key_id.is_some() || encoded.is_some() {
                bail!("PUBLISHER_SIGNATURE_METADATA");
            }
            bail!("PUBLISHER_SIGNATURE_REQUIRED");
        };
        validate_key_id(key_id)?;
        let record = self.records.get(key_id).context("PUBLISHER_KEY_UNKNOWN")?;
        if record.state == PublisherState::Revoked {
            bail!("PUBLISHER_KEY_REVOKED");
        }
        let bytes = STANDARD.decode(encoded).context("PUBLISHER_SIGNATURE_ENCODING")?;
        let signature = Signature::from_slice(&bytes).map_err(|_| anyhow::anyhow!("PUBLISHER_SIGNATURE_LENGTH"))?;
        let verifying_key = VerifyingKey::from_bytes(&record.public_key)
            .map_err(|_| anyhow::anyhow!("PUBLISHER_KEY_INVALID"))?;
        verifying_key
            .verify(&package.signature_message(key_id)?, &signature)
            .map_err(|_| anyhow::anyhow!("PUBLISHER_SIGNATURE_INVALID"))
    }
}

impl Drop for PublisherTrustStore {
    fn drop(&mut self) {
        #[cfg(target_os = "linux")]
        {
            use std::os::fd::AsRawFd;
            unsafe { libc::flock(self.lock.as_raw_fd(), libc::LOCK_UN); }
        }
    }
}

impl Package {
    pub fn signature_message(&self, key_id: &str) -> Result<Vec<u8>> {
        validate_key_id(key_id)?;
        let mut message = Vec::with_capacity(128 + self.bytes.len());
        message.extend_from_slice(SIGNATURE_DOMAIN);
        put_text(&mut message, key_id);
        message.extend_from_slice(&self.manifest.schema_version.to_be_bytes());
        put_text(&mut message, &self.manifest.id);
        put_text(&mut message, &self.manifest.version);
        message.extend_from_slice(&self.manifest.sdk_version.to_be_bytes());
        put_text(&mut message, &self.manifest.entry);
        message.push(u8::from(self.manifest.private_storage));
        message.push(u8::from(self.manifest.public_storage));
        let migration = serde_json::to_vec(&self.manifest.data_migration)
            .context("PACKAGE_MIGRATION_SERIALIZE")?;
        message.extend_from_slice(&(migration.len() as u64).to_be_bytes());
        message.extend_from_slice(&migration);
        message.extend_from_slice(&(self.bytes.len() as u64).to_be_bytes());
        message.extend_from_slice(&self.bytes);
        Ok(message)
    }
}

fn put_text(output: &mut Vec<u8>, value: &str) {
    output.extend_from_slice(&(value.len() as u64).to_be_bytes());
    output.extend_from_slice(value.as_bytes());
}

pub(crate) fn validate_key_id(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 64
        || !value.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
    {
        bail!("PUBLISHER_KEY_ID");
    }
    Ok(())
}

fn verify_root(root: &Path) -> Result<()> {
    if !root.is_absolute() {
        bail!("PUBLISHER_ROOT_ABSOLUTE");
    }
    let metadata = fs::symlink_metadata(root).context("PUBLISHER_ROOT")?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        bail!("PUBLISHER_ROOT_DIRECTORY");
    }
    verify_private_owner(&metadata, "PUBLISHER_ROOT_PERMISSIONS")
}

fn open_lock(root: &Path, create: bool) -> Result<PublisherLock> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(create);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW | if create { 0 } else { libc::O_NONBLOCK });
    }
    let lock = options.open(root.join(LOCK_NAME)).context("PUBLISHER_KEYRING_LOCK")?;
    #[cfg(target_os = "linux")]
    {
        use std::os::fd::AsRawFd;
        use std::os::unix::fs::MetadataExt;
        if !create {
            let metadata = lock.metadata().context("PUBLISHER_KEYRING_LOCK_METADATA")?;
            if !metadata.is_file() || metadata.nlink() != 1 { bail!("PUBLISHER_KEYRING_LOCK_FILE"); }
            verify_private_owner(&metadata, "PUBLISHER_KEYRING_LOCK_PERMISSIONS")?;
        }
        let result = unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
        if result != 0 { bail!("PUBLISHER_KEYRING_BUSY"); }
    }
    Ok(PublisherLock { file: Some(lock) })
}

fn load_records(path: &Path, allow_missing: bool) -> Result<BTreeMap<String, PublisherRecord>> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if allow_missing && error.kind() == std::io::ErrorKind::NotFound => return Ok(BTreeMap::new()),
        Err(error) => return Err(error).context("PUBLISHER_KEYRING_FILE"),
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        bail!("PUBLISHER_KEYRING_FILE");
    }
    verify_private_owner(&metadata, "PUBLISHER_KEYRING_PERMISSIONS")?;
    if metadata.len() > MAX_KEYRING_BYTES { bail!("PUBLISHER_KEYRING_TOO_LARGE"); }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let mut bytes = Vec::new();
    options.open(path).context("PUBLISHER_KEYRING_OPEN")?.read_to_end(&mut bytes).context("PUBLISHER_KEYRING_READ")?;
    let text = std::str::from_utf8(&bytes).context("PUBLISHER_KEYRING_UTF8")?;
    let body = text.strip_prefix(HEADER).context("PUBLISHER_KEYRING_HEADER")?;
    let mut records = BTreeMap::new();
    for line in body.lines() {
        let mut fields = line.split('\t');
        let key_id = fields.next().context("PUBLISHER_KEYRING_LINE")?;
        validate_key_id(key_id)?;
        let state = match fields.next().context("PUBLISHER_KEYRING_LINE")? {
            "active" => PublisherState::Active,
            "revoked" => PublisherState::Revoked,
            _ => bail!("PUBLISHER_KEYRING_STATE"),
        };
        let encoded = fields.next().context("PUBLISHER_KEYRING_LINE")?;
        if fields.next().is_some() || records.contains_key(key_id) {
            bail!("PUBLISHER_KEYRING_LINE");
        }
        let bytes = STANDARD.decode(encoded).context("PUBLISHER_KEYRING_KEY_ENCODING")?;
        let public_key: [u8; 32] = bytes.try_into().map_err(|_| anyhow::anyhow!("PUBLISHER_KEYRING_KEY_LENGTH"))?;
        VerifyingKey::from_bytes(&public_key).map_err(|_| anyhow::anyhow!("PUBLISHER_KEY_INVALID"))?;
        records.insert(key_id.to_owned(), PublisherRecord { public_key, state });
    }
    if records.is_empty() { bail!("PUBLISHER_KEYRING_EMPTY"); }
    Ok(records)
}

fn persist_records(root: &Path, records: &BTreeMap<String, PublisherRecord>) -> Result<()> {
    let mut bytes = HEADER.as_bytes().to_vec();
    for (key_id, record) in records {
        bytes.extend_from_slice(key_id.as_bytes());
        bytes.push(b'\t');
        bytes.extend_from_slice(match record.state { PublisherState::Active => b"active", PublisherState::Revoked => b"revoked" });
        bytes.push(b'\t');
        bytes.extend_from_slice(STANDARD.encode(record.public_key).as_bytes());
        bytes.push(b'\n');
    }
    let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let temporary = root.join(format!(".ming-trusted-publishers.tmp-{}-{sequence}", std::process::id()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let result = (|| -> Result<()> {
        let mut file = options.open(&temporary).context("PUBLISHER_KEYRING_TEMP")?;
        file.write_all(&bytes).context("PUBLISHER_KEYRING_WRITE")?;
        file.sync_all().context("PUBLISHER_KEYRING_SYNC")?;
        fs::rename(&temporary, root.join(KEYRING_NAME)).context("PUBLISHER_KEYRING_RENAME")?;
        File::open(root).context("PUBLISHER_ROOT_OPEN")?.sync_all().context("PUBLISHER_ROOT_SYNC")?;
        Ok(())
    })();
    if result.is_err() { let _ = fs::remove_file(&temporary); }
    result
}

#[cfg(target_os = "linux")]
fn verify_private_owner(metadata: &std::fs::Metadata, error: &str) -> Result<()> {
    use std::os::unix::fs::MetadataExt;
    if metadata.uid() != unsafe { libc::geteuid() } || metadata.mode() & 0o077 != 0 {
        bail!("{error}");
    }
    Ok(())
}

#[cfg(not(target_os = "linux"))]
fn verify_private_owner(_metadata: &std::fs::Metadata, _error: &str) -> Result<()> { Ok(()) }

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;
    use ed25519_dalek::SigningKey;
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::{MetadataExt, PermissionsExt, symlink};
    use std::os::unix::net::UnixStream;

    struct InheritedDescriptors { pid: libc::pid_t, release: UnixStream }

    impl Drop for InheritedDescriptors {
        fn drop(&mut self) {
            let _ = self.release.write_all(&[1]);
            loop {
                let result = unsafe { libc::waitpid(self.pid, std::ptr::null_mut(), 0) };
                if result >= 0 || std::io::Error::last_os_error().raw_os_error() != Some(libc::EINTR) { break; }
            }
        }
    }

    fn retain_inherited_descriptors() -> InheritedDescriptors {
        let (parent, child) = UnixStream::pair().unwrap();
        let parent_fd = parent.as_raw_fd();
        let child_fd = child.as_raw_fd();
        let pid = unsafe { libc::fork() };
        assert!(pid >= 0, "fork: {}", std::io::Error::last_os_error());
        if pid == 0 {
            // A multithreaded test process may only use async-signal-safe calls
            // after fork. Hold every inherited descriptor until the parent says
            // to leave this deliberately extended fork-to-exec window.
            unsafe {
                libc::close(parent_fd);
                let mut byte = 1u8;
                if libc::write(child_fd, (&byte as *const u8).cast(), 1) != 1 { libc::_exit(71); }
                while libc::read(child_fd, (&mut byte as *mut u8).cast(), 1) < 0 {
                    if *libc::__errno_location() != libc::EINTR { libc::_exit(72); }
                }
                libc::_exit(0);
            }
        }
        drop(child);
        let mut holder = InheritedDescriptors { pid, release: parent };
        let mut ready = [0];
        holder.release.read_exact(&mut ready).unwrap();
        assert_eq!(ready, [1]);
        holder
    }

    #[test]
    fn dropping_publisher_lock_releases_it_before_inheriting_child_execs() {
        let root = tempfile::tempdir().unwrap();
        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
        let lock = open_lock(root.path(), true).unwrap();
        let _child_before_exec = retain_inherited_descriptors();
        let error = open_lock(root.path(), true).unwrap_err();
        assert!(format!("{error:#}").contains("PUBLISHER_KEYRING_BUSY"));
        drop(lock);
        let _next = open_lock(root.path(), true)
            .expect("publisher lock must end with its owner even while a forked child retains its descriptor");
    }

    fn fixture() -> tempfile::TempDir {
        let root = tempfile::tempdir().unwrap();
        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700)).unwrap();
        let key = SigningKey::from_bytes(&[7; 32]);
        PublisherTrustStore::provision(root.path(), "test-key", key.verifying_key().as_bytes()).unwrap();
        root
    }

    fn tree(root: &Path) -> BTreeMap<String, (u32, u64, u64, u64, Option<Vec<u8>>)> {
        fs::read_dir(root).unwrap().map(|entry| {
            let entry = entry.unwrap();
            let metadata = fs::symlink_metadata(entry.path()).unwrap();
            let bytes = metadata.is_file().then(|| fs::read(entry.path()).unwrap());
            (entry.file_name().into_string().unwrap(),
                (metadata.mode(), metadata.nlink(), metadata.ino(), metadata.len(), bytes))
        }).collect()
    }

    #[test]
    fn open_existing_requires_lock_without_creating_it() {
        let root = fixture();
        fs::remove_file(root.path().join(LOCK_NAME)).unwrap();
        let before = tree(root.path());
        let error = PublisherTrustStore::open_existing(root.path()).unwrap_err();
        assert!(format!("{error:#}").contains("PUBLISHER_KEYRING_LOCK"), "{error:#}");
        assert_eq!(tree(root.path()), before);
        assert!(!root.path().join(LOCK_NAME).exists());
    }

    #[test]
    fn open_existing_preserves_files_and_holds_exclusive_lock() {
        let root = fixture();
        let before = tree(root.path());
        let store = PublisherTrustStore::open_existing(root.path()).unwrap();
        assert_eq!(store.records["test-key"].state, PublisherState::Active);
        assert_eq!(store.records["test-key"].public_key, SigningKey::from_bytes(&[7; 32]).verifying_key().to_bytes());
        let error = PublisherTrustStore::open_existing(root.path()).unwrap_err();
        assert!(format!("{error:#}").contains("PUBLISHER_KEYRING_BUSY"), "{error:#}");
        assert_eq!(tree(root.path()), before);
        drop(store);
        assert!(PublisherTrustStore::open_existing(root.path()).is_ok());
        assert_eq!(tree(root.path()), before);
    }

    #[test]
    fn open_existing_rejects_unsafe_locks_without_mutation() {
        for case in ["symlink", "hardlink", "permissions", "directory", "fifo"] {
            let root = fixture();
            let lock = root.path().join(LOCK_NAME);
            fs::remove_file(&lock).unwrap();
            match case {
                "symlink" => symlink(root.path().join(KEYRING_NAME), &lock).unwrap(),
                "hardlink" => fs::hard_link(root.path().join(KEYRING_NAME), &lock).unwrap(),
                "permissions" => {
                    fs::write(&lock, b"").unwrap();
                    fs::set_permissions(&lock, fs::Permissions::from_mode(0o644)).unwrap();
                }
                "directory" => fs::create_dir(&lock).unwrap(),
                "fifo" => {
                    use std::{ffi::CString, os::unix::ffi::OsStrExt};
                    let name = CString::new(lock.as_os_str().as_bytes()).unwrap();
                    assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
                }
                _ => unreachable!(),
            }
            let before = tree(root.path());
            assert!(PublisherTrustStore::open_existing(root.path()).is_err(), "{case}");
            assert_eq!(tree(root.path()), before, "{case}");
        }
    }

    #[test]
    fn open_existing_missing_keyring_does_not_create_it() {
        let root = fixture();
        fs::remove_file(root.path().join(KEYRING_NAME)).unwrap();
        let before = tree(root.path());
        assert!(PublisherTrustStore::open_existing(root.path()).is_err());
        assert_eq!(tree(root.path()), before);
        assert!(!root.path().join(KEYRING_NAME).exists());
    }

    #[test]
    fn ordinary_open_still_creates_missing_lock() {
        let root = fixture();
        fs::remove_file(root.path().join(LOCK_NAME)).unwrap();
        let before = fs::read(root.path().join(KEYRING_NAME)).unwrap();
        let store = PublisherTrustStore::open(root.path()).unwrap();
        assert_eq!(store.records["test-key"].state, PublisherState::Active);
        assert!(root.path().join(LOCK_NAME).is_file());
        assert_eq!(fs::read(root.path().join(KEYRING_NAME)).unwrap(), before);
    }
}
