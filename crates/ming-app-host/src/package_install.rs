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
    sync::{Mutex, atomic::{AtomicU64, Ordering}},
};
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

const HEADER: &str = "ming-installed-packages-v1\n";
const RECORD_NAME: &str = "installed-packages-v1";
const LOCK_NAME: &str = ".ming-installed-packages.lock";
const CONTENT_DIR_NAME: &str = "packages";
const JOURNAL_NAME: &str = "update-journal-v1";
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
enum UpdatePhase {
    Planned,
    DataSwapped,
    RecordPublished,
    StartupAttempted,
    StartupConfirmed,
    RollingBack,
}

pub struct PackageInstallRegistry {
    root: PathBuf,
    operation: Mutex<()>,
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
        Ok(Self { root: root.to_path_buf(), operation: Mutex::new(()), records: Mutex::new(records), lock, publisher_trust })
    }

    pub fn install_package(&self, package: &Package) -> Result<InstallState> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
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
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        if self.has_pending_update()? { bail!("INSTALL_UPDATE_PENDING"); }
        let package = load_package(manifest_path).context("INSTALL_PACKAGE_LOAD")?;
        self.install_loaded_package_locked(&package)
    }

    /// Commits the exact package bytes approved by the desktop confirmation.
    /// The source path is not reopened after that identity check.
    pub(crate) fn install_loaded_package(&self, package: &Package) -> Result<InstallState> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
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
        let mut records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
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
        let records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
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
        let mut records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
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

    /// Publishes an update with a retained snapshot of the old private files.
    /// The backup survives until a matching desktop startup is confirmed.
    pub fn update_from_manifest(&self, manifest_path: &Path, authority: &PackageStorageAuthority) -> Result<InstallState> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.recover_pending_update_locked(authority)?;
        if self.has_pending_update()? { bail!("INSTALL_UPDATE_PENDING"); }
        let package = load_package(manifest_path).context("INSTALL_PACKAGE_LOAD")?;
        self.update_loaded_package_locked(&package, authority)
    }

    /// Updates from the exact package checked against the presented prompt.
    pub(crate) fn update_loaded_package(&self, package: &Package, authority: &PackageStorageAuthority) -> Result<InstallState> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        self.recover_pending_update_locked(authority)?;
        if self.has_pending_update()? { bail!("INSTALL_UPDATE_PENDING"); }
        self.update_loaded_package_locked(package, authority)
    }

    fn update_loaded_package_locked(&self, package: &Package, authority: &PackageStorageAuthority) -> Result<InstallState> {
        self.verify_publisher(package)?;
        validate_package_record(&package.manifest.id, &package.manifest.version)?;
        let mut records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
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
        self.recover_pending_update_locked(authority)
    }

    fn recover_pending_update_locked(&self, authority: &PackageStorageAuthority) -> Result<bool> {
        let Some(journal) = self.load_journal()? else { return Ok(false); };
        let current = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?
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
        journal.phase = UpdatePhase::StartupAttempted;
        self.persist_journal(&journal)?;
        Ok(true)
    }

    /// Called only by the trusted desktop after validating the complete receipt.
    pub(crate) fn confirm_pending_update(
        &self, authority: &PackageStorageAuthority, package_id: &str, version: &str, fingerprint: [u8; 32],
    ) -> Result<bool> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        let Some(mut journal) = self.load_journal()? else { return Ok(false); };
        if journal.phase != UpdatePhase::StartupAttempted || journal.package_id != package_id
            || journal.new_version != version || decode_hex(&journal.new_fingerprint)? != fingerprint {
            bail!("INSTALL_UPDATE_CONFIRMATION_MISMATCH");
        }
        let current = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?
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
        self.rollback_pending_update_locked(authority)
    }

    fn rollback_pending_update_locked(&self, authority: &PackageStorageAuthority) -> Result<bool> {
        let Some(mut journal) = self.load_journal()? else { return Ok(false); };
        if journal.phase == UpdatePhase::StartupConfirmed { bail!("INSTALL_UPDATE_ALREADY_CONFIRMED"); }
        let mut records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
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

    /// Reports the persisted lifecycle state without reloading package content.
    /// A missing record is not considered removed.
    pub fn is_removed(&self, package_id: &str) -> Result<bool> {
        if !valid_package_id(package_id) { bail!("INSTALL_PACKAGE_ID"); }
        let records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        Ok(records.get(package_id).is_some_and(|record| record.state == RecordState::Removed))
    }

    pub fn uninstall(&self, package_id: &str) -> Result<bool> {
        let _operation = self.operation.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
        if self.has_pending_update()? { bail!("INSTALL_UPDATE_PENDING"); }
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
        self.verify_package_identity(package)?;
        let records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
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
        let records = self.records.lock().map_err(|_| anyhow::anyhow!("INSTALL_ROOT_POISONED"))?;
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

    fn load_journal(&self) -> Result<Option<UpdateJournal>> {
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
        Ok(Some(journal))
    }

    fn persist_journal(&self, journal: &UpdateJournal) -> Result<()> {
        let bytes = serde_json::to_vec(journal).context("INSTALL_UPDATE_JOURNAL_SERIALIZE")?;
        if bytes.len() > MAX_JOURNAL_BYTES as usize { bail!("INSTALL_UPDATE_JOURNAL_TOO_LARGE"); }
        let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let temporary = self.root.join(format!(".ming-update-journal.tmp-{}-{sequence}", std::process::id()));
        let result = (|| -> Result<()> {
            write_private_file(&temporary, &bytes).context("INSTALL_UPDATE_JOURNAL_TEMP")?;
            fs::rename(&temporary, self.journal_path()).context("INSTALL_UPDATE_JOURNAL_RENAME")?;
            File::open(&self.root).context("INSTALL_UPDATE_ROOT_OPEN")?.sync_all().context("INSTALL_UPDATE_ROOT_SYNC")?;
            Ok(())
        })();
        if result.is_err() { let _ = fs::remove_file(&temporary); }
        result
    }

    fn clear_journal(&self) -> Result<()> {
        match fs::remove_file(self.journal_path()) {
            Ok(()) => {},
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
            Err(error) => return Err(error).context("INSTALL_UPDATE_JOURNAL_REMOVE"),
        }
        File::open(&self.root)?.sync_all().context("INSTALL_UPDATE_JOURNAL_REMOVE_SYNC")
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
