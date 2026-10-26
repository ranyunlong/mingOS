use anyhow::{Context, Result, bail};
use serde::Deserialize;
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

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Manifest {
    pub schema_version: u32,
    pub id: String,
    pub version: String,
    pub sdk_version: u32,
    pub entry: String,
}

#[derive(Debug)]
pub struct Package {
    pub manifest: Manifest,
    pub bytes: Vec<u8>,
}

fn valid_id(id: &str) -> bool {
    id.len() <= 128
        && id.as_bytes().first().is_some_and(u8::is_ascii_lowercase)
        && id.contains(['.', '-'])
        && id.split(['.', '-']).all(|s| {
            !s.is_empty()
                && s.bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
        })
}

pub fn load_package(path: &Path) -> Result<Package> {
    let raw = read_bounded(path, 16_384, "MANIFEST")?;
    let manifest: Manifest = serde_json::from_slice(&raw).context("MANIFEST_INVALID")?;
    if manifest.schema_version != 1 || !matches!(manifest.sdk_version, 1..=6) {
        bail!("MANIFEST_UNSUPPORTED_VERSION");
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
