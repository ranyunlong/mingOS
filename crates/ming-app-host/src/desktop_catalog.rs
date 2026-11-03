//! Trusted host configuration for bounded Ming desktop development sessions.
use crate::{
    graphics::RendererKind,
    package::{load_package, read_bounded},
};
#[cfg(target_os = "linux")]
use crate::package_install::PackageInstallRegistry;
use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
#[cfg(target_os = "linux")]
use std::sync::atomic::{AtomicU64, Ordering};
#[cfg(target_os = "linux")]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

#[cfg(target_os = "linux")]
static CATALOG_TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);
#[derive(Clone, Debug)]
pub struct CatalogEntry {
    pub id: u16,
    pub title: String,
    pub manifest_path: PathBuf,
    pub package_id: String,
    #[cfg(target_os = "linux")]
    pub(crate) package_fingerprint: [u8; 32],
}
#[derive(Clone, Debug)]
pub struct Catalog {
    pub apps: Vec<CatalogEntry>,
    pub autostart: Vec<u16>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DiskCatalog {
    schema_version: u32,
    apps: Vec<DiskEntry>,
    autostart: Vec<u16>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DiskEntry {
    id: u16,
    title: String,
    #[serde(default)]
    manifest: Option<PathBuf>,
    #[serde(default)]
    package_id: Option<String>,
    #[serde(default)] identity: Option<DiskIdentity>,
    #[serde(default)] data_key: Option<String>,
    #[serde(default)] slots: Option<Vec<u8>>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DiskIdentity {
    package_id: String,
    fingerprint: String,
}
#[derive(Clone, Debug)]
pub(crate) struct TrustedAppData { pub data_key: String, pub slots: Vec<u8> }
#[derive(Debug, Default)]
pub(crate) struct DesktopDataConfig {
    pub root: Option<crate::app_data::AppDataRoot>,
    pub grants: BTreeMap<u16, TrustedAppData>,
}

#[cfg(target_os = "linux")]
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedCatalog {
    schema_version: u32,
    apps: Vec<PersistedCatalogEntry>,
    autostart: Vec<u16>,
}

#[cfg(target_os = "linux")]
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedCatalogEntry {
    id: u16,
    title: String,
    package_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    data_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    slots: Option<Vec<u8>>,
}

/// Atomically persists the trusted schema-3 catalog after an installation.
/// The registry remains the package identity authority; this file only records
/// which installed package IDs the desktop should expose and their UI IDs.
#[cfg(target_os = "linux")]
pub(crate) fn encode_installed_catalog(catalog: &Catalog, grants: &BTreeMap<u16, TrustedAppData>) -> Result<Vec<u8>> {
    if catalog.apps.is_empty() || catalog.apps.len() > 4 || catalog.autostart.len() > 4 {
        bail!("DESKTOP_CATALOG_LIMIT");
    }
    let mut ids = BTreeSet::new();
    let mut packages = BTreeSet::new();
    let mut apps = Vec::with_capacity(catalog.apps.len());
    for app in &catalog.apps {
        if app.id == 0 || !ids.insert(app.id) || !packages.insert(app.package_id.clone())
            || app.title.is_empty() || app.title.len() > 20
            || !app.title.bytes().all(|c| c.is_ascii_graphic() || c == b' ')
        {
            bail!("DESKTOP_CATALOG_ENTRY");
        }
        let grant = grants.get(&app.id);
        if grant.is_some_and(|grant| crate::app_data::validate_grant(&grant.data_key, &grant.slots).is_err()) {
            bail!("DESKTOP_DATA_GRANT");
        }
        apps.push(PersistedCatalogEntry {
            id: app.id,
            title: app.title.clone(),
            package_id: app.package_id.clone(),
            data_key: grant.map(|grant| grant.data_key.clone()),
            slots: grant.map(|grant| grant.slots.clone()),
        });
    }
    if catalog.autostart.iter().any(|id| !ids.contains(id)) {
        bail!("DESKTOP_CATALOG_AUTOSTART");
    }
    let mut bytes = serde_json::to_vec(&PersistedCatalog { schema_version: 3, apps, autostart: catalog.autostart.clone() })
        .context("DESKTOP_CATALOG_SERIALIZE")?;
    if bytes.len() > 16 * 1024 { bail!("DESKTOP_CATALOG_TOO_LARGE"); }
    bytes.push(b'\n');
    Ok(bytes)
}

#[cfg(target_os = "linux")]
pub(crate) fn persist_installed_catalog(
    path: &Path, catalog: &Catalog, grants: &BTreeMap<u16, TrustedAppData>,
) -> Result<()> {
    if !path.is_absolute() { bail!("DESKTOP_CATALOG_PATH_ABSOLUTE"); }
    let bytes = encode_installed_catalog(catalog, grants)?;
    let parent = path.parent().context("DESKTOP_CATALOG_PARENT")?;
    let parent_metadata = fs::symlink_metadata(parent).context("DESKTOP_CATALOG_PARENT")?;
    if parent_metadata.file_type().is_symlink() || !parent_metadata.is_dir() {
        bail!("DESKTOP_CATALOG_PARENT");
    }
    let target_metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() || !metadata.is_file() { bail!("DESKTOP_CATALOG_FILE"); }
            Some(metadata)
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(error).context("DESKTOP_CATALOG_FILE"),
    };
    let sequence = CATALOG_TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let temporary = parent.join(format!(".ming-catalog.tmp-{}-{sequence}", std::process::id()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    options.mode(target_metadata.as_ref().map_or(0o600, |metadata| metadata.permissions().mode() & 0o777));
    let result = (|| -> Result<()> {
        let mut file = options.open(&temporary).context("DESKTOP_CATALOG_TEMP")?;
        file.write_all(&bytes).context("DESKTOP_CATALOG_WRITE")?;
        file.sync_all().context("DESKTOP_CATALOG_SYNC")?;
        fs::rename(&temporary, path).context("DESKTOP_CATALOG_RENAME")?;
        File::open(parent).context("DESKTOP_CATALOG_PARENT_OPEN")?.sync_all().context("DESKTOP_CATALOG_PARENT_SYNC")?;
        Ok(())
    })();
    if result.is_err() { let _ = fs::remove_file(&temporary); }
    result
}
pub fn load_catalog(path: &Path) -> Result<Catalog> {
    load_catalog_with_data(path).map(|(catalog, _)| catalog)
}
pub(crate) fn load_catalog_with_data(path: &Path) -> Result<(Catalog, BTreeMap<u16, TrustedAppData>)> {
    #[cfg(target_os = "linux")]
    { return load_catalog_with_data_impl(path, None); }
    #[cfg(not(target_os = "linux"))]
    { load_catalog_with_data_impl(path) }
}
#[cfg(target_os = "linux")]
pub(crate) fn load_catalog_with_install_registry(
    path: &Path,
    registry: &PackageInstallRegistry,
) -> Result<(Catalog, BTreeMap<u16, TrustedAppData>)> {
    load_catalog_with_data_impl(path, Some(registry))
}
fn load_catalog_with_data_impl(
    path: &Path,
    #[cfg(target_os = "linux")] registry: Option<&PackageInstallRegistry>,
) -> Result<(Catalog, BTreeMap<u16, TrustedAppData>)> {
    let disk: DiskCatalog = serde_json::from_slice(&read_bounded(path, 16384, "DESKTOP_CATALOG")?)
        .context("DESKTOP_CATALOG_INVALID")?;
    if !matches!(disk.schema_version, 1 | 2 | 3)
        || disk.apps.is_empty()
        || disk.apps.len() > 4
        || disk.autostart.len() > 4
    {
        bail!("DESKTOP_CATALOG_LIMIT");
    }
    let mut ids = BTreeSet::new();
    let mut data_keys = BTreeSet::new();
    for app in &disk.apps {
        match (&app.data_key, &app.slots) {
            (None, None) => (),
            (Some(key), Some(slots)) => {
                crate::app_data::validate_grant(key, slots).context("DESKTOP_DATA_GRANT")?;
                if !data_keys.insert(key.clone()) { bail!("DESKTOP_DATA_KEY_SHARED"); }
            }
            _ => bail!("DESKTOP_DATA_GRANT_PAIR"),
        }
        if app.id == 0
            || !ids.insert(app.id)
            || app.title.is_empty()
            || app.title.len() > 20
            || !app.title.bytes().all(|c| c.is_ascii_graphic() || c == b' ')
        {
            bail!("DESKTOP_CATALOG_ENTRY");
        }
    }
    let mut start = BTreeSet::new();
    for id in &disk.autostart {
        if !ids.contains(id) || !start.insert(*id) {
            bail!("DESKTOP_CATALOG_AUTOSTART");
        }
    }
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut apps = Vec::new();
    let mut bindings = BTreeMap::new();
    let mut package_ids = BTreeSet::new();
    for app in disk.apps {
        let manifest = match disk.schema_version {
            1 | 2 => {
                if app.package_id.is_some() { bail!("DESKTOP_CATALOG_ENTRY"); }
                let manifest = app.manifest.context("DESKTOP_CATALOG_MANIFEST")?;
                if manifest.is_absolute() { manifest } else { parent.join(manifest) }
            }
            3 => {
                if app.manifest.is_some() || app.identity.is_some() { bail!("DESKTOP_CATALOG_ENTRY"); }
                let package_id = app.package_id.as_deref().context("DESKTOP_INSTALL_PACKAGE_ID")?;
                #[cfg(not(target_os = "linux"))]
                {
                    let _ = package_id;
                    bail!("DESKTOP_INSTALL_IDENTITY_UNSUPPORTED");
                }
                #[cfg(target_os = "linux")]
                {
                    let registry = registry.context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?;
                    registry.active_manifest_path(package_id).context("DESKTOP_INSTALL_PACKAGE")?
                }
            }
            _ => unreachable!(),
        };
        let package = load_package(&manifest).context("DESKTOP_CATALOG_PACKAGE")?;
        #[cfg(target_os = "linux")]
        if let Some(registry) = registry {
            registry.verify_package(&package).context("DESKTOP_INSTALL_REGISTRY")?;
        }
        if !package_ids.insert(package.manifest.id.clone()) { bail!("DESKTOP_PACKAGE_ID_SHARED"); }
        if package.manifest.sdk_version < 2 {
            bail!("DESKTOP_GRAPHICAL_PACKAGE_REQUIRED");
        }
        if disk.schema_version == 2 {
            #[cfg(not(target_os = "linux"))]
            bail!("DESKTOP_INSTALL_IDENTITY_UNSUPPORTED");
            #[cfg(target_os = "linux")]
            {
                let identity = app.identity.as_ref().context("DESKTOP_INSTALL_IDENTITY_MISSING")?;
                if identity.package_id != package.manifest.id {
                    bail!("DESKTOP_INSTALL_IDENTITY_ID");
                }
                if decode_fingerprint(&identity.fingerprint)? != package.storage_fingerprint() {
                    bail!("DESKTOP_INSTALL_IDENTITY_MISMATCH");
                }
            }
        } else if app.identity.is_some() {
            bail!("DESKTOP_CATALOG_ENTRY");
        }
        if let (Some(data_key), Some(slots)) = (app.data_key, app.slots) {
            if package.manifest.sdk_version < 6 { bail!("DESKTOP_DATA_SDK"); }
            bindings.insert(app.id, TrustedAppData { data_key, slots });
        }
        apps.push(CatalogEntry {
            id: app.id,
            title: app.title,
            manifest_path: manifest,
            #[cfg(target_os = "linux")]
            package_fingerprint: package.storage_fingerprint(),
            package_id: package.manifest.id,
        });
    }
    Ok((Catalog {
        apps,
        autostart: disk.autostart,
    }, bindings))
}
#[cfg(target_os = "linux")]
fn decode_fingerprint(value: &str) -> Result<[u8; 32]> {
    if value.len() != 64 || !value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
        bail!("DESKTOP_INSTALL_IDENTITY_FINGERPRINT");
    }
    let mut result = [0u8; 32];
    for (index, byte) in result.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&value[index * 2..index * 2 + 2], 16)
            .map_err(|_| anyhow::anyhow!("DESKTOP_INSTALL_IDENTITY_FINGERPRINT"))?;
    }
    Ok(result)
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "op", rename_all = "camelCase", deny_unknown_fields)]
pub enum DesktopAction {
    /// A trusted parent-side request. Guest applications cannot emit desktop
    /// actions, and the source is reloaded and signature-checked before use.
    Install { source: PathBuf },
    Launch {
        app: u16,
    },
    Close {
        app: u16,
    },
    Minimize {
        app: u16,
    },
    Restore {
        app: u16,
    },
    Maximize {
        app: u16,
    },
    Focus {
        app: u16,
    },
    Geometry {
        app: u16,
        x: i32,
        y: i32,
        width: i32,
        height: i32,
    },
    Click {
        app: u16,
        x: i32,
        y: i32,
    },
    Terminate {
        app: u16,
    },
    Text {
        app: u16,
        text: String,
    },
    Preedit {
        app: u16,
        text: String,
        start: i32,
        end: i32,
    },
    Key {
        app: u16,
        key: i32,
        down: bool,
        repeat: bool,
        modifiers: u32,
    },
    Shutdown,
}
impl DesktopAction {
    pub fn app(&self) -> u16 {
        match *self {
            Self::Shutdown => 0,
            Self::Install { .. } => 0,
            Self::Launch { app }
            | Self::Close { app }
            | Self::Minimize { app }
            | Self::Restore { app }
            | Self::Maximize { app }
            | Self::Focus { app }
            | Self::Geometry { app, .. }
            | Self::Click { app, .. }
            | Self::Terminate { app }
            | Self::Text { app, .. }
            | Self::Preedit { app, .. }
            | Self::Key { app, .. } => app,
        }
    }
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TimedAction {
    pub after_ms: u64,
    pub action: DesktopAction,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ReviewedDesktopEventScript {
    schema_version: u32,
    require_apps: Vec<u16>,
    steps: Vec<ReviewedDesktopEventStep>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ReviewedDesktopEventStep {
    name: String,
    #[serde(default)]
    after_ms: Option<u64>,
    #[serde(default)]
    action: Option<DesktopAction>,
    #[serde(default)]
    events: Option<serde_json::Value>,
    #[serde(default)]
    expect: Option<serde_json::Value>,
}

fn parse_desktop_event_script(bytes: &[u8]) -> Result<Vec<TimedAction>> {
    let value: serde_json::Value = serde_json::from_slice(bytes).context("DESKTOP_EVENTS_INVALID")?;
    if value.is_array() {
        return serde_json::from_value(value).context("DESKTOP_EVENTS_INVALID");
    }
    let script: ReviewedDesktopEventScript =
        serde_json::from_value(value).context("DESKTOP_EVENTS_INVALID")?;
    if script.schema_version != 1
        || script.require_apps.is_empty()
        || script.require_apps.iter().any(|app| *app == 0)
        || script.steps.is_empty()
        || script.steps.len() > 128
    {
        bail!("DESKTOP_EVENTS_INVALID");
    }
    let mut actions = Vec::new();
    for step in script.steps {
        if step.name.is_empty() || step.name.len() > 64 {
            bail!("DESKTOP_EVENTS_INVALID");
        }
        let _ = (&step.events, &step.expect);
        if let Some(action) = step.action {
            let after_ms = step.after_ms.context("DESKTOP_EVENTS_INVALID")?;
            actions.push(TimedAction { after_ms, action });
        }
    }
    Ok(actions)
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum IsolationMode { Off, Strict }
#[derive(Debug)]
pub struct DesktopOptions {
    pub(crate) app_data: DesktopDataConfig,
    pub install_root: Option<PathBuf>,
    pub persistent_install_root: Option<PathBuf>,
    pub persistent_install_receipts: bool,
    /// A trusted parent-selected package source for the native install Dock
    /// action. Guest applications cannot modify this value.
    pub install_source: Option<PathBuf>,
    #[cfg(target_os = "linux")]
    pub(crate) install_registry: Option<PackageInstallRegistry>,
    pub catalog_path: PathBuf,
    pub renderer: RendererKind,
    pub offscreen: bool,
    pub width: i32,
    pub height: i32,
    pub duration_ms: u64,
    pub snapshot: Option<PathBuf>,
    pub state_file: Option<PathBuf>,
    pub isolation: IsolationMode,
    pub actions: Vec<TimedAction>,
}

// Outputs may not exist yet. Resolve the existing parent in that case, but
// never mistake a dangling final symlink for an absent regular output file.
fn persistent_output_path(path: &Path, root: &Path) -> Result<PathBuf> {
    let absolute = if path.is_absolute() { path.to_path_buf() } else { std::env::current_dir()?.join(path) };
    let resolved = match fs::canonicalize(&absolute) {
        Ok(path) => path,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            match fs::symlink_metadata(&absolute) {
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(error).context("DESKTOP_PERSISTENT_OUTPUT_PATH"),
                Ok(_) => bail!("DESKTOP_PERSISTENT_OUTPUT_PATH"),
            }
            let parent = fs::canonicalize(absolute.parent().context("DESKTOP_PERSISTENT_OUTPUT_PATH")?)
                .context("DESKTOP_PERSISTENT_OUTPUT_PATH")?;
            parent.join(absolute.file_name().context("DESKTOP_PERSISTENT_OUTPUT_PATH")?)
        }
        Err(error) => return Err(error).context("DESKTOP_PERSISTENT_OUTPUT_PATH"),
    };
    if resolved.starts_with(root) { bail!("DESKTOP_PERSISTENT_PATH_CONFLICT"); }
    Ok(resolved)
}
impl DesktopOptions {
    pub fn parse(arguments: impl IntoIterator<Item = OsString>) -> Result<Self> {
        let mut args = arguments.into_iter();
        let catalog_path = args
            .next()
            .context("USAGE: desktop <catalog.json> [options]")?
            .into();
        let mut result = Self {
            app_data: DesktopDataConfig::default(),
            install_root: None,
            persistent_install_root: None,
            persistent_install_receipts: false,
            install_source: None,
            #[cfg(target_os = "linux")]
            install_registry: None,
            catalog_path,
            renderer: RendererKind::Gpu,
            offscreen: false,
            width: 1280,
            height: 800,
            duration_ms: 60000,
            snapshot: None,
            state_file: None,
            isolation: IsolationMode::Off,
            actions: Vec::new(),
        };
        let mut seen = BTreeSet::new();
        let mut data_root_path = None;
        while let Some(flag) = args.next() {
            let flag = flag.to_str().context("DESKTOP_OPTION_ENCODING")?;
            if !seen.insert(flag.to_owned()) {
                bail!("DESKTOP_DUPLICATE_OPTION");
            }
            let value = args.next().context("DESKTOP_OPTION_VALUE")?;
            let text = value.to_str().context("DESKTOP_OPTION_ENCODING")?;
            match flag {
                "--data-root" => {
                    result.app_data.root = Some(crate::app_data::AppDataRoot::open(Path::new(&value)).context("DESKTOP_DATA_ROOT")?);
                    data_root_path = Some(PathBuf::from(value));
                }
                "--install-root" => {
                    result.install_root = Some(PathBuf::from(value));
                }
                "--persistent-install-state" => {
                    if !cfg!(target_os = "linux") { bail!("DESKTOP_PERSISTENT_INSTALL_UNSUPPORTED"); }
                    let root = PathBuf::from(value);
                    let raw = root.to_str().context("DESKTOP_PERSISTENT_ROOT_ENCODING")?;
                    if !root.is_absolute() || root.file_name().is_none() || raw.len() > 4096
                        || raw.ends_with('/') || raw.contains("//")
                        || raw.split('/').any(|part| part == "." || part == "..")
                    { bail!("DESKTOP_PERSISTENT_ROOT"); }
                    result.persistent_install_root = Some(root);
                }
                "--qualification-receipts" => {
                    if text != "persistent-install-pointer-ready-v1" { bail!("DESKTOP_QUALIFICATION_RECEIPTS"); }
                    result.persistent_install_receipts = true;
                }
                "--install-source" => {
                    let source = PathBuf::from(value);
                    if !source.is_absolute() || source.as_os_str().len() > 4096 {
                        bail!("DESKTOP_INSTALL_SOURCE");
                    }
                    result.install_source = Some(source);
                }
                "--renderer" => {
                    result.renderer = match text {
                        "cpu" => RendererKind::Cpu,
                        "gpu" => RendererKind::Gpu,
                        _ => bail!("DESKTOP_RENDERER"),
                    }
                }
                "--backend" => {
                    result.offscreen = match text {
                        "native" => false,
                        "offscreen" => true,
                        _ => bail!("DESKTOP_BACKEND"),
                    }
                }
                "--width" => result.width = text.parse().context("DESKTOP_WIDTH")?,
                "--height" => result.height = text.parse().context("DESKTOP_HEIGHT")?,
                "--duration-ms" => result.duration_ms = text.parse().context("DESKTOP_DURATION")?,
                "--snapshot" => result.snapshot = Some(value.into()),
                "--state-file" => result.state_file = Some(value.into()),
                "--isolation" => result.isolation = match text {
                    "off" => IsolationMode::Off,
                    "strict" => IsolationMode::Strict,
                    _ => bail!("DESKTOP_ISOLATION: expected off or strict"),
                },
                "--events" => {
                    result.actions = parse_desktop_event_script(&read_bounded(
                        Path::new(&value),
                        16384,
                        "DESKTOP_EVENTS",
                    )?)?;
                }
                _ => bail!("DESKTOP_UNKNOWN_OPTION: {flag}"),
            }
        }
        if result.persistent_install_receipts && (result.persistent_install_root.is_none()
            || result.offscreen || result.state_file.is_none()) {
            bail!("DESKTOP_QUALIFICATION_RECEIPTS_REQUIRES_PERSISTENT_NATIVE_STATE");
        }
        if let Some(root) = &result.persistent_install_root {
            let resolved_root = match fs::canonicalize(root) {
                Ok(path) => path,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => root.clone(),
                Err(error) => return Err(error).context("DESKTOP_PERSISTENT_ROOT"),
            };
            let resolved_data_root = data_root_path.map(fs::canonicalize).transpose().context("DESKTOP_DATA_ROOT")?;
            if result.install_root.is_some() || result.catalog_path.as_os_str() != root.join("catalog.json").as_os_str()
                || resolved_data_root.as_ref().is_some_and(|path| path.starts_with(&resolved_root))
                || result.install_source.as_ref().is_some_and(|path| path.starts_with(root))
            { bail!("DESKTOP_PERSISTENT_PATH_CONFLICT"); }
            result.state_file = result.state_file.as_ref().map(|path| persistent_output_path(path, &resolved_root)).transpose()?;
            result.snapshot = result.snapshot.as_ref().map(|path| persistent_output_path(path, &resolved_root)).transpose()?;
        }
        if !(320..=4096).contains(&result.width)
            || !(240..=4096).contains(&result.height)
            || i64::from(result.width) * i64::from(result.height) > 4_194_304
            || (result.duration_ms != 0 && !(100..=60000).contains(&result.duration_ms))
            || result.actions.len() > 128
        {
            bail!("DESKTOP_SESSION_LIMIT");
        }
        let mut previous = 0;
        for step in &result.actions {
            if step.after_ms < previous
                || step.after_ms
                    > if result.duration_ms == 0 {
                        3_600_000
                    } else {
                        result.duration_ms
                    }
                || (step.action.app() == 0 && !matches!(step.action, DesktopAction::Shutdown | DesktopAction::Install { .. }))
            {
                bail!("DESKTOP_EVENT_ORDER");
            }
            previous = step.after_ms;
            match &step.action {
                DesktopAction::Text { text, .. } => {
                    if text.is_empty() || text.len() > 512 {
                        bail!("DESKTOP_TEXT_LIMIT");
                    }
                }
                DesktopAction::Preedit {
                    text, start, end, ..
                } => {
                    if text.len() > 512
                        || !((*start == -1 && *end == -1)
                            || (*start >= 0
                                && *end >= 0
                                && (*start as usize) <= text.len()
                                && (*end as usize) <= text.len()
                                && text.is_char_boundary(*start as usize)
                                && text.is_char_boundary(*end as usize)))
                    {
                        bail!("DESKTOP_PREEDIT_INVALID");
                    }
                }
                DesktopAction::Key { key, modifiers, .. }
                    if !(1..=15).contains(key) || modifiers & !15 != 0 =>
                {
                    bail!("DESKTOP_KEY_INVALID");
                }
                DesktopAction::Install { source } => {
                    if !source.is_absolute() || source.as_os_str().len() > 4096 {
                        bail!("DESKTOP_INSTALL_SOURCE");
                    }
                }
                _ => {}
            }
            if let DesktopAction::Geometry {
                x,
                y,
                width,
                height,
                ..
            } = step.action
                && (!(-1_000_000..=1_000_000).contains(&x)
                    || !(-1_000_000..=1_000_000).contains(&y)
                    || !(1..=4096).contains(&width)
                    || !(1..=4096).contains(&height))
            {
                bail!("DESKTOP_EVENT_GEOMETRY");
            }
            if let DesktopAction::Click { x, y, .. } = step.action
                && (!(0..4096).contains(&x) || !(0..4096).contains(&y))
            {
                bail!("DESKTOP_EVENT_POINT");
            }
        }
        Ok(result)
    }
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;
    use std::{fmt::Write as _, os::unix::fs::PermissionsExt};

    fn hex(value: [u8; 32]) -> String {
        let mut text = String::with_capacity(64);
        for byte in value { write!(&mut text, "{byte:02x}").unwrap(); }
        text
    }

    #[test]
    fn catalog_v2_binds_each_entry_to_the_loaded_package_identity() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir(dir.path().join("app")).unwrap();
        std::fs::write(dir.path().join("app/main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(
            dir.path().join("app/app.json"),
            r#"{"schemaVersion":1,"id":"dev.example.installed","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#,
        )
        .unwrap();
        let catalog = dir.path().join("catalog.json");
        std::fs::write(
            &catalog,
            r#"{"schemaVersion":1,"apps":[{"id":1,"title":"Installed","manifest":"app/app.json"}],"autostart":[]}"#,
        )
        .unwrap();
        let fingerprint = load_catalog(&catalog).unwrap().apps[0].package_fingerprint;
        std::fs::write(
            &catalog,
            format!(
                r#"{{"schemaVersion":2,"apps":[{{"id":1,"title":"Installed","manifest":"app/app.json","identity":{{"packageId":"dev.example.installed","fingerprint":"{}"}}}}],"autostart":[]}}"#,
                hex(fingerprint),
            ),
        )
        .unwrap();
        assert!(load_catalog(&catalog).is_ok());
        std::fs::write(
            &catalog,
            format!(
                r#"{{"schemaVersion":2,"apps":[{{"id":1,"title":"Installed","manifest":"app/app.json","identity":{{"packageId":"dev.example.installed","fingerprint":"{}"}}}}],"autostart":[]}}"#,
                "0".repeat(64),
            ),
        )
        .unwrap();
        let error = load_catalog(&catalog).unwrap_err();
        assert!(format!("{error:#}").contains("DESKTOP_INSTALL_IDENTITY_MISMATCH"));
    }

    #[test]
    fn configured_install_registry_rejects_unregistered_and_removed_packages() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::create_dir(dir.path().join("app")).unwrap();
        std::fs::write(dir.path().join("app/main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        let manifest = dir.path().join("app/app.json");
        std::fs::write(
            &manifest,
            r#"{"schemaVersion":1,"id":"dev.example.installed","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#,
        )
        .unwrap();
        let catalog = dir.path().join("catalog.json");
        std::fs::write(&catalog, r#"{"schemaVersion":1,"apps":[{"id":1,"title":"Installed","manifest":"app/app.json"}],"autostart":[]}"#).unwrap();
        let registry = crate::package_install::PackageInstallRegistry::open(dir.path()).unwrap();
        let package = crate::package::load_package(&manifest).unwrap();
        assert!(format!("{:#}", load_catalog_with_install_registry(&catalog, &registry).unwrap_err()).contains("INSTALL_NOT_REGISTERED"));
        registry.install_package(&package).unwrap();
        assert!(load_catalog_with_install_registry(&catalog, &registry).is_ok());
        registry.uninstall(&package.manifest.id).unwrap();
        assert!(format!("{:#}", load_catalog_with_install_registry(&catalog, &registry).unwrap_err()).contains("INSTALL_PACKAGE_REMOVED"));
    }

    #[test]
    fn catalog_v3_resolves_only_from_the_installed_package_store() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let source = dir.path().join("source");
        std::fs::create_dir(&source).unwrap();
        std::fs::write(source.join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        let manifest = source.join("app.json");
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.installed","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        let registry_root = dir.path().join("registry");
        std::fs::create_dir(&registry_root).unwrap();
        std::fs::set_permissions(&registry_root, std::fs::Permissions::from_mode(0o700)).unwrap();
        let registry = crate::package_install::PackageInstallRegistry::open(&registry_root).unwrap();
        registry.install_from_manifest(&manifest).unwrap();
        let catalog = dir.path().join("catalog.json");
        std::fs::write(&catalog, r#"{"schemaVersion":3,"apps":[{"id":1,"title":"Installed","packageId":"dev.example.installed"}],"autostart":[]}"#).unwrap();
        let loaded = load_catalog_with_install_registry(&catalog, &registry).unwrap();
        assert!(loaded.0.apps[0].manifest_path.starts_with(&registry_root));
        assert!(loaded.0.apps[0].manifest_path != manifest);
    }
}
