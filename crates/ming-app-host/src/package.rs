use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
#[cfg(target_os = "linux")]
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::Read,
    path::{Path, PathBuf},
};

pub const MAX_MODULE_BYTES: usize = 4 * 1024 * 1024;

pub fn open_regular(path: &Path, label: &str) -> Result<File> {
    let info = fs::symlink_metadata(path).with_context(|| format!("{label}_OPEN"))?;
    if info.file_type().is_symlink() {
        bail!("{label}_SYMLINK_DENIED");
    }
    if !info.is_file() {
        bail!("{label}_NOT_FILE");
    }
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x0020_0000); // FILE_FLAG_OPEN_REPARSE_POINT
    }
    let file = options
        .open(path)
        .with_context(|| format!("{label}_OPEN"))?;
    let info = file.metadata()?;
    if !info.is_file() || info.file_type().is_symlink() {
        bail!("{label}_NOT_FILE");
    }
    Ok(file)
}

pub fn read_bounded(path: &Path, limit: usize, label: &str) -> Result<Vec<u8>> {
    let mut file = open_regular(path, label)?;
    let size = file.metadata()?.len();
    if size > limit as u64 {
        bail!("{label}_TOO_LARGE");
    }
    let mut bytes = vec![0; size as usize];
    file.read_exact(&mut bytes)
        .with_context(|| format!("{label}_CHANGED"))?;
    Ok(bytes)
}

#[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Manifest {
    pub schema_version: u32,
    pub id: String,
    pub version: String,
    pub sdk_version: u32,
    pub entry: String,
    #[serde(default)]
    pub private_storage: bool,
    #[serde(default)]
    pub public_storage: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub publisher_key_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub publisher_signature: Option<String>,
}

#[derive(Debug, PartialEq, Eq)]
pub struct Package {
    pub manifest: Manifest,
    pub bytes: Vec<u8>,
}

#[cfg(target_os = "linux")]
impl Package {
    pub(crate) fn storage_fingerprint(&self) -> [u8; 32] {
        let mut hash = Sha256::new();
        hash.update(b"ming-private-storage-package-v1\0");
        hash.update(self.manifest.schema_version.to_be_bytes());
        for value in [&self.manifest.id, &self.manifest.version, &self.manifest.entry] {
            hash.update((value.len() as u64).to_be_bytes());
            hash.update(value.as_bytes());
        }
        hash.update(self.manifest.sdk_version.to_be_bytes());
        hash.update((self.bytes.len() as u64).to_be_bytes());
        hash.update(&self.bytes);
        if self.manifest.private_storage { hash.update(b"\0private-storage-required-v1"); }
        if self.manifest.public_storage { hash.update(b"\0public-storage-requested-v1"); }
        if let Some(key_id) = &self.manifest.publisher_key_id {
            hash.update(b"\0publisher-key-id-v1");
            hash.update((key_id.len() as u64).to_be_bytes());
            hash.update(key_id.as_bytes());
        }
        hash.finalize().into()
    }
}

#[cfg(target_os = "linux")]
impl crate::app_data::PackageStorageAuthority {
    pub(crate) fn default_private_session_for_package(self: &std::sync::Arc<Self>, package: &Package) -> Result<crate::app_data::PrivateFileSession, crate::app_data::AppDataError> {
        if package.manifest.sdk_version < 6 { return Err(crate::app_data::AppDataError::Denied); }
        self.default_private_session(&package.manifest.id, package.storage_fingerprint())
    }
    /// The caller must obtain explicit user approval for this exact loaded package.
    pub fn authorize_package(&self, package: &Package) -> Result<crate::app_data::AuthorizationReceipt, crate::app_data::AppDataError> {
        if !package.manifest.private_storage || package.manifest.sdk_version < 6 { return Err(crate::app_data::AppDataError::Denied); }
        self.authorize_bound(&package.manifest.id, package.storage_fingerprint())
    }
    pub fn file_session_for_package(self: &std::sync::Arc<Self>, package: &Package) -> Result<crate::app_data::PrivateFileSession, crate::app_data::AppDataError> {
        if !package.manifest.private_storage || package.manifest.sdk_version < 6 { return Err(crate::app_data::AppDataError::Denied); }
        self.file_session_bound(&package.manifest.id, package.storage_fingerprint())
    }
    pub fn authorize_public_package(&self, package: &Package) -> Result<crate::app_data::AuthorizationReceipt, crate::app_data::AppDataError> {
        if !package.manifest.public_storage || package.manifest.sdk_version < 6 { return Err(crate::app_data::AppDataError::Denied); }
        self.authorize_public_bound(&package.manifest.id, package.storage_fingerprint())
    }
    pub fn public_session_for_package(self: &std::sync::Arc<Self>, package: &Package) -> Result<crate::app_data::PublicFileSession, crate::app_data::AppDataError> {
        if !package.manifest.public_storage || package.manifest.sdk_version < 6 { return Err(crate::app_data::AppDataError::Denied); }
        self.public_session_bound(&package.manifest.id, package.storage_fingerprint())
    }
}

#[cfg(target_os = "linux")]
impl crate::app_data::PrivateFileSession {
    pub(crate) fn belongs_to_package(&self, package: &Package) -> bool {
        self.belongs_to(&package.manifest.id) && self.matches_fingerprint(package.storage_fingerprint())
    }
}

#[cfg(target_os = "linux")]
impl crate::app_data::PublicFileSession {
    pub(crate) fn belongs_to_package(&self, package: &Package) -> bool {
        self.belongs_to(&package.manifest.id) && self.matches_fingerprint(package.storage_fingerprint())
    }
}

fn valid_id(id: &str) -> bool {
    crate::app_data::valid_package_id(id)
}

pub fn load_package(path: &Path) -> Result<Package> {
    let raw = read_bounded(path, 16_384, "MANIFEST")?;
    let manifest: Manifest = serde_json::from_slice(&raw).context("MANIFEST_INVALID")?;
    if manifest.schema_version != 1 || !matches!(manifest.sdk_version, 1..=6) {
        bail!("MANIFEST_UNSUPPORTED_VERSION");
    }
    if manifest.private_storage && manifest.sdk_version < 6 {
        bail!("MANIFEST_PRIVATE_STORAGE_SDK");
    }
    if manifest.public_storage && manifest.sdk_version < 6 {
        bail!("MANIFEST_PUBLIC_STORAGE_SDK");
    }
    if !valid_id(&manifest.id) {
        bail!("MANIFEST_INVALID_ID");
    }
    let version: Vec<_> = manifest.version.split('.').collect();
    if version.len() != 3
        || version
            .iter()
            .any(|s| s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()))
    {
        bail!("MANIFEST_INVALID_VERSION");
    }
    let entry = &manifest.entry;
    if entry.len() > 255
        || !entry.ends_with(".wasm")
        || !entry
            .as_bytes()
            .first()
            .is_some_and(u8::is_ascii_alphanumeric)
        || !entry
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    {
        bail!("MANIFEST_INVALID_ENTRY");
    }
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let dir = fs::canonicalize(parent).context("MANIFEST_DIRECTORY")?;
    let module_path: PathBuf = dir.join(entry);
    let bytes = read_bounded(&module_path, MAX_MODULE_BYTES, "MODULE")?;
    if bytes.len() < 8 || &bytes[..8] != b"\0asm\x01\0\0\0" {
        bail!("MODULE_INVALID: expected binary Wasm");
    }
    Ok(Package { manifest, bytes })
}
