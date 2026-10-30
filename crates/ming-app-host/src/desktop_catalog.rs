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
    path::{Path, PathBuf},
};
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
    manifest: PathBuf,
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
    if !matches!(disk.schema_version, 1 | 2)
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
        let manifest = if app.manifest.is_absolute() {
            app.manifest
        } else {
            parent.join(app.manifest)
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
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum IsolationMode { Off, Strict }
#[derive(Debug)]
pub struct DesktopOptions {
    pub(crate) app_data: DesktopDataConfig,
    pub install_root: Option<PathBuf>,
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
                }
                "--install-root" => {
                    result.install_root = Some(PathBuf::from(value));
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
                    result.actions = serde_json::from_slice(&read_bounded(
                        Path::new(&value),
                        16384,
                        "DESKTOP_EVENTS",
                    )?)
                    .context("DESKTOP_EVENTS_INVALID")?;
                }
                _ => bail!("DESKTOP_UNKNOWN_OPTION: {flag}"),
            }
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
                || (step.action.app() == 0 && !matches!(step.action, DesktopAction::Shutdown))
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
}
