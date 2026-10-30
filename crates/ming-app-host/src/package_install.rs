//! Parent-owned installation records for the Linux desktop.
//!
//! This is a local trust anchor supplied by the trusted parent. The development `open()` path
//! binds a package ID to exact loaded package bytes and version; `open_verified()` additionally
//! requires a trusted publisher signature.

use crate::{app_data::{PackageStorageAuthority, valid_package_id}, package::{Package, load_package}};
use crate::publisher::PublisherTrustStore;
use anyhow::{Context, Result, bail};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{Mutex, atomic::{AtomicU64, Ordering}},
};
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

const HEADER: &str = "ming-installed-packages-v1\n";
const RECORD_NAME: &str = "installed-packages-v1";
const LOCK_NAME: &str = ".ming-installed-packages.lock";
const CONTENT_DIR_NAME: &str = "packages";
const MAX_RECORD_BYTES: u64 = 64 * 1024;
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InstallState {
    Installed,
    Unchanged,
    Updated,
    Reinstalled,
}

#[derive(Clone, Copy)]
pub enum UninstallDataPolicy<'a> {
    PreservePrivateData,
    PurgePrivateData(&'a PackageStorageAuthority),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct UninstallOutcome {
    pub registry_changed: bool,
    pub private_data_purged: bool,
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
    publisher_trust: Option<PublisherTrustStore>,
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
        Self::open_with_publisher_trust(root, None)
    }

    pub fn open_verified(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref();
        let publisher_trust = PublisherTrustStore::open(root)?;
        Self::open_with_publisher_trust(root, Some(publisher_trust))
    }

    fn open_with_publisher_trust(root: &Path, publisher_trust: Option<PublisherTrustStore>) -> Result<Self> {
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
        Ok(Self { root: root.to_path_buf(), records: Mutex::new(records), lock, publisher_trust })
    }

    pub fn install_package(&self, package: &Package) -> Result<InstallState> {
        self.verify_publisher(package)?;
        let id = &package.manifest.id;
        validate_package_record(id, &package.manifest.version)?;
        let candidate = Record {
            version: package.manifest.version.clone(),
            fingerprint: package.storage_fingerprint(),
            state: RecordState::Active,
        };
        let mut records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        let previous = records.clone();
        let state = next_state(records.get(id), &candidate)?;
        if state != InstallState::Unchanged {
            records.insert(id.clone(), candidate);
            if let Err(error) = self.persist_locked(&records) {
                *records = previous;
                return Err(error);
            }
        }
        Ok(state)
    }

    /// Loads a package through the trusted parent, stores its content under an immutable
    /// versioned directory, and then publishes the matching active record atomically.
    pub fn install_from_manifest(&self, manifest_path: &Path) -> Result<InstallState> {
        let package = load_package(manifest_path).context("INSTALL_PACKAGE_LOAD")?;
        self.verify_publisher(&package)?;
        validate_package_record(&package.manifest.id, &package.manifest.version)?;
        let candidate = Record {
            version: package.manifest.version.clone(),
            fingerprint: package.storage_fingerprint(),
            state: RecordState::Active,
        };
        let mut records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        let previous = records.clone();
        let state = next_state(records.get(&package.manifest.id), &candidate)?;
        let content_path = self.content_path(&package.manifest.id, &candidate);
        let content_was_created = if let Ok(metadata) = fs::symlink_metadata(&content_path) {
            if metadata.file_type().is_symlink() || !metadata.is_dir() {
                bail!("INSTALL_CONTENT_DIRECTORY");
            }
            self.verify_stored_package(&content_path, &package)?;
            false
        } else {
            self.stage_package(&content_path, &package)?;
            true
        };
        if state != InstallState::Unchanged {
            records.insert(package.manifest.id.clone(), candidate);
            if let Err(error) = self.persist_locked(&records) {
                *records = previous;
                if content_was_created { let _ = fs::remove_dir_all(&content_path); }
                return Err(error);
            }
        }
        Ok(state)
    }

    pub fn active_manifest_path(&self, package_id: &str) -> Result<PathBuf> {
        if !valid_package_id(package_id) { bail!("INSTALL_PACKAGE_ID"); }
        let records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        let record = records.get(package_id).context("INSTALL_NOT_FOUND")?;
        if record.state != RecordState::Active { bail!("INSTALL_PACKAGE_REMOVED"); }
        let path = self.content_path(package_id, record).join("app.json");
        let package = load_package(&path).context("INSTALL_CONTENT_MISSING")?;
        if package.manifest.id != package_id
            || package.manifest.version != record.version
            || package.storage_fingerprint() != record.fingerprint
        {
            bail!("INSTALL_CONTENT_MISMATCH");
        }
        self.verify_publisher(&package)?;
        Ok(path)
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

    /// Marks the package removed and optionally performs the separate destructive data purge.
    /// A purge failure leaves the package removed and its private data available for retry.
    pub fn uninstall_with_data_policy(&self, package_id: &str, policy: UninstallDataPolicy<'_>) -> Result<UninstallOutcome> {
        let registry_changed = self.uninstall(package_id)?;
        let private_data_purged = match policy {
            UninstallDataPolicy::PreservePrivateData => false,
            UninstallDataPolicy::PurgePrivateData(authority) => authority
                .purge_private_data(package_id)
                .map_err(|error| anyhow::anyhow!("INSTALL_PRIVATE_DATA_PURGE: {error}"))?,
        };
        Ok(UninstallOutcome { registry_changed, private_data_purged })
    }

    pub fn verify_package(&self, package: &Package) -> Result<()> {
        self.verify_publisher(package)?;
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

    fn verify_publisher(&self, package: &Package) -> Result<()> {
        if let Some(trust) = &self.publisher_trust {
            trust.verify_package(package).context("INSTALL_PUBLISHER")?;
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

    fn content_path(&self, package_id: &str, record: &Record) -> PathBuf {
        self.root
            .join(CONTENT_DIR_NAME)
            .join(hex_bytes(package_id.as_bytes()))
            .join(format!("{}-{}", record.version, hex(&record.fingerprint)))
    }

    fn stage_package(&self, destination: &Path, package: &Package) -> Result<()> {
        let package_root = self.root.join(CONTENT_DIR_NAME);
        ensure_private_directory(&package_root)?;
        let id_root = package_root.join(hex_bytes(package.manifest.id.as_bytes()));
        ensure_private_directory(&id_root)?;
        if destination.exists() { bail!("INSTALL_CONTENT_RACE"); }
        let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let temporary = id_root.join(format!(".tmp-{}-{sequence}", std::process::id()));
        ensure_private_directory(&temporary)?;
        let result = (|| -> Result<()> {
            let manifest = serde_json::to_vec(&package.manifest).context("INSTALL_MANIFEST_SERIALIZE")?;
            write_private_file(&temporary.join("app.json"), &manifest)?;
            write_private_file(&temporary.join(&package.manifest.entry), &package.bytes)?;
            File::open(&temporary).context("INSTALL_CONTENT_OPEN")?.sync_all().context("INSTALL_CONTENT_SYNC")?;
            fs::rename(&temporary, destination).context("INSTALL_CONTENT_RENAME")?;
            File::open(&id_root).context("INSTALL_CONTENT_PARENT_OPEN")?.sync_all().context("INSTALL_CONTENT_PARENT_SYNC")?;
            File::open(&package_root).context("INSTALL_CONTENT_ROOT_OPEN")?.sync_all().context("INSTALL_CONTENT_ROOT_SYNC")?;
            Ok(())
        })();
        if result.is_err() { let _ = fs::remove_dir_all(&temporary); }
        result
    }

    fn verify_stored_package(&self, path: &Path, expected: &Package) -> Result<()> {
        let stored = load_package(&path.join("app.json")).context("INSTALL_CONTENT_LOAD")?;
        if stored.manifest.id != expected.manifest.id
            || stored.manifest.version != expected.manifest.version
            || stored.storage_fingerprint() != expected.storage_fingerprint()
        {
            bail!("INSTALL_CONTENT_MISMATCH");
        }
        Ok(())
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

fn next_state(existing: Option<&Record>, candidate: &Record) -> Result<InstallState> {
    match existing {
        None => Ok(InstallState::Installed),
        Some(existing) if existing.state == RecordState::Active
            && existing.version == candidate.version
            && existing.fingerprint == candidate.fingerprint => Ok(InstallState::Unchanged),
        Some(existing) if existing.state == RecordState::Removed
            && existing.version == candidate.version
            && existing.fingerprint == candidate.fingerprint => Ok(InstallState::Reinstalled),
        Some(existing) if !version_is_newer(&candidate.version, &existing.version) => {
            bail!("INSTALL_VERSION_NOT_NEWER");
        }
        Some(existing) if existing.state == RecordState::Removed => Ok(InstallState::Reinstalled),
        Some(_) => Ok(InstallState::Updated),
    }
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

fn hex_bytes(bytes: &[u8]) -> String {
    let mut value = String::with_capacity(bytes.len() * 2);
    for byte in bytes { value.push_str(&format!("{byte:02x}")); }
    value
}

fn ensure_private_directory(path: &Path) -> Result<()> {
    if let Ok(metadata) = fs::symlink_metadata(path) {
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            bail!("INSTALL_CONTENT_DIRECTORY");
        }
        verify_private_owner(&metadata, "INSTALL_CONTENT_PERMISSIONS")?;
        return Ok(());
    }
    fs::create_dir(path).context("INSTALL_CONTENT_MKDIR")?;
    #[cfg(unix)]
    fs::set_permissions(path, std::fs::Permissions::from_mode(0o700)).context("INSTALL_CONTENT_CHMOD")?;
    let metadata = fs::symlink_metadata(path).context("INSTALL_CONTENT_METADATA")?;
    verify_private_owner(&metadata, "INSTALL_CONTENT_PERMISSIONS")
}

fn write_private_file(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let mut file = options.open(path).context("INSTALL_CONTENT_FILE")?;
    file.write_all(bytes).context("INSTALL_CONTENT_WRITE")?;
    file.sync_all().context("INSTALL_CONTENT_FILE_SYNC")
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
