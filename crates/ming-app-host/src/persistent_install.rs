//! Strict, parent-owned persistent installation state. Opening never seeds or repairs.
//! The caller authorizes first initialization using its boot-role and disk-marker contract.
use crate::{desktop_catalog::load_catalog_with_install_registry, package::{Package, load_package},
    package_install::PackageInstallRegistry, publisher::PublisherTrustStore};
use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, ffi::CString, fs::{self, File, OpenOptions}, io::{Read, Write},
    os::unix::{ffi::OsStrExt, fs::{DirBuilderExt, MetadataExt, OpenOptionsExt}},
    path::{Component, Path, PathBuf}};

pub const CATALOG_NAME: &str = "catalog.json";
pub const BOOTSTRAP_NAME: &str = "desktop-install-state-v1.json";
pub const PENDING_NAME: &str = "desktop-install-pending-v1.json";
const REGISTRY_NAME: &str = "installed-packages-v1";
const TRUST_NAME: &str = "trusted-publishers-v1";
const LOCKS: [&str; 2] = [".ming-installed-packages.lock", ".ming-publisher-keyring.lock"];
const MAX_FILES: usize = 256;
const MAX_TOTAL: usize = 64 * 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ContentFile { pub path: String, pub sha256: String }
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ContentApp {
    pub app: u16, pub package_id: String, pub version: String, pub fingerprint: String,
    pub manifest_path: String, pub manifest_sha256: String, pub wasm_path: String, pub wasm_sha256: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ContentSnapshot {
    pub catalog_sha256: String, pub registry_sha256: String, pub trust_sha256: String,
    pub bootstrap_sha256: String, pub apps: Vec<ContentApp>, pub files: Vec<ContentFile>,
}
#[derive(Debug)]
pub struct OpenedInstall { pub registry: PackageInstallRegistry, pub content: ContentSnapshot }
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Bootstrap {
    schema_version: u32, profile: String, initial_package_id: String, initial_version: String,
    initial_fingerprint: String, initial_wasm_sha256: String, initial_catalog_sha256: String,
    trust_sha256: String,
}

pub fn sha256(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }
fn fingerprint(bytes: [u8; 32]) -> String { bytes.iter().map(|b| format!("{b:02x}")).collect() }
fn metadata(path: &Path, directory: bool) -> Result<fs::Metadata> {
    let info = fs::symlink_metadata(path).with_context(|| format!("PERSISTENT_METADATA {}", path.display()))?;
    ensure!(!info.file_type().is_symlink() && if directory { info.is_dir() } else { info.is_file() }, "PERSISTENT_FILE_TYPE");
    ensure!(info.uid() == unsafe { libc::geteuid() } && info.mode() & 0o7777 == if directory { 0o700 } else { 0o600 }, "PERSISTENT_PERMISSIONS");
    ensure!(directory || info.nlink() == 1, "PERSISTENT_HARDLINK");
    Ok(info)
}
fn check_path(root: &Path) -> Result<()> {
    ensure!(root.is_absolute() && root.file_name().is_some(), "PERSISTENT_ROOT_ABSOLUTE");
    // Reject aliases including symlink ancestors, rather than normalizing an unsafe path.
    let mut cursor = PathBuf::new();
    for component in root.components() {
        ensure!(matches!(component, Component::RootDir | Component::Normal(_)), "PERSISTENT_ROOT_COMPONENT");
        cursor.push(component);
        if cursor != root {
            let info = fs::symlink_metadata(&cursor).context("PERSISTENT_ROOT_ANCESTOR")?;
            ensure!(info.is_dir() && !info.file_type().is_symlink(), "PERSISTENT_ROOT_ANCESTOR");
        }
    }
    ensure!(!root.as_os_str().as_bytes().split(|b| *b == b'/').any(|p| p == b"." || p == b".."), "PERSISTENT_ROOT_COMPONENT");
    Ok(())
}
fn read_file(path: &Path, limit: usize) -> Result<Vec<u8>> {
    let before = metadata(path, false)?;
    ensure!(before.len() <= limit as u64, "PERSISTENT_FILE_LIMIT");
    let mut file = OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK).open(path)?;
    let opened = file.metadata()?;
    ensure!(opened.dev() == before.dev() && opened.ino() == before.ino(), "PERSISTENT_FILE_CHANGED");
    let mut bytes = Vec::new();
    Read::by_ref(&mut file).take(limit as u64 + 1).read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= limit && bytes.len() as u64 == opened.len(), "PERSISTENT_FILE_CHANGED");
    Ok(bytes)
}
fn write_file(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut file = OpenOptions::new().write(true).create_new(true).mode(0o600)
        .custom_flags(libc::O_NOFOLLOW).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}
fn absent(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
        Ok(_) => bail!("PERSISTENT_UNFINISHED_OR_EXISTING {}", path.display()),
    }
}
fn relative(root: &Path, path: &Path) -> Result<String> {
    let value = path.strip_prefix(root).context("PERSISTENT_CONTENT_ESCAPE")?.to_str().context("PERSISTENT_CONTENT_UTF8")?;
    ensure!(!value.is_empty() && value.len() <= 768, "PERSISTENT_CONTENT_PATH");
    Ok(value.into())
}
fn collect(root: &Path, directory: &Path, files: &mut BTreeMap<String, Vec<u8>>, total: &mut usize, pending: Option<&[u8]>) -> Result<()> {
    metadata(directory, true)?;
    ensure!(directory.strip_prefix(root)?.components().count() <= 3, "PERSISTENT_CONTENT_DEPTH");
    let children = fs::read_dir(directory)?.take(MAX_FILES + 1).collect::<std::io::Result<Vec<_>>>()?;
    ensure!(children.len() <= MAX_FILES, "PERSISTENT_DIRECTORY_LIMIT");
    if directory != root {
        ensure!(!children.is_empty(), "PERSISTENT_EMPTY_DIRECTORY");
        if directory.strip_prefix(root)?.components().count() == 3 {
            ensure!(children.len() == 2 && children.iter().all(|entry| entry.file_name() == "app.json" || entry.file_name() == "main.wasm"), "PERSISTENT_PACKAGE_FILES");
        }
    }
    for entry in children {
        let path = entry.path();
        let rel = relative(root, &path)?;
        let info = fs::symlink_metadata(&path)?;
        if info.is_dir() && !info.file_type().is_symlink() {
            let parts: Vec<_> = rel.split('/').collect();
            ensure!(parts[0] == "packages" && parts.len() <= 3, "PERSISTENT_UNKNOWN_DIRECTORY");
            if parts.len() >= 2 { ensure!(!parts[1].is_empty() && parts[1].len() <= 512 && parts[1].len() % 2 == 0 && parts[1].bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)), "PERSISTENT_PACKAGE_DIRECTORY"); }
            if parts.len() == 3 { ensure!(parts[2].len() <= 160 && parts[2].bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c) || c == b'.' || c == b'-'), "PERSISTENT_VERSION_DIRECTORY"); }
            collect(root, &path, files, total, pending)?;
        } else {
            let parts: Vec<_> = rel.split('/').collect();
            if rel == PENDING_NAME {
                if let Some(expected) = pending {
                    ensure!(read_file(&path, 4096)? == expected, "PERSISTENT_PENDING_CHANGED");
                    continue;
                }
            }
            let limit = if parts.len() == 1 {
                ensure!([CATALOG_NAME, BOOTSTRAP_NAME, REGISTRY_NAME, TRUST_NAME].contains(&rel.as_str()) || LOCKS.contains(&rel.as_str()), "PERSISTENT_UNKNOWN_FILE");
                if LOCKS.contains(&rel.as_str()) { metadata(&path, false)?; continue; }
                if rel == CATALOG_NAME { 16 * 1024 } else if rel == BOOTSTRAP_NAME { 4096 } else { 64 * 1024 }
            } else {
                ensure!(parts.len() == 4 && parts[0] == "packages" && ["app.json", "main.wasm"].contains(&parts[3]), "PERSISTENT_UNKNOWN_CONTENT");
                if parts[3] == "main.wasm" { crate::package::MAX_MODULE_BYTES } else { 64 * 1024 }
            };
            ensure!(files.len() < MAX_FILES, "PERSISTENT_FILE_COUNT");
            let bytes = read_file(&path, limit)?;
            *total += bytes.len();
            ensure!(*total <= MAX_TOTAL, "PERSISTENT_CONTENT_LIMIT");
            files.insert(rel, bytes);
        }
    }
    Ok(())
}

/// Validates all controlled content while borrowing the already-held registry lock.
/// No content is created, repaired, chmodded, or removed by this operation.
pub fn snapshot(registry: &PackageInstallRegistry) -> Result<ContentSnapshot> {
    snapshot_inner(registry, None)
}

// Only the transaction that constructed these exact pending bytes may inspect
// content while that marker exists. Public reopen always rejects every marker.
fn snapshot_while_pending(registry: &PackageInstallRegistry, pending: &Pending, bytes: &[u8], committed: bool) -> Result<ContentSnapshot> {
    ensure!(serde_json::to_vec(pending)? == bytes, "PERSISTENT_PENDING_TRANSACTION");
    let content = snapshot_inner(registry, Some(bytes))?;
    if committed {
        ensure!(content.catalog_sha256 == pending.new_catalog_sha256
            && content.apps.iter().any(|app| app.package_id == pending.package_id && app.version == pending.version && app.fingerprint == pending.fingerprint), "PERSISTENT_COMMIT_READBACK");
    } else {
        ensure!(content.registry_sha256 == pending.old_registry_sha256 && content.catalog_sha256 == pending.old_catalog_sha256, "PERSISTENT_ROLLBACK_READBACK");
    }
    Ok(content)
}

fn snapshot_inner(registry: &PackageInstallRegistry, pending: Option<&[u8]>) -> Result<ContentSnapshot> {
    let root = registry.root_path();
    check_path(root)?;
    metadata(root, true)?;
    if let Some(expected) = pending {
        ensure!(read_file(&root.join(PENDING_NAME), 4096)? == expected, "PERSISTENT_PENDING_CHANGED");
    } else { absent(&root.join(PENDING_NAME))?; }
    absent(&root.join("update-journal-v1"))?;
    let mut files = BTreeMap::new();
    collect(root, root, &mut files, &mut 0, pending)?;
    let required = |name: &str| -> Result<&Vec<u8>> { files.get(name).with_context(|| format!("PERSISTENT_REQUIRED {name}")) };
    // The receipt hashes these bytes; validate the same bytes rather than
    // accidentally combining a changed disk registry with cached records.
    registry.verify_records_snapshot(required(REGISTRY_NAME)?)?;
    let bootstrap: Bootstrap = serde_json::from_slice(required(BOOTSTRAP_NAME)?).context("PERSISTENT_BOOTSTRAP")?;
    ensure!(bootstrap.schema_version == 1 && bootstrap.profile == "persistent-install", "PERSISTENT_BOOTSTRAP_SCHEMA");
    for digest in [&bootstrap.initial_fingerprint, &bootstrap.initial_wasm_sha256, &bootstrap.initial_catalog_sha256, &bootstrap.trust_sha256] {
        ensure!(digest.len() == 64 && digest.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)), "PERSISTENT_BOOTSTRAP_HASH");
    }
    ensure!(bootstrap.trust_sha256 == sha256(required(TRUST_NAME)?), "PERSISTENT_BOOTSTRAP_TRUST");
    // Typed serde decoding rejects duplicate catalog fields; v1/v2 are never persistent input.
    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct PersistentCatalog { schema_version: u32, apps: Vec<PersistentApp>, autostart: Vec<u16> }
    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct PersistentApp { id: u16, #[serde(rename = "title")] _title: String, package_id: String }
    let disk: PersistentCatalog = serde_json::from_slice(required(CATALOG_NAME)?).context("PERSISTENT_CATALOG")?;
    ensure!(disk.schema_version == 3 && disk.autostart == [1] && (1..=4).contains(&disk.apps.len()), "PERSISTENT_CATALOG_SCHEMA");
    ensure!(disk.apps.iter().any(|app| app.id == 1 && app.package_id == bootstrap.initial_package_id), "PERSISTENT_INITIAL_CATALOG");
    // Existing loader enforces unique UI/package IDs, titles and exact installed identity.
    let (catalog, _) = load_catalog_with_install_registry(&root.join(CATALOG_NAME), registry)?;
    let active = registry.active_records()?;
    ensure!(active.len() == catalog.apps.len() && active.len() == disk.apps.len(), "PERSISTENT_ACTIVE_CATALOG_SET");
    let initial = active.get(&bootstrap.initial_package_id).context("PERSISTENT_INITIAL_PACKAGE")?;
    ensure!(initial.0 == bootstrap.initial_version && fingerprint(initial.1) == bootstrap.initial_fingerprint, "PERSISTENT_INITIAL_IDENTITY");
    let mut apps = Vec::new();
    for entry in catalog.apps {
        let (version, digest) = active.get(&entry.package_id).context("PERSISTENT_ACTIVE_CATALOG_SET")?;
        let manifest_path = relative(root, &entry.manifest_path)?;
        let manifest = required(&manifest_path)?;
        let package = load_package(&entry.manifest_path)?;
        ensure!(package.manifest.sdk_version == 6 && package.manifest.entry == "main.wasm", "PERSISTENT_PACKAGE_SCHEMA");
        let wasm_path = relative(root, &entry.manifest_path.with_file_name("main.wasm"))?;
        let wasm = required(&wasm_path)?;
        ensure!(&package.bytes == wasm && read_file(&entry.manifest_path, 64 * 1024)? == *manifest, "PERSISTENT_FILE_CHANGED");
        if entry.id == 1 { ensure!(sha256(wasm) == bootstrap.initial_wasm_sha256, "PERSISTENT_INITIAL_WASM"); }
        apps.push(ContentApp { app: entry.id, package_id: entry.package_id, version: version.clone(),
            fingerprint: fingerprint(*digest), manifest_path, manifest_sha256: sha256(manifest),
            wasm_path, wasm_sha256: sha256(wasm) });
    }
    apps.sort_by(|a, b| a.package_id.cmp(&b.package_id));
    for (path, bytes) in &files {
        ensure!(read_file(&root.join(path), bytes.len())? == *bytes, "PERSISTENT_FILE_CHANGED");
    }
    if let Some(expected) = pending {
        ensure!(read_file(&root.join(PENDING_NAME), 4096)? == expected, "PERSISTENT_PENDING_CHANGED");
    }
    Ok(ContentSnapshot { catalog_sha256: sha256(required(CATALOG_NAME)?), registry_sha256: sha256(required(REGISTRY_NAME)?),
        trust_sha256: sha256(required(TRUST_NAME)?), bootstrap_sha256: sha256(required(BOOTSTRAP_NAME)?), apps,
        files: files.iter().map(|(path, bytes)| ContentFile { path: path.clone(), sha256: sha256(bytes) }).collect() })
}

pub fn open_existing(root: &Path) -> Result<OpenedInstall> {
    check_path(root)?;
    metadata(root, true)?;
    absent(&root.join(PENDING_NAME))?;
    // Reject incomplete/unsafe content before the existing registry opener can create its locks.
    let mut files = BTreeMap::new();
    collect(root, root, &mut files, &mut 0, None)?;
    for name in [CATALOG_NAME, BOOTSTRAP_NAME, REGISTRY_NAME, TRUST_NAME] {
        ensure!(files.contains_key(name), "PERSISTENT_REQUIRED {name}");
    }
    let registry = PackageInstallRegistry::open_verified(root)?;
    let content = snapshot(&registry)?;
    Ok(OpenedInstall { registry, content })
}

/// Caller must have established the authorized first boot and fresh disk marker.
/// Existing roots (including empty roots) and abandoned staging directories are never repaired.
pub fn initialize_first(root: &Path, initial: &Package, key_id: &str, public_key: &[u8; 32], catalog: &[u8]) -> Result<ContentSnapshot> {
    initialize(root, initial, key_id, public_key, catalog, |_| Ok(()))
}
fn initialize(root: &Path, initial: &Package, key_id: &str, public_key: &[u8; 32], catalog: &[u8], mut checkpoint: impl FnMut(&str) -> Result<()>) -> Result<ContentSnapshot> {
    check_path(root)?;
    absent(root)?;
    let parent = root.parent().context("PERSISTENT_PARENT")?;
    metadata(parent, true)?;
    let name = root.file_name().and_then(|name| name.to_str()).context("PERSISTENT_ROOT_NAME")?;
    let prefix = format!(".{name}.staging-");
    ensure!(!fs::read_dir(parent)?.any(|entry| entry.map(|entry| entry.file_name().to_string_lossy().starts_with(&prefix)).unwrap_or(true)), "PERSISTENT_STAGING_EXISTS");
    ensure!(catalog.len() <= 16 * 1024 && initial.manifest.sdk_version == 6 && initial.manifest.entry == "main.wasm", "PERSISTENT_INITIAL_INPUT");
    let mut random = [0; 16];
    getrandom::fill(&mut random).map_err(|error| anyhow::anyhow!("PERSISTENT_RANDOM {error}"))?;
    let staging = parent.join(format!("{prefix}{}", random.iter().map(|b| format!("{b:02x}")).collect::<String>()));
    fs::DirBuilder::new().mode(0o700).create(&staging)?;
    checkpoint("created-staging")?;
    PublisherTrustStore::provision(&staging, key_id, public_key)?;
    let registry = PackageInstallRegistry::open_verified(&staging)?;
    registry.install_loaded_package(initial)?;
    drop(registry);
    write_file(&staging.join(CATALOG_NAME), catalog)?;
    let bootstrap = Bootstrap { schema_version: 1, profile: "persistent-install".into(),
        initial_package_id: initial.manifest.id.clone(), initial_version: initial.manifest.version.clone(),
        initial_fingerprint: fingerprint(initial.storage_fingerprint()), initial_wasm_sha256: sha256(&initial.bytes),
        initial_catalog_sha256: sha256(catalog), trust_sha256: sha256(&read_file(&staging.join(TRUST_NAME), 64 * 1024)?) };
    write_file(&staging.join(BOOTSTRAP_NAME), &serde_json::to_vec(&bootstrap)?)?;
    let opened = open_existing(&staging)?;
    ensure!(opened.content.apps.len() == 1 && opened.content.apps[0].app == 1, "PERSISTENT_INITIAL_ONLY");
    for file in &opened.content.files { File::open(staging.join(&file.path))?.sync_all()?; checkpoint("file-sync")?; }
    sync_directories(&staging, &mut checkpoint)?;
    drop(opened);
    checkpoint("before-publish")?;
    let source = CString::new(staging.as_os_str().as_bytes())?;
    let target = CString::new(root.as_os_str().as_bytes())?;
    let code = unsafe { libc::renameat2(libc::AT_FDCWD, source.as_ptr(), libc::AT_FDCWD, target.as_ptr(), libc::RENAME_NOREPLACE) };
    ensure!(code == 0, "PERSISTENT_PUBLISH {}", std::io::Error::last_os_error());
    checkpoint("published")?;
    File::open(parent)?.sync_all()?;
    checkpoint("parent-sync")?;
    Ok(open_existing(root)?.content)
}
fn sync_directories(root: &Path, checkpoint: &mut impl FnMut(&str) -> Result<()>) -> Result<()> {
    for entry in fs::read_dir(root)? {
        let path = entry?.path();
        if fs::symlink_metadata(&path)?.is_dir() { sync_directories(&path, checkpoint)?; }
    }
    File::open(root)?.sync_all()?;
    checkpoint("directory-sync")
}

#[cfg(test)]
mod initialization_tests {
    use super::*;
    use base64::{Engine as _, engine::general_purpose::STANDARD};
    use ed25519_dalek::{Signer, SigningKey};
    use std::os::unix::fs::PermissionsExt;
    fn input() -> (Package, SigningKey, Vec<u8>) {
        let key=SigningKey::from_bytes(&[7;32]);
        let mut package=Package {manifest:crate::package::Manifest {schema_version:1,id:"dev.ming.text-entry-demo".into(),version:"0.1.0".into(),sdk_version:6,
            entry:"main.wasm".into(),private_storage:false,public_storage:false,data_migration:None,publisher_key_id:Some("mingos-dev-2026".into()),publisher_signature:None},bytes:b"\0asm\x01\0\0\0".to_vec()};
        package.manifest.publisher_signature=Some(STANDARD.encode(key.sign(&package.signature_message("mingos-dev-2026").unwrap()).to_bytes()));
        let catalog=br#"{"schemaVersion":3,"apps":[{"id":1,"title":"TextLab","packageId":"dev.ming.text-entry-demo"}],"autostart":[1]}"#.to_vec();
        (package,key,catalog)
    }
    #[test]
    fn atomic_publication_never_clobbers_a_racing_destination() {
        let temp=tempfile::tempdir().unwrap();fs::set_permissions(temp.path(),fs::Permissions::from_mode(0o700)).unwrap();
        let root=temp.path().join("install-root");let(package,key,catalog)=input();
        let result=initialize(&root,&package,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,|point|{
            if point=="before-publish" {fs::create_dir(&root)?;fs::write(root.join("sentinel"),b"keep")?;}Ok(())
        });
        assert!(result.is_err());assert_eq!(fs::read(root.join("sentinel")).unwrap(),b"keep");
        assert!(!root.join(CATALOG_NAME).exists());
        assert!(fs::read_dir(temp.path()).unwrap().any(|entry|entry.unwrap().file_name().to_string_lossy().starts_with(".install-root.staging-")));
    }
    #[test]
    fn initialization_failure_boundaries_are_retained_and_never_replayed() {
        for failure in ["created-staging","file-sync","directory-sync","before-publish","published","parent-sync"] {
            let temp=tempfile::tempdir().unwrap();fs::set_permissions(temp.path(),fs::Permissions::from_mode(0o700)).unwrap();
            let root=temp.path().join("install-root");let(package,key,catalog)=input();
            let result=initialize(&root,&package,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,|point|{
                if point==failure {bail!("injected boundary {point}");}Ok(())
            });
            assert!(result.is_err(),"{failure}");
            assert_eq!(root.exists(),matches!(failure,"published"|"parent-sync"),"{failure}");
            assert!(initialize_first(&root,&package,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog).is_err(),"must not replay {failure}");
            if root.exists(){assert_eq!(open_existing(&root).unwrap().content.apps.len(),1);}
        }
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct InstallCommit {
    pub schema_version: u32,
    pub status: &'static str,
    pub package_id: String,
    pub version: String,
    pub fingerprint: String,
    pub registry_root: PathBuf,
    pub catalog_path: PathBuf,
    pub content: ContentSnapshot,
    pub persistent_block_confirmed: bool,
}
#[derive(Debug)]
pub(crate) struct CommittedInstall {
    pub catalog: crate::desktop_catalog::Catalog,
    pub receipt: InstallCommit,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct InstallFailure {
    pub schema_version: u32,
    pub status: &'static str,
    pub stage: &'static str,
    pub persistent_block_confirmed: bool,
    pub compensation_attempted: bool,
    pub compensation_stage: Option<&'static str>,
    #[serde(skip)]
    pub error: String,
}
impl InstallFailure {
    pub fn fatal(&self) -> bool { self.status != "rolled-back" }
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Pending {
    schema_version: u32,
    package_id: String,
    version: String,
    fingerprint: String,
    old_registry_sha256: String,
    old_catalog_sha256: String,
    new_catalog_sha256: String,
}
#[derive(Debug)]
struct PhaseError { stage: &'static str, error: anyhow::Error }
fn at<T>(hook: &mut impl FnMut(&'static str) -> Result<()>, stage: &'static str, operation: impl FnOnce() -> Result<T>) -> std::result::Result<T, PhaseError> {
    hook(stage).and_then(|_| operation()).map_err(|error| PhaseError { stage, error })
}
fn failed(stage: &'static str, error: impl std::fmt::Display, fatal: bool) -> InstallFailure {
    InstallFailure { schema_version: 1, status: if fatal { "commit-uncertain" } else { "rolled-back" }, stage,
        persistent_block_confirmed: false, compensation_attempted: false, compensation_stage: None, error: error.to_string() }
}
fn replace_catalog(root: &Path, bytes: &[u8], restore: bool, hook: &mut impl FnMut(&'static str) -> Result<()>) -> std::result::Result<(), PhaseError> {
    let phases = if restore {
        ["catalog-restore-create", "catalog-restore-write", "catalog-restore-file-sync", "catalog-restore-rename", "catalog-restore-directory-sync"]
    } else {
        ["catalog-create", "catalog-write", "catalog-file-sync", "catalog-rename", "catalog-directory-sync"]
    };
    let mut random = [0u8; 16];
    getrandom::fill(&mut random).map_err(|error| PhaseError { stage: phases[0], error: anyhow::anyhow!("PERSISTENT_RANDOM {error}") })?;
    let suffix: String = random.iter().map(|byte| format!("{byte:02x}")).collect();
    let temporary = root.join(format!(".ming-persistent-catalog-{suffix}"));
    let mut created = false;
    let result = (|| {
        let mut file = at(hook, phases[0], || {
            let file = OpenOptions::new().write(true).create_new(true).mode(0o600).custom_flags(libc::O_NOFOLLOW).open(&temporary)?;
            created = true; Ok(file)
        })?;
        at(hook, phases[1], || { file.write_all(bytes)?; Ok(()) })?;
        at(hook, phases[2], || { file.sync_all()?; Ok(()) })?;
        at(hook, phases[3], || { fs::rename(&temporary, root.join(CATALOG_NAME))?; Ok(()) })?;
        created = false;
        at(hook, phases[4], || { File::open(root)?.sync_all()?; Ok(()) })
    })();
    if created {
        if let Err(error) = fs::remove_file(&temporary) {
            return Err(PhaseError { stage: "catalog-temporary-cleanup", error: error.into() });
        }
    }
    result
}
#[derive(Clone, Copy)]
struct MarkerIdentity { device: u64, inode: u64 }
impl MarkerIdentity {
    fn of(file: &File) -> Result<Self> {
        let info = file.metadata()?;
        Ok(Self { device: info.dev(), inode: info.ino() })
    }
    fn verify(self, root: &Path, bytes: &[u8]) -> Result<()> {
        let path = root.join(PENDING_NAME);
        let before = metadata(&path, false)?;
        ensure!(before.dev() == self.device && before.ino() == self.inode, "PERSISTENT_PENDING_REPLACED");
        ensure!(read_file(&path, 4096)? == bytes, "PERSISTENT_PENDING_CHANGED");
        let after = metadata(&path, false)?;
        ensure!(after.dev() == self.device && after.ino() == self.inode, "PERSISTENT_PENDING_REPLACED");
        Ok(())
    }
}
fn create_pending(root: &Path, bytes: &[u8], hook: &mut impl FnMut(&'static str) -> Result<()>) -> std::result::Result<MarkerIdentity, PhaseError> {
    let mut file = at(hook, "pending-create", || Ok(OpenOptions::new().write(true).create_new(true).mode(0o600).custom_flags(libc::O_NOFOLLOW).open(root.join(PENDING_NAME))?))?;
    let identity = at(hook, "pending-identity", || MarkerIdentity::of(&file))?;
    at(hook, "pending-write", || { file.write_all(bytes)?; Ok(()) })?;
    at(hook, "pending-file-sync", || { file.sync_all()?; Ok(()) })?;
    at(hook, "pending-directory-sync", || { File::open(root)?.sync_all()?; Ok(()) })?;
    at(hook, "pending-readback", || identity.verify(root, bytes))?;
    Ok(identity)
}
// One bounded compensation attempt. A failed or absent namespace check is not
// evidence that a marker remains durable. Never overwrite a pre-existing file.
fn block_after_failure(root: &Path, bytes: &[u8], error: PhaseError, original_durable: Option<MarkerIdentity>,
    hook: &mut impl FnMut(&'static str) -> Result<()>) -> InstallFailure {
    let mut failure = failed(error.stage, &error.error, true);
    if original_durable.is_some_and(|identity| identity.verify(root, bytes).is_ok()) {
        failure.status = "persistent-blocked";
        failure.persistent_block_confirmed = true;
        return failure;
    }
    failure.compensation_attempted = true;
    let compensation = (|| {
        let mut file = at(hook, "compensation-create", || Ok(OpenOptions::new().write(true).create_new(true).mode(0o600).custom_flags(libc::O_NOFOLLOW).open(root.join(PENDING_NAME))?))?;
        let identity = at(hook, "compensation-identity", || MarkerIdentity::of(&file))?;
        at(hook, "compensation-write", || { file.write_all(bytes)?; Ok(()) })?;
        at(hook, "compensation-file-sync", || { file.sync_all()?; Ok(()) })?;
        at(hook, "compensation-directory-sync", || { File::open(root)?.sync_all()?; Ok(()) })?;
        at(hook, "compensation-readback", || identity.verify(root, bytes))
    })();
    match compensation {
        Ok(()) => {
            failure.status = "persistent-blocked";
            failure.persistent_block_confirmed = true;
            failure.compensation_stage = Some("confirmed");
        }
        Err(compensation) => {
            failure.compensation_stage = Some(compensation.stage);
            failure.error = format!("{}; compensation {}: {:#}", failure.error, compensation.stage, compensation.error);
        }
    }
    failure
}
fn remove_pending(root: &Path, bytes: &[u8], identity: MarkerIdentity, hook: &mut impl FnMut(&'static str) -> Result<()>) -> std::result::Result<(), InstallFailure> {
    if let Err(error) = at(hook, "pending-remove-readback", || {
        identity.verify(root, bytes)
    }) { return Err(block_after_failure(root, bytes, error, None, hook)); }
    if let Err(error) = at(hook, "pending-unlink", || { fs::remove_file(root.join(PENDING_NAME))?; Ok(()) }) {
        return Err(block_after_failure(root, bytes, error, Some(identity), hook));
    }
    if let Err(error) = at(hook, "pending-remove-directory-sync", || { File::open(root)?.sync_all()?; Ok(()) }) {
        return Err(block_after_failure(root, bytes, error, None, hook));
    }
    Ok(())
}
pub(crate) fn install_new(registry: &PackageInstallRegistry, package: &Package) -> std::result::Result<CommittedInstall, InstallFailure> {
    install_new_with_hook(registry, package, &mut |_| Ok(()))
}
pub(crate) fn install_new_with_hook(registry: &PackageInstallRegistry, package: &Package,
    hook: &mut impl FnMut(&'static str) -> Result<()>) -> std::result::Result<CommittedInstall, InstallFailure> {
    let root = registry.root_path();
    let old = snapshot(registry).map_err(|error| failed("preflight-state", error, true))?;
    registry.verify_package_source(package).map_err(|error| failed("preflight-package", error, false))?;
    let prepare = (|| -> Result<_> {
        ensure!(package.manifest.sdk_version == 6 && package.manifest.entry == "main.wasm" && package.manifest.data_migration.is_none(), "PERSISTENT_PACKAGE_SCHEMA");
        let (mut candidate, grants) = load_catalog_with_install_registry(&root.join(CATALOG_NAME), registry)?;
        ensure!(grants.is_empty() && candidate.apps.len() < 4 && !candidate.apps.iter().any(|app| app.package_id == package.manifest.id), "PERSISTENT_NEW_INSTALL_ONLY");
        let id = candidate.apps.iter().map(|app| app.id).max().unwrap_or(0).checked_add(1).context("PERSISTENT_APP_ID")?;
        ensure!(id <= 4, "PERSISTENT_APP_ID");
        candidate.apps.push(crate::desktop_catalog::CatalogEntry { id, title: package.manifest.id.chars().take(20).collect(), manifest_path: PathBuf::new(),
            package_id: package.manifest.id.clone(), package_fingerprint: package.storage_fingerprint() });
        let catalog = crate::desktop_catalog::encode_installed_catalog(&candidate, &grants)?;
        let old_registry = read_file(&root.join(REGISTRY_NAME), 64 * 1024)?;
        let old_catalog = read_file(&root.join(CATALOG_NAME), 16 * 1024)?;
        ensure!(sha256(&old_registry) == old.registry_sha256 && sha256(&old_catalog) == old.catalog_sha256, "PERSISTENT_PREFLIGHT_DRIFT");
        let pending = Pending { schema_version: 1, package_id: package.manifest.id.clone(), version: package.manifest.version.clone(),
            fingerprint: fingerprint(package.storage_fingerprint()), old_registry_sha256: old.registry_sha256.clone(), old_catalog_sha256: old.catalog_sha256.clone(), new_catalog_sha256: sha256(&catalog) };
        let pending_bytes = serde_json::to_vec(&pending)?;
        ensure!(pending_bytes.len() <= 4096, "PERSISTENT_PENDING_LIMIT");
        Ok((catalog, old_registry, old_catalog, pending, pending_bytes))
    })();
    let (catalog_bytes, old_registry, old_catalog, pending, pending_bytes) = prepare.map_err(|error| failed("prepare", error, false))?;
    let pending_identity = match create_pending(root, &pending_bytes, hook) {
        Ok(identity) => identity,
        Err(error) => {
            if error.stage == "pending-create" && absent(&root.join(PENDING_NAME)).is_ok() {
                return Err(failed(error.stage, error.error, false));
            }
            return Err(block_after_failure(root, &pending_bytes, error, None, hook));
        }
    };
    let operation = (|| {
        at(hook, "registry-install", || { registry.install_loaded_package(package)?; Ok(()) })?;
        replace_catalog(root, &catalog_bytes, false, hook)?;
        at(hook, "commit-readback", || {
            let content = snapshot_while_pending(registry, &pending, &pending_bytes, true)?;
            ensure!(content.trust_sha256 == old.trust_sha256 && content.bootstrap_sha256 == old.bootstrap_sha256, "PERSISTENT_COMMIT_BASE_DRIFT"); Ok(())
        })
    })();
    if let Err(error) = operation {
        // Attempt both exact restorations even when one fails. No rollback
        // failure is swallowed or converted into a retryable install failure.
        let restored_registry = at(hook, "registry-restore", || registry.restore_records_snapshot(&old_registry));
        let restored_catalog = replace_catalog(root, &old_catalog, true, hook);
        let restorations = match (restored_registry, restored_catalog) {
            (Ok(()), Ok(())) => Ok(()),
            (Err(registry), Err(catalog)) => Err(PhaseError { stage: registry.stage,
                error: anyhow::anyhow!("{}: {:#}; {}: {:#}", registry.stage, registry.error, catalog.stage, catalog.error) }),
            (Err(error), _) | (_, Err(error)) => Err(error),
        };
        let restored = restorations.and_then(|_| at(hook, "rollback-readback", || {
            let current = snapshot_while_pending(registry, &pending, &pending_bytes, false)?;
            ensure!(current.apps == old.apps && current.trust_sha256 == old.trust_sha256 && current.bootstrap_sha256 == old.bootstrap_sha256, "PERSISTENT_ROLLBACK_BASE_DRIFT"); Ok(())
        }));
        if let Err(rollback) = restored { return Err(block_after_failure(root, &pending_bytes, rollback, Some(pending_identity), hook)); }
        remove_pending(root, &pending_bytes, pending_identity, hook)?;
        at(hook, "rollback-final-readback", || snapshot(registry))
            .map_err(|error| block_after_failure(root, &pending_bytes, error, None, hook))?;
        return Err(failed(error.stage, error.error, false));
    }
    remove_pending(root, &pending_bytes, pending_identity, hook)?;
    let catalog = at(hook, "catalog-final-load", || {
        let (catalog, grants) = load_catalog_with_install_registry(&root.join(CATALOG_NAME), registry)?;
        ensure!(crate::desktop_catalog::encode_installed_catalog(&catalog, &grants)? == catalog_bytes, "PERSISTENT_COMMIT_CHANGED");
        Ok(catalog)
    }).map_err(|error| block_after_failure(root, &pending_bytes, error, None, hook))?;
    let content = at(hook, "final-readback", || snapshot(registry))
        .map_err(|error| block_after_failure(root, &pending_bytes, error, None, hook))?;
    if content.catalog_sha256 != pending.new_catalog_sha256 {
        return Err(block_after_failure(root, &pending_bytes, PhaseError { stage: "final-readback", error: anyhow::anyhow!("PERSISTENT_COMMIT_CHANGED") }, None, hook));
    }
    Ok(CommittedInstall { catalog, receipt: InstallCommit { schema_version: 1, status: "committed", package_id: package.manifest.id.clone(),
        version: package.manifest.version.clone(), fingerprint: fingerprint(package.storage_fingerprint()), registry_root: root.into(), catalog_path: root.join(CATALOG_NAME), content, persistent_block_confirmed: false } })
}

#[cfg(test)]
pub(crate) mod transaction_tests {
    use super::*;
    use base64::{Engine as _, engine::general_purpose::STANDARD};
    use ed25519_dalek::{Signer, SigningKey};
    use std::os::unix::fs::PermissionsExt;
    pub(crate) fn fixture() -> (tempfile::TempDir, OpenedInstall, Package) {
        let parent = tempfile::tempdir().unwrap();
        fs::set_permissions(parent.path(), fs::Permissions::from_mode(0o700)).unwrap();
        let key = SigningKey::from_bytes(&[7; 32]);
        let mut package = Package { manifest: crate::package::Manifest { schema_version: 1,
            id: "dev.ming.text-entry-demo".into(), version: "0.1.0".into(), sdk_version: 6,
            entry: "main.wasm".into(), private_storage: false, public_storage: false,
            data_migration: None, publisher_key_id: Some("mingos-dev-2026".into()), publisher_signature: None },
            bytes: b"\0asm\x01\0\0\0".to_vec() };
        package.manifest.publisher_signature = Some(STANDARD.encode(key.sign(&package.signature_message("mingos-dev-2026").unwrap()).to_bytes()));
        let root = parent.path().join("install-root");
        initialize_first(&root, &package, "mingos-dev-2026", key.verifying_key().as_bytes(),
            br#"{"schemaVersion":3,"apps":[{"id":1,"title":"Text Lab","packageId":"dev.ming.text-entry-demo"}],"autostart":[1]}"#).unwrap();
        package.manifest.id = "dev.ming.install-demo".into();
        package.manifest.version = "1.0.0".into();
        package.manifest.publisher_signature = None;
        package.manifest.publisher_signature = Some(STANDARD.encode(key.sign(&package.signature_message("mingos-dev-2026").unwrap()).to_bytes()));
        let opened = open_existing(&root).unwrap();
        (parent, opened, package)
    }
    #[test]
    fn commit_publishes_both_states_only_after_pending_is_synchronously_removed() {
        let (_parent, opened, package) = fixture();
        let root = opened.registry.root_path().to_path_buf();
        let mut seen = Vec::new();
        let committed = install_new_with_hook(&opened.registry, &package, &mut |stage| {
            if stage == "pending-unlink" {
                assert!(root.join(PENDING_NAME).is_file());
                assert!(snapshot(&opened.registry).is_err());
            }
            seen.push(stage); Ok(())
        }).unwrap();
        assert_eq!(committed.receipt.status, "committed");
        assert_eq!(committed.catalog.autostart, [1]);
        assert_eq!(committed.receipt.content.apps.len(), 2);
        assert_eq!(snapshot(&opened.registry).unwrap(), committed.receipt.content);
        assert!(!root.join(PENDING_NAME).exists());
        assert!(seen.contains(&"pending-directory-sync") && seen.contains(&"pending-remove-directory-sync"));
        drop(opened);
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "persistent_install::transaction_tests::committed_reopen_child", "--nocapture"])
            .env("MING_TEST_COMMITTED_ROOT", &root).output().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        let stdout = String::from_utf8(output.stdout).unwrap();
        let content = stdout.lines().find_map(|line| line.strip_prefix("COMMITTED_REOPEN ")).unwrap();
        assert_eq!(serde_json::from_str::<ContentSnapshot>(content).unwrap(), committed.receipt.content);
    }
    #[test]
    fn committed_reopen_child() {
        let Some(root) = std::env::var_os("MING_TEST_COMMITTED_ROOT") else { return; };
        let opened = open_existing(Path::new(&root)).unwrap();
        assert_eq!(opened.content.apps.len(), 2);
        println!("COMMITTED_REOPEN {}", serde_json::to_string(&opened.content).unwrap());
    }
    #[test]
    fn pending_unlink_directory_sync_failure_is_fatal_and_compensated() {
        let (parent, opened, package) = fixture();
        let root = opened.registry.root_path().to_path_buf();
        let failure = install_new_with_hook(&opened.registry, &package, &mut |stage| {
            if stage == "pending-remove-directory-sync" {
                assert!(!root.join(PENDING_NAME).exists()); bail!("injected after successful unlink");
            } Ok(())
        }).unwrap_err();
        assert!(failure.fatal());
        assert_eq!(failure.status, "persistent-blocked");
        assert!(failure.persistent_block_confirmed && failure.compensation_attempted);
        assert_eq!(failure.compensation_stage, Some("confirmed"));
        drop(opened);
        assert_child_rejects_pending(&parent.path().join("install-root"));
    }

    fn assert_child_rejects_pending(root: &Path) {
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "persistent_install::transaction_tests::pending_reopen_child", "--nocapture"])
            .env("MING_TEST_PENDING_ROOT", root).output().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        assert!(String::from_utf8_lossy(&output.stdout).contains("PENDING_REOPEN_REJECTED"));
    }
    #[test]
    fn pending_reopen_child() {
        let Some(root) = std::env::var_os("MING_TEST_PENDING_ROOT") else { return; };
        let error = open_existing(Path::new(&root)).unwrap_err();
        let message = format!("{error:#}");
        assert!(message.contains("PERSISTENT_UNFINISHED_OR_EXISTING") && message.contains(PENDING_NAME), "{message}");
        println!("PENDING_REOPEN_REJECTED");
    }
    #[test]
    fn rollback_reports_both_restoration_failures() {
        let (_parent, opened, package) = fixture();
        let failure = install_new_with_hook(&opened.registry, &package, &mut |stage| {
            if ["catalog-write", "registry-restore", "catalog-restore-create"].contains(&stage) { bail!("injected {stage}"); }
            Ok(())
        }).unwrap_err();
        assert!(failure.error.contains("registry-restore") && failure.error.contains("catalog-restore-create"), "{}", failure.error);
    }
    #[test]
    fn replacement_pending_does_not_inherit_original_durability() {
        let (_parent, opened, package) = fixture();
        let root = opened.registry.root_path();
        let failure = install_new_with_hook(&opened.registry, &package, &mut |stage| {
            if stage == "pending-unlink" {
                let bytes = fs::read(root.join(PENDING_NAME))?;
                fs::rename(root.join(PENDING_NAME), root.join("original-marker"))?;
                OpenOptions::new().create_new(true).write(true).mode(0o600).open(root.join(PENDING_NAME))?.write_all(&bytes)?;
                bail!("injected replaced marker");
            }
            Ok(())
        }).unwrap_err();
        assert_eq!(failure.status, "commit-uncertain");
        assert!(!failure.persistent_block_confirmed && failure.compensation_attempted);
        assert_eq!(failure.compensation_stage, Some("compensation-create"));
    }

    #[test]
    fn install_failures_restore_exact_registry_and_catalog_bytes() {
        for fault in ["registry-install", "catalog-create", "catalog-write", "catalog-file-sync", "catalog-rename", "catalog-directory-sync", "commit-readback"] {
            let (_parent, opened, package) = fixture();
            let root = opened.registry.root_path().to_path_buf();
            let registry = fs::read(root.join(REGISTRY_NAME)).unwrap();
            let catalog = fs::read(root.join(CATALOG_NAME)).unwrap();
            let old = opened.content.clone();
            let failure = install_new_with_hook(&opened.registry, &package, &mut |stage| {
                if stage == fault { bail!("injected {stage}"); } Ok(())
            }).unwrap_err();
            assert_eq!(failure.status, "rolled-back", "{fault}: {failure:?}");
            assert_eq!(failure.stage, fault);
            assert!(!failure.fatal() && !failure.persistent_block_confirmed && !failure.compensation_attempted);
            assert_eq!(fs::read(root.join(REGISTRY_NAME)).unwrap(), registry, "{fault}");
            assert_eq!(fs::read(root.join(CATALOG_NAME)).unwrap(), catalog, "{fault}");
            let current = snapshot(&opened.registry).unwrap();
            assert_eq!(current.apps, old.apps, "{fault}");
            assert_eq!(current.registry_sha256, old.registry_sha256);
            assert_eq!(current.catalog_sha256, old.catalog_sha256);
            assert!(!root.join(PENDING_NAME).exists());
            drop(opened);
            assert_eq!(open_existing(&root).unwrap().content, current, "{fault}");
        }
    }

    #[test]
    fn every_unconfirmed_rollback_keeps_a_confirmed_block_and_rejects_another_process() {
        for fault in ["registry-restore", "catalog-restore-create", "catalog-restore-write", "catalog-restore-file-sync", "catalog-restore-rename", "catalog-restore-directory-sync", "rollback-readback"] {
            let (_parent, opened, package) = fixture();
            let root = opened.registry.root_path().to_path_buf();
            let mut seen = Vec::new();
            let failure = install_new_with_hook(&opened.registry, &package, &mut |stage| {
                seen.push(stage);
                if stage == "catalog-directory-sync" || stage == fault { bail!("injected {stage}"); } Ok(())
            }).unwrap_err();
            assert_eq!(failure.stage, fault, "{failure:?}");
            assert_eq!(failure.status, "persistent-blocked");
            assert!(failure.fatal() && failure.persistent_block_confirmed && !failure.compensation_attempted);
            assert!(seen.contains(&"registry-restore") && seen.contains(&"catalog-restore-create"));
            assert!(!seen.contains(&"pending-unlink"));
            drop(opened);
            assert_child_rejects_pending(&root);
        }
    }

    #[test]
    fn failed_unlink_checks_the_actual_namespace_before_claiming_a_retained_marker() {
        for remove in [false, true] {
            let (_parent, opened, package) = fixture();
            let root = opened.registry.root_path().to_path_buf();
            let failure = install_new_with_hook(&opened.registry, &package, &mut |stage| {
                if stage == "pending-unlink" {
                    if remove { fs::remove_file(root.join(PENDING_NAME))?; }
                    bail!("injected unlink failure");
                } Ok(())
            }).unwrap_err();
            assert_eq!(failure.status, "persistent-blocked");
            assert!(failure.persistent_block_confirmed);
            assert_eq!(failure.compensation_attempted, remove);
            assert_eq!(failure.compensation_stage, remove.then_some("confirmed"));
            drop(opened);
            assert_child_rejects_pending(&root);
        }
    }

    #[test]
    fn every_failed_compensation_reports_uncertain_with_one_attempt() {
        for fault in ["compensation-create", "compensation-identity", "compensation-write", "compensation-file-sync", "compensation-directory-sync", "compensation-readback"] {
            let (_parent, opened, package) = fixture();
            let root = opened.registry.root_path();
            let mut seen = Vec::new();
            let failure = install_new_with_hook(&opened.registry, &package, &mut |stage| {
                seen.push(stage);
                if stage == "pending-remove-directory-sync" {
                    assert!(!root.join(PENDING_NAME).exists());
                    bail!("injected after unlink");
                }
                if stage == fault { bail!("injected {stage}"); } Ok(())
            }).unwrap_err();
            assert_eq!(failure.stage, "pending-remove-directory-sync");
            assert_eq!(failure.status, "commit-uncertain", "{fault}: {failure:?}");
            assert!(failure.fatal() && failure.compensation_attempted && !failure.persistent_block_confirmed);
            assert_eq!(failure.compensation_stage, Some(fault));
            assert_eq!(seen.iter().filter(|stage| **stage == "compensation-create").count(), 1);
            assert_eq!(seen.last(), Some(&fault));
            // No claim that a new process will reject when persistence could not be confirmed.
            if fault == "compensation-create" { assert!(!root.join(PENDING_NAME).exists()); }
        }
    }

    #[test]
    fn incomplete_initial_marker_never_mutates_install_content_or_reports_success() {
        for fault in ["pending-create", "pending-identity", "pending-write", "pending-file-sync", "pending-directory-sync", "pending-readback"] {
            let (_parent, opened, package) = fixture();
            let root = opened.registry.root_path();
            let registry = fs::read(root.join(REGISTRY_NAME)).unwrap();
            let catalog = fs::read(root.join(CATALOG_NAME)).unwrap();
            let mut seen = Vec::new();
            let failure = install_new_with_hook(&opened.registry, &package, &mut |stage| {
                seen.push(stage);
                if stage == fault { bail!("injected {stage}"); } Ok(())
            }).unwrap_err();
            assert_eq!(failure.stage, fault);
            assert!(!seen.contains(&"registry-install"));
            assert_eq!(fs::read(root.join(REGISTRY_NAME)).unwrap(), registry);
            assert_eq!(fs::read(root.join(CATALOG_NAME)).unwrap(), catalog);
            assert_eq!(failure.status, if fault == "pending-create" { "rolled-back" } else { "commit-uncertain" });
            assert!(!failure.persistent_block_confirmed);
        }
    }

    #[test]
    fn final_readback_failures_after_unlink_are_fatal_and_compensated() {
        for fault in ["catalog-final-load", "final-readback", "rollback-final-readback"] {
            let (_parent, opened, package) = fixture();
            let root = opened.registry.root_path().to_path_buf();
            let failure = install_new_with_hook(&opened.registry, &package, &mut |stage| {
                if fault == "rollback-final-readback" && stage == "catalog-write" { bail!("injected rollback trigger"); }
                if stage == fault {
                    assert!(!root.join(PENDING_NAME).exists());
                    bail!("injected {stage}");
                } Ok(())
            }).unwrap_err();
            assert_eq!(failure.stage, fault);
            assert_eq!(failure.status, "persistent-blocked");
            assert!(failure.fatal() && failure.persistent_block_confirmed && failure.compensation_attempted);
            drop(opened);
            assert_child_rejects_pending(&root);
        }
    }

    #[test]
    fn pending_snapshot_requires_this_transaction_and_unchanged_disk_bytes() {
        let (_parent, opened, package) = fixture();
        let root = opened.registry.root_path();
        let mut checked = false;
        install_new_with_hook(&opened.registry, &package, &mut |stage| {
            if stage == "commit-readback" {
                let bytes = read_file(&root.join(PENDING_NAME), 4096)?;
                let pending: Pending = serde_json::from_slice(&bytes)?;
                assert!(snapshot_while_pending(&opened.registry, &pending, &bytes, true).is_ok());
                assert!(snapshot_while_pending(&opened.registry, &pending, &bytes, false).is_err());
                let mut other: Pending = serde_json::from_slice(&bytes)?;
                other.version = "2.0.0".into();
                let other_bytes = serde_json::to_vec(&other)?;
                for (object, expected) in [(&other, &other_bytes), (&pending, &other_bytes)] {
                    assert!(snapshot_while_pending(&opened.registry, object, expected, true).is_err());
                    assert_eq!(fs::read(root.join(PENDING_NAME))?, bytes);
                }
                fs::write(root.join(PENDING_NAME), &other_bytes)?;
                assert!(snapshot_while_pending(&opened.registry, &pending, &bytes, true).is_err());
                assert_eq!(fs::read(root.join(PENDING_NAME))?, other_bytes);
                fs::write(root.join(PENDING_NAME), &bytes)?;
                let registry = fs::read(root.join(REGISTRY_NAME))?;
                let mut changed = registry.clone(); changed.extend_from_slice(b"invalid-record\n");
                fs::write(root.join(REGISTRY_NAME), &changed)?;
                assert!(snapshot_while_pending(&opened.registry, &pending, &bytes, true).is_err());
                assert_eq!(fs::read(root.join(REGISTRY_NAME))?, changed);
                fs::write(root.join(REGISTRY_NAME), registry)?;
                checked = true;
            } Ok(())
        }).unwrap();
        assert!(checked);
    }
}
