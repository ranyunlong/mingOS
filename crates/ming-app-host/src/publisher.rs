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
        Ok(Self { root: root.to_path_buf(), records, lock })
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

fn open_lock(root: &Path, create: bool) -> Result<File> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(create);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let lock = options.open(root.join(LOCK_NAME)).context("PUBLISHER_KEYRING_LOCK")?;
    #[cfg(target_os = "linux")]
    {
        use std::os::fd::AsRawFd;
        let result = unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
        if result != 0 { bail!("PUBLISHER_KEYRING_BUSY"); }
    }
    Ok(lock)
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
