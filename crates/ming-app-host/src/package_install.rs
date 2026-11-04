//! Parent-owned installation records for the Linux desktop.
//!
//! This is a local trust anchor supplied by the trusted parent. The development `open()` path
//! binds a package ID to exact loaded package bytes and version; `open_verified()` additionally
//! requires a trusted publisher signature.

use crate::{app_data::{PackageStorageAuthority, valid_package_id}, package::{Package, load_package}};
use crate::publisher::PublisherTrustStore;
use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{Mutex, MutexGuard, atomic::{AtomicBool, AtomicU64, Ordering}},
};
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

const HEADER: &str = "ming-installed-packages-v1\n";
const RECORD_NAME: &str = "installed-packages-v1";
const LOCK_NAME: &str = ".ming-installed-packages.lock";
const CONTENT_DIR_NAME: &str = "packages";
const JOURNAL_NAME: &str = "update-journal-v1";
pub(crate) const COORDINATED_UPDATE_PENDING_NAME: &str = "desktop-update-pending-v1.json";
const MAX_JOURNAL_BYTES: u64 = 16 * 1024;
const MAX_RECORD_BYTES: u64 = 64 * 1024;
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InstallState {
    Installed,
    Unchanged,
    Updated,
    Reinstalled,
}

/// Identity of an update whose new package is active but whose data backup is
/// still retained until the desktop reports a successful startup.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PendingUpdate {
    pub package_id: String,
    pub old_version: String,
    pub new_version: String,
    pub new_fingerprint: [u8; 32],
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

#[derive(Debug, Clone)]
pub(crate) struct InstallRollback {
    package_id: String,
    previous: Option<Record>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct UpdateJournal {
    package_id: String,
    old_version: String,
    old_fingerprint: String,
    new_version: String,
    new_fingerprint: String,
    backup_name: String,
    migration_declared: bool,
    phase: UpdatePhase,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(crate) enum UpdatePhase {
    Planned,
    DataSwapped,
    RecordPublished,
    StartupAttempted,
    StartupConfirmed,
    RollingBack,
}

#[cfg(feature = "desktop")]
#[derive(Debug, Clone)]
pub(crate) struct CoordinatedJournal {
    pub phase: UpdatePhase,
    pub package_id: String,
    pub old_version: String,
    pub old_fingerprint: String,
    pub new_version: String,
    pub new_fingerprint: String,
    pub backup_name: String,
}

#[cfg(feature = "desktop")]
#[derive(Debug, Clone)]
/// The parent retains the target launch gate for the entire coordinated call.
/// The disk outer and every identity are rebound inside the operation mutex.
pub(crate) struct CoordinatedRegistryContext {
    pub package_id: String,
    pub old_version: String,
    pub old_fingerprint: [u8; 32],
    pub new_version: String,
    pub new_fingerprint: [u8; 32],
    pub data: crate::app_data::CoordinatedPrivateUpdate,
    pub outer: crate::persistent_update::UpdateRecord,
    /// Explicit trusted input, never a path supplied by the outer record.
    pub data_root: PathBuf,
}

pub struct PackageInstallRegistry {
    root: PathBuf,
    operation: Mutex<()>,
    records: Mutex<BTreeMap<String, Record>>,
    records_unusable: AtomicBool,
    lock: File,
    publisher_trust: Option<PublisherTrustStore>,
    #[cfg(test)]
    record_persist_fault: Mutex<Option<&'static str>>,
}

impl std::fmt::Debug for PackageInstallRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PackageInstallRegistry")
            .field("root", &self.root)
            .finish_non_exhaustive()
    }
}

impl PackageInstallRegistry {
    /// Outer creation and removal are serialized with ordinary registry writes.
    /// Do not call the self-locking coordinated mutators inside this closure.
    #[cfg(feature = "desktop")]
    pub(crate) fn with_update_operation<T>(&self, action: impl FnOnce() -> Result<T>) -> Result<T> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.ensure_records_usable()?;
        action()
    }

    pub(crate) fn reject_update_outer(&self) -> Result<()> {
        match fs::symlink_metadata(self.root.join(COORDINATED_UPDATE_PENDING_NAME)) {
            Ok(_) => bail!("INSTALL_COORDINATED_UPDATE_PENDING"),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
            Err(error) => return Err(error.into()),
        }
        Ok(())
    }

    fn ensure_records_usable(&self) -> Result<()> {
        if self.records_unusable.load(Ordering::Acquire) { bail!("INSTALL_RECORD_CACHE_UNUSABLE"); }
        Ok(())
    }

    fn coordinated_checkpoint(&self, stage: &str) -> Result<()> {
        #[cfg(all(test, feature = "desktop"))]
        crate::persistent_update::test_checkpoint(stage)?;
        let _ = stage;
        Ok(())
    }

    fn lock_records(&self) -> Result<MutexGuard<'_, BTreeMap<String, Record>>> {
        self.ensure_records_usable()?;
        let records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.ensure_records_usable()?;
        Ok(records)
    }
    /// Strict snapshots must keep using the locks acquired by this registry,
    /// rather than validating an unrelated replacement lock at the same path.
    pub(crate) fn verify_persistent_lock_binding(&self) -> Result<()> {
        use std::os::unix::fs::MetadataExt;
        let held=self.lock.metadata()?;
        let current=fs::symlink_metadata(self.root.join(LOCK_NAME))?;
        for info in [&held,&current] {
            if !info.is_file() || info.file_type().is_symlink() || info.nlink()!=1
                || info.uid()!=unsafe {libc::geteuid()} || info.mode() & 0o7777 != 0o600 {bail!("INSTALL_ROOT_LOCK_PERMISSIONS");}
        }
        if held.dev()!=current.dev() || held.ino()!=current.ino() {bail!("INSTALL_ROOT_LOCK_CHANGED");}
        self.publisher_trust.as_ref().context("INSTALL_PUBLISHER_REQUIRED")?.verify_persistent_lock_binding()?;
        Ok(())
    }
    pub(crate) fn root_path(&self) -> &Path { &self.root }

    pub(crate) fn active_records(&self) -> Result<BTreeMap<String, (String, [u8; 32])>> {
        let records = self.lock_records()?;
        Ok(records.iter().filter(|(_, record)| record.state == RecordState::Active)
            .map(|(id, record)| (id.clone(), (record.version.clone(), record.fingerprint))).collect())
    }

    /// Checks the exact bounded disk bytes used by a persistent snapshot against
    /// every record protected by this registry's already-held process lock.
    pub(crate) fn verify_records_snapshot(&self, bytes: &[u8]) -> Result<()> {
        let disk = parse_records(bytes)?;
        let records = self.lock_records()?;
        if disk != *records { bail!("INSTALL_RECORD_SNAPSHOT_DRIFT"); }
        Ok(())
    }

    /// Checks the complete disk/cache transition, not merely the target's absence
    /// from the active set. No other record or identity may change.
    pub(crate) fn verify_removed_transition(&self, before: &[u8], package: &Package) -> Result<()> {
        let mut expected = parse_records(before)?;
        let previous = expected.get_mut(&package.manifest.id).context("INSTALL_NOT_REGISTERED")?;
        if previous.state != RecordState::Active || previous.version != package.manifest.version
            || previous.fingerprint != package.storage_fingerprint()
        {
            bail!("INSTALL_REMOVE_IDENTITY");
        }
        previous.state = RecordState::Removed;
        let bytes = crate::package::read_bounded(&self.root.join(RECORD_NAME), MAX_RECORD_BYTES as usize, "INSTALL_RECORD")?;
        self.verify_records_snapshot(&bytes)?;
        if parse_records(&bytes)? != expected { bail!("INSTALL_REMOVE_TRANSITION"); }
        Ok(())
    }

    /// Reloads removed content at the record-derived path and verifies the actual
    /// stored signature. A trusted in-memory source cannot stand in for that file.
    pub(crate) fn load_removed_package(&self, package_id: &str) -> Result<Package> {
        if !valid_package_id(package_id) { bail!("INSTALL_PACKAGE_ID"); }
        let records = self.lock_records()?;
        let record = records.get(package_id).context("INSTALL_NOT_FOUND")?;
        if record.state != RecordState::Removed { bail!("INSTALL_PACKAGE_NOT_REMOVED"); }
        let package = load_package(&self.content_path(package_id, record).join("app.json")).context("INSTALL_CONTENT_MISSING")?;
        if package.manifest.id != package_id || package.manifest.version != record.version
            || package.storage_fingerprint() != record.fingerprint
        {
            bail!("INSTALL_CONTENT_MISMATCH");
        }
        self.verify_publisher(&package)?;
        Ok(package)
    }

    pub fn open(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref();
        Self::open_with_publisher_trust(root, None, true)
    }

    pub fn open_verified(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref();
        let publisher_trust = PublisherTrustStore::open(root)?;
        Self::open_with_publisher_trust(root, Some(publisher_trust), true)
    }

    pub(crate) fn open_verified_existing(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref();
        let publisher_trust = PublisherTrustStore::open_existing(root)?;
        Self::open_with_publisher_trust(root, Some(publisher_trust), false)
    }

    fn open_with_publisher_trust(root: &Path, publisher_trust: Option<PublisherTrustStore>, create_lock: bool) -> Result<Self> {
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
        lock_options.read(true).write(true).create(create_lock);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            lock_options.mode(0o600).custom_flags(libc::O_NOFOLLOW | if create_lock { 0 } else { libc::O_NONBLOCK });
        }
        let lock = lock_options.open(&lock_path).context("INSTALL_ROOT_LOCK")?;
        if !create_lock {
            use std::os::unix::fs::MetadataExt;
            let info = lock.metadata().context("INSTALL_ROOT_LOCK_METADATA")?;
            if !info.is_file() || info.nlink() != 1 || info.uid() != unsafe { libc::geteuid() }
                || info.mode() & 0o7777 != 0o600
            {
                bail!("INSTALL_ROOT_LOCK_PERMISSIONS");
            }
        }
        #[cfg(target_os = "linux")]
        {
            use std::os::fd::AsRawFd;
            let result = unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
            if result != 0 {
                bail!("INSTALL_ROOT_BUSY");
            }
        }
        let records = load_records(root.join(RECORD_NAME))?;
        Ok(Self { root: root.to_path_buf(), operation: Mutex::new(()), records: Mutex::new(records), records_unusable: AtomicBool::new(false), lock, publisher_trust,
            #[cfg(test)]
            record_persist_fault: Mutex::new(None),
        })
    }

    pub fn install_package(&self, package: &Package) -> Result<InstallState> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.reject_update_outer()?;
        if self.has_pending_update()? { bail!("INSTALL_UPDATE_PENDING"); }
        if package.manifest.data_migration.is_some() { bail!("INSTALL_MIGRATION_REQUIRES_UPDATE"); }
        self.verify_publisher(package)?;
        let id = &package.manifest.id;
        validate_package_record(id, &package.manifest.version)?;
        let candidate = Record {
            version: package.manifest.version.clone(),
            fingerprint: package.storage_fingerprint(),
            state: RecordState::Active,
        };
        let mut records = self.lock_records()?;
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
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.reject_update_outer()?;
        if self.has_pending_update()? { bail!("INSTALL_UPDATE_PENDING"); }
        let package = load_package(manifest_path).context("INSTALL_PACKAGE_LOAD")?;
        self.install_loaded_package_locked(&package)
    }

    /// Commits the exact package bytes approved by the desktop confirmation.
    /// The source path is not reopened after that identity check.
    pub(crate) fn install_loaded_package(&self, package: &Package) -> Result<InstallState> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.reject_update_outer()?;
        if self.has_pending_update()? { bail!("INSTALL_UPDATE_PENDING"); }
        self.install_loaded_package_locked(package)
    }

    fn install_loaded_package_locked(&self, package: &Package) -> Result<InstallState> {
        if package.manifest.data_migration.is_some() { bail!("INSTALL_MIGRATION_REQUIRES_UPDATE"); }
        self.verify_publisher(package)?;
        validate_package_record(&package.manifest.id, &package.manifest.version)?;
        let candidate = Record {
            version: package.manifest.version.clone(),
            fingerprint: package.storage_fingerprint(),
            state: RecordState::Active,
        };
        let mut records = self.lock_records()?;
        let previous = records.clone();
        let state = next_state(records.get(&package.manifest.id), &candidate)?;
        let content_path = self.content_path(&package.manifest.id, &candidate);
        let content_was_created = if let Ok(metadata) = fs::symlink_metadata(&content_path) {
            if metadata.file_type().is_symlink() || !metadata.is_dir() {
                bail!("INSTALL_CONTENT_DIRECTORY");
            }
            self.verify_stored_package(&content_path, package)?;
            false
        } else {
            self.stage_package(&content_path, package)?;
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

    /// Captures the registry state immediately before a desktop install so a
    /// later catalog write failure can restore the exact prior record.
    pub(crate) fn prepare_install_rollback(&self, package_id: &str) -> Result<InstallRollback> {
        if !valid_package_id(package_id) {
            bail!("INSTALL_PACKAGE_ID");
        }
        let records = self.lock_records()?;
        Ok(InstallRollback { package_id: package_id.to_owned(), previous: records.get(package_id).cloned() })
    }

    /// Restores the record captured by `prepare_install_rollback` after a
    /// desktop catalog transaction fails. Versioned package content is kept as
    /// an auditable immutable artifact; only the active registry record is
    /// restored.
    pub(crate) fn rollback_install(
        &self,
        rollback: InstallRollback,
        version: &str,
        fingerprint: [u8; 32],
    ) -> Result<()> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.reject_update_outer()?;
        let mut records = self.lock_records()?;
        let current = records.get(&rollback.package_id).cloned().context("INSTALL_ROLLBACK_RECORD_MISSING")?;
        if current.state != RecordState::Active || current.version != version || current.fingerprint != fingerprint {
            bail!("INSTALL_ROLLBACK_RECORD_MISMATCH");
        }
        let previous_records = records.clone();
        match rollback.previous {
            Some(previous) => { records.insert(rollback.package_id, previous); }
            None => { records.remove(&rollback.package_id); }
        }
        if let Err(error) = self.persist_locked(&records) {
            *records = previous_records;
            return Err(error).context("INSTALL_ROLLBACK_RECORD");
        }
        Ok(())
    }

    /// Restores the exact validated bytes and all cached records for a parent
    /// persistent-install rollback. Immutable package history is retained.
    pub(crate) fn restore_records_snapshot(&self, bytes: &[u8]) -> Result<()> {
        let parsed = parse_records(bytes)?;
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.reject_update_outer()?;
        let mut records = self.lock_records()?;
        self.persist_record_bytes(bytes)?;
        *records = parsed;
        Ok(())
    }

    /// Publishes an update with a retained snapshot of the old private files.
    /// The backup survives until a matching desktop startup is confirmed.
    pub fn update_from_manifest(&self, manifest_path: &Path, authority: &PackageStorageAuthority) -> Result<InstallState> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.reject_update_outer()?;
        self.recover_pending_update_locked(authority)?;
        if self.has_pending_update()? { bail!("INSTALL_UPDATE_PENDING"); }
        let package = load_package(manifest_path).context("INSTALL_PACKAGE_LOAD")?;
        self.update_loaded_package_locked(&package, authority)
    }

    /// Updates from the exact package checked against the presented prompt.
    pub(crate) fn update_loaded_package(&self, package: &Package, authority: &PackageStorageAuthority) -> Result<InstallState> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.reject_update_outer()?;
        self.recover_pending_update_locked(authority)?;
        if self.has_pending_update()? { bail!("INSTALL_UPDATE_PENDING"); }
        self.update_loaded_package_locked(package, authority)
    }

    fn update_loaded_package_locked(&self, package: &Package, authority: &PackageStorageAuthority) -> Result<InstallState> {
        self.verify_publisher(package)?;
        validate_package_record(&package.manifest.id, &package.manifest.version)?;
        let mut records = self.lock_records()?;
        let old = records.get(&package.manifest.id).context("INSTALL_NOT_FOUND")?.clone();
        if old.state != RecordState::Active { bail!("INSTALL_PACKAGE_REMOVED"); }
        let candidate = Record { version: package.manifest.version.clone(), fingerprint: package.storage_fingerprint(), state: RecordState::Active };
        let state = next_state(Some(&old), &candidate)?;
        if state == InstallState::Unchanged { return Ok(state); }
        if state != InstallState::Updated { bail!("INSTALL_UPDATE_REQUIRED"); }
        if let Some(migration) = &package.manifest.data_migration {
            if migration.from_version != old.version { bail!("INSTALL_MIGRATION_SOURCE"); }
        }
        // A usable old package is required for rollback, including updates
        // without a declared layout migration.
        let old_path = self.content_path(&package.manifest.id, &old).join("app.json");
        let old_package = load_package(&old_path).context("INSTALL_UPDATE_OLD_CONTENT")?;
        if old_package.manifest.id != package.manifest.id || old_package.manifest.version != old.version
            || old_package.storage_fingerprint() != old.fingerprint {
            bail!("INSTALL_UPDATE_OLD_CONTENT_MISMATCH");
        }
        self.verify_publisher(&old_package)?;
        let content_path = self.content_path(&package.manifest.id, &candidate);
        match fs::symlink_metadata(&content_path) {
            Ok(metadata) => {
                if metadata.file_type().is_symlink() || !metadata.is_dir() { bail!("INSTALL_CONTENT_DIRECTORY"); }
                self.verify_stored_package(&content_path, package)?;
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => self.stage_package(&content_path, package)?,
            Err(error) => return Err(error).context("INSTALL_CONTENT_METADATA"),
        }
        let mut journal = UpdateJournal {
            package_id: package.manifest.id.clone(),
            old_version: old.version.clone(),
            old_fingerprint: hex(&old.fingerprint),
            new_version: candidate.version.clone(),
            new_fingerprint: hex(&candidate.fingerprint),
            backup_name: format!(".ming-update-backup-{}-{}", std::process::id(), TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed)),
            migration_declared: package.manifest.data_migration.is_some(),
            phase: UpdatePhase::Planned,
        };
        self.persist_journal(&journal)?;
        let unchanged_layout = crate::package::DataMigration { from_version: old.version, steps: Vec::new() };
        let plan = package.manifest.data_migration.as_ref().unwrap_or(&unchanged_layout);
        let result = (|| -> Result<()> {
            authority.begin_private_data_migration_with_backup(&package.manifest.id, plan, &journal.backup_name)
                .map_err(|error| anyhow::anyhow!("INSTALL_MIGRATION: {error}"))?;
            journal.phase = UpdatePhase::DataSwapped;
            self.persist_journal(&journal)?;
            records.insert(package.manifest.id.clone(), candidate);
            self.persist_locked(&records)?;
            journal.phase = UpdatePhase::RecordPublished;
            self.persist_journal(&journal)?;
            Ok(())
        })();
        drop(records);
        if let Err(error) = result {
            return match self.rollback_pending_update_locked(authority) {
                Ok(_) => Err(error),
                Err(recovery) => Err(error.context(format!("INSTALL_UPDATE_ROLLBACK_PENDING: {recovery:#}"))),
            };
        }
        Ok(state)
    }

    /// Recovers interrupted writes. A published, never-attempted update can be
    /// tried once; interrupted startup or rollback restores the old version.
    pub fn recover_pending_update(&self, authority: &PackageStorageAuthority) -> Result<bool> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.reject_update_outer()?;
        self.recover_pending_update_locked(authority)
    }

    fn recover_pending_update_locked(&self, authority: &PackageStorageAuthority) -> Result<bool> {
        let Some(journal) = self.load_journal()? else { return Ok(false); };
        let current = self.lock_records()?
            .get(&journal.package_id).cloned().context("INSTALL_UPDATE_RECORD_MISSING")?;
        let new_matches = current.state == RecordState::Active
            && current.version == journal.new_version && hex(&current.fingerprint) == journal.new_fingerprint;
        if new_matches && journal.phase == UpdatePhase::RecordPublished { return Ok(false); }
        if new_matches && journal.phase == UpdatePhase::StartupConfirmed {
            let transaction = crate::app_data::PrivateMigrationTransaction {
                package_id: journal.package_id, backup_name: journal.backup_name,
            };
            authority.commit_private_data_migration(&transaction)
                .map_err(|error| anyhow::anyhow!("INSTALL_UPDATE_RECOVERY_COMMIT: {error}"))?;
            self.clear_journal()?;
            return Ok(true);
        }
        self.rollback_pending_update_locked(authority)
    }

    pub fn has_pending_update(&self) -> Result<bool> {
        Ok(self.load_journal()?.is_some())
    }

    pub fn pending_update(&self) -> Result<Option<PendingUpdate>> {
        let Some(journal) = self.load_journal()? else { return Ok(None); };
        if !matches!(journal.phase, UpdatePhase::RecordPublished | UpdatePhase::StartupAttempted) { return Ok(None); }
        Ok(Some(PendingUpdate {
            package_id: journal.package_id,
            old_version: journal.old_version,
            new_version: journal.new_version,
            new_fingerprint: decode_hex(&journal.new_fingerprint)?,
        }))
    }

    pub(crate) fn begin_update_startup(&self, package_id: &str, fingerprint: [u8; 32]) -> Result<bool> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        let Some(mut journal) = self.load_journal()? else { return Ok(false); };
        if journal.package_id != package_id { return Ok(false); }
        if journal.phase != UpdatePhase::RecordPublished || decode_hex(&journal.new_fingerprint)? != fingerprint {
            bail!("INSTALL_UPDATE_STARTUP_STATE");
        }
        #[cfg(feature = "desktop")]
        let transaction = self.read_coordinated_marker(COORDINATED_UPDATE_PENDING_NAME,
            crate::persistent_update::MAX_UPDATE_RECORD_BYTES as u64)?.map(|raw| -> Result<String> {
                let outer = crate::persistent_update::parse_update_record(&raw)?;
                crate::persistent_update::validate_inner_binding(&outer, &serde_json::to_vec(&journal)?)?;
                Ok(outer.transaction_id)
            }).transpose()?;
        #[cfg(not(feature = "desktop"))]
        let transaction: Option<String> = { self.reject_update_outer()?; None };
        journal.phase = UpdatePhase::StartupAttempted;
        self.persist_journal_with_transaction(&journal, transaction.as_deref())?;
        self.coordinated_checkpoint("inner-startup-attempted")?;
        Ok(true)
    }

    /// Called only by the trusted desktop after validating the complete receipt.
    pub(crate) fn confirm_pending_update(
        &self, authority: &PackageStorageAuthority, package_id: &str, version: &str, fingerprint: [u8; 32],
    ) -> Result<bool> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.reject_update_outer()?;
        let Some(mut journal) = self.load_journal()? else { return Ok(false); };
        if journal.phase != UpdatePhase::StartupAttempted || journal.package_id != package_id
            || journal.new_version != version || decode_hex(&journal.new_fingerprint)? != fingerprint {
            bail!("INSTALL_UPDATE_CONFIRMATION_MISMATCH");
        }
        let current = self.lock_records()?
            .get(package_id).cloned().context("INSTALL_UPDATE_RECORD_MISSING")?;
        if current.state != RecordState::Active || current.version != journal.new_version
            || hex(&current.fingerprint) != journal.new_fingerprint {
            bail!("INSTALL_UPDATE_CONFIRMATION_RECORD");
        }
        // Persist the commit decision before deleting any backup bytes.
        journal.phase = UpdatePhase::StartupConfirmed;
        self.persist_journal(&journal)?;
        let transaction = crate::app_data::PrivateMigrationTransaction {
            package_id: journal.package_id, backup_name: journal.backup_name,
        };
        authority.commit_private_data_migration(&transaction)
            .map_err(|error| anyhow::anyhow!("INSTALL_UPDATE_CONFIRMATION_COMMIT: {error}"))?;
        self.clear_journal()?;
        Ok(true)
    }

    /// Restores both the old record and the old tree. A durable RollingBack
    /// decision makes all rename/record crash windows retryable on reopening.
    pub fn rollback_pending_update(&self, authority: &PackageStorageAuthority) -> Result<bool> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.reject_update_outer()?;
        self.rollback_pending_update_locked(authority)
    }

    fn rollback_pending_update_locked(&self, authority: &PackageStorageAuthority) -> Result<bool> {
        let Some(mut journal) = self.load_journal()? else { return Ok(false); };
        if journal.phase == UpdatePhase::StartupConfirmed { bail!("INSTALL_UPDATE_ALREADY_CONFIRMED"); }
        let mut records = self.lock_records()?;
        let current = records.get(&journal.package_id).context("INSTALL_UPDATE_RECORD_MISSING")?;
        let old_matches = current.state == RecordState::Active && current.version == journal.old_version
            && hex(&current.fingerprint) == journal.old_fingerprint;
        let new_matches = current.state == RecordState::Active && current.version == journal.new_version
            && hex(&current.fingerprint) == journal.new_fingerprint;
        if !old_matches && !new_matches { bail!("INSTALL_UPDATE_ROLLBACK_RECORD"); }
        journal.phase = UpdatePhase::RollingBack;
        self.persist_journal(&journal)?;
        let previous = records.clone();
        records.insert(journal.package_id.clone(), Record {
            version: journal.old_version, fingerprint: decode_hex(&journal.old_fingerprint)?, state: RecordState::Active,
        });
        if let Err(error) = self.persist_locked(&records) {
            *records = previous;
            return Err(error);
        }
        let transaction = crate::app_data::PrivateMigrationTransaction {
            package_id: journal.package_id, backup_name: journal.backup_name,
        };
        authority.rollback_private_data_migration(&transaction)
            .map_err(|error| anyhow::anyhow!("INSTALL_UPDATE_ROLLBACK_DATA: {error}"))?;
        self.clear_journal()?;
        Ok(true)
    }

    pub fn active_manifest_path(&self, package_id: &str) -> Result<PathBuf> {
        if !valid_package_id(package_id) { bail!("INSTALL_PACKAGE_ID"); }
        let records = self.lock_records()?;
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

    /// Reports the persisted lifecycle state without reloading package content.
    /// A missing record is not considered removed.
    pub fn is_removed(&self, package_id: &str) -> Result<bool> {
        if !valid_package_id(package_id) { bail!("INSTALL_PACKAGE_ID"); }
        let records = self.lock_records()?;
        Ok(records.get(package_id).is_some_and(|record| record.state == RecordState::Removed))
    }

    pub fn uninstall(&self, package_id: &str) -> Result<bool> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.uninstall_locked(package_id)
    }

    fn uninstall_locked(&self, package_id: &str) -> Result<bool> {
        self.reject_update_outer()?;
        if self.has_pending_update()? { bail!("INSTALL_UPDATE_PENDING"); }
        if !valid_package_id(package_id) {
            bail!("INSTALL_PACKAGE_ID");
        }
        let mut records = self.lock_records()?;
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
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        let registry_changed = self.uninstall_locked(package_id)?;
        let private_data_purged = match policy {
            UninstallDataPolicy::PreservePrivateData => false,
            UninstallDataPolicy::PurgePrivateData(authority) => authority
                .purge_private_data(package_id)
                .map_err(|error| anyhow::anyhow!("INSTALL_PRIVATE_DATA_PURGE: {error}"))?,
        };
        Ok(UninstallOutcome { registry_changed, private_data_purged })
    }

    pub fn verify_package(&self, package: &Package) -> Result<()> {
        self.verify_package_identity(package)?;
        let records = self.lock_records()?;
        let record = records.get(&package.manifest.id).context("INSTALL_NOT_REGISTERED")?;
        if record.state != RecordState::Active {
            bail!("INSTALL_PACKAGE_REMOVED");
        }
        Ok(())
    }

    /// Verifies a package that has not been registered yet. This is the
    /// parent-side check used by the desktop install confirmation flow; it
    /// never grants an active install record by itself.
    pub fn verify_package_source(&self, package: &Package) -> Result<()> {
        self.verify_publisher(package)
    }

    /// Verifies the stored package identity and publisher without requiring an
    /// active lifecycle state. Purge retry uses this after the registry entry
    /// has already been marked removed.
    pub fn verify_package_identity(&self, package: &Package) -> Result<()> {
        self.verify_publisher(package)?;
        let records = self.lock_records()?;
        let record = records.get(&package.manifest.id).context("INSTALL_NOT_REGISTERED")?;
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

    fn journal_path(&self) -> PathBuf { self.root.join(JOURNAL_NAME) }

    #[cfg(feature = "desktop")]
    pub(crate) fn read_coordinated_marker(&self, name: &str, limit: u64) -> Result<Option<Vec<u8>>> {
        self.ensure_records_usable()?;
        use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
        let path = self.root.join(name);
        let before = match fs::symlink_metadata(&path) {
            Ok(info) => info,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error).context("INSTALL_COORDINATED_MARKER_METADATA"),
        };
        let stamp = |info: &fs::Metadata| (info.dev(), info.ino(), info.len(), info.mode(), info.uid(), info.nlink(),
            info.mtime(), info.mtime_nsec(), info.ctime(), info.ctime_nsec());
        if !before.is_file() || before.file_type().is_symlink() || before.nlink() != 1
            || before.uid() != unsafe { libc::geteuid() } || before.mode() & 0o7777 != 0o600 || before.len() > limit {
            bail!("INSTALL_COORDINATED_MARKER_FILE");
        }
        let mut file = OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK).open(&path)?;
        if stamp(&before) != stamp(&file.metadata()?) { bail!("INSTALL_COORDINATED_MARKER_CHANGED"); }
        let mut bytes = Vec::new();
        (&mut file).take(limit + 1).read_to_end(&mut bytes)?;
        if bytes.len() as u64 > limit || stamp(&before) != stamp(&file.metadata()?)
            || stamp(&before) != stamp(&fs::symlink_metadata(&path)?) { bail!("INSTALL_COORDINATED_MARKER_CHANGED"); }
        Ok(Some(bytes))
    }

    #[cfg(feature = "desktop")]
    pub(crate) fn inspect_coordinated_journal(&self) -> Result<Option<CoordinatedJournal>> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        Self::coordinated_journal_dto(self.read_coordinated_marker(JOURNAL_NAME, MAX_JOURNAL_BYTES)?)
    }

    #[cfg(all(feature = "desktop", test))]
    fn inspect_coordinated_journal_after_read(&self, after_read: impl FnOnce()) -> Result<Option<CoordinatedJournal>> {
        let bytes = self.read_coordinated_marker(JOURNAL_NAME, MAX_JOURNAL_BYTES)?;
        after_read();
        Self::coordinated_journal_dto(bytes)
    }

    #[cfg(feature = "desktop")]
    fn coordinated_journal_dto(bytes: Option<Vec<u8>>) -> Result<Option<CoordinatedJournal>> {
        let Some(bytes) = bytes else { return Ok(None); };
        let journal: UpdateJournal = serde_json::from_slice(&bytes)?;
        Self::validate_journal(&journal)?;
        Ok(Some(CoordinatedJournal { phase: journal.phase, package_id: journal.package_id,
            old_version: journal.old_version, old_fingerprint: journal.old_fingerprint,
            new_version: journal.new_version, new_fingerprint: journal.new_fingerprint, backup_name: journal.backup_name }))
    }

    /// Each mutation calls this under its own operation mutex. Caller DTOs are
    /// compared with the current disk markers and never serve as proof alone.
    #[cfg(feature = "desktop")]
    fn validate_coordinated_context(&self, authority: &PackageStorageAuthority, context: &CoordinatedRegistryContext)
        -> Result<(Vec<u8>, Option<Vec<u8>>, Option<UpdateJournal>)> {
        let outer = &context.outer;
        if context.package_id != outer.old.package_id || context.old_version != outer.old.version
            || hex(&context.old_fingerprint) != outer.old.fingerprint || context.new_version != outer.new.version
            || hex(&context.new_fingerprint) != outer.new.fingerprint || context.data.baseline != outer.private_before
            || context.data.transaction_id != outer.transaction_id
            || context.data.backup_name != format!(".ming-update-backup-{}", outer.transaction_id) {
            bail!("INSTALL_COORDINATED_CONTEXT");
        }
        let raw = self.read_coordinated_marker(crate::persistent_update::UPDATE_PENDING_NAME,
            crate::persistent_update::MAX_UPDATE_RECORD_BYTES as u64)?.context("INSTALL_COORDINATED_OUTER_MISSING")?;
        let inner = self.read_coordinated_marker(JOURNAL_NAME, MAX_JOURNAL_BYTES)?;
        crate::persistent_update::validate_transaction_snapshot(self, authority, &context.data_root, outer, &raw, inner.as_deref())?;
        let journal = inner.as_deref().map(serde_json::from_slice).transpose()?;
        Ok((raw, inner, journal))
    }

    #[cfg(feature = "desktop")]
    fn recheck_coordinated_markers(&self, outer: &[u8], inner: Option<&[u8]>) -> Result<()> {
        if self.read_coordinated_marker(crate::persistent_update::UPDATE_PENDING_NAME,
            crate::persistent_update::MAX_UPDATE_RECORD_BYTES as u64)?.as_deref() != Some(outer)
            || self.read_coordinated_marker(JOURNAL_NAME, MAX_JOURNAL_BYTES)?.as_deref() != inner {
            bail!("INSTALL_COORDINATED_MARKERS_CHANGED");
        }
        Ok(())
    }

    /// A record rename may succeed before directory fsync fails. Keep the cache
    /// aligned with the actual disk record even while returning that failure.
    #[cfg(feature = "desktop")]
    fn persist_coordinated_record(&self, context: &CoordinatedRegistryContext, old: bool) -> Result<()> {
        let mut records = self.lock_records()?;
        records.insert(context.package_id.clone(), Record { state: RecordState::Active,
            version: if old { context.old_version.clone() } else { context.new_version.clone() },
            fingerprint: if old { context.old_fingerprint } else { context.new_fingerprint } });
        if let Err(error) = self.persist_locked_with_transaction(&records, Some(&context.outer.transaction_id)) {
            match self.read_coordinated_marker(RECORD_NAME, MAX_RECORD_BYTES)
                .and_then(|bytes| parse_records(&bytes.context("INSTALL_RECORD_MISSING")?)) {
                Ok(disk) => *records = disk,
                Err(reload) => { // Every records accessor fails closed until this registry is reopened.
                    self.records_unusable.store(true, Ordering::Release);
                    return Err(error.context(format!("INSTALL_COORDINATED_RECORD_RELOAD: {reload:#}")));
                }
            }
            return Err(error);
        }
        Ok(())
    }

    #[cfg(feature = "desktop")]
    pub(crate) fn publish_coordinated_update(&self, package: &Package, authority: &PackageStorageAuthority,
        context: &CoordinatedRegistryContext) -> Result<()> {
        use crate::{app_data::CoordinatedCleanupPhase, persistent_update::UpdateDecision};
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        let (outer, inner, journal) = self.validate_coordinated_context(authority, context)?;
        if context.outer.decision != UpdateDecision::Prepared || journal.is_some() { bail!("INSTALL_COORDINATED_PUBLISH_PHASE"); }
        self.verify_publisher(package)?;
        let identity = &context.outer.new;
        if package.manifest.id != context.package_id || package.manifest.version != context.new_version
            || package.storage_fingerprint() != context.new_fingerprint
            || crate::persistent_install::sha256(&serde_json::to_vec(&package.manifest)?) != identity.manifest_sha256
            || crate::persistent_install::sha256(&package.bytes) != identity.wasm_sha256
            || crate::persistent_install::sha256(&serde_json::to_vec(&package.manifest.data_migration)?) != identity.migration_sha256
            || package.manifest.data_migration.as_ref().is_some_and(|migration| migration.from_version != context.old_version) {
            bail!("INSTALL_COORDINATED_CANDIDATE");
        }
        authority.validate_coordinated_cleanup(&context.data, &context.data.baseline, CoordinatedCleanupPhase::JournalCleared, false)?;
        self.recheck_coordinated_markers(&outer, inner.as_deref())?;
        let candidate = Record { version: context.new_version.clone(), fingerprint: context.new_fingerprint, state: RecordState::Active };
        let path = self.content_path(&context.package_id, &candidate);
        match fs::symlink_metadata(&path) {
            Ok(info) => { if !info.is_dir() || info.file_type().is_symlink() { bail!("INSTALL_CONTENT_DIRECTORY"); } self.verify_stored_package(&path, package)?; }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => self.stage_package_with_transaction(&path, package, Some(&context.outer.transaction_id))?,
            Err(error) => return Err(error.into()),
        }
        self.recheck_coordinated_markers(&outer, inner.as_deref())?;
        self.coordinated_checkpoint("candidate-staged")?;
        let mut journal = UpdateJournal { package_id: context.package_id.clone(), old_version: context.old_version.clone(),
            old_fingerprint: hex(&context.old_fingerprint), new_version: context.new_version.clone(), new_fingerprint: hex(&context.new_fingerprint),
            backup_name: context.data.backup_name.clone(), migration_declared: package.manifest.data_migration.is_some(), phase: UpdatePhase::Planned };
        self.persist_journal_with_transaction(&journal, Some(&context.outer.transaction_id))?;
        self.coordinated_checkpoint("inner-planned")?;
        self.validate_coordinated_context(authority, context)?;
        authority.prepare_coordinated_update(&context.data, package.manifest.data_migration.as_ref())?;
        journal.phase = UpdatePhase::DataSwapped;
        self.persist_journal_with_transaction(&journal, Some(&context.outer.transaction_id))?;
        self.coordinated_checkpoint("inner-data-swapped")?;
        self.validate_coordinated_context(authority, context)?;
        self.persist_coordinated_record(context, false)?;
        self.coordinated_checkpoint("record-published")?;
        journal.phase = UpdatePhase::RecordPublished;
        self.persist_journal_with_transaction(&journal, Some(&context.outer.transaction_id))?;
        self.coordinated_checkpoint("inner-record-published")
    }

    #[cfg(feature = "desktop")]
    pub(crate) fn rollback_coordinated_update(&self, authority: &PackageStorageAuthority, context: &CoordinatedRegistryContext) -> Result<()> {
        use crate::{app_data::{CoordinatedCleanupPhase, PrivateUpdatePhase}, persistent_update::UpdateDecision};
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        let (outer, inner, journal) = self.validate_coordinated_context(authority, context)?;
        if context.outer.decision != UpdateDecision::Prepared { bail!("INSTALL_COORDINATED_ROLLBACK_DECISION"); }
        let Some(mut journal) = journal else {
            authority.validate_coordinated_cleanup(&context.data, &context.data.baseline, CoordinatedCleanupPhase::JournalCleared, false)?;
            self.recheck_coordinated_markers(&outer, None)?;
            return self.clear_journal();
        };
        let phase = match journal.phase {
            UpdatePhase::Planned => PrivateUpdatePhase::BeforeSwap,
            UpdatePhase::DataSwapped | UpdatePhase::RecordPublished | UpdatePhase::StartupAttempted => PrivateUpdatePhase::SwappedUnconfirmed,
            UpdatePhase::RollingBack => PrivateUpdatePhase::RestoringOld,
            UpdatePhase::StartupConfirmed => bail!("INSTALL_COORDINATED_ROLLBACK_PHASE"),
        };
        authority.validate_coordinated_rollback(&context.data, phase)?;
        if journal.migration_declared && phase == PrivateUpdatePhase::SwappedUnconfirmed
            && authority.observe_coordinated_update(&context.data)?.files.is_none() { bail!("INSTALL_COORDINATED_MIGRATED_FILES_MISSING"); }
        self.recheck_coordinated_markers(&outer, inner.as_deref())?;
        journal.phase = UpdatePhase::RollingBack;
        self.persist_journal_with_transaction(&journal, Some(&context.outer.transaction_id))?;
        self.coordinated_checkpoint("inner-rolling-back")?;
        self.validate_coordinated_context(authority, context)?;
        self.persist_coordinated_record(context, true)?;
        self.validate_coordinated_context(authority, context)?;
        authority.rollback_coordinated_update(&context.data, PrivateUpdatePhase::RestoringOld)?;
        authority.validate_coordinated_cleanup(&context.data, &context.data.baseline, CoordinatedCleanupPhase::JournalCleared, false)?;
        self.validate_coordinated_context(authority, context)?;
        self.clear_journal()
    }

    #[cfg(feature = "desktop")]
    pub(crate) fn finish_coordinated_update(&self, authority: &PackageStorageAuthority, context: &CoordinatedRegistryContext,
        confirmed: &crate::app_data::PrivateUpdateBaseline) -> Result<()> {
        use crate::{app_data::{CoordinatedCleanupPhase, PrivateUpdatePhase}, persistent_update::UpdateDecision};
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        let (outer, inner, journal) = self.validate_coordinated_context(authority, context)?;
        if context.outer.decision != UpdateDecision::CommitDecided || context.outer.private_confirmed.as_ref() != Some(confirmed) {
            bail!("INSTALL_COORDINATED_FINISH_DECISION");
        }
        let required = context.data.baseline.files.is_some()
            || context.outer.new.migration_sha256 != crate::persistent_install::sha256(b"null");
        let phase = match journal.as_ref().map(|journal| journal.phase) {
            Some(UpdatePhase::StartupAttempted) => CoordinatedCleanupPhase::BeforeConfirmation,
            Some(UpdatePhase::StartupConfirmed) => CoordinatedCleanupPhase::Cleaning,
            None => CoordinatedCleanupPhase::JournalCleared,
            _ => bail!("INSTALL_COORDINATED_FINISH_PHASE"),
        };
        authority.validate_coordinated_cleanup(&context.data, confirmed, phase, required)?;
        self.recheck_coordinated_markers(&outer, inner.as_deref())?;
        let Some(mut journal) = journal else { return self.clear_journal(); };
        // A previous rename may have succeeded before its directory sync failed.
        // Re-establish durable confirmation on every retry before deleting backup.
        journal.phase = UpdatePhase::StartupConfirmed;
        self.persist_journal_with_transaction(&journal, Some(&context.outer.transaction_id))?;
        self.coordinated_checkpoint("inner-confirmed")?;
        self.validate_coordinated_context(authority, context)?;
        authority.finish_coordinated_update(&context.data, PrivateUpdatePhase::CommittedCleaning, confirmed)?;
        self.validate_coordinated_context(authority, context)?;
        self.clear_journal()
    }

    fn load_journal(&self) -> Result<Option<UpdateJournal>> {
        self.ensure_records_usable()?;
        let path = self.journal_path();
        let metadata = match fs::symlink_metadata(&path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error).context("INSTALL_UPDATE_JOURNAL_METADATA"),
        };
        if metadata.file_type().is_symlink() || !metadata.is_file() || metadata.len() > MAX_JOURNAL_BYTES {
            bail!("INSTALL_UPDATE_JOURNAL_FILE");
        }
        verify_private_owner(&metadata, "INSTALL_UPDATE_JOURNAL_PERMISSIONS")?;
        let bytes = crate::package::read_bounded(&path, MAX_JOURNAL_BYTES as usize, "INSTALL_UPDATE_JOURNAL")?;
        let journal: UpdateJournal = serde_json::from_slice(&bytes).context("INSTALL_UPDATE_JOURNAL_JSON")?;
        Self::validate_journal(&journal)?;
        Ok(Some(journal))
    }

    fn validate_journal(journal: &UpdateJournal) -> Result<()> {
        validate_package_record(&journal.package_id, &journal.old_version)?;
        validate_package_record(&journal.package_id, &journal.new_version)?;
        let _ = decode_hex(&journal.old_fingerprint)?;
        let _ = decode_hex(&journal.new_fingerprint)?;
        if !crate::package::version_is_newer(&journal.new_version, &journal.old_version) {
            bail!("INSTALL_UPDATE_JOURNAL_VERSION");
        }
        if !crate::app_data::valid_migration_backup_name(&journal.backup_name) {
            bail!("INSTALL_UPDATE_JOURNAL_BACKUP");
        }
        Ok(())
    }

    fn persist_journal(&self, journal: &UpdateJournal) -> Result<()> {
        self.persist_journal_with_transaction(journal, None)
    }

    fn persist_journal_with_transaction(&self, journal: &UpdateJournal, transaction: Option<&str>) -> Result<()> {
        let bytes = serde_json::to_vec(journal).context("INSTALL_UPDATE_JOURNAL_SERIALIZE")?;
        if bytes.len() > MAX_JOURNAL_BYTES as usize { bail!("INSTALL_UPDATE_JOURNAL_TOO_LARGE"); }
        let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let temporary = self.root.join(match transaction {
            Some(id) => format!(".ming-update-journal-{id}.tmp"),
            None => format!(".ming-update-journal.tmp-{}-{sequence}", std::process::id()),
        });
        let result = (|| -> Result<()> {
            let mut options = OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            { use std::os::unix::fs::OpenOptionsExt; options.mode(0o600).custom_flags(libc::O_NOFOLLOW); }
            #[cfg(test)]
            self.record_persist_checkpoint("registry-journal-create")?;
            let mut file = options.open(&temporary).context("INSTALL_UPDATE_JOURNAL_TEMP")?;
            #[cfg(test)]
            self.record_persist_checkpoint("registry-journal-write")?;
            file.write_all(&bytes).context("INSTALL_UPDATE_JOURNAL_WRITE")?;
            #[cfg(test)]
            self.record_persist_checkpoint("registry-journal-file-sync")?;
            file.sync_all().context("INSTALL_UPDATE_JOURNAL_FILE_SYNC")?;
            #[cfg(test)]
            self.record_persist_checkpoint("registry-journal-rename")?;
            fs::rename(&temporary, self.journal_path()).context("INSTALL_UPDATE_JOURNAL_RENAME")?;
            #[cfg(test)]
            self.record_persist_checkpoint("registry-journal-directory-sync")?;
            File::open(&self.root).context("INSTALL_UPDATE_ROOT_OPEN")?.sync_all().context("INSTALL_UPDATE_ROOT_SYNC")?;
            Ok(())
        })();
        if let Err(error) = result {
            // Coordinated recovery validates and removes its exact unpublished
            // object. Never unlink a pre-existing name after create_new fails.
            if transaction.is_some() { return Err(error); }
            return match fs::remove_file(&temporary) {
                Ok(()) => Err(error),
                Err(cleanup) if cleanup.kind() == std::io::ErrorKind::NotFound => Err(error),
                Err(cleanup) => Err(error.context(format!("INSTALL_UPDATE_JOURNAL_TEMP_CLEANUP: {cleanup}"))),
            };
        }
        result
    }

    fn clear_journal(&self) -> Result<()> {
        #[cfg(test)]
        self.record_persist_checkpoint("registry-journal-remove")?;
        match fs::remove_file(self.journal_path()) {
            Ok(()) => {},
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
            Err(error) => return Err(error).context("INSTALL_UPDATE_JOURNAL_REMOVE"),
        }
        #[cfg(test)]
        self.record_persist_checkpoint("registry-journal-remove-directory-sync")?;
        File::open(&self.root)?.sync_all().context("INSTALL_UPDATE_JOURNAL_REMOVE_SYNC")
    }

    fn persist_locked(&self, records: &BTreeMap<String, Record>) -> Result<()> {
        self.persist_locked_with_transaction(records, None)
    }

    fn persist_locked_with_transaction(&self, records: &BTreeMap<String, Record>, transaction: Option<&str>) -> Result<()> {
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
        self.persist_record_bytes_with_transaction(&bytes, transaction)
    }

    fn persist_record_bytes(&self, bytes: &[u8]) -> Result<()> {
        self.persist_record_bytes_with_transaction(bytes, None)
    }

    fn persist_record_bytes_with_transaction(&self, bytes: &[u8], transaction: Option<&str>) -> Result<()> {
        if bytes.len() as u64 > MAX_RECORD_BYTES { bail!("INSTALL_RECORD_TOO_LARGE"); }
        let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let temp_path = self.root.join(match transaction {
            Some(id) => format!(".ming-update-record-{id}.tmp"),
            None => format!(".ming-installed-packages.tmp-{}-{sequence}", std::process::id()),
        });
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
        }
        let result = (|| -> Result<()> {
            #[cfg(test)]
            self.record_persist_checkpoint("registry-record-create")?;
            let mut temp = options.open(&temp_path).context("INSTALL_RECORD_TEMP")?;
            #[cfg(test)]
            self.record_persist_checkpoint("registry-record-write")?;
            temp.write_all(&bytes).context("INSTALL_RECORD_WRITE")?;
            #[cfg(test)]
            self.record_persist_checkpoint("registry-record-file-sync")?;
            temp.sync_all().context("INSTALL_RECORD_SYNC")?;
            #[cfg(test)]
            self.record_persist_checkpoint("registry-record-rename")?;
            fs::rename(&temp_path, self.root.join(RECORD_NAME)).context("INSTALL_RECORD_RENAME")?;
            #[cfg(test)]
            self.record_persist_checkpoint("registry-record-directory-sync")?;
            File::open(&self.root).context("INSTALL_ROOT_OPEN")?.sync_all().context("INSTALL_ROOT_SYNC")?;
            Ok(())
        })();
        if let Err(error) = result {
            // Coordinated recovery validates and removes its exact unpublished
            // object. Never unlink a pre-existing name after create_new fails.
            if transaction.is_some() { return Err(error); }
            return match fs::remove_file(&temp_path) {
                Ok(()) => Err(error),
                Err(cleanup) if cleanup.kind() == std::io::ErrorKind::NotFound => Err(error),
                Err(cleanup) => Err(error.context(format!("INSTALL_RECORD_TEMP_CLEANUP: {cleanup}"))),
            };
        }
        result
    }

    #[cfg(test)]
    pub(crate) fn fail_next_record_persist_at(&self, stage: &'static str) {
        *self.record_persist_fault.lock().unwrap() = Some(stage);
    }

    #[cfg(test)]
    fn record_persist_checkpoint(&self, stage: &'static str) -> Result<()> {
        #[cfg(feature = "desktop")]
        crate::persistent_update::test_checkpoint(stage)?;
        let mut fault = self.record_persist_fault.lock().unwrap();
        if *fault == Some(stage) {
            *fault = None;
            bail!("injected {stage}");
        }
        Ok(())
    }

    fn content_path(&self, package_id: &str, record: &Record) -> PathBuf {
        self.root
            .join(CONTENT_DIR_NAME)
            .join(hex_bytes(package_id.as_bytes()))
            .join(format!("{}-{}", record.version, hex(&record.fingerprint)))
    }

    fn stage_package(&self, destination: &Path, package: &Package) -> Result<()> {
        self.stage_package_with_transaction(destination, package, None)
    }

    fn stage_package_with_transaction(&self, destination: &Path, package: &Package, transaction: Option<&str>) -> Result<()> {
        let package_root = self.root.join(CONTENT_DIR_NAME);
        ensure_private_directory(&package_root)?;
        let id_root = package_root.join(hex_bytes(package.manifest.id.as_bytes()));
        ensure_private_directory(&id_root)?;
        if destination.exists() { bail!("INSTALL_CONTENT_RACE"); }
        let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let temporary = id_root.join(match transaction {
            Some(id) => format!(".ming-update-candidate-{id}.tmp"),
            None => format!(".tmp-{}-{sequence}", std::process::id()),
        });
        if transaction.is_some() {
            use std::os::unix::fs::DirBuilderExt;
            fs::DirBuilder::new().mode(0o700).create(&temporary).context("INSTALL_CONTENT_TEMP_CREATE")?;
        } else { ensure_private_directory(&temporary)?; }
        let result = (|| -> Result<()> {
            self.coordinated_checkpoint("candidate-created")?;
            let manifest = serde_json::to_vec(&package.manifest).context("INSTALL_MANIFEST_SERIALIZE")?;
            write_private_file(&temporary.join("app.json"), &manifest)?;
            self.coordinated_checkpoint("candidate-manifest-written")?;
            write_private_file(&temporary.join(&package.manifest.entry), &package.bytes)?;
            self.coordinated_checkpoint("candidate-wasm-written")?;
            File::open(&temporary).context("INSTALL_CONTENT_OPEN")?.sync_all().context("INSTALL_CONTENT_SYNC")?;
            self.coordinated_checkpoint("candidate-directory-synced")?;
            fs::rename(&temporary, destination).context("INSTALL_CONTENT_RENAME")?;
            self.coordinated_checkpoint("candidate-renamed")?;
            File::open(&id_root).context("INSTALL_CONTENT_PARENT_OPEN")?.sync_all().context("INSTALL_CONTENT_PARENT_SYNC")?;
            self.coordinated_checkpoint("candidate-parent-synced")?;
            File::open(&package_root).context("INSTALL_CONTENT_ROOT_OPEN")?.sync_all().context("INSTALL_CONTENT_ROOT_SYNC")?;
            Ok(())
        })();
        if let Err(error) = result {
            // Coordinated recovery validates and removes its exact unpublished
            // object. Never unlink a pre-existing name after create_new fails.
            if transaction.is_some() { return Err(error); }
            return match fs::remove_dir_all(&temporary) {
                Ok(()) => Err(error),
                Err(cleanup) if cleanup.kind() == std::io::ErrorKind::NotFound => Err(error),
                Err(cleanup) => Err(error.context(format!("INSTALL_CONTENT_TEMP_CLEANUP: {cleanup}"))),
            };
        }
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
    parse_records(&bytes)
}

fn parse_records(bytes: &[u8]) -> Result<BTreeMap<String, Record>> {
    if bytes.len() as u64 > MAX_RECORD_BYTES { bail!("INSTALL_RECORD_TOO_LARGE"); }
    let text = std::str::from_utf8(bytes).context("INSTALL_RECORD_UTF8")?;
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
        Some(existing) if !crate::package::version_is_newer(&candidate.version, &existing.version) => {
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

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;
    use crate::app_data::{AppDataRoot, AppDataError};
    use std::{fs, os::unix::fs::PermissionsExt, sync::Arc};

    fn pending_update_fixture() -> (tempfile::TempDir, PathBuf, Arc<PackageStorageAuthority>, [u8; 32]) {
        let temp = tempfile::tempdir().unwrap();
        fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
        let source = temp.path().join("source");
        let install_root = temp.path().join("install");
        let data_root = temp.path().join("data");
        for path in [&source, &install_root, &data_root] {
            fs::create_dir(path).unwrap();
            fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
        }
        fs::write(source.join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        let manifest = source.join("app.json");
        fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.installed","version":"1.0.0","sdkVersion":6,"entry":"main.wasm","privateStorage":true}"#).unwrap();
        {
            let registry = PackageInstallRegistry::open(&install_root).unwrap();
            assert_eq!(registry.install_from_manifest(&manifest).unwrap(), InstallState::Installed);
        }
        let authority = Arc::new(PackageStorageAuthority::new(AppDataRoot::open(&data_root).unwrap()));
        authority.authorize("dev.example.installed").unwrap();
        authority.file_session("dev.example.installed").unwrap().write_file("counter", b"42").unwrap();
        authority.revoke("dev.example.installed").unwrap();

        fs::write(source.join("main.wasm"), b"\0asm\x01\0\0\0\x00").unwrap();
        fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.installed","version":"1.1.0","sdkVersion":6,"entry":"main.wasm","privateStorage":true,"dataMigration":{"fromVersion":"1.0.0","steps":[{"op":"ensureDir","path":"state"},{"op":"renameFile","from":"counter","to":"state/counter"}]}}"#).unwrap();
        {
            let registry = PackageInstallRegistry::open(&install_root).unwrap();
            assert_eq!(registry.update_from_manifest(&manifest, &authority).unwrap(), InstallState::Updated);
        }
        let registry = PackageInstallRegistry::open(&install_root).unwrap();
        let active = registry.active_manifest_path("dev.example.installed").unwrap();
        let fingerprint = load_package(&active).unwrap().storage_fingerprint();
        drop(registry);
        (temp, install_root, authority, fingerprint)
    }

    #[cfg(not(feature = "desktop"))]
    #[test]
    fn no_desktop_startup_rejects_coordinated_outer_before_writing() {
        let (_temp,root,_authority,fingerprint)=pending_update_fixture();
        let registry=PackageInstallRegistry::open(&root).unwrap();
        fs::write(root.join(COORDINATED_UPDATE_PENDING_NAME),b"blocked").unwrap();
        let before=fs::read(registry.journal_path()).unwrap();
        assert!(registry.begin_update_startup("dev.example.installed",fingerprint).is_err());
        assert_eq!(fs::read(registry.journal_path()).unwrap(),before);
    }

    #[test]
    fn pending_update_reopens_and_preserves_startup_retry_after_record_publish() {
        let (_temp, install_root, authority, new_fingerprint) = pending_update_fixture();
        let registry = PackageInstallRegistry::open(&install_root).unwrap();
        assert!(registry.has_pending_update().unwrap());
        drop(registry);

        let registry = PackageInstallRegistry::open(&install_root).unwrap();
        assert!(!registry.recover_pending_update(&authority).unwrap());
        assert!(registry.has_pending_update().unwrap());
        let active = registry.active_manifest_path("dev.example.installed").unwrap();
        assert_eq!(load_package(&active).unwrap().manifest.version, "1.1.0");
        assert!(registry.begin_update_startup("dev.example.installed", new_fingerprint).unwrap());
        drop(registry);

        let registry = PackageInstallRegistry::open(&install_root).unwrap();
        assert!(registry.recover_pending_update(&authority).unwrap());
        assert!(!registry.has_pending_update().unwrap());
        let active = registry.active_manifest_path("dev.example.installed").unwrap();
        assert_eq!(load_package(&active).unwrap().manifest.version, "1.0.0");
        authority.authorize("dev.example.installed").unwrap();
        let session = authority.file_session("dev.example.installed").unwrap();
        assert_eq!(session.read_file("counter").unwrap(), b"42");
        assert_eq!(session.read_file("state/counter"), Err(AppDataError::NotFound));
    }

    #[test]
    fn startup_attempted_update_reopens_and_rolls_back_after_process_restart() {
        let (_temp, install_root, authority, new_fingerprint) = pending_update_fixture();
        let registry = PackageInstallRegistry::open(&install_root).unwrap();
        assert!(registry.begin_update_startup("dev.example.installed", new_fingerprint).unwrap());
        drop(registry);

        let registry = PackageInstallRegistry::open(&install_root).unwrap();
        assert!(registry.recover_pending_update(&authority).unwrap());
        assert!(!registry.has_pending_update().unwrap());
        let active = registry.active_manifest_path("dev.example.installed").unwrap();
        assert_eq!(load_package(&active).unwrap().manifest.version, "1.0.0");
        authority.authorize("dev.example.installed").unwrap();
        assert_eq!(authority.file_session("dev.example.installed").unwrap().read_file("counter").unwrap(), b"42");
    }

    #[test]
    fn startup_confirmation_commits_migration_and_clears_journal() {
        let (_temp, install_root, authority, new_fingerprint) = pending_update_fixture();
        let registry = PackageInstallRegistry::open(&install_root).unwrap();
        assert!(registry.begin_update_startup("dev.example.installed", new_fingerprint).unwrap());
        assert!(registry.confirm_pending_update(&authority, "dev.example.installed", "1.1.0", new_fingerprint).unwrap());
        assert!(!registry.has_pending_update().unwrap());
        assert!(!registry.recover_pending_update(&authority).unwrap());
        let active = registry.active_manifest_path("dev.example.installed").unwrap();
        assert_eq!(load_package(&active).unwrap().manifest.version, "1.1.0");
        authority.authorize("dev.example.installed").unwrap();
        let session = authority.file_session("dev.example.installed").unwrap();
        assert_eq!(session.read_file("state/counter").unwrap(), b"42");
        assert_eq!(session.read_file("counter"), Err(AppDataError::NotFound));
    }

    #[test]
    fn loaded_install_commits_confirmed_bytes_after_source_replacement() {
        let temp = tempfile::tempdir().unwrap();
        fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
        let source = temp.path().join("source");
        let install_root = temp.path().join("install");
        for path in [&source, &install_root] {
            fs::create_dir(path).unwrap();
            fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
        }
        let manifest = source.join("app.json");
        fs::write(source.join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.confirmed","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        let confirmed = load_package(&manifest).unwrap();
        let registry = PackageInstallRegistry::open(&install_root).unwrap();
        registry.verify_package_source(&confirmed).unwrap();

        fs::write(source.join("main.wasm"), b"\0asm\x01\0\0\0\x00").unwrap();
        fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.replacement","version":"2.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        assert_eq!(registry.install_loaded_package(&confirmed).unwrap(), InstallState::Installed);

        let active = registry.active_manifest_path("dev.example.confirmed").unwrap();
        assert_eq!(load_package(&active).unwrap(), confirmed);
        assert!(registry.active_manifest_path("dev.example.replacement").is_err());
    }

    #[test]
    fn loaded_update_commits_confirmed_bytes_after_source_replacement() {
        let temp = tempfile::tempdir().unwrap();
        fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
        let source = temp.path().join("source");
        let install_root = temp.path().join("install");
        let data_root = temp.path().join("data");
        for path in [&source, &install_root, &data_root] {
            fs::create_dir(path).unwrap();
            fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
        }
        let manifest = source.join("app.json");
        fs::write(source.join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.confirmed","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        let registry = PackageInstallRegistry::open(&install_root).unwrap();
        registry.install_from_manifest(&manifest).unwrap();
        let authority = PackageStorageAuthority::new(AppDataRoot::open(&data_root).unwrap());
        fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.confirmed","version":"1.1.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        let confirmed = load_package(&manifest).unwrap();
        registry.verify_package_source(&confirmed).unwrap();

        fs::write(source.join("main.wasm"), b"\0asm\x01\0\0\0\x00").unwrap();
        fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.confirmed","version":"2.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        assert_eq!(registry.update_loaded_package(&confirmed, &authority).unwrap(), InstallState::Updated);

        let active = registry.active_manifest_path("dev.example.confirmed").unwrap();
        assert_eq!(load_package(&active).unwrap(), confirmed);
        assert_eq!(registry.pending_update().unwrap().unwrap().new_version, "1.1.0");
    }

    #[test]
    fn catalog_failure_rollback_restores_an_absent_install_record() {
        let temp = tempfile::tempdir().unwrap();
        fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
        let source = temp.path().join("source");
        let install_root = temp.path().join("install");
        fs::create_dir(&source).unwrap();
        fs::create_dir(&install_root).unwrap();
        for path in [&source, &install_root] {
            fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
        }
        fs::write(source.join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        let manifest = source.join("app.json");
        fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.rollback","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        let registry = PackageInstallRegistry::open(&install_root).unwrap();
        let rollback = registry.prepare_install_rollback("dev.example.rollback").unwrap();
        let package = load_package(&manifest).unwrap();
        assert_eq!(registry.install_from_manifest(&manifest).unwrap(), InstallState::Installed);
        registry.rollback_install(rollback, &package.manifest.version, package.storage_fingerprint()).unwrap();
        assert!(registry.active_manifest_path("dev.example.rollback").is_err());
        let records = fs::read_to_string(install_root.join(RECORD_NAME)).unwrap();
        assert!(!records.contains("dev.example.rollback"));

        registry.install_from_manifest(&manifest).unwrap();
        registry.uninstall("dev.example.rollback").unwrap();
        let rollback = registry.prepare_install_rollback("dev.example.rollback").unwrap();
        registry.install_from_manifest(&manifest).unwrap();
        registry.rollback_install(rollback, &package.manifest.version, package.storage_fingerprint()).unwrap();
        assert!(registry.is_removed("dev.example.rollback").unwrap());
    }
}

#[cfg(all(test, target_os="linux", feature="desktop"))]
mod coordinated_tests {
    use super::*;
    use crate::{app_data::{AppDataRoot,CoordinatedPrivateUpdate},persistent_update::{self,UpdateDecision,UpdateLifecycleProof}};
    use base64::{Engine as _,engine::general_purpose::STANDARD};
    use ed25519_dalek::{Signer,SigningKey};
    use std::{collections::BTreeMap,os::unix::fs::{MetadataExt,PermissionsExt},sync::Arc};
    fn write(path:&Path,bytes:&[u8]) {fs::write(path,bytes).unwrap();fs::set_permissions(path,fs::Permissions::from_mode(0o600)).unwrap();}
    fn fixture(files:bool,migration:bool)->(tempfile::TempDir,PackageInstallRegistry,Arc<PackageStorageAuthority>,Package,CoordinatedRegistryContext) {
        let (temp,opened,old)=crate::persistent_install::transaction_tests::fixture();
        crate::persistent_install::install_new(&opened.registry,&old).unwrap();
        let data_root=temp.path().join("data");fs::create_dir(&data_root).unwrap();fs::set_permissions(&data_root,fs::Permissions::from_mode(0o700)).unwrap();
        let authority=Arc::new(PackageStorageAuthority::open_default_private(AppDataRoot::open(&data_root).unwrap()).unwrap());
        if files {let session=authority.default_private_session_for_package(&old).unwrap();session.write_file("one",b"old-one").unwrap();session.write_file("two",b"old-two").unwrap();}
        let old_fingerprint=old.storage_fingerprint();let old_version=old.manifest.version.clone();
        let mut new=old;new.manifest.version="2.0.0".into();
        if migration {new.manifest.data_migration=Some(crate::package::DataMigration {from_version:old_version.clone(),steps:vec![crate::package::MigrationStep::EnsureDir {path:"state".into()}]});}
        new.manifest.publisher_signature=None;new.manifest.publisher_signature=Some(STANDARD.encode(SigningKey::from_bytes(&[7;32]).sign(&new.signature_message("mingos-dev-2026").unwrap()).to_bytes()));
        let outer=persistent_update::prepare_record(&opened.registry,&authority,&data_root,2,&new,"task4-test").unwrap();
        write(&opened.registry.root_path().join(persistent_update::UPDATE_PENDING_NAME),&serde_json::to_vec(&outer).unwrap());
        let context=CoordinatedRegistryContext {package_id:new.manifest.id.clone(),old_version,old_fingerprint,
            new_version:new.manifest.version.clone(),new_fingerprint:new.storage_fingerprint(),
            data:CoordinatedPrivateUpdate {transaction_id:outer.transaction_id.clone(),baseline:outer.private_before.clone(),backup_name:format!(".ming-update-backup-{}",outer.transaction_id)},
            outer,data_root};
        (temp,opened.registry,authority,new,context)
    }
    fn namespace(context:&CoordinatedRegistryContext)->PathBuf {
        context.data_root.join(crate::app_data::package_directory_name(&context.package_id))
    }
    fn commit(registry:&PackageInstallRegistry,authority:&PackageStorageAuthority,context:&mut CoordinatedRegistryContext) {
        context.outer.private_confirmed=Some(authority.observe_coordinated_update(&context.data).unwrap());
        context.outer.confirmation=Some(UpdateLifecycleProof {app:context.outer.app,package:context.outer.new.clone(),generation:1,window:1,worker_pid:1,input_ready:true,
            presented_source_sequence:1,completed:true,result_accepted:true,native_exit_ok:true,reaped:true});
        context.outer.decision=UpdateDecision::CommitDecided;
        write(&registry.root.join(persistent_update::UPDATE_PENDING_NAME),&serde_json::to_vec(&context.outer).unwrap());
    }
    fn tree(root:&Path)->BTreeMap<PathBuf,(u32,u64,Vec<u8>)> {
        fn walk(root:&Path,path:&Path,out:&mut BTreeMap<PathBuf,(u32,u64,Vec<u8>)>) {
            let info=fs::symlink_metadata(path).unwrap();let bytes=if info.file_type().is_symlink(){fs::read_link(path).unwrap().as_os_str().as_encoded_bytes().to_vec()}else if info.is_file(){fs::read(path).unwrap()}else{Vec::new()};
            out.insert(path.strip_prefix(root).unwrap().into(),(info.mode(),info.nlink(),bytes));if info.is_dir(){for entry in fs::read_dir(path).unwrap(){walk(root,&entry.unwrap().path(),out);}}
        }
        let mut out=BTreeMap::new();walk(root,root,&mut out);out
    }
    #[test]
    fn coordinated_publish_does_not_run_legacy_recovery() {
        let (_temp,registry,authority,new,context)=fixture(true,false);
        registry.persist_journal(&UpdateJournal {package_id:context.package_id.clone(),old_version:context.old_version.clone(),old_fingerprint:hex(&context.old_fingerprint),
            new_version:context.new_version.clone(),new_fingerprint:hex(&context.new_fingerprint),backup_name:".ming-update-backup-legacy".into(),migration_declared:false,phase:UpdatePhase::RollingBack}).unwrap();
        let before=tree(&registry.root);let data=tree(&context.data_root);
        assert!(registry.publish_coordinated_update(&new,&authority,&context).is_err());
        assert_eq!(tree(&registry.root),before);assert_eq!(tree(&context.data_root),data);
    }
    #[test]
    fn coordinated_missing_files_uses_bound_baseline() {
        let (_temp,registry,authority,new,context)=fixture(false,false);
        registry.publish_coordinated_update(&new,&authority,&context).unwrap();
        let journal = registry.inspect_coordinated_journal().unwrap().unwrap();
        assert_eq!(journal.phase, UpdatePhase::RecordPublished);
        assert_eq!(journal.package_id, context.package_id);
        assert_eq!(journal.old_version, context.old_version);
        assert_eq!(journal.old_fingerprint, hex(&context.old_fingerprint));
        assert_eq!(journal.new_version, context.new_version);
        assert_eq!(journal.new_fingerprint, hex(&context.new_fingerprint));
        assert_eq!(journal.backup_name, context.data.backup_name);
        assert_eq!(authority.observe_coordinated_update(&context.data).unwrap(),context.data.baseline);
        assert!(!namespace(&context).exists());
        registry.rollback_coordinated_update(&authority,&context).unwrap();
        assert!(registry.inspect_coordinated_journal().unwrap().is_none());assert!(!namespace(&context).exists());
        assert_eq!(load_package(&registry.active_manifest_path(&context.package_id).unwrap()).unwrap().manifest.version,context.old_version);
    }
    #[test]
    fn coordinated_finish_resumes_partial_backup_cleanup() {
        let (_temp,registry,authority,new,mut context)=fixture(true,false);
        registry.publish_coordinated_update(&new,&authority,&context).unwrap();registry.begin_update_startup(&context.package_id,context.new_fingerprint).unwrap();commit(&registry,&authority,&mut context);
        let mut journal=registry.load_journal().unwrap().unwrap();journal.phase=UpdatePhase::StartupConfirmed;registry.persist_journal(&journal).unwrap();
        fs::remove_file(namespace(&context).join(&context.data.backup_name).join("one")).unwrap();
        registry.finish_coordinated_update(&authority,&context,context.outer.private_confirmed.as_ref().unwrap()).unwrap();
        assert!(registry.inspect_coordinated_journal().unwrap().is_none());assert!(!namespace(&context).join(&context.data.backup_name).exists());
    }
    #[test]
    fn coordinated_finish_rejects_cleared_journal_with_backup() {
        let (_temp,registry,authority,new,mut context)=fixture(true,false);
        registry.publish_coordinated_update(&new,&authority,&context).unwrap();registry.begin_update_startup(&context.package_id,context.new_fingerprint).unwrap();commit(&registry,&authority,&mut context);registry.clear_journal().unwrap();
        let before=tree(&registry.root);let data=tree(&context.data_root);
        assert!(registry.finish_coordinated_update(&authority,&context,context.outer.private_confirmed.as_ref().unwrap()).is_err());
        assert_eq!(tree(&registry.root),before);assert_eq!(tree(&context.data_root),data);
    }
    #[test]
    fn coordinated_rollback_validates_original_phase_before_persisting_rolling_back() {
        let (_temp,registry,authority,new,context)=fixture(true,false);registry.publish_coordinated_update(&new,&authority,&context).unwrap();
        let mut journal=registry.load_journal().unwrap().unwrap();journal.phase=UpdatePhase::DataSwapped;registry.persist_journal(&journal).unwrap();
        fs::remove_dir_all(namespace(&context).join(&context.data.backup_name)).unwrap();let before=tree(&registry.root);let data=tree(&context.data_root);
        assert!(registry.rollback_coordinated_update(&authority,&context).is_err());assert_eq!(tree(&registry.root),before);assert_eq!(tree(&context.data_root),data);
    }
    #[test]
    fn coordinated_finish_attempted_requires_complete_backup_before_advancing_phase() {
        let (_temp,registry,authority,new,mut context)=fixture(true,false);registry.publish_coordinated_update(&new,&authority,&context).unwrap();
        registry.begin_update_startup(&context.package_id,context.new_fingerprint).unwrap();commit(&registry,&authority,&mut context);
        fs::remove_dir_all(namespace(&context).join(&context.data.backup_name)).unwrap();let before=tree(&registry.root);let data=tree(&context.data_root);
        assert!(registry.finish_coordinated_update(&authority,&context,context.outer.private_confirmed.as_ref().unwrap()).is_err());assert_eq!(tree(&registry.root),before);assert_eq!(tree(&context.data_root),data);
    }
    #[test]
    fn coordinated_missing_migrated_tree_cannot_be_reclassified_as_original_absence() {
        let (_temp,registry,authority,new,context)=fixture(false,true);registry.publish_coordinated_update(&new,&authority,&context).unwrap();
        fs::remove_dir_all(namespace(&context).join("files")).unwrap();let before=tree(&registry.root);let data=tree(&context.data_root);
        assert!(registry.rollback_coordinated_update(&authority,&context).is_err());assert_eq!(tree(&registry.root),before);assert_eq!(tree(&context.data_root),data);
    }
    #[test]
    fn coordinated_mutations_reject_stale_outer_and_raw_public_policy_drift() {
        for stale_outer in [false,true] {
            let (_temp,registry,authority,new,context)=fixture(false,false);
            if stale_outer {let mut changed=context.outer.clone();changed.transaction_id="different".into();write(&registry.root.join(persistent_update::UPDATE_PENDING_NAME),&serde_json::to_vec(&changed).unwrap());}
            else {write(&context.data_root.join("system-public-storage-policy-v1/slot-1"),b"ming-public-storage-policy-v1\n");}
            let before=tree(&registry.root);let data=tree(&context.data_root);assert!(registry.publish_coordinated_update(&new,&authority,&context).is_err());assert_eq!(tree(&registry.root),before);assert_eq!(tree(&context.data_root),data);
        }
    }

    #[test]
    fn coordinated_journal_faults_leave_recoverable_disk_phases() {
        for stage in ["registry-journal-create", "registry-journal-write", "registry-journal-file-sync",
            "registry-journal-rename", "registry-journal-directory-sync"] {
            let (_temp, registry, authority, new, context) = fixture(true, false);
            registry.fail_next_record_persist_at(stage);
            assert!(registry.publish_coordinated_update(&new, &authority, &context).is_err(), "{stage}");
            assert_eq!(registry.active_records().unwrap()[&context.package_id].0, context.old_version);
            assert_eq!(authority.observe_coordinated_update(&context.data).unwrap(), context.data.baseline);
            let phase = registry.inspect_coordinated_journal().unwrap().map(|journal| journal.phase);
            assert_eq!(phase, (stage == "registry-journal-directory-sync").then_some(UpdatePhase::Planned), "{stage}");
            let root = registry.root.clone(); drop(registry);
            let reopened = PackageInstallRegistry::open_verified_existing(&root).unwrap();
            if !stage.ends_with("-create") && !stage.ends_with("-directory-sync") {
                let before=tree(&root);let data=tree(&context.data_root);
                assert!(reopened.rollback_coordinated_update(&authority,&context).is_err());
                assert_eq!(tree(&root),before);assert_eq!(tree(&context.data_root),data);
            }
            persistent_update::recover_loaded_update(&reopened,&authority,&context.data_root,&context.outer.transaction_id).unwrap();
            assert!(reopened.inspect_coordinated_journal().unwrap().is_none());
            assert_eq!(authority.observe_coordinated_update(&context.data).unwrap(), context.data.baseline);
        }
    }

    #[test]
    fn coordinated_record_faults_align_cache_with_disk_and_reopen_rollback() {
        for stage in ["registry-record-create", "registry-record-write", "registry-record-file-sync",
            "registry-record-rename", "registry-record-directory-sync"] {
            let (_temp, registry, authority, new, context) = fixture(true, false);
            registry.fail_next_record_persist_at(stage);
            assert!(registry.publish_coordinated_update(&new, &authority, &context).is_err(), "{stage}");
            assert_eq!(registry.inspect_coordinated_journal().unwrap().unwrap().phase, UpdatePhase::DataSwapped);
            let expected = if stage == "registry-record-directory-sync" { &context.new_version } else { &context.old_version };
            assert_eq!(&registry.active_records().unwrap()[&context.package_id].0, expected, "{stage}");
            registry.verify_records_snapshot(&fs::read(registry.root.join(RECORD_NAME)).unwrap()).unwrap();
            let root = registry.root.clone(); drop(registry);
            let reopened = PackageInstallRegistry::open_verified_existing(&root).unwrap();
            if !stage.ends_with("-create") && !stage.ends_with("-directory-sync") {
                let before=tree(&root);let data=tree(&context.data_root);
                assert!(reopened.rollback_coordinated_update(&authority,&context).is_err());
                assert_eq!(tree(&root),before);assert_eq!(tree(&context.data_root),data);
            }
            persistent_update::recover_loaded_update(&reopened,&authority,&context.data_root,&context.outer.transaction_id).unwrap();
            assert_eq!(reopened.active_records().unwrap()[&context.package_id].0, context.old_version);
            assert_eq!(authority.observe_coordinated_update(&context.data).unwrap(), context.data.baseline);
        }
    }

    #[test]
    fn coordinated_rollback_record_rename_sync_failure_reopens_with_old_record_and_retained_backup() {
        let (_temp, registry, authority, new, context) = fixture(true, false);
        registry.publish_coordinated_update(&new, &authority, &context).unwrap();
        registry.fail_next_record_persist_at("registry-record-directory-sync");
        assert!(registry.rollback_coordinated_update(&authority, &context).is_err());
        assert_eq!(registry.active_records().unwrap()[&context.package_id].0, context.old_version);
        registry.verify_records_snapshot(&fs::read(registry.root.join(RECORD_NAME)).unwrap()).unwrap();
        assert_eq!(registry.inspect_coordinated_journal().unwrap().unwrap().phase, UpdatePhase::RollingBack);
        assert!(namespace(&context).join(&context.data.backup_name).is_dir());
        let root = registry.root.clone(); drop(registry);
        let reopened = PackageInstallRegistry::open_verified_existing(&root).unwrap();
        reopened.rollback_coordinated_update(&authority, &context).unwrap();
        assert!(reopened.inspect_coordinated_journal().unwrap().is_none());
        assert_eq!(authority.observe_coordinated_update(&context.data).unwrap(), context.data.baseline);
    }

    #[test]
    fn coordinated_journal_remove_faults_retry_sync_even_after_unlink() {
        for committed in [false, true] {
            for stage in ["registry-journal-remove", "registry-journal-remove-directory-sync"] {
                let (_temp, registry, authority, new, mut context) = fixture(true, false);
                registry.publish_coordinated_update(&new, &authority, &context).unwrap();
                if committed { registry.begin_update_startup(&context.package_id, context.new_fingerprint).unwrap(); commit(&registry, &authority, &mut context); }
                let finish = || if committed { registry.finish_coordinated_update(&authority, &context, context.outer.private_confirmed.as_ref().unwrap()) }
                    else { registry.rollback_coordinated_update(&authority, &context) };
                registry.fail_next_record_persist_at(stage);
                assert!(finish().is_err(), "committed {committed}, {stage}");
                assert_eq!(registry.journal_path().exists(), stage == "registry-journal-remove");
                // A second root-sync failure proves that None does not return early.
                registry.fail_next_record_persist_at("registry-journal-remove-directory-sync");
                assert!(finish().is_err());
                assert!(!registry.journal_path().exists());
                finish().unwrap();
                assert!(registry.inspect_coordinated_journal().unwrap().is_none());
            }
        }
    }

    #[test]
    fn coordinated_record_reload_failure_seals_all_cache_and_journal_entries() {
        let (_temp, registry, authority, new, context) = fixture(true, false);
        // A real malformed disk record makes readback fail after the injected
        // create failure, before any record rename can repair that corruption.
        write(&registry.root.join(RECORD_NAME), b"invalid records");
        registry.fail_next_record_persist_at("registry-record-create");
        assert!(registry.persist_coordinated_record(&context, false).is_err());
        let before = tree(&registry.root); let data = tree(&context.data_root);
        assert!(registry.active_records().is_err());
        assert!(registry.active_manifest_path(&context.package_id).is_err());
        assert!(registry.verify_package(&new).is_err());
        assert!(registry.verify_package_identity(&new).is_err());
        assert!(registry.is_removed(&context.package_id).is_err());
        assert!(registry.prepare_install_rollback(&context.package_id).is_err());
        assert!(registry.restore_records_snapshot(HEADER.as_bytes()).is_err());
        assert!(registry.install_loaded_package(&new).is_err());
        assert!(registry.uninstall(&context.package_id).is_err());
        assert!(registry.publish_coordinated_update(&new, &authority, &context).is_err());
        assert!(registry.rollback_coordinated_update(&authority, &context).is_err());
        assert!(registry.begin_update_startup(&context.package_id, context.new_fingerprint).is_err());
        assert!(registry.inspect_coordinated_journal().is_err());
        assert!(registry.pending_update().is_err());
        assert!(registry.recover_pending_update(&authority).is_err());
        assert_eq!(tree(&registry.root), before); assert_eq!(tree(&context.data_root), data);
    }

    #[test]
    fn coordinated_inspect_validates_captured_bytes_when_path_is_replaced() {
        let (_temp, registry, _authority, _new, context) = fixture(false, false);
        let valid = UpdateJournal { package_id: context.package_id.clone(), old_version: context.old_version.clone(),
            old_fingerprint: hex(&context.old_fingerprint), new_version: context.new_version.clone(),
            new_fingerprint: hex(&context.new_fingerprint), backup_name: context.data.backup_name.clone(),
            migration_declared: false, phase: UpdatePhase::Planned };
        let mut invalid = valid.clone(); invalid.new_version = invalid.old_version.clone();
        registry.persist_journal(&invalid).unwrap();
        let _operation = registry.operation.lock().unwrap();
        assert!(registry.inspect_coordinated_journal_after_read(|| { registry.persist_journal(&valid).unwrap(); }).is_err());
    }

    #[test]
    fn coordinated_mutations_reject_replaced_inner_and_unsafe_objects_without_writes() {
        for invalid in ["identity", "backup", "migration", "hardlink", "mode"] {
            let (_temp, registry, authority, new, context) = fixture(true, false);
            registry.publish_coordinated_update(&new, &authority, &context).unwrap();
            let mut journal = registry.load_journal().unwrap().unwrap();
            match invalid {
                "identity" => { journal.old_fingerprint = "1".repeat(64); registry.persist_journal(&journal).unwrap(); },
                "backup" => { journal.backup_name = ".ming-update-backup-other".into(); registry.persist_journal(&journal).unwrap(); },
                "migration" => { journal.migration_declared = true; registry.persist_journal(&journal).unwrap(); },
                "hardlink" => fs::hard_link(registry.journal_path(), registry.root.join("journal-link")).unwrap(),
                "mode" => fs::set_permissions(registry.journal_path(), fs::Permissions::from_mode(0o400)).unwrap(),
                _ => unreachable!(),
            }
            let before = tree(&registry.root); let data = tree(&context.data_root);
            assert!(registry.rollback_coordinated_update(&authority, &context).is_err(), "{invalid}");
            assert_eq!(tree(&registry.root), before); assert_eq!(tree(&context.data_root), data);
        }
    }

    #[test]
    fn coordinated_finish_never_confirms_disappeared_migrated_files() {
        for phase in [Some(UpdatePhase::StartupAttempted), Some(UpdatePhase::StartupConfirmed), None] {
            let (_temp, registry, authority, new, mut context) = fixture(false, true);
            registry.publish_coordinated_update(&new, &authority, &context).unwrap();
            registry.begin_update_startup(&context.package_id, context.new_fingerprint).unwrap();
            fs::remove_dir_all(namespace(&context).join("files")).unwrap();
            // Even a confirmation recording the impossible absence is rejected.
            commit(&registry, &authority, &mut context);
            match phase {
                Some(phase) => { let mut journal = registry.load_journal().unwrap().unwrap(); journal.phase = phase; registry.persist_journal(&journal).unwrap(); },
                None => registry.clear_journal().unwrap(),
            }
            let before = tree(&registry.root); let data = tree(&context.data_root);
            assert!(registry.finish_coordinated_update(&authority, &context, context.outer.private_confirmed.as_ref().unwrap()).is_err());
            assert_eq!(tree(&registry.root), before); assert_eq!(tree(&context.data_root), data);
        }
    }

    #[test]
    fn coordinated_confirmed_retry_syncs_journal_before_any_backup_cleanup() {
        let (_temp, registry, authority, new, mut context) = fixture(true, false);
        registry.publish_coordinated_update(&new, &authority, &context).unwrap();
        registry.begin_update_startup(&context.package_id, context.new_fingerprint).unwrap();
        commit(&registry, &authority, &mut context);
        let data = tree(&context.data_root);
        for _ in 0..2 {
            registry.fail_next_record_persist_at("registry-journal-directory-sync");
            assert!(registry.finish_coordinated_update(&authority, &context, context.outer.private_confirmed.as_ref().unwrap()).is_err());
            assert_eq!(registry.inspect_coordinated_journal().unwrap().unwrap().phase, UpdatePhase::StartupConfirmed);
            assert_eq!(tree(&context.data_root), data);
        }
        registry.finish_coordinated_update(&authority, &context, context.outer.private_confirmed.as_ref().unwrap()).unwrap();
        assert!(registry.inspect_coordinated_journal().unwrap().is_none());
    }

    #[test]
    fn coordinated_publish_uses_loaded_candidate_after_source_replacement() {
        let (temp, registry, authority, new, context) = fixture(true, false);
        let source = temp.path().join("candidate-source"); fs::create_dir(&source).unwrap();
        write(&source.join("app.json"), &serde_json::to_vec(&new.manifest).unwrap());
        write(&source.join(&new.manifest.entry), &new.bytes);
        let loaded = load_package(&source.join("app.json")).unwrap();
        write(&source.join("app.json"), b"{}"); write(&source.join(&new.manifest.entry), b"replaced");
        registry.publish_coordinated_update(&loaded, &authority, &context).unwrap();
        let active = load_package(&registry.active_manifest_path(&context.package_id).unwrap()).unwrap();
        assert_eq!(active.bytes, new.bytes); assert_eq!(active.storage_fingerprint(), context.new_fingerprint);
    }
}
