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
fn collect(root: &Path, directory: &Path, files: &mut BTreeMap<String, Vec<u8>>, total: &mut usize) -> Result<()> {
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
            collect(root, &path, files, total)?;
        } else {
            let parts: Vec<_> = rel.split('/').collect();
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
    let root = registry.root_path();
    check_path(root)?;
    metadata(root, true)?;
    absent(&root.join(PENDING_NAME))?;
    absent(&root.join("update-journal-v1"))?;
    let mut files = BTreeMap::new();
    collect(root, root, &mut files, &mut 0)?;
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
    Ok(ContentSnapshot { catalog_sha256: sha256(required(CATALOG_NAME)?), registry_sha256: sha256(required(REGISTRY_NAME)?),
        trust_sha256: sha256(required(TRUST_NAME)?), bootstrap_sha256: sha256(required(BOOTSTRAP_NAME)?), apps,
        files: files.iter().map(|(path, bytes)| ContentFile { path: path.clone(), sha256: sha256(bytes) }).collect() })
}

pub fn open_existing(root: &Path) -> Result<OpenedInstall> {
    check_path(root)?;
    metadata(root, true)?;
    // Reject incomplete/unsafe content before the existing registry opener can create its locks.
    let mut files = BTreeMap::new();
    collect(root, root, &mut files, &mut 0)?;
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
