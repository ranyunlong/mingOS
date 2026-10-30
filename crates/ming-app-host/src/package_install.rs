//! Parent-owned installation records for the Linux desktop.
//!
//! This is a local trust anchor supplied by the trusted parent. It binds a package ID to the
//! exact loaded package bytes and version, but it is not a publisher signature or a replacement
//! for a future signed distribution format.

use crate::{app_data::valid_package_id, package::Package};
use anyhow::{Context, Result, bail};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{Mutex, atomic::{AtomicU64, Ordering}},
};

const HEADER: &str = "ming-installed-packages-v1\n";
const RECORD_NAME: &str = "installed-packages-v1";
const LOCK_NAME: &str = ".ming-installed-packages.lock";
const MAX_RECORD_BYTES: u64 = 64 * 1024;
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InstallState {
    Installed,
    Unchanged,
    Updated,
    Reinstalled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RecordState {
    Active,
    Removed,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Record {
    version: String,
    fingerprint: [u8; 32],
    state: RecordState,
}

pub struct PackageInstallRegistry {
    root: PathBuf,
    records: Mutex<BTreeMap<String, Record>>,
    lock: File,
}

impl std::fmt::Debug for PackageInstallRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PackageInstallRegistry")
            .field("root", &self.root)
            .finish_non_exhaustive()
    }
}

impl PackageInstallRegistry {
    pub fn open(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref();
        if !root.is_absolute() {
            bail!("INSTALL_ROOT_ABSOLUTE");
        }
        let metadata = fs::symlink_metadata(root).context("INSTALL_ROOT")?;
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            bail!("INSTALL_ROOT_DIRECTORY");
        }
        verify_private_owner(&metadata, "INSTALL_ROOT_PERMISSIONS")?;

        let lock_path = root.join(LOCK_NAME);
        let mut lock_options = OpenOptions::new();
        lock_options.read(true).write(true).create(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            lock_options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
        }
        let lock = lock_options.open(&lock_path).context("INSTALL_ROOT_LOCK")?;
        #[cfg(target_os = "linux")]
        {
            use std::os::fd::AsRawFd;
            let result = unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
            if result != 0 {
                bail!("INSTALL_ROOT_BUSY");
            }
        }
        let records = load_records(root.join(RECORD_NAME))?;
        Ok(Self { root: root.to_path_buf(), records: Mutex::new(records), lock })
    }

    pub fn install_package(&self, package: &Package) -> Result<InstallState> {
        let id = &package.manifest.id;
        validate_package_record(id, &package.manifest.version)?;
        let candidate = Record {
            version: package.manifest.version.clone(),
            fingerprint: package.storage_fingerprint(),
            state: RecordState::Active,
        };
        let mut records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        let previous = records.clone();
        let state = match records.get(id) {
            None => InstallState::Installed,
            Some(existing) if existing.state == RecordState::Active
                && existing.version == candidate.version
                && existing.fingerprint == candidate.fingerprint => InstallState::Unchanged,
            Some(existing) if existing.state == RecordState::Active
                && !version_is_newer(&candidate.version, &existing.version) => {
                bail!("INSTALL_VERSION_NOT_NEWER");
            }
            Some(existing) if existing.state == RecordState::Removed
                && existing.version == candidate.version
                && existing.fingerprint == candidate.fingerprint => InstallState::Reinstalled,
            Some(existing) if existing.state == RecordState::Removed
                && !version_is_newer(&candidate.version, &existing.version) => {
                bail!("INSTALL_VERSION_NOT_NEWER");
            }
            Some(existing) if existing.state == RecordState::Removed => InstallState::Reinstalled,
            Some(_) => InstallState::Updated,
        };
        if state != InstallState::Unchanged {
            records.insert(id.clone(), candidate);
            if let Err(error) = self.persist_locked(&records) {
                *records = previous;
                return Err(error);
            }
        }
        Ok(state)
    }

    pub fn uninstall(&self, package_id: &str) -> Result<bool> {
        if !valid_package_id(package_id) {
            bail!("INSTALL_PACKAGE_ID");
        }
        let mut records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        let previous = records.clone();
        let record = records.get_mut(package_id).context("INSTALL_NOT_FOUND")?;
        if record.state == RecordState::Removed {
            return Ok(false);
        }
        record.state = RecordState::Removed;
        if let Err(error) = self.persist_locked(&records) {
            *records = previous;
            return Err(error);
        }
        Ok(true)
    }

    pub fn verify_package(&self, package: &Package) -> Result<()> {
        let records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        let record = records.get(&package.manifest.id).context("INSTALL_NOT_REGISTERED")?;
        if record.state != RecordState::Active {
            bail!("INSTALL_PACKAGE_REMOVED");
        }
        if record.version != package.manifest.version
            || record.fingerprint != package.storage_fingerprint()
        {
            bail!("INSTALL_PACKAGE_MISMATCH");
        }
        Ok(())
    }

    fn persist_locked(&self, records: &BTreeMap<String, Record>) -> Result<()> {
        let mut bytes = HEADER.as_bytes().to_vec();
        for (id, record) in records {
            bytes.extend_from_slice(id.as_bytes());
            bytes.push(b'\t');
            bytes.extend_from_slice(match record.state { RecordState::Active => b"active", RecordState::Removed => b"removed" });
            bytes.push(b'\t');
            bytes.extend_from_slice(record.version.as_bytes());
            bytes.push(b'\t');
            bytes.extend_from_slice(hex(&record.fingerprint).as_bytes());
            bytes.push(b'\n');
        }
        let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let temp_path = self.root.join(format!(".ming-installed-packages.tmp-{}-{sequence}", std::process::id()));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
        }
        let result = (|| -> Result<()> {
            let mut temp = options.open(&temp_path).context("INSTALL_RECORD_TEMP")?;
            temp.write_all(&bytes).context("INSTALL_RECORD_WRITE")?;
            temp.sync_all().context("INSTALL_RECORD_SYNC")?;
            fs::rename(&temp_path, self.root.join(RECORD_NAME)).context("INSTALL_RECORD_RENAME")?;
            File::open(&self.root).context("INSTALL_ROOT_OPEN")?.sync_all().context("INSTALL_ROOT_SYNC")?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temp_path);
        }
        result
    }
}

impl Drop for PackageInstallRegistry {
    fn drop(&mut self) {
        #[cfg(target_os = "linux")]
        {
            use std::os::fd::AsRawFd;
            unsafe { libc::flock(self.lock.as_raw_fd(), libc::LOCK_UN); }
        }
    }
}

fn load_records(path: PathBuf) -> Result<BTreeMap<String, Record>> {
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(BTreeMap::new()),
        Err(error) => return Err(error).context("INSTALL_RECORD_METADATA"),
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        bail!("INSTALL_RECORD_FILE");
    }
    verify_private_owner(&metadata, "INSTALL_RECORD_PERMISSIONS")?;
    if metadata.len() > MAX_RECORD_BYTES {
        bail!("INSTALL_RECORD_TOO_LARGE");
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let mut bytes = Vec::new();
    options.open(path).context("INSTALL_RECORD_OPEN")?.read_to_end(&mut bytes).context("INSTALL_RECORD_READ")?;
    let text = std::str::from_utf8(&bytes).context("INSTALL_RECORD_UTF8")?;
    let body = text.strip_prefix(HEADER).context("INSTALL_RECORD_HEADER")?;
    let mut records = BTreeMap::new();
    for line in body.lines() {
        let mut fields = line.split('\t');
        let id = fields.next().context("INSTALL_RECORD_LINE")?;
        let state = match fields.next().context("INSTALL_RECORD_LINE")? {
            "active" => RecordState::Active,
            "removed" => RecordState::Removed,
            _ => bail!("INSTALL_RECORD_STATE"),
        };
        let version = fields.next().context("INSTALL_RECORD_LINE")?;
        let fingerprint = decode_hex(fields.next().context("INSTALL_RECORD_LINE")?)?;
        if fields.next().is_some() || !valid_package_id(id) || !valid_version(version) || records.contains_key(id) {
            bail!("INSTALL_RECORD_LINE");
        }
        records.insert(id.to_owned(), Record { version: version.to_owned(), fingerprint, state });
    }
    Ok(records)
}

fn validate_package_record(id: &str, version: &str) -> Result<()> {
    if !valid_package_id(id) { bail!("INSTALL_PACKAGE_ID"); }
    if !valid_version(version) { bail!("INSTALL_PACKAGE_VERSION"); }
    Ok(())
}

fn valid_version(version: &str) -> bool {
    let parts: Vec<_> = version.split('.').collect();
    parts.len() == 3 && parts.iter().all(|part| !part.is_empty() && part.bytes().all(|b| b.is_ascii_digit()))
}

fn version_is_newer(candidate: &str, previous: &str) -> bool {
    for (candidate_part, previous_part) in candidate.split('.').zip(previous.split('.')) {
        let candidate_part = candidate_part.trim_start_matches('0');
        let previous_part = previous_part.trim_start_matches('0');
        let candidate_part = if candidate_part.is_empty() { "0" } else { candidate_part };
        let previous_part = if previous_part.is_empty() { "0" } else { previous_part };
        if candidate_part.len() != previous_part.len() {
            return candidate_part.len() > previous_part.len();
        }
        if candidate_part != previous_part {
            return candidate_part > previous_part;
        }
    }
    false
}

fn decode_hex(value: &str) -> Result<[u8; 32]> {
    if value.len() != 64 || !value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
        bail!("INSTALL_RECORD_FINGERPRINT");
    }
    let mut result = [0; 32];
    for (index, byte) in result.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&value[index * 2..index * 2 + 2], 16).context("INSTALL_RECORD_FINGERPRINT")?;
    }
    Ok(result)
}

fn hex(bytes: &[u8; 32]) -> String {
    let mut value = String::with_capacity(64);
    for byte in bytes { value.push_str(&format!("{byte:02x}")); }
    value
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
