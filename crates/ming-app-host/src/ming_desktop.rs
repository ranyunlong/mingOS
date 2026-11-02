//! Bounded multi-process desktop development session, shared by native/offscreen sinks.
use crate::{
    desktop_catalog::{Catalog, DesktopAction, DesktopOptions, load_catalog_with_data},
    desktop_model::{Effect, Rect, WindowId, WindowView},
    desktop_scene::DesktopScene,
    graphics::{RenderedFrame, RendererKind, compositor::Compositor},
    supervisor::{self, Options, Outcome},
    ui::{InputEvent, KeyLatch, PointerSample, ViewBridge},
};
use anyhow::{Context, Result, anyhow, bail};
use serde::Serialize;
use std::{
    collections::VecDeque,
    sync::{
        Arc,
        atomic::{AtomicU32, Ordering},
        mpsc::{self, Receiver, SyncSender, TryRecvError},
    },
    thread,
    time::{Duration, Instant},
};
#[cfg(target_os = "linux")]
use std::path::PathBuf;
#[cfg(target_os = "linux")]
use std::collections::BTreeSet;
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppRecord {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub module_source: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fuel_consumed: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub frame_clock: Option<crate::frame_clock_service::FrameClockStats>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub app_data: Option<supervisor::AppDataStats>,
    pub app: u16,
    pub generation: u64,
    pub window: u64,
    pub title: String,
    pub worker_pid: u32,
    pub status: &'static str,
    pub completed: bool,
    pub first_frame_ms: Option<u128>,
    pub first_ready_ms: Option<u128>,
    pub frames_submitted: Option<u64>,
    pub last_source_sequence: u64,
    pub presented_source_sequence: u64,
    pub presented_viewport: Option<(usize, usize)>,
    pub package_id: Option<String>,
    pub requests: Option<u64>,
    pub input_counts: Option<[u64; 9]>,
    pub events: Option<Vec<crate::broker::Event>>,
    pub gpu_stats: Option<crate::graphics::GpuStats>,
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub isolation: Option<supervisor::IsolationStatus>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Trace {
    at_ms: u128,
    app: u16,
    window: u64,
    generation: u64,
    kind: &'static str,
    detail: String,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopOutcome {
    pub runtime: &'static str,
    pub gpu: Option<crate::graphics::GpuInfo>,
    pub window_system: &'static str,
    pub presentation_backend: &'static str,
    pub width: i32,
    pub height: i32,
    pub duration_ms: u128,
    pub active_time_ms: u128,
    pub accepted_root: Option<crate::supervisor::PresentedFrameSummary>,
    pub presented_frame: Option<crate::supervisor::PresentedFrameSummary>,
    pub frames_composed: u64,
    pub frames_presented: u64,
    pub offscreen_frames: u64,
    pub max_concurrent_workers: usize,
    pub overlapping_worker_pids: Vec<u32>,
    pub active_workers_after_shutdown: usize,
    pub actions_executed: usize,
    pub instances: Vec<AppRecord>,
    pub trace: Vec<Trace>,
    pub dropped_trace: u64,
    pub dropped_inputs: u64,
    pub scene_gpu_stats: Option<crate::graphics::GpuStats>,
    pub shell_gpu_stats: Option<crate::graphics::GpuStats>,
    pub surface_gpu_stats: Option<crate::graphics::GpuStats>,
}
struct Instance {
    #[cfg(target_os = "linux")]
    update_startup: Option<UpdateStartupIdentity>,
    app_data: supervisor::AppDataService,
    app: u16,
    window: Option<WindowId>,
    record: usize,
    bridge: ViewBridge,
    events: SyncSender<InputEvent>,
    result: Receiver<Result<Outcome>>,
    worker: Option<thread::JoinHandle<()>>,
    pid: Arc<AtomicU32>,
    sequence: u64,
    closing: Option<Instant>,
    lifecycle_logged: crate::ui::WorkerLifecycle,
    deadline_cancel_logged: bool,
    pointer: PointerSample,
    keys: KeyLatch,
    isolation: Option<supervisor::StrictLaunch>,
    #[cfg(feature = "gpu")]
    gpu: Option<crate::graphics::GpuSession>,
}

#[cfg(any(target_os = "linux", test))]
#[derive(Clone, Debug, PartialEq, Eq)]
struct UpdateStartupIdentity {
    package_id: String,
    version: String,
    fingerprint: [u8; 32],
    app: u16,
    generation: u64,
    window: u64,
}

#[cfg(any(target_os = "linux", test))]
#[derive(Clone)]
struct UpdateStartupEvidence {
    identity: UpdateStartupIdentity,
    worker_pid: u32,
    input_ready: bool,
    presented_source_sequence: u64,
    completed: bool,
    result_accepted: bool,
    native_exit_ok: bool,
    reaped: bool,
}

#[cfg(any(target_os = "linux", test))]
impl UpdateStartupEvidence {
    fn confirms(&self, expected: &UpdateStartupIdentity) -> bool {
        self.identity == *expected && !expected.package_id.is_empty()
            && expected.app != 0 && expected.generation != 0 && expected.window != 0
            && self.worker_pid != 0 && self.input_ready && self.presented_source_sequence != 0
            && self.completed && self.result_accepted && self.native_exit_ok && self.reaped
    }
}
#[cfg(target_os = "linux")]
struct PendingDesktopStorage {
    app: u16,
    pending: DesktopStorageDecision,
    prompt: crate::graphics::desktop_shell::StoragePrompt,
    composed: Option<u64>,
    presented: bool,
    presented_sequence: Option<u64>,
}
#[cfg(target_os = "linux")]
enum DesktopStorageDecision {
    Install { package_id: String, version: String, fingerprint: String, manifest_path: PathBuf, update: bool, retry: bool },
    Management(crate::storage_consent::StorageManagement),
    PublicManagement(crate::storage_consent::PublicStorageManagement),
    Public(crate::storage_consent::PendingPublicConsent),
    Uninstall { package_id: String, version: String, fingerprint: String, manifest_path: PathBuf },
    UninstallBlocked,
    Purge { package_id: String, version: String, fingerprint: String, manifest_path: PathBuf, retry: bool },
    PurgeBlocked,
    Information,
}

#[cfg(target_os = "linux")]
fn package_fingerprint_hex(package: &crate::package::Package) -> String {
    let mut fingerprint = String::with_capacity(64);
    for byte in package.storage_fingerprint() {
        use std::fmt::Write as _;
        write!(&mut fingerprint, "{byte:02x}").expect("string write");
    }
    fingerprint
}
impl Instance {
    fn lifecycle_trace(&mut self) -> Vec<(Instant, &'static str, &'static str)> {
        let current = self.bridge.worker_lifecycle();
        let logged = std::mem::replace(&mut self.lifecycle_logged, current);
        let mut events = Vec::new();
        if logged.terminal_reply_written.is_none()
            && let Some((at, kind)) = current.terminal_reply_written
        {
            let detail = match kind {
                crate::ui::TerminalReplyKind::Close => "close",
                crate::ui::TerminalReplyKind::Cancel => "cancel",
            };
            events.push((at, "terminal-reply-written", detail));
        }
        if logged.result_accepted.is_none() && let Some(at) = current.result_accepted {
            events.push((at, "result-accepted", ""));
        }
        if logged.reaped.is_none() && let Some(at) = current.reaped {
            events.push((at, "worker-reaped", ""));
        }
        events
    }
}

pub(crate) struct Session {
    app_data: crate::desktop_catalog::DesktopDataConfig,
    #[cfg(target_os = "linux")]
    storage_authority: Option<Arc<crate::app_data::PackageStorageAuthority>>,
    #[cfg(target_os = "linux")]
    pending_storage: Option<PendingDesktopStorage>,
    pub options: DesktopOptions,
    pub catalog: Catalog,
    pub scene: DesktopScene,
    instances: Vec<Instance>,
    pending_autostart: VecDeque<u16>,
    #[cfg(target_os = "linux")]
    public_denied: BTreeSet<u16>,
    startup_limit: usize,
    records: Vec<AppRecord>,
    trace: Vec<Trace>,
    dropped_trace: u64,
    dropped_inputs: u64,
    #[cfg(feature = "gpu")]
    gpu: Option<crate::graphics::GpuSession>,
    started: Instant,
    last_tick: Instant,
    active_time: Duration,
    action_index: usize,
    pub closing: Option<Instant>,
    pub dirty: bool,
    sink_ready: bool,
    pub frames_composed: u64,
    pub frames_presented: u64,
    pub offscreen_frames: u64,
    max_concurrent_workers: usize,
    overlap: Vec<u32>,
    last_accepted: Option<(u64, usize, usize)>,
    state_revision: u64,
    state_last_write: Option<Instant>,
    state_session: String,
    state_boot: String,
    state_error: Option<String>,
    _strict_session: Option<StrictSessionLock>,
    next_isolation_uid: u32,
}
fn autostart_parallelism(strict: bool, cpus: usize) -> usize {
    if strict { cpus.saturating_sub(1).clamp(1, 4) } else { 4 }
}
impl Session {
    pub fn new(
        mut options: DesktopOptions,
        catalog: Catalog,
        #[cfg(feature = "gpu")] gpu: Option<crate::graphics::GpuSession>,
    ) -> Result<Self> {
        let strict_session = if options.isolation == crate::desktop_catalog::IsolationMode::Strict {
            Some(StrictSessionLock::acquire()?)
        } else { None };
        // A reviewed install script may launch the newly assigned app after the
        // install action has committed the catalog entry.  The entry cannot be
        // present in the initial catalog, so defer that one identity check to
        // the normal runtime lookup instead of rejecting the reviewed script at
        // session construction time.
        let reviewed_install_script = options.actions.iter().any(|step| matches!(step.action, DesktopAction::Install { .. }));
        for step in &options.actions {
            if !matches!(step.action, DesktopAction::Shutdown | DesktopAction::Install { .. })
                && !catalog.apps.iter().any(|a| a.id == step.action.app())
                && !(reviewed_install_script && matches!(step.action, DesktopAction::Launch { .. }) && (1..=4).contains(&step.action.app()))
            {
                bail!("DESKTOP_EVENT_UNKNOWN_APP");
            }
        }
        let compositor = match options.renderer {
            RendererKind::Cpu => Compositor::cpu(),
            RendererKind::Gpu => {
                #[cfg(feature = "gpu")]
                {
                    Compositor::gpu(gpu.as_ref().context("DESKTOP_GPU_SESSION")?)?
                }
                #[cfg(not(feature = "gpu"))]
                {
                    bail!("GPU_FEATURE_REQUIRED");
                }
            }
        };
        let now = Instant::now();
        let scene = DesktopScene::new(options.width, options.height, compositor)?;
        let app_data = std::mem::take(&mut options.app_data);
        let startup_limit = autostart_parallelism(options.isolation == crate::desktop_catalog::IsolationMode::Strict && options.duration_ms == 0, thread::available_parallelism().map_or(1, |n| n.get()));
        Ok(Self {
            app_data,
            #[cfg(target_os = "linux")]
            storage_authority: None,
            #[cfg(target_os = "linux")]
            pending_storage: None,
            options,
            catalog,
            scene,
            instances: Vec::new(),
            pending_autostart: VecDeque::new(),
            #[cfg(target_os = "linux")]
            public_denied: BTreeSet::new(),
            startup_limit,
            records: Vec::new(),
            trace: Vec::new(),
            dropped_trace: 0,
            dropped_inputs: 0,
            #[cfg(feature = "gpu")]
            gpu,
            started: now,
            last_tick: now,
            active_time: Duration::ZERO,
            action_index: 0,
            closing: None,
            dirty: true,
            sink_ready: false,
            frames_composed: 0,
            frames_presented: 0,
            offscreen_frames: 0,
            max_concurrent_workers: 0,
            overlap: Vec::new(),
            last_accepted: None,
            state_revision: 0,
            state_last_write: None,
            state_session: format!(
                "ming-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_nanos()
            ),
            state_boot: std::fs::read_to_string("/proc/sys/kernel/random/boot_id")
                .map(|s| s.trim().to_owned())
                .unwrap_or_else(|_| "host-process".into()),
            state_error: None,
            _strict_session: strict_session,
            next_isolation_uid: 20000,
        })
    }
    fn log_at(&mut self, at: Instant, app: u16, window: u64, generation: u64, kind: &'static str, detail: String) {
        if self.trace.len() < 512 {
            self.trace.push(Trace {
                at_ms: at.saturating_duration_since(self.started).as_millis(),
                app,
                window,
                generation,
                kind,
                detail,
            });
        } else {
            self.dropped_trace += 1;
        }
    }
    fn log(&mut self, app: u16, window: u64, generation: u64, kind: &'static str, detail: String) {
        self.log_at(Instant::now(), app, window, generation, kind, detail);
    }
    pub fn start(&mut self) -> Result<()> {
        if self.options.isolation == crate::desktop_catalog::IsolationMode::Strict && self.options.duration_ms == 0 {
            let observed_cpus = thread::available_parallelism().ok().map(|n| n.get());
            self.log(0, 0, 0, "startup_budget", serde_json::json!({"observedCpus":observed_cpus,"limit":self.startup_limit}).to_string());
        }
        self.pending_autostart = self.catalog.autostart.clone().into();
        self.launch_pending_autostart()?;
        self.write_state(true)?;
        Ok(())
    }
    fn take_pending_autostart(&mut self) -> Option<u16> {
        #[cfg(target_os = "linux")]
        if self.pending_storage.is_some() { return None; }
        if self.pending_autostart.is_empty() || self.closing.is_some() {
            return None;
        }
        if self.options.isolation == crate::desktop_catalog::IsolationMode::Strict && self.options.duration_ms == 0 {
            let initializing = self.instances.iter().filter(|instance| {
                !self.records[instance.record].isolation.as_ref().is_some_and(|status| {
                    status.applied && status.limits.as_ref().is_some_and(|limits| {
                        limits.cpu_max == crate::linux_isolation::STEADY_CPU_MAX
                    })
                })
            }).count();
            if initializing >= self.startup_limit {
                return None;
            }
        }
        self.pending_autostart.pop_front()
    }
    fn launch_pending_autostart(&mut self) -> Result<()> {
        while let Some(app) = self.take_pending_autostart() {
            self.launch(app)?;
        }
        Ok(())
    }
    pub fn views(&self) -> Vec<WindowView> {
        self.scene.model().views()
    }
    pub fn has_frame(&self, id: WindowId) -> bool {
        self.instances
            .iter()
            .any(|a| a.window == Some(id) && a.sequence > 0)
    }
    pub fn app_status(&self, app: u16) -> &'static str {
        if let Some(instance) = self.instances.iter().find(|a| a.app == app) {
            if instance.closing.is_some() {
                return "closing";
            }
            if instance
                .window
                .and_then(|w| self.scene.model().view(w).ok())
                .is_some_and(|v| v.minimized)
            {
                return "minimized";
            }
            return self.records[instance.record].status;
        }
        self.records
            .iter()
            .rev()
            .find(|r| r.app == app)
            .map_or("closed", |r| r.status)
    }
    fn window(&self, app: u16) -> Result<WindowId> {
        self.instances
            .iter()
            .find(|a| a.app == app)
            .and_then(|a| a.window)
            .context("DESKTOP_APP_NOT_RUNNING")
    }
    pub fn focused(&self) -> Option<WindowId> {
        self.views()
            .iter()
            .find(|w| w.focused && !w.minimized)
            .map(|w| w.id)
    }
    pub fn input_bridge(&self, id: WindowId) -> Option<ViewBridge> {
        if self.closing.is_some() {
            return None;
        }
        #[cfg(target_os = "linux")]
        if self.pending_storage.is_some() { return None; }
        self.instances
            .iter()
            .find(|instance| {
                instance.window == Some(id)
                    && instance.closing.is_none()
                    && !instance.bridge.closed()
            })
            .map(|instance| instance.bridge.clone())
    }
    pub fn note_input_drop(&mut self, id: WindowId, reason: &'static str) {
        let Some(instance) = self
            .instances
            .iter()
            .find(|instance| instance.window == Some(id))
        else {
            return;
        };
        let app = instance.app;
        let generation = self.records[instance.record].generation;
        self.dropped_inputs += 1;
        self.log(app, id.0, generation, "input_dropped", reason.into());
    }
    pub fn pointer_sink_ready(&self) -> bool {
        self.sink_ready
    }
    pub fn changed(&mut self) {
        self.dirty = true;
        self.sink_ready = false;
        #[cfg(target_os = "linux")]
        if let Some(pending) = &mut self.pending_storage {
            pending.composed = None;
            pending.presented = false;
            pending.presented_sequence = None;
        }
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn storage_prompt(&self) -> Option<&crate::graphics::desktop_shell::StoragePrompt> {
        self.pending_storage.as_ref().map(|pending| &pending.prompt)
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn storage_prompt_composed(&mut self, sequence: u64) {
        if let Some(pending) = &mut self.pending_storage { pending.composed = Some(sequence); }
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn storage_prompt_target(&self, x: i32, y: i32) -> Option<(bool, u64)> {
        let pending = self.pending_storage.as_ref()?;
        if !pending.presented || !self.sink_ready { return None; }
        let sequence = pending.presented_sequence?;
        let geometry = crate::graphics::desktop_shell::storage_prompt_geometry(self.options.width, self.options.height, !pending.prompt.has_allow())?;
        if geometry.deny.contains(x, y) { Some((false, sequence)) }
        else if geometry.allow.is_some_and(|r| r.contains(x, y)) { Some((true, sequence)) }
        else { None }
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn respond_storage_prompt(&mut self, allow: bool) -> Result<()> {
        let pending = self.pending_storage.as_ref().context("DESKTOP_STORAGE_NO_PROMPT")?;
        if !pending.presented || !self.sink_ready { bail!("DESKTOP_STORAGE_NOT_PRESENTED"); }
        if allow && matches!(&pending.pending, DesktopStorageDecision::Information) { bail!("DESKTOP_STORAGE_CONFLICT"); }
        let pending = self.pending_storage.take().expect("checked pending");
        let app = pending.app;
        match pending.pending {
            DesktopStorageDecision::Install { package_id, version, fingerprint, manifest_path, update, retry: _ } => {
                if !allow {
                    self.changed();
                    self.launch_pending_autostart()?;
                    return Ok(());
                }
                let registry = self.options.install_registry.as_ref().context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?;
                let package = crate::package::load_package(&manifest_path).context("DESKTOP_INSTALL_PACKAGE")?;
                let actual_fingerprint = package_fingerprint_hex(&package);
                if package.manifest.id != package_id || package.manifest.version != version || actual_fingerprint != fingerprint {
                    self.log(0, 0, 0, "install_not_saved", "package identity changed".into());
                    let prompt = crate::graphics::desktop_shell::StoragePrompt::install(&package_id, &version, &fingerprint, update, true, false)?;
                    self.pending_storage = Some(PendingDesktopStorage { app, pending: DesktopStorageDecision::Install { package_id, version, fingerprint, manifest_path, update, retry: true }, prompt, composed: None, presented: false, presented_sequence: None });
                    self.changed();
                    return Ok(());
                }
                if let Err(error) = registry.verify_package_source(&package) {
                    self.log(0, 0, 0, "install_not_saved", format!("{error:#}"));
                    let prompt = crate::graphics::desktop_shell::StoragePrompt::install(&package_id, &version, &fingerprint, update, true, false)?;
                    self.pending_storage = Some(PendingDesktopStorage { app, pending: DesktopStorageDecision::Install { package_id, version, fingerprint, manifest_path, update, retry: true }, prompt, composed: None, presented: false, presented_sequence: None });
                    self.changed();
                    return Ok(());
                }
                let previous_catalog = self.catalog.clone();
                let authority = if update {
                    if self.storage_authority.is_none() {
                        let root = self.app_data.root.clone().context("DESKTOP_DATA_ROOT_REQUIRED")?;
                        self.storage_authority = Some(Arc::new(crate::app_data::PackageStorageAuthority::open_default_private(root).context("DESKTOP_STORAGE_AUTHORITY")?));
                    }
                    self.storage_authority.clone()
                } else { None };
                let install_rollback = if update {
                    None
                } else {
                    Some(registry.prepare_install_rollback(&package_id)?)
                };
                let install_result = if update {
                    registry.update_from_manifest(&manifest_path, authority.as_deref().context("DESKTOP_STORAGE_AUTHORITY")?)
                } else {
                    registry.install_from_manifest(&manifest_path)
                };
                let install_state = match install_result {
                    Ok(state) => state,
                    Err(error) => {
                    self.log(0, 0, 0, "install_not_saved", format!("{error:#}"));
                    let prompt = crate::graphics::desktop_shell::StoragePrompt::install(&package_id, &version, &fingerprint, update, true, false)?;
                    self.pending_storage = Some(PendingDesktopStorage { app, pending: DesktopStorageDecision::Install { package_id, version, fingerprint, manifest_path, update, retry: true }, prompt, composed: None, presented: false, presented_sequence: None });
                    self.changed();
                    return Ok(());
                    }
                };
                let active_manifest = registry.active_manifest_path(&package_id)?;
                if update {
                    let entry = self.catalog.apps.iter_mut().find(|entry| entry.package_id == package_id).context("DESKTOP_INSTALL_CATALOG_ENTRY")?;
                    entry.manifest_path = active_manifest;
                    entry.package_fingerprint = package.storage_fingerprint();
                } else {
                    let id = self.catalog.apps.iter().map(|entry| entry.id).max().unwrap_or(0).checked_add(1).context("DESKTOP_INSTALL_APP_ID")?;
                    self.catalog.apps.push(crate::desktop_catalog::CatalogEntry {
                        id,
                        title: package.manifest.id.chars().take(20).collect(),
                        manifest_path: active_manifest,
                        package_id: package_id.clone(),
                        package_fingerprint: package.storage_fingerprint(),
                    });
                }
                if let Err(error) = crate::desktop_catalog::persist_installed_catalog(&self.options.catalog_path, &self.catalog, &self.app_data.grants) {
                    self.catalog = previous_catalog;
                    if update {
                        if let Some(authority) = authority.as_deref() { let _ = registry.rollback_pending_update(authority); }
                    } else if !matches!(install_state, crate::package_install::InstallState::Unchanged) {
                        if let Some(rollback) = install_rollback {
                            let _ = registry.rollback_install(rollback, &version, package.storage_fingerprint());
                        }
                    }
                    self.log(0, 0, 0, "install_not_saved", format!("{error:#}"));
                    let prompt = crate::graphics::desktop_shell::StoragePrompt::install(&package_id, &version, &fingerprint, update, true, false)?;
                    self.pending_storage = Some(PendingDesktopStorage { app, pending: DesktopStorageDecision::Install { package_id, version, fingerprint, manifest_path, update, retry: true }, prompt, composed: None, presented: false, presented_sequence: None });
                    self.changed();
                    return Ok(());
                }
                self.log(0, 0, 0, if update { "updated" } else { "installed" }, package_id);
                self.changed();
                self.launch_pending_autostart()?;
                return Ok(());
            }
            DesktopStorageDecision::Management(management) => {
                if allow {
                    match management.revoke() {
                        Ok(receipt) if receipt.persisted && receipt.directory_synced => {
                            self.log(app, 0, 0, "storage_revoked", String::new());
                        }
                        result => {
                            self.log(app, 0, 0, "storage_revoke_not_saved", format!("{result:?}"));
                            let prompt = crate::graphics::desktop_shell::StoragePrompt::management(management.app_id(), management.fingerprint().as_deref(), true)?;
                            self.pending_storage = Some(PendingDesktopStorage { app, pending: DesktopStorageDecision::Management(management), prompt, composed: None, presented: false, presented_sequence: None });
                            self.changed();
                            return Ok(());
                        }
                    }
                }
                self.changed();
                self.launch_pending_autostart()?;
                return Ok(());
            }
            DesktopStorageDecision::PublicManagement(management) => {
                if allow {
                    match management.revoke() {
                        Ok(receipt) if receipt.persisted && receipt.directory_synced => {
                            self.log(app, 0, 0, "public_storage_revoked", String::new());
                        }
                        result => {
                            self.log(app, 0, 0, "public_storage_revoke_not_saved", format!("{result:?}"));
                            let prompt = crate::graphics::desktop_shell::StoragePrompt::public_management(management.app_id(), management.fingerprint().as_deref(), true)?;
                            self.pending_storage = Some(PendingDesktopStorage { app, pending: DesktopStorageDecision::PublicManagement(management), prompt, composed: None, presented: false, presented_sequence: None });
                            self.changed();
                            return Ok(());
                        }
                    }
                }
                self.changed();
                self.launch_pending_autostart()?;
                return Ok(());
            }
            DesktopStorageDecision::Uninstall { package_id, version, fingerprint, manifest_path } => {
                if allow {
                    let package = crate::package::load_package(&manifest_path).context("DESKTOP_UNINSTALL_PACKAGE")?;
                    let actual_fingerprint = package_fingerprint_hex(&package);
                    if package.manifest.id != package_id
                        || package.manifest.version != version
                        || actual_fingerprint != fingerprint
                    {
                        self.changed();
                        bail!("DESKTOP_PACKAGE_CHANGED");
                    }
                    self.options
                        .install_registry
                        .as_ref()
                        .context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?
                        .verify_package(&package)
                        .context("DESKTOP_UNINSTALL_PACKAGE")?;
                    let result = self
                        .options
                        .install_registry
                        .as_ref()
                        .context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?
                        .uninstall_with_data_policy(&package_id, crate::package_install::UninstallDataPolicy::PreservePrivateData);
                    match result {
                        Ok(_) => {
                            self.catalog.apps.retain(|entry| entry.package_id != package_id);
                            self.catalog.autostart.retain(|id| self.catalog.apps.iter().any(|entry| entry.id == *id));
                            self.pending_autostart.retain(|id| self.catalog.apps.iter().any(|entry| entry.id == *id));
                            self.log(app, 0, 0, "uninstalled", package_id);
                        }
                        Err(error) => {
                            self.log(app, 0, 0, "uninstall_not_saved", format!("{error:#}"));
                            let prompt = crate::graphics::desktop_shell::StoragePrompt::uninstall(&package_id, &version, &fingerprint, true)?;
                            self.pending_storage = Some(PendingDesktopStorage {
                                app,
                                pending: DesktopStorageDecision::Uninstall { package_id, version, fingerprint, manifest_path },
                                prompt,
                                composed: None,
                                presented: false,
                                presented_sequence: None,
                            });
                            self.changed();
                            return Ok(());
                        }
                    }
                }
                self.changed();
                self.launch_pending_autostart()?;
                return Ok(());
            }
            DesktopStorageDecision::UninstallBlocked => {
                self.changed();
                self.launch_pending_autostart()?;
                return Ok(());
            }
            DesktopStorageDecision::Purge { package_id, version, fingerprint, manifest_path, retry } => {
                if allow {
                    let package = crate::package::load_package(&manifest_path).context("DESKTOP_UNINSTALL_PACKAGE")?;
                    let actual_fingerprint = package_fingerprint_hex(&package);
                    if package.manifest.id != package_id
                        || package.manifest.version != version
                        || actual_fingerprint != fingerprint
                    {
                        self.changed();
                        bail!("DESKTOP_PACKAGE_CHANGED");
                    }
                    let registry = self.options.install_registry.as_ref().context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?;
                    if retry {
                        registry.verify_package_identity(&package).context("DESKTOP_UNINSTALL_PACKAGE")?;
                        if !registry.is_removed(&package_id)? {
                            self.changed();
                            bail!("DESKTOP_PACKAGE_STATE_CHANGED");
                        }
                    } else {
                        registry.verify_package(&package).context("DESKTOP_UNINSTALL_PACKAGE")?;
                    }
                    let root = self.app_data.root.clone().context("DESKTOP_DATA_ROOT_REQUIRED")?;
                    let authority = if let Some(authority) = &self.storage_authority {
                        authority.clone()
                    } else {
                        let authority = Arc::new(crate::app_data::PackageStorageAuthority::open_default_private(root).context("DESKTOP_STORAGE_AUTHORITY")?);
                        self.storage_authority = Some(authority.clone());
                        authority
                    };
                    let result = self
                        .options
                        .install_registry
                        .as_ref()
                        .context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?
                        .uninstall_with_data_policy(&package_id, crate::package_install::UninstallDataPolicy::PurgePrivateData(&authority));
                    match result {
                        Ok(_) => {
                            self.catalog.apps.retain(|entry| entry.package_id != package_id);
                            self.catalog.autostart.retain(|id| self.catalog.apps.iter().any(|entry| entry.id == *id));
                            self.pending_autostart.retain(|id| self.catalog.apps.iter().any(|entry| entry.id == *id));
                            self.log(app, 0, 0, "uninstalled_and_purged", package_id);
                        }
                        Err(error) => {
                            let removed = self.options.install_registry.as_ref()
                                .and_then(|registry| registry.is_removed(&package_id).ok())
                                .unwrap_or(false);
                            if removed {
                                self.catalog.apps.retain(|entry| entry.package_id != package_id);
                                self.catalog.autostart.retain(|id| self.catalog.apps.iter().any(|entry| entry.id == *id));
                                self.pending_autostart.retain(|id| self.catalog.apps.iter().any(|entry| entry.id == *id));
                                self.log(app, 0, 0, "private_data_purge_not_saved", format!("{error:#}"));
                                let prompt = crate::graphics::desktop_shell::StoragePrompt::purge_retry(&package_id, &version, &fingerprint)?;
                                self.pending_storage = Some(PendingDesktopStorage {
                                    app,
                                    pending: DesktopStorageDecision::Purge { package_id, version, fingerprint, manifest_path, retry: true },
                                    prompt,
                                    composed: None,
                                    presented: false,
                                    presented_sequence: None,
                                });
                                self.changed();
                                return Ok(());
                            }
                            self.log(app, 0, 0, "uninstall_not_saved", format!("{error:#}"));
                            let prompt = crate::graphics::desktop_shell::StoragePrompt::purge(&package_id, &version, &fingerprint, false)?;
                            self.pending_storage = Some(PendingDesktopStorage {
                                app,
                                pending: DesktopStorageDecision::Purge { package_id, version, fingerprint, manifest_path, retry: false },
                                prompt,
                                composed: None,
                                presented: false,
                                presented_sequence: None,
                            });
                            self.changed();
                            return Ok(());
                        }
                    }
                }
                self.changed();
                self.launch_pending_autostart()?;
                return Ok(());
            }
            DesktopStorageDecision::PurgeBlocked => {
                self.changed();
                self.launch_pending_autostart()?;
                return Ok(());
            }
            DesktopStorageDecision::Information => {
                self.changed();
                self.launch_pending_autostart()?;
                return Ok(());
            }
            DesktopStorageDecision::Public(pending) => {
                if allow {
                    match pending.allow() {
                        Ok(granted) if granted.receipt.is_some_and(|receipt| receipt.persisted && receipt.directory_synced) => {
                            self.log(app, 0, 0, "public_storage_granted", String::new());
                        }
                        Ok(_) | Err(_) => {
                            let entry = self.catalog.apps.iter().find(|entry| entry.id == app).context("DESKTOP_UNKNOWN_APP")?;
                            let package = crate::package::load_package(&entry.manifest_path).context("DESKTOP_PUBLIC_STORAGE_PACKAGE")?;
                            let authority = self.storage_authority.as_ref().context("DESKTOP_STORAGE_AUTHORITY")?.clone();
                            let consent = crate::storage_consent::PublicStorageConsent::new(authority);
                            let pending = match consent.request_package(&package)? {
                                crate::storage_consent::PublicConsentRequest::Prompt(pending) => pending,
                                crate::storage_consent::PublicConsentRequest::Allowed(_) => bail!("DESKTOP_PUBLIC_STORAGE_NOT_DURABLE"),
                            };
                            let prompt = crate::graphics::desktop_shell::StoragePrompt::public_retry(&package.manifest.id)?;
                            self.pending_storage = Some(PendingDesktopStorage { app, pending: DesktopStorageDecision::Public(pending), prompt, composed: None, presented: false, presented_sequence: None });
                            self.changed();
                            return Ok(());
                        }
                    }
                }
                if !allow {
                    self.public_denied.insert(app);
                }
                self.changed();
                self.launch(app)?;
                return Ok(());
            }
        }
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn begin_storage_management(&mut self, app: u16) -> Result<()> {
        if self.pending_storage.is_some() || self.closing.is_some() || self.options.offscreen { bail!("DESKTOP_STORAGE_UNAVAILABLE"); }
        let entry = self.catalog.apps.iter().find(|entry| entry.id == app).context("DESKTOP_UNKNOWN_APP")?;
        let package = crate::package::load_package(&entry.manifest_path).ok();
        if self.storage_authority.is_none() && let Some(root) = self.app_data.root.clone() {
            self.storage_authority = crate::app_data::PackageStorageAuthority::open_persistent(root).ok().map(Arc::new);
        }
        let (pending, prompt) = match (package, self.storage_authority.as_ref()) {
            (Some(package), Some(authority)) => {
                let public = crate::storage_consent::PublicStorageConsent::new(authority.clone());
                if let Some(management) = public.management(&package.manifest.id)? {
                    let prompt = crate::graphics::desktop_shell::StoragePrompt::public_management(management.app_id(), management.fingerprint().as_deref(), management.revocation_pending())?;
                    (DesktopStorageDecision::PublicManagement(management), prompt)
                } else {
                let consent = crate::storage_consent::StorageConsent::new(authority.clone());
                match consent.management(&package.manifest.id) {
                    Ok(Some(management)) => {
                        let prompt = crate::graphics::desktop_shell::StoragePrompt::management(management.app_id(), management.fingerprint().as_deref(), management.revocation_pending())?;
                        (DesktopStorageDecision::Management(management), prompt)
                    }
                    Ok(None) => (DesktopStorageDecision::Information, crate::graphics::desktop_shell::StoragePrompt::status(Some(&package.manifest.id), true)?),
                    Err(_) => (DesktopStorageDecision::Information, crate::graphics::desktop_shell::StoragePrompt::status(Some(&package.manifest.id), false)?),
                }
                }
            }
            (package, _) => (DesktopStorageDecision::Information, crate::graphics::desktop_shell::StoragePrompt::status(package.as_ref().map(|p| p.manifest.id.as_str()), false)?),
        };
        self.pending_storage = Some(PendingDesktopStorage { app, pending, prompt, composed: None, presented: false, presented_sequence: None });
        self.reset_input()?;
        self.changed();
        Ok(())
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn begin_install_from_dock(&mut self) -> Result<()> {
        let source = self
            .options
            .install_source
            .clone()
            .context("DESKTOP_INSTALL_SOURCE_REQUIRED")?;
        self.begin_install(&source)
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn begin_install(&mut self, source: &std::path::Path) -> Result<()> {
        if self.pending_storage.is_some() || self.closing.is_some() || self.options.offscreen {
            bail!("DESKTOP_INSTALL_UNAVAILABLE");
        }
        if !source.is_absolute() {
            bail!("DESKTOP_INSTALL_SOURCE");
        }
        let registry = self.options.install_registry.as_ref().context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?;
        let package = crate::package::load_package(source).context("DESKTOP_INSTALL_PACKAGE")?;
        registry.verify_package_source(&package).context("DESKTOP_INSTALL_PUBLISHER")?;
        let existing = self.catalog.apps.iter().find(|entry| entry.package_id == package.manifest.id).cloned();
        let (update, app) = if let Some(entry) = existing {
            let current = crate::package::load_package(&entry.manifest_path).context("DESKTOP_INSTALL_CURRENT")?;
            if !crate::package::version_is_newer(&package.manifest.version, &current.manifest.version) {
                bail!("DESKTOP_INSTALL_VERSION");
            }
            (true, entry.id)
        } else {
            if self.catalog.apps.len() >= 4 { bail!("DESKTOP_CATALOG_LIMIT"); }
            (false, 0)
        };
        let mut fingerprint = package_fingerprint_hex(&package);
        let blocked = update && self.instances.iter().any(|instance| instance.app == app);
        let prompt = crate::graphics::desktop_shell::StoragePrompt::install(
            &package.manifest.id,
            &package.manifest.version,
            &fingerprint,
            update,
            false,
            blocked,
        )?;
        self.pending_storage = Some(PendingDesktopStorage {
            app,
            pending: DesktopStorageDecision::Install {
                package_id: package.manifest.id,
                version: package.manifest.version,
                fingerprint: std::mem::take(&mut fingerprint),
                manifest_path: source.to_path_buf(),
                update,
                retry: false,
            },
            prompt,
            composed: None,
            presented: false,
            presented_sequence: None,
        });
        self.reset_input()?;
        self.changed();
        Ok(())
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn begin_uninstall(&mut self, app: u16) -> Result<()> {
        if self.pending_storage.is_some() || self.closing.is_some() || self.options.offscreen {
            bail!("DESKTOP_UNINSTALL_UNAVAILABLE");
        }
        let entry = self.catalog.apps.iter().find(|entry| entry.id == app).context("DESKTOP_UNKNOWN_APP")?;
        let registry = self.options.install_registry.as_ref().context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?;
        let package = crate::package::load_package(&entry.manifest_path).context("DESKTOP_UNINSTALL_PACKAGE")?;
        registry.verify_package(&package).context("DESKTOP_UNINSTALL_PACKAGE")?;
        let (pending, prompt) = if self.instances.iter().any(|instance| instance.app == app) {
            (DesktopStorageDecision::UninstallBlocked, crate::graphics::desktop_shell::StoragePrompt::uninstall_blocked(&package.manifest.id)?)
        } else {
            let mut fingerprint = String::with_capacity(64);
            for byte in package.storage_fingerprint() { use std::fmt::Write as _; write!(&mut fingerprint, "{byte:02x}").expect("string write"); }
            (
                DesktopStorageDecision::Uninstall {
                    package_id: package.manifest.id.clone(),
                    version: package.manifest.version.clone(),
                    fingerprint: fingerprint.clone(),
                    manifest_path: entry.manifest_path.clone(),
                },
                crate::graphics::desktop_shell::StoragePrompt::uninstall(&package.manifest.id, &package.manifest.version, &fingerprint, false)?,
            )
        };
        self.pending_storage = Some(PendingDesktopStorage { app, pending, prompt, composed: None, presented: false, presented_sequence: None });
        self.reset_input()?;
        self.changed();
        Ok(())
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn begin_uninstall_with_purge(&mut self, app: u16) -> Result<()> {
        if self.pending_storage.is_some() || self.closing.is_some() || self.options.offscreen {
            bail!("DESKTOP_UNINSTALL_UNAVAILABLE");
        }
        let root = self.app_data.root.clone().context("DESKTOP_DATA_ROOT_REQUIRED")?;
        if self.storage_authority.is_none() {
            let authority = crate::app_data::PackageStorageAuthority::open_default_private(root)
                .context("DESKTOP_STORAGE_AUTHORITY")?;
            self.storage_authority = Some(Arc::new(authority));
        }
        let entry = self.catalog.apps.iter().find(|entry| entry.id == app).context("DESKTOP_UNKNOWN_APP")?;
        let registry = self.options.install_registry.as_ref().context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?;
        let package = crate::package::load_package(&entry.manifest_path).context("DESKTOP_UNINSTALL_PACKAGE")?;
        registry.verify_package(&package).context("DESKTOP_UNINSTALL_PACKAGE")?;
        let (pending, prompt) = if self.instances.iter().any(|instance| instance.app == app) {
            (DesktopStorageDecision::PurgeBlocked, crate::graphics::desktop_shell::StoragePrompt::purge_blocked(&package.manifest.id)?)
        } else {
            let mut fingerprint = String::with_capacity(64);
            for byte in package.storage_fingerprint() { use std::fmt::Write as _; write!(&mut fingerprint, "{byte:02x}").expect("string write"); }
            (
                DesktopStorageDecision::Purge {
                    package_id: package.manifest.id.clone(),
                    version: package.manifest.version.clone(),
                    fingerprint: fingerprint.clone(),
                    manifest_path: entry.manifest_path.clone(),
                    retry: false,
                },
                crate::graphics::desktop_shell::StoragePrompt::purge(&package.manifest.id, &package.manifest.version, &fingerprint, false)?,
            )
        };
        self.pending_storage = Some(PendingDesktopStorage { app, pending, prompt, composed: None, presented: false, presented_sequence: None });
        self.reset_input()?;
        self.changed();
        Ok(())
    }
    fn effects(&mut self, effects: Vec<Effect>) -> Result<()> {
        for effect in effects {
            match effect {
                Effect::ResetInput(id) => {
                    if let Some(a) = self.instances.iter_mut().find(|a| a.window == Some(id)) {
                        a.bridge.clear_input();
                        a.pointer.cancel();
                        a.keys.cancel();
                    }
                }
                Effect::Resize {
                    window,
                    width,
                    height,
                } => {
                    if let Some(a) = self.instances.iter().find(|a| a.window == Some(window)) {
                        a.bridge.resize(width, height);
                    }
                }
                Effect::Close(id) => {
                    let mut close_trace = None;
                    if let Some(a) = self.instances.iter_mut().find(|a| a.window == Some(id)) {
                        let at = Instant::now();
                        a.bridge.request_close();
                        a.closing = Some(at);
                        a.window = None;
                        self.records[a.record].status = "closing";
                        let record = &self.records[a.record];
                        close_trace = Some((at, record.app, record.window, record.generation));
                    }
                    if let Some((at, app, window, generation)) = close_trace {
                        self.log_at(at, app, window, generation, "close-request", String::new());
                    }
                }
                Effect::Focus(_) | Effect::Visibility { .. } => {}
            }
        }
        self.scene.sync();
        Ok(())
    }
    fn launch(&mut self, app: u16) -> Result<()> {
        if self.closing.is_some() {
            bail!("DESKTOP_CLOSING");
        }
        if let Some(existing) = self.instances.iter().find(|a| a.app == app) {
            let Some(id) = existing.window else {
                return Ok(());
            };
            let effects = self.scene.model_mut().restore(id).map_err(model_error)?;
            self.effects(effects)?;
            self.changed();
            return Ok(());
        }
        if self.instances.len() >= 4 || self.records.len() >= 128 {
            bail!("DESKTOP_INSTANCE_LIMIT");
        }
        let entry = self
            .catalog
            .apps
            .iter()
            .find(|e| e.id == app)
            .cloned()
            .context("DESKTOP_UNKNOWN_APP")?;
        #[cfg(target_os = "linux")]
        let (granted, public_granted) = if let Some(root) = self.app_data.root.clone() {
            let package = crate::package::load_package(&entry.manifest_path).context("DESKTOP_STORAGE_PACKAGE")?;
            if package.manifest.id != entry.package_id { bail!("DESKTOP_PACKAGE_ID_CHANGED"); }
            if package.storage_fingerprint() != entry.package_fingerprint { bail!("DESKTOP_PACKAGE_CHANGED"); }
            if package.manifest.sdk_version >= 6 {
                if self.storage_authority.as_ref().is_none_or(|authority| {
                    package.manifest.public_storage && !authority.supports_public_persistence()
                }) {
                    let authority = if package.manifest.public_storage {
                        crate::app_data::PackageStorageAuthority::open_persistent(root).context("DESKTOP_STORAGE_AUTHORITY")?
                    } else {
                        crate::app_data::PackageStorageAuthority::open_default_private(root).context("DESKTOP_STORAGE_AUTHORITY")?
                    };
                    self.storage_authority = Some(Arc::new(authority));
                }
                let session = self.storage_authority.as_ref().expect("initialized").default_private_session_for_package(&package).context("DESKTOP_PRIVATE_STORAGE")?;
                let public = if package.manifest.public_storage && !self.public_denied.remove(&app) {
                    let authority = self.storage_authority.as_ref().expect("initialized").clone();
                    let consent = crate::storage_consent::PublicStorageConsent::new(authority);
                    match consent.request_package(&package)? {
                        crate::storage_consent::PublicConsentRequest::Allowed(granted) => Some((granted.app_id, granted.session)),
                        crate::storage_consent::PublicConsentRequest::Prompt(pending) if !self.options.offscreen => {
                            let prompt = crate::graphics::desktop_shell::StoragePrompt::public_request(&package.manifest.id)?;
                            self.pending_storage = Some(PendingDesktopStorage { app, pending: DesktopStorageDecision::Public(pending), prompt, composed: None, presented: false, presented_sequence: None });
                            self.reset_input()?;
                            self.changed();
                            return Ok(());
                        }
                        crate::storage_consent::PublicConsentRequest::Prompt(_) => None,
                    }
                } else { None };
                (Some(crate::storage_consent::GrantedStorage { package, session, receipt: None }), public)
            } else { (None, None) }
        } else { (None, None) };
        self.launch_resolved(entry, #[cfg(target_os = "linux")] granted, #[cfg(target_os = "linux")] public_granted)
    }
    fn launch_resolved(&mut self, entry: crate::desktop_catalog::CatalogEntry, #[cfg(target_os = "linux")] granted: Option<crate::storage_consent::GrantedStorage>, #[cfg(target_os = "linux")] public_granted: Option<(String, crate::app_data::PublicFileSession)>) -> Result<()> {
        let app = entry.id;
        let grant = match (&self.app_data.root, self.app_data.grants.get(&app)) {
            (Some(root), Some(binding)) => Some(root.grant(&binding.data_key, &binding.slots).context("DESKTOP_DATA_OPEN")?),
            _ => None,
        };
        let app_data = supervisor::AppDataService::new(grant);
        #[cfg(target_os = "linux")]
        let app_data = match granted { Some(granted) => app_data.with_private_files_for_package(granted.session, granted.package), None => app_data };
        #[cfg(target_os = "linux")]
        let app_data = match public_granted { Some((package_id, session)) => {
            let package = crate::package::load_package(&entry.manifest_path).context("DESKTOP_PUBLIC_STORAGE_PACKAGE")?;
            if package.manifest.id != package_id { bail!("DESKTOP_PUBLIC_STORAGE_ID_CHANGED"); }
            app_data.with_public_files_for_package(session, package)
        }, None => app_data };
        #[cfg(feature = "gpu")]
        let context = if let Some(root) = &self.gpu {
            Some(root.fork_render_context()?)
        } else {
            None
        };
        let (bridge, events) = ViewBridge::new();
        let pid = Arc::new(AtomicU32::new(0));
        let mut options = Options::new(entry.manifest_path);
        options.preview = true;
        options.timeout_ms = 300000;
        options.fuel = 300000000;
        options.live = Some(bridge.clone());
        options.renderer = self.options.renderer;
        let worker_pid = pid.clone();
        let persistent = self.options.duration_ms == 0;
        #[cfg(feature = "gpu")]
        {
            options.gpu_session = context.clone();
        }
        options.validate()?;
        let generation = self
            .records
            .iter()
            .filter(|r| r.app == app)
            .map(|r| r.generation)
            .max()
            .unwrap_or(0)
            + 1;
        let isolation = if self.options.isolation == crate::desktop_catalog::IsolationMode::Strict {
            self.next_isolation_uid = self.next_isolation_uid.checked_add(1).context("ISOLATION_IDENTITY_EXHAUSTED")?;
            Some(supervisor::StrictLaunch::new(app, generation, self.next_isolation_uid)?)
        } else { None };
        let (window, effects) = self.scene.model_mut().open(app).map_err(model_error)?;
        #[cfg(target_os = "linux")]
        let update_startup = if let Some(registry) = self.options.install_registry.as_ref() {
            match registry.pending_update()?.filter(|pending| pending.package_id == entry.package_id) {
                Some(pending) => {
                    if pending.new_fingerprint != entry.package_fingerprint { bail!("DESKTOP_UPDATE_PACKAGE_CHANGED"); }
                    registry.begin_update_startup(&pending.package_id, pending.new_fingerprint)?;
                    Some(UpdateStartupIdentity { package_id: pending.package_id, version: pending.new_version,
                        fingerprint: pending.new_fingerprint, app, generation, window: window.0 })
                }
                None => None,
            }
        } else { None };
        let record = self.records.len();
        self.records.push(AppRecord {
            module_source: None, fuel_consumed: None,
            frame_clock: None,
            app_data: app_data.snapshot(),
            app,
            generation,
            window: window.0,
            title: entry.title,
            worker_pid: 0,
            status: "starting",
            completed: false,
            first_frame_ms: None,
            first_ready_ms: None,
            frames_submitted: None,
            last_source_sequence: 0,
            presented_source_sequence: 0,
            presented_viewport: None,
            package_id: None,
            requests: None,
            input_counts: None,
            events: None,
            gpu_stats: None,
            error: None,
            isolation: isolation.as_ref().map(|s|s.snapshot()),
        });
        let (tx, result) = mpsc::sync_channel(1);
        self.instances.push(Instance {
            #[cfg(target_os = "linux")]
            update_startup,
            app_data: app_data.clone(),
            app,
            window: Some(window),
            record,
            bridge,
            events,
            result,
            worker: None,
            pid,
            sequence: 0,
            closing: None,
            lifecycle_logged: Default::default(),
            deadline_cancel_logged: false,
            pointer: PointerSample::default(),
            keys: KeyLatch::default(),
            isolation: isolation.clone(),
            #[cfg(feature = "gpu")]
            gpu: context,
        });
        self.effects(effects)?;
        match thread::Builder::new()
            .name(format!("ming-app-{app}-{generation}"))
            .spawn(move || {
                let _ = tx.send(supervisor::run_desktop(options, worker_pid, persistent, supervisor::DesktopLaunchContext { isolation, app_data }));
            }) {
            Ok(worker) => {
                self.instances.last_mut().expect("inserted instance").worker = Some(worker);
            }
            Err(e) => {
                if let Some(instance) = self.instances.pop() { instance.app_data.revoke(); }
                self.records[record].completed = true;
                self.records[record].status = "failed";
                self.records[record].error = Some(format!("DESKTOP_THREAD_SPAWN: {e}"));
                let effects = self.scene.model_mut().close(window).map_err(model_error)?;
                self.effects(effects)?;
                self.reset_input()?;
                bail!("DESKTOP_THREAD_SPAWN: {e}");
            }
        }
        self.log(app, window.0, generation, "launch", String::new());
        self.changed();
        Ok(())
    }
    pub fn perform(&mut self, action: &DesktopAction) -> Result<()> {
        if matches!(action, DesktopAction::Shutdown) {
            return self.begin_shutdown();
        }
        if let DesktopAction::Install { source } = action {
            #[cfg(target_os = "linux")]
            { return self.begin_install(source); }
            #[cfg(not(target_os = "linux"))]
            { let _ = source; bail!("DESKTOP_INSTALL_UNSUPPORTED"); }
        }
        #[cfg(target_os = "linux")]
        if self.pending_storage.is_some() { bail!("DESKTOP_STORAGE_PROMPT_ACTIVE"); }
        let app = action.app();
        if matches!(action, DesktopAction::Launch { .. }) {
            self.pending_autostart.retain(|pending| *pending != app);
            return self.launch(app);
        }
        if matches!(action, DesktopAction::Close { .. }) && self.pending_autostart.contains(&app) {
            self.pending_autostart.retain(|pending| *pending != app);
            if !self.instances.iter().any(|instance| instance.app == app) {
                self.write_state(false)?;
                return Ok(());
            }
        }
        let id = self.window(app)?;
        if matches!(
            action,
            DesktopAction::Text { .. } | DesktopAction::Preedit { .. } | DesktopAction::Key { .. }
        ) {
            let view = self.scene.model().view(id).map_err(model_error)?;
            if !self.sink_ready || !view.focused || !view.input_ready || view.minimized {
                bail!("DESKTOP_INPUT_NOT_READY");
            }
            let bridge = self.input_bridge(id).context("DESKTOP_INPUT_WINDOW")?;
            if !bridge.text_input_enabled() {
                bail!("DESKTOP_TYPED_INPUT_REQUIRES_SDK6");
            }
            match action {
                DesktopAction::Text { text, .. } => bridge.text_commit(text, 0)?,
                DesktopAction::Preedit {
                    text, start, end, ..
                } => bridge.text_preedit(
                    text,
                    if *start < 0 {
                        None
                    } else {
                        Some((*start as usize, *end as usize))
                    },
                    0,
                )?,
                DesktopAction::Key {
                    key,
                    down,
                    repeat,
                    modifiers,
                    ..
                } => bridge.text_key(*key, *down, *repeat, *modifiers)?,
                _ => unreachable!(),
            }
            return Ok(());
        }
        let effects = match *action {
            DesktopAction::Close { .. } => self.scene.model_mut().close(id).map_err(model_error)?,
            DesktopAction::Minimize { .. } => {
                self.scene.model_mut().minimize(id).map_err(model_error)?
            }
            DesktopAction::Restore { .. } => {
                self.scene.model_mut().restore(id).map_err(model_error)?
            }
            DesktopAction::Maximize { .. } => {
                self.scene.model_mut().maximize(id).map_err(model_error)?
            }
            DesktopAction::Focus { .. } => self
                .scene
                .model_mut()
                .focus(Some(id))
                .map_err(model_error)?,
            DesktopAction::Geometry {
                x,
                y,
                width,
                height,
                ..
            } => self
                .scene
                .model_mut()
                .geometry(
                    id,
                    Rect {
                        x,
                        y,
                        width,
                        height,
                    },
                )
                .map_err(model_error)?,
            DesktopAction::Terminate { .. } => {
                self.instances
                    .iter()
                    .find(|a| a.app == app)
                    .context("DESKTOP_APP_NOT_RUNNING")?
                    .bridge
                    .cancel
                    .store(true, Ordering::SeqCst);
                self.log(app, id.0, 0, "terminate", String::new());
                return Ok(());
            }
            DesktopAction::Click { x, y, .. } => {
                let view = self.scene.model().view(id).map_err(model_error)?;
                if !self.sink_ready || !view.input_ready {
                    bail!("DESKTOP_INPUT_NOT_READY");
                }
                let content = view.outer.content();
                if x < 0 || y < 0 || x >= content.width || y >= content.height {
                    bail!("DESKTOP_INPUT_OUTSIDE");
                }
                let effects = self
                    .scene
                    .model_mut()
                    .focus(Some(id))
                    .map_err(model_error)?;
                self.effects(effects)?;
                self.send(id, [4, x, y, 1])?;
                self.changed();
                return Ok(());
            }
            DesktopAction::Launch { .. }
            | DesktopAction::Install { .. }
            | DesktopAction::Shutdown
            | DesktopAction::Text { .. }
            | DesktopAction::Preedit { .. }
            | DesktopAction::Key { .. } => unreachable!(),
        };
        self.effects(effects)?;
        self.changed();
        Ok(())
    }
    pub fn user_action(&mut self, action: &DesktopAction) -> Result<()> {
        if let Err(error) = self.perform(action) {
            self.log(action.app(), 0, 0, "action_rejected", format!("{error:#}"));
            #[cfg(feature = "gpu")]
            if let Some(gpu) = &self.gpu {
                gpu.stats()?;
            }
        }
        Ok(())
    }
    fn send(&mut self, id: WindowId, event: InputEvent) -> Result<()> {
        let instance = self
            .instances
            .iter()
            .find(|a| a.window == Some(id))
            .context("DESKTOP_INPUT_WINDOW")?;
        if instance.bridge.text_input_enabled() && event[0] == 4 {
            let bridge = instance.bridge.clone();
            let result = bridge
                .text_pointer(Some((event[1], event[2])), true, 0)
                .and_then(|()| bridge.text_pointer(Some((event[1], event[2])), false, 0));
            if result.is_err() {
                self.note_input_drop(id, "typed_pointer_rejected");
            }
            return Ok(());
        }
        match event[0] {
            2 => instance.bridge.zoom(event[1]),
            3 => instance.bridge.pan(event[1], event[2]),
            _ => {
                if let Err(error) = instance.events.try_send(event) {
                    let app = instance.app;
                    let generation = self.records[instance.record].generation;
                    let reason = match error {
                        mpsc::TrySendError::Full(_) => "queue_full",
                        mpsc::TrySendError::Disconnected(_) => "queue_disconnected",
                    };
                    self.dropped_inputs += 1;
                    self.log(app, id.0, generation, "input_dropped", reason.into());
                }
            }
        }
        Ok(())
    }
    pub fn pointer_begin_modifiers(&mut self, x: i32, y: i32, modifiers: u32) -> Result<()> {
        if !self.sink_ready {
            return Ok(());
        }
        let Some(id) = self.scene.model().hit_content(x, y) else {
            return Ok(());
        };
        if !self
            .scene
            .model()
            .view(id)
            .map_err(model_error)?
            .input_ready
        {
            return Ok(());
        }
        let effects = self
            .scene
            .model_mut()
            .begin_content(id)
            .map_err(model_error)?;
        self.effects(effects)?;
        self.pointer_update_modifiers(x, y, true, modifiers)?;
        self.changed();
        Ok(())
    }
    pub fn pointer_update_modifiers(
        &mut self,
        x: i32,
        y: i32,
        down: bool,
        modifiers: u32,
    ) -> Result<()> {
        let route = self.scene.model_mut().route_pointer(x, y, down);
        if let Some((id, position, down)) = route {
            if let Some(bridge) = self.input_bridge(id)
                && bridge.text_input_enabled()
            {
                if bridge.text_pointer(position, down, modifiers).is_err() {
                    self.note_input_drop(id, "typed_pointer_rejected");
                }
                return Ok(());
            }
            let message = self
                .instances
                .iter_mut()
                .find(|a| a.window == Some(id))
                .and_then(|a| a.pointer.sample(position, down));
            if let Some(event) = message {
                self.send(id, event)?;
            }
        }
        Ok(())
    }
    pub fn key(&mut self, index: usize, down: bool) -> Result<()> {
        if !self.sink_ready {
            return Ok(());
        }
        let Some(id) = self.focused() else {
            return Ok(());
        };
        if !self
            .scene
            .model()
            .view(id)
            .map_err(model_error)?
            .input_ready
        {
            return Ok(());
        }
        let event = self
            .instances
            .iter_mut()
            .find(|a| a.window == Some(id))
            .and_then(|a| a.keys.edge(index, down));
        if let Some(event) = event {
            self.send(id, event)?;
        }
        Ok(())
    }
    pub fn scroll(&mut self, x: i32, y: i32, delta: i32) -> Result<()> {
        if self.sink_ready
            && let Some(id) = self.scene.model().hit_content(x, y)
            && self
                .scene
                .model()
                .view(id)
                .map_err(model_error)?
                .input_ready
        {
            self.send(id, [2, delta, 0, 0])?;
        }
        Ok(())
    }
    pub fn reset_input(&mut self) -> Result<()> {
        let effects = self.scene.model_mut().cancel_capture();
        self.effects(effects)?;
        for a in &mut self.instances {
            a.bridge.clear_input();
            a.pointer.cancel();
            a.keys.cancel();
        }
        Ok(())
    }
    pub fn resize(&mut self, width: i32, height: i32) -> Result<()> {
        let effects = self.scene.resize_screen(width, height)?;
        self.effects(effects)?;
        self.options.width = width;
        self.options.height = height;
        self.changed();
        Ok(())
    }
    pub fn geometry(&mut self, id: WindowId, rect: Rect) -> Result<()> {
        let effects = self
            .scene
            .model_mut()
            .geometry(id, rect)
            .map_err(model_error)?;
        self.effects(effects)?;
        self.changed();
        Ok(())
    }
    fn ready(&self) -> bool {
        #[cfg(target_os = "linux")]
        if self.pending_storage.is_some() { return false; }
        self.pending_autostart.is_empty() && self.sink_ready
            && self.instances.iter().all(|a| {
                a.closing.is_none()
                    && a.window
                        .and_then(|id| self.scene.model().view(id).ok())
                        .is_some_and(|v| v.minimized || v.input_ready)
            })
    }
    fn poll(&mut self) -> Result<()> {
        // Close bounds guest execution; a validated Result may still await native exit.
        // The supervisor retains its existing watchdog, status and reap checks.
        // Strict workers keep their quarter-core limit and Close grants zero fuel.
        let close_grace = if self.options.isolation == crate::desktop_catalog::IsolationMode::Strict {
            Duration::from_secs(2)
        } else {
            Duration::from_millis(200)
        };
        let mut updates = Vec::new();
        let mut completed = Vec::new();
        let mut diagnostic_trace = Vec::new();
        for (i, a) in self.instances.iter_mut().enumerate() {
            self.records[a.record].worker_pid = a.pid.load(Ordering::SeqCst);
            self.records[a.record].isolation = a.isolation.as_ref().map(|s|s.snapshot());
            self.records[a.record].app_data = a.app_data.snapshot();
            for (at, kind, detail) in a.lifecycle_trace() {
                let record = &self.records[a.record];
                diagnostic_trace.push((at, record.app, record.window, record.generation, kind, detail.into()));
            }
            if a.closing.is_some_and(|at| at.elapsed() >= close_grace) {
                let (at, cancelled) = a.bridge.cancel_if_executing();
                // Keep checking on each poll; bound only the diagnostic emission.
                if !a.deadline_cancel_logged {
                    let record = &self.records[a.record];
                    diagnostic_trace.push((at, record.app, record.window, record.generation,
                        if cancelled { "close-deadline-cancel" } else { "close-deadline-skipped" },
                        if cancelled {
                            format!("graceMs={}", close_grace.as_millis())
                        } else if a.bridge.worker_lifecycle().result_accepted.is_some() {
                            "result-accepted".into()
                        } else {
                            "terminal-reply-written".into()
                        }));
                    a.deadline_cancel_logged = true;
                }
            }
            if a.window.is_some() {
                let f = a
                    .bridge
                    .latest
                    .lock()
                    .map_err(|_| anyhow!("DESKTOP_FRAME_SLOT_POISON"))?
                    .clone();
                if let Some(frame) = f
                    && frame.sequence > a.sequence
                {
                    updates.push((i, frame));
                }
            }
            match a.result.try_recv() {
                Ok(outcome) => completed.push((i, outcome)),
                Err(TryRecvError::Empty) => {}
                Err(TryRecvError::Disconnected) => {
                    completed.push((i, Err(anyhow!("DESKTOP_WORKER_CHANNEL_CLOSED"))))
                }
            }
        }
        for (at, app, window, generation, kind, detail) in diagnostic_trace {
            self.log_at(at, app, window, generation, kind, detail);
        }
        let pids: Vec<_> = self
            .instances
            .iter()
            .map(|a| a.pid.load(Ordering::SeqCst))
            .filter(|p| *p != 0)
            .collect();
        if pids.len() > self.max_concurrent_workers {
            self.max_concurrent_workers = pids.len();
            self.overlap = pids;
        }
        for (index, frame) in updates {
            let a = &mut self.instances[index];
            let id = a.window.expect("checked live window");
            let seq = frame.sequence;
            self.scene.set_frame(id, frame)?;
            a.sequence = seq;
            let record = &mut self.records[a.record];
            record.last_source_sequence = seq;
            record
                .first_frame_ms
                .get_or_insert(self.started.elapsed().as_millis());
            self.dirty = true;
        }
        for (index, result) in completed.into_iter().rev() {
            let mut a = self.instances.remove(index);
            let join = a.worker.take().map(|w| w.join());
            #[cfg(target_os = "linux")]
            let update_startup = a.update_startup.take();
            #[cfg(target_os = "linux")]
            let native_exit_ok = result.as_ref().is_ok_and(|out| out.exit_code == 0)
                && !join.as_ref().is_some_and(|result| result.is_err());
            // Acceptance/reap can happen after the snapshot above and before the
            // final channel receive. Joining makes this last snapshot complete.
            for (at, kind, detail) in a.lifecycle_trace() {
                let record = &self.records[a.record];
                self.log_at(at, record.app, record.window, record.generation, kind, detail.into());
            }
            a.app_data.revoke();
            let was_closing = a.closing.is_some();
            if let Some(id) = a.window.take() {
                let effects = self.scene.model_mut().close(id).map_err(model_error)?;
                self.effects(effects)?;
            }
            let record = &mut self.records[a.record];
            record.completed = true;
            record.app_data = a.app_data.snapshot();
            record.worker_pid = a.pid.load(Ordering::SeqCst);
            #[cfg(feature = "gpu")]
            {
                record.gpu_stats = a.gpu.as_ref().and_then(|g| g.stats().ok());
            }
            if join.is_some_and(|r| r.is_err()) {
                record.status = "failed";
                record.error = Some("DESKTOP_WORKER_THREAD_PANIC".into());
            } else {
                match result {
                    Ok(out) => {
                        record.module_source = Some(out.module_source);
                        record.fuel_consumed = Some(out.fuel_consumed);
                        record.frame_clock = out.frame_clock;
                        record.package_id = Some(out.app_id);
                        record.frames_submitted = Some(out.frames_submitted);
                        record.requests = Some(out.requests);
                        record.input_counts = Some(out.input_counts);
                        record.events = Some(out.events);
                        record.status = "closed";
                    }
                    Err(e) => {
                        let error = format!("{e:#}");
                        record.status = if was_closing && error.contains("APP_CANCELLED") {
                            "closed"
                        } else {
                            "failed"
                        };
                        record.error = Some(error);
                    }
                }
            }
            let (app, window, generation, status, error) = (
                record.app,
                record.window,
                record.generation,
                record.status,
                record.error.clone().unwrap_or_default(),
            );
            self.log(app, window, generation, "worker-result", error.clone());
            self.log(app, window, generation, status, error);
            self.changed();
            #[cfg(target_os = "linux")]
            if let Some(expected) = update_startup {
                let record = &self.records[a.record];
                let lifecycle = a.bridge.worker_lifecycle();
                let evidence = UpdateStartupEvidence {
                    identity: UpdateStartupIdentity { package_id: record.package_id.clone().unwrap_or_default(),
                        version: expected.version.clone(), fingerprint: expected.fingerprint,
                        app: record.app, generation: record.generation, window: record.window },
                    worker_pid: record.worker_pid,
                    input_ready: record.first_ready_ms.is_some(),
                    presented_source_sequence: record.presented_source_sequence,
                    completed: record.completed && record.error.is_none(),
                    result_accepted: lifecycle.result_accepted.is_some(),
                    native_exit_ok,
                    reaped: lifecycle.reaped.is_some(),
                };
                // Release every private-file session before rollback can swap
                // the failed generation's directory back to the old layout.
                drop(a);
                self.finish_update_startup(&expected, &evidence)?;
            }
        }
        Ok(())
    }
    #[cfg(target_os = "linux")]
    fn finish_update_startup(&mut self, expected: &UpdateStartupIdentity, evidence: &UpdateStartupEvidence) -> Result<()> {
        let registry = self.options.install_registry.as_ref().context("DESKTOP_UPDATE_REGISTRY")?;
        let authority = self.storage_authority.as_ref().context("DESKTOP_UPDATE_STORAGE_AUTHORITY")?;
        if evidence.confirms(expected) {
            registry.confirm_pending_update(authority, &expected.package_id, &expected.version, expected.fingerprint)?;
            self.log(expected.app, expected.window, expected.generation, "startup-confirmed", expected.package_id.clone());
        } else {
            registry.rollback_pending_update(authority).context("DESKTOP_UPDATE_ROLLBACK")?;
            let manifest_path = registry.active_manifest_path(&expected.package_id)?;
            let package = crate::package::load_package(&manifest_path)?;
            let entry = self.catalog.apps.iter_mut().find(|entry| entry.id == expected.app).context("DESKTOP_UPDATE_CATALOG")?;
            entry.manifest_path = manifest_path;
            entry.package_fingerprint = package.storage_fingerprint();
            self.log(expected.app, expected.window, expected.generation, "update-rolled-back", expected.package_id.clone());
            if self.closing.is_none() { self.launch(expected.app)?; }
        }
        Ok(())
    }
    pub fn tick(&mut self) -> Result<bool> {
        let now = Instant::now();
        let elapsed = now.saturating_duration_since(self.last_tick);
        self.last_tick = now;
        if self.closing.is_none() && self.ready() {
            self.active_time += elapsed;
        }
        self.poll()?;
        self.launch_pending_autostart()?;
        self.write_state(false)?;
        if self.closing.is_some() {
            return Ok(self.instances.is_empty());
        }
        // Host shutdown is a control-plane deadline, independent of a guest's
        // first frame or pending UI actions. Only persistent scripts use wall
        // time here; bounded session scheduling remains unchanged.
        if self.options.duration_ms == 0
            && self.options.actions[self.action_index..]
                .iter()
                .any(|step| {
                    matches!(step.action, DesktopAction::Shutdown)
                        && u128::from(step.after_ms) <= self.started.elapsed().as_millis()
                })
        {
            self.perform(&DesktopAction::Shutdown)?;
            self.action_index += 1;
            self.log(
                0,
                0,
                0,
                "action",
                serde_json::to_string(&DesktopAction::Shutdown)?,
            );
            return Ok(false);
        }
        if self.options.duration_ms != 0 && self.started.elapsed() > Duration::from_secs(300) {
            bail!("DESKTOP_WALL_TIMEOUT");
        }
        while self.closing.is_none()
            && self.ready()
            && self.action_index < self.options.actions.len()
            && self.options.actions[self.action_index].after_ms as u128
                <= self.active_time.as_millis()
        {
            let action = self.options.actions[self.action_index].action.clone();
            self.perform(&action).context("DESKTOP_SCRIPT_ACTION")?;
            self.action_index += 1;
            self.log(
                action.app(),
                0,
                0,
                "action",
                serde_json::to_string(&action)?,
            );
        }
        if self.options.duration_ms != 0
            && self.active_time >= Duration::from_millis(self.options.duration_ms)
            && self.action_index == self.options.actions.len()
        {
            self.begin_shutdown()?;
        }
        Ok(false)
    }
    pub fn compose(&mut self) -> Result<RenderedFrame> {
        let frame = self.scene.compose()?;
        self.frames_composed += 1;
        Ok(frame)
    }
    pub fn accepted(&mut self, sequence: u64, offscreen: bool) -> Result<()> {
        let effects = self.scene.presented(sequence)?;
        self.effects(effects)?;
        if offscreen {
            self.offscreen_frames += 1;
        } else {
            self.frames_presented += 1;
        }
        self.last_accepted = Some((
            sequence,
            self.options.width as usize,
            self.options.height as usize,
        ));
        self.sink_ready = true;
        self.dirty = false;
        #[cfg(target_os = "linux")]
        if let Some(pending) = &mut self.pending_storage {
            pending.presented = !offscreen && pending.composed == Some(sequence);
            pending.presented_sequence = pending.presented.then_some(sequence);
            pending.composed = None;
        }
        let ready: Vec<_> = self.views().into_iter().filter(|v| v.input_ready).collect();
        for v in ready {
            if let Some(a) = self.instances.iter().find(|a| a.window == Some(v.id)) {
                let record = &mut self.records[a.record];
                record.presented_source_sequence = v.presented_sequence;
                let content = v.outer.content();
                record.presented_viewport = Some((content.width as usize, content.height as usize));
                if record.first_ready_ms.is_none() {
                    record.first_ready_ms = Some(self.started.elapsed().as_millis());
                    record.status = "running";
                    self.dirty = true;
                    let (app, generation) = (record.app, record.generation);
                    self.log(
                        app,
                        v.id.0,
                        generation,
                        "ready",
                        format!("source={}", v.presented_sequence),
                    );
                }
            }
        }
        self.write_state(false)?;
        Ok(())
    }
    pub fn begin_shutdown(&mut self) -> Result<()> {
        #[cfg(target_os = "linux")]
        { self.pending_storage = None; }
        self.pending_autostart.clear();
        if self.closing.is_none() {
            self.closing = Some(Instant::now());
            let ids: Vec<_> = self.views().into_iter().map(|v| v.id).collect();
            for id in ids {
                let effects = self.scene.model_mut().close(id).map_err(model_error)?;
                self.effects(effects)?;
            }
        }
        self.write_state(true)?;
        Ok(())
    }
    pub fn finish(&mut self) -> Result<()> {
        self.begin_shutdown()?;
        while !self.instances.is_empty() {
            self.poll()?;
            thread::sleep(Duration::from_millis(5));
        }
        self.write_state(true)?;
        Ok(())
    }
    pub fn note_state_failure(&mut self, error: &anyhow::Error) {
        self.state_error = Some(format!("{error:#}").chars().take(2048).collect());
        let _ = self.write_state(true);
    }
    fn write_state(&mut self, force: bool) -> Result<()> {
        use std::io::Write;
        let Some(path) = self.options.state_file.clone() else {
            return Ok(());
        };
        if !force
            && self
                .state_last_write
                .is_some_and(|at| at.elapsed() < Duration::from_millis(100))
        {
            return Ok(());
        }
        let revision = self
            .state_revision
            .checked_add(1)
            .context("DESKTOP_STATE_REVISION")?;
        let phase = if self.state_error.is_some() {
            "failed"
        } else if self.closing.is_some() {
            if self.instances.is_empty() {
                "stopped"
            } else {
                "stopping"
            }
        } else if self.ready() {
            "ready"
        } else {
            "starting"
        };
        let apps:Vec<_>=self.catalog.apps.iter().filter_map(|app|self.records.iter().rev().find(|r|r.app==app.id)).map(|r|{
            let view=self.scene.model().view(WindowId(r.window)).ok();
            let mut value = serde_json::json!({"app":r.app,"generation":r.generation,"window":r.window,"workerPid":r.worker_pid,"state":self.app_status(r.app),"sourceSequence":r.last_source_sequence,"presentedSourceSequence":r.presented_source_sequence,"firstReadyMs":r.first_ready_ms,"inputReady":view.is_some_and(|v|v.input_ready),"minimized":view.is_some_and(|v|v.minimized),"outer":view.map(|v|serde_json::json!({"x":v.outer.x,"y":v.outer.y,"width":v.outer.width,"height":v.outer.height}))});
            if let Some(isolation)=&r.isolation { value["isolation"]=serde_json::to_value(isolation).expect("isolation serialization"); }
            if let Some(data)=&r.app_data { value["appData"]=serde_json::to_value(data).expect("app data serialization"); }
            value
        }).collect();
        let root = self.last_accepted.map(|(sequence, width, height)| {
            let mut value = serde_json::json!({"width":width,"height":height,"presentedSequence":sequence});
            #[cfg(target_os = "linux")]
            let storage_prompt = self.pending_storage.as_ref()
                .map(|pending| pending.prompt.state_value())
                .unwrap_or(serde_json::Value::Null);
            #[cfg(not(target_os = "linux"))]
            let storage_prompt = serde_json::Value::Null;
            value["storagePrompt"] = storage_prompt;
            value
        });
        let state = serde_json::json!({"schemaVersion":1,"bootId":self.state_boot,"sessionId":self.state_session,"revision":revision,"phase":phase,"root":root,"apps":apps,"error":self.state_error});
        let parent = path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(std::path::Path::new("."));
        let name = path
            .file_name()
            .context("DESKTOP_STATE_FILENAME")?
            .to_string_lossy();
        let temp = parent.join(format!(".{name}.{}.{}.tmp", std::process::id(), revision));
        let mut created = false;
        let result = (|| -> Result<()> {
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temp)?;
            created = true;
            file.write_all(&serde_json::to_vec(&state)?)?;
            file.write_all(b"\n")?;
            drop(file);
            std::fs::rename(&temp, &path)?;
            Ok(())
        })();
        if result.is_err() && created {
            let _ = std::fs::remove_file(&temp);
        }
        result.context("DESKTOP_STATE_WRITE")?;
        self.state_revision = revision;
        self.state_last_write = Some(Instant::now());
        Ok(())
    }
    pub fn outcome(
        &mut self,
        window_system: &'static str,
        presentation_backend: &'static str,
        shell_gpu_stats: Option<crate::graphics::GpuStats>,
        surface_gpu_stats: Option<crate::graphics::GpuStats>,
    ) -> Result<DesktopOutcome> {
        #[cfg(feature = "gpu")]
        let scene_gpu_stats = self.scene.gpu_stats()?;
        #[cfg(not(feature = "gpu"))]
        let scene_gpu_stats = None;
        #[cfg(feature = "gpu")]
        let gpu = self.gpu.as_ref().and_then(|g| {
            crate::graphics::Graphics::with_gpu_session(g.clone())
                .gpu_info()
                .cloned()
        });
        #[cfg(not(feature = "gpu"))]
        let gpu = None;
        Ok(DesktopOutcome {
            runtime: "ming-desktop",
            gpu,
            window_system,
            presentation_backend,
            width: self.options.width,
            height: self.options.height,
            duration_ms: self.started.elapsed().as_millis(),
            active_time_ms: self.active_time.as_millis(),
            accepted_root: self.last_accepted.map(|(sequence, width, height)| {
                crate::supervisor::PresentedFrameSummary {
                    sequence,
                    width,
                    height,
                }
            }),
            presented_frame: if self.frames_presented > 0 {
                self.last_accepted.map(|(sequence, width, height)| {
                    crate::supervisor::PresentedFrameSummary {
                        sequence,
                        width,
                        height,
                    }
                })
            } else {
                None
            },
            frames_composed: self.frames_composed,
            frames_presented: self.frames_presented,
            offscreen_frames: self.offscreen_frames,
            max_concurrent_workers: self.max_concurrent_workers,
            overlapping_worker_pids: self.overlap.clone(),
            active_workers_after_shutdown: self.instances.len(),
            actions_executed: self.action_index,
            instances: std::mem::take(&mut self.records),
            trace: std::mem::take(&mut self.trace),
            dropped_trace: self.dropped_trace,
            dropped_inputs: self.dropped_inputs,
            scene_gpu_stats,
            shell_gpu_stats,
            surface_gpu_stats,
        })
    }
}
impl Drop for Session {
    fn drop(&mut self) {
        for a in &self.instances {
            a.bridge.cancel.store(true, Ordering::SeqCst);
        }
        for a in &mut self.instances {
            if let Some(worker) = a.worker.take() {
                let _ = worker.join();
            }
        }
    }
}
fn model_error(e: crate::desktop_model::ModelError) -> anyhow::Error {
    anyhow!("DESKTOP_MODEL: {e:?}")
}

pub fn run(arguments: impl IntoIterator<Item = std::ffi::OsString>) -> Result<DesktopOutcome> {
    let mut options = DesktopOptions::parse(arguments)?;
    #[cfg(target_os = "linux")]
    if let Some(root) = options.install_root.as_ref() {
        options.install_registry = Some(crate::package_install::PackageInstallRegistry::open_verified(root)?);
    }
    #[cfg(not(target_os = "linux"))]
    if options.install_root.is_some() {
        bail!("DESKTOP_INSTALL_REGISTRY_UNSUPPORTED");
    }
    #[cfg(target_os = "linux")]
    let update_registry_root = options.install_root.clone();
    #[cfg(target_os = "linux")]
    let update_data_root = options.app_data.root.clone();
    #[cfg(target_os = "linux")]
    let update_was_pending = options.install_registry.as_ref()
        .map(|registry| registry.has_pending_update())
        .transpose()?
        .unwrap_or(false);
    #[cfg(target_os = "linux")]
    let (catalog, data_bindings) = if let Some(registry) = options.install_registry.as_ref() {
        if registry.has_pending_update()? {
            let data_root = options.app_data.root.clone().context("DESKTOP_UPDATE_DATA_ROOT_REQUIRED")?;
            let authority = crate::app_data::PackageStorageAuthority::open_default_private(data_root)
                .context("DESKTOP_UPDATE_STORAGE_AUTHORITY")?;
            registry.recover_pending_update(&authority).context("DESKTOP_UPDATE_RECOVERY")?;
        }
        crate::desktop_catalog::load_catalog_with_install_registry(&options.catalog_path, registry)?
    } else {
        load_catalog_with_data(&options.catalog_path)?
    };
    #[cfg(not(target_os = "linux"))]
    let (catalog, data_bindings) = load_catalog_with_data(&options.catalog_path)?;
    options.app_data.grants = data_bindings;
    let result = if options.offscreen {
        crate::desktop_native::offscreen(options, catalog)
    } else {
        crate::desktop_native::native(options, catalog)
    };
    #[cfg(target_os = "linux")]
    if update_was_pending {
        if let Some(root) = update_registry_root {
            let registry = crate::package_install::PackageInstallRegistry::open_verified(&root)
                .context("DESKTOP_UPDATE_REGISTRY_REOPEN")?;
            if registry.has_pending_update()? {
                let data_root = update_data_root.clone().context("DESKTOP_UPDATE_DATA_ROOT_REQUIRED")?;
                let authority = crate::app_data::PackageStorageAuthority::open_default_private(data_root)
                    .context("DESKTOP_UPDATE_STORAGE_AUTHORITY")?;
                match &result {
                    Ok(_) => {
                        registry.rollback_pending_update(&authority).context("DESKTOP_UPDATE_STARTUP_ROLLBACK")?;
                        return Err(anyhow!("DESKTOP_UPDATE_STARTUP_UNCONFIRMED"));
                    }
                    Err(_) => {
                        registry.rollback_pending_update(&authority).context("DESKTOP_UPDATE_FAILURE_ROLLBACK")?;
                    }
                }
            }
        }
    }
    result
}

#[cfg(test)]
mod input_backpressure_tests {
    use super::*;
    #[cfg(target_os = "linux")]
    #[test]
    fn native_install_requires_a_trusted_source_option() {
        let options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into()]).unwrap();
        let mut session = Session::new(
            options,
            Catalog { apps: Vec::new(), autostart: Vec::new() },
            #[cfg(feature = "gpu")]
            None,
        )
        .unwrap();
        let error = session.begin_install_from_dock().unwrap_err();
        assert!(format!("{error:#}").contains("DESKTOP_INSTALL_SOURCE_REQUIRED"));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn install_root_requires_the_publisher_trust_root() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        std::fs::set_permissions(temp.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let catalog = temp.path().join("catalog.json");
        std::fs::write(
            &catalog,
            r#"{"schemaVersion":3,"apps":[{"id":1,"title":"Signed","packageId":"dev.example.signed"}],"autostart":[]}"#,
        )
        .unwrap();
        let error = match run([
            catalog.into_os_string(),
            "--install-root".into(),
            temp.path().as_os_str().to_owned(),
            "--renderer".into(),
            "cpu".into(),
            "--backend".into(),
            "offscreen".into(),
        ])
        {
            Ok(_) => panic!("install root without publisher keyring must fail"),
            Err(error) => error,
        };
        assert!(format!("{error:#}").contains("PUBLISHER_KEYRING_FILE"), "{error:#}");
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn install_catalog_failure_restores_the_registry_record() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        std::fs::set_permissions(temp.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let source = temp.path().join("source");
        let install = temp.path().join("install");
        let catalog_path = temp.path().join("catalog.json");
        for path in [&source, &install] {
            std::fs::create_dir(path).unwrap();
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700)).unwrap();
        }
        std::fs::write(source.join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        let manifest = source.join("app.json");
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.install.rollback","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        std::fs::write(&catalog_path, r#"{"schemaVersion":3,"apps":[],"autostart":[]}"#).unwrap();
        let registry = crate::package_install::PackageInstallRegistry::open(&install).unwrap();
        let catalog = Catalog { apps: Vec::new(), autostart: Vec::new() };
        let mut options = DesktopOptions::parse([catalog_path.into_os_string(), "--renderer".into(), "cpu".into()]).unwrap();
        options.install_root = Some(install);
        options.install_registry = Some(registry);
        let mut session = Session::new(options, catalog, #[cfg(feature = "gpu")] None).unwrap();
        session.begin_install(&manifest).unwrap();
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        session.options.catalog_path = temp.path().join("missing-parent").join("catalog.json");
        session.respond_storage_prompt(true).unwrap();
        let registry = session.options.install_registry.as_ref().unwrap();
        assert!(registry.active_manifest_path("dev.example.install.rollback").is_err());
        assert!(!registry.is_removed("dev.example.install.rollback").unwrap());
        assert!(session.storage_prompt().is_some());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn default_and_legacy_storage_authorities_share_the_root_lock() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        std::fs::set_permissions(temp.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let root = crate::app_data::AppDataRoot::open(temp.path()).unwrap();
        let default = crate::app_data::PackageStorageAuthority::open_default_private(root.clone()).unwrap();
        assert!(crate::app_data::PackageStorageAuthority::open_persistent(root.clone()).is_err());
        drop(default);
        let legacy = crate::app_data::PackageStorageAuthority::open_persistent(root.clone()).unwrap();
        assert!(crate::app_data::PackageStorageAuthority::open_default_private(root.clone()).is_err());
        drop(legacy);
        assert!(crate::app_data::PackageStorageAuthority::open_default_private(root).is_ok());
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn desktop_default_private_session_requires_exclusive_root_authority() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let data = temp.path().join("data");
        std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::write(temp.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(
            temp.path().join("app.json"),
            r#"{"schemaVersion":1,"id":"dev.example.app","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#,
        )
        .unwrap();
        let catalog_path = temp.path().join("catalog.json");
        std::fs::write(
            &catalog_path,
            r#"{"schemaVersion":1,"apps":[{"id":1,"title":"Example","manifest":"app.json"}],"autostart":[1]}"#,
        )
        .unwrap();
        let catalog = crate::desktop_catalog::load_catalog(&catalog_path).unwrap();
        let held = crate::app_data::PackageStorageAuthority::open_default_private(
            crate::app_data::AppDataRoot::open(&data).unwrap(),
        )
        .unwrap();
        let options = DesktopOptions::parse([
            catalog_path.into_os_string(),
            "--renderer".into(),
            "cpu".into(),
            "--data-root".into(),
            data.into_os_string(),
        ])
        .unwrap();
        let mut session = Session::new(options, catalog, #[cfg(feature = "gpu")] None).unwrap();
        let error = session.start().unwrap_err();
        assert!(format!("{error:#}").contains("DESKTOP_STORAGE_AUTHORITY"), "{error:#}");
        assert!(session.instances.is_empty());
        drop(held);
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn default_private_storage_does_not_depend_on_legacy_permission_record() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let data = temp.path().join("data");
        std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        let root = crate::app_data::AppDataRoot::open(&data).unwrap();
        let legacy = crate::app_data::PackageStorageAuthority::open_persistent(root.clone()).unwrap();
        legacy.authorize("dev.example.old").unwrap();
        drop(legacy);
        let policy = data.join("system-private-storage-policy-v1/slot-1");
        std::fs::write(&policy, b"invalid legacy policy").unwrap();
        assert!(crate::app_data::PackageStorageAuthority::open_persistent(root).is_err());
        std::fs::write(temp.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(temp.path().join("app.json"), r#"{"schemaVersion":1,"id":"dev.example.app","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        let catalog_path = temp.path().join("catalog.json");
        std::fs::write(&catalog_path, r#"{"schemaVersion":1,"apps":[{"id":1,"title":"Example","manifest":"app.json"}],"autostart":[1]}"#).unwrap();
        let catalog = crate::desktop_catalog::load_catalog(&catalog_path).unwrap();
        let options = DesktopOptions::parse([catalog_path.into_os_string(), "--renderer".into(), "cpu".into(), "--data-root".into(), data.into_os_string()]).unwrap();
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        session.start().unwrap();
        assert_eq!(session.instances.len(), 1);
        assert!(session.storage_prompt().is_none());
        assert_eq!(std::fs::read(policy).unwrap(), b"invalid legacy policy");
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn replaced_package_with_same_identity_is_rejected_before_private_session() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let data = temp.path().join("data");
        std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        let module = temp.path().join("main.wasm");
        std::fs::write(&module, wat::parse_str("(module)").unwrap()).unwrap();
        std::fs::write(temp.path().join("app.json"), r#"{"schemaVersion":1,"id":"dev.example.app","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        let catalog_path = temp.path().join("catalog.json");
        std::fs::write(&catalog_path, r#"{"schemaVersion":1,"apps":[{"id":1,"title":"Example","manifest":"app.json"}],"autostart":[1]}"#).unwrap();
        let catalog = crate::desktop_catalog::load_catalog(&catalog_path).unwrap();
        std::fs::write(&module, wat::parse_str("(module (func))").unwrap()).unwrap();
        let options = DesktopOptions::parse([catalog_path.into_os_string(), "--renderer".into(), "cpu".into(), "--data-root".into(), data.into_os_string()]).unwrap();
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        let error = session.start().unwrap_err();
        assert!(format!("{error:#}").contains("DESKTOP_PACKAGE_CHANGED"), "{error:#}");
        assert!(session.instances.is_empty());
        assert!(session.storage_authority.is_none());
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn changed_package_identity_is_rejected_before_worker_start() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let data = temp.path().join("data");
        std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        let manifest = temp.path().join("app.json");
        std::fs::write(temp.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.alpha","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        let catalog_path = temp.path().join("catalog.json");
        std::fs::write(&catalog_path, r#"{"schemaVersion":1,"apps":[{"id":1,"title":"Example","manifest":"app.json"}],"autostart":[1]}"#).unwrap();
        let catalog = crate::desktop_catalog::load_catalog(&catalog_path).unwrap();
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.beta","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        let options = DesktopOptions::parse([catalog_path.into_os_string(), "--renderer".into(), "cpu".into(), "--data-root".into(), data.into_os_string()]).unwrap();
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        let error = session.start().unwrap_err();
        assert!(format!("{error:#}").contains("DESKTOP_PACKAGE_ID_CHANGED"), "{error:#}");
        assert!(session.instances.is_empty());
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn storage_management_reports_missing_authorization_and_unavailable_root() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        std::fs::set_permissions(temp.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let manifest = temp.path().join("app.json");
        std::fs::write(temp.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.app","version":"1.0.0","sdkVersion":6,"entry":"main.wasm","privateStorage":true}"#).unwrap();
        let catalog = Catalog { apps: vec![crate::desktop_catalog::CatalogEntry { id: 1, title: "Example".into(), package_fingerprint: crate::package::load_package(&manifest).unwrap().storage_fingerprint(), manifest_path: manifest, package_id: "dev.example.app".into() }], autostart: vec![] };
        let options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into(), "--data-root".into(), temp.path().as_os_str().to_owned()]).unwrap();
        let mut session = Session::new(options, catalog.clone(), #[cfg(feature="gpu")] None).unwrap();
        session.begin_storage_management(1).unwrap();
        assert!(session.storage_prompt().unwrap().lines(600).iter().any(|line| line == b"NO AUTHORIZATION"));
        assert!(session.respond_storage_prompt(false).is_err());
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        session.respond_storage_prompt(false).unwrap();
        assert!(session.storage_prompt().is_none());
        drop(session);
        let options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into()]).unwrap();
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        session.begin_storage_management(1).unwrap();
        assert!(session.storage_prompt().unwrap().lines(600).iter().any(|line| line == b"STORAGE UNAVAILABLE"));
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn storage_management_requires_presented_frame_and_retries_failed_revoke() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let data = temp.path().join("data");
        std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        let manifest = temp.path().join("app.json");
        std::fs::write(temp.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.app","version":"1.0.0","sdkVersion":6,"entry":"main.wasm","privateStorage":true}"#).unwrap();
        let package = crate::package::load_package(&manifest).unwrap();
        let root = crate::app_data::AppDataRoot::open(&data).unwrap();
        let authority = Arc::new(crate::app_data::PackageStorageAuthority::open_persistent(root).unwrap());
        authority.authorize_package(&package).unwrap();
        let files = authority.file_session_for_package(&package).unwrap();
        files.write_file("note", b"kept").unwrap();
        let options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into(), "--data-root".into(), data.into_os_string()]).unwrap();
        let catalog = Catalog { apps: vec![crate::desktop_catalog::CatalogEntry { id: 1, title: "Example".into(), package_fingerprint: crate::package::load_package(&manifest).unwrap().storage_fingerprint(), manifest_path: manifest, package_id: "dev.example.app".into() }], autostart: vec![] };
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        session.storage_authority = Some(authority.clone());
        session.begin_storage_management(1).unwrap();
        assert!(session.respond_storage_prompt(true).is_err());
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        authority.fail_next_policy_commit();
        session.respond_storage_prompt(true).unwrap();
        assert_eq!(files.read_file("note"), Err(crate::app_data::AppDataError::Denied));
        assert!(session.storage_prompt().unwrap().lines(600).iter().any(|line| line == b"REVOCATION NOT SAVED"));
        assert!(session.respond_storage_prompt(true).is_err());
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        session.respond_storage_prompt(true).unwrap();
        assert!(session.storage_prompt().is_none());
        authority.authorize_package(&package).unwrap();
        assert_eq!(authority.file_session_for_package(&package).unwrap().read_file("note").unwrap(), b"kept");
        assert!(session.instances.is_empty());
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn public_storage_management_revokes_access_and_keeps_shared_data() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let data = temp.path().join("data");
        std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        let manifest = temp.path().join("manifest.json");
        std::fs::write(temp.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.public","version":"1.0.0","sdkVersion":6,"entry":"main.wasm","publicStorage":true}"#).unwrap();
        let package = crate::package::load_package(&manifest).unwrap();
        let root = crate::app_data::AppDataRoot::open(&data).unwrap();
        let authority = Arc::new(crate::app_data::PackageStorageAuthority::open_persistent(root).unwrap());
        authority.authorize_public(&package.manifest.id).unwrap();
        let public = authority.public_session(&package.manifest.id).unwrap();
        public.write_file("shared", b"keep").unwrap();
        let catalog = Catalog { apps: vec![crate::desktop_catalog::CatalogEntry { id: 1, title: "Public".into(), package_fingerprint: package.storage_fingerprint(), manifest_path: manifest, package_id: package.manifest.id.clone() }], autostart: vec![] };
        let options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into(), "--data-root".into(), data.into_os_string()]).unwrap();
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        session.storage_authority = Some(authority.clone());
        session.begin_storage_management(1).unwrap();
        assert!(session.storage_prompt().unwrap().lines(600).iter().any(|line| line == b"PUBLIC STORAGE ACCESS"));
        assert!(session.respond_storage_prompt(true).is_err());
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        authority.fail_next_public_policy_commit();
        session.respond_storage_prompt(true).unwrap();
        assert!(session.storage_prompt().unwrap().lines(600).iter().any(|line| line == b"REVOCATION NOT SAVED"));
        assert_eq!(public.read_file("shared"), Err(crate::app_data::AppDataError::Denied));
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        session.respond_storage_prompt(true).unwrap();
        assert!(session.storage_prompt().is_none());
        authority.authorize_public(&package.manifest.id).unwrap();
        assert_eq!(authority.public_session(&package.manifest.id).unwrap().read_file("shared").unwrap(), b"keep");
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn uninstall_prompt_marks_registry_removed_and_keeps_private_data() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let data = temp.path().join("data");
        let install = temp.path().join("install");
        std::fs::create_dir(&data).unwrap();
        std::fs::create_dir(&install).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::set_permissions(&install, std::fs::Permissions::from_mode(0o700)).unwrap();
        let manifest = temp.path().join("manifest.json");
        std::fs::write(temp.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.uninstall","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        let package = crate::package::load_package(&manifest).unwrap();
        let authority = Arc::new(crate::app_data::PackageStorageAuthority::open_default_private(crate::app_data::AppDataRoot::open(&data).unwrap()).unwrap());
        authority.default_private_session_for_package(&package).unwrap().write_file("keep", b"data").unwrap();
        drop(authority);
        let registry = crate::package_install::PackageInstallRegistry::open(&install).unwrap();
        registry.install_package(&package).unwrap();
        let catalog = Catalog { apps: vec![crate::desktop_catalog::CatalogEntry { id: 1, title: "Uninstall".into(), package_fingerprint: package.storage_fingerprint(), manifest_path: manifest, package_id: package.manifest.id.clone() }], autostart: vec![] };
        let mut options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into(), "--data-root".into(), data.clone().into_os_string()]).unwrap();
        options.install_registry = Some(registry);
        options.install_root = Some(install);
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        session.begin_uninstall(1).unwrap();
        assert!(session.storage_prompt().unwrap().lines(600).iter().any(|line| line == b"PRIVATE DATA WILL BE KEPT"));
        assert!(session.respond_storage_prompt(true).is_err());
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        session.respond_storage_prompt(true).unwrap();
        assert!(session.storage_prompt().is_none());
        assert!(session.catalog.apps.is_empty());
        assert!(session.options.install_registry.as_ref().unwrap().active_manifest_path("dev.example.uninstall").is_err());
        let authority = Arc::new(crate::app_data::PackageStorageAuthority::open_default_private(crate::app_data::AppDataRoot::open(&data).unwrap()).unwrap());
        assert_eq!(authority.default_private_session_for_package(&package).unwrap().read_file("keep").unwrap(), b"data");
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn purge_prompt_removes_private_data_after_presented_delete() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let data = temp.path().join("data");
        let install = temp.path().join("install");
        std::fs::create_dir(&data).unwrap();
        std::fs::create_dir(&install).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::set_permissions(&install, std::fs::Permissions::from_mode(0o700)).unwrap();
        let manifest = temp.path().join("manifest.json");
        std::fs::write(temp.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.purge","version":"1.0.0","sdkVersion":6,"entry":"main.wasm","publicStorage":true}"#).unwrap();
        let package = crate::package::load_package(&manifest).unwrap();
        let authority = Arc::new(crate::app_data::PackageStorageAuthority::open_default_private(crate::app_data::AppDataRoot::open(&data).unwrap()).unwrap());
        authority.default_private_session_for_package(&package).unwrap().write_file("delete", b"data").unwrap();
        let sibling = crate::package::Package {
            manifest: crate::package::Manifest {
                schema_version: 1,
                id: "dev.example.sibling".into(),
                version: "1.0.0".into(),
                sdk_version: 6,
                entry: "main.wasm".into(),
                private_storage: false,
                public_storage: true,
                data_migration: None,
                publisher_key_id: None,
                publisher_signature: None,
            },
            bytes: b"\0asm\x01\0\0\0".to_vec(),
        };
        let sibling_private = authority.default_private_session_for_package(&sibling).unwrap();
        sibling_private.write_file("keep", b"sibling").unwrap();
        drop(sibling_private);
        authority.authorize_public(&package.manifest.id).unwrap();
        authority.authorize_public(&sibling.manifest.id).unwrap();
        let target_public = authority.public_session(&package.manifest.id).unwrap();
        target_public.write_file("shared", b"target-shared").unwrap();
        let sibling_public = authority.public_session(&sibling.manifest.id).unwrap();
        sibling_public.write_file("shared", b"sibling-shared").unwrap();
        let registry = crate::package_install::PackageInstallRegistry::open(&install).unwrap();
        registry.install_package(&package).unwrap();
        let catalog = Catalog { apps: vec![crate::desktop_catalog::CatalogEntry { id: 1, title: "Purge".into(), package_fingerprint: package.storage_fingerprint(), manifest_path: manifest, package_id: package.manifest.id.clone() }], autostart: vec![] };
        let mut options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into(), "--data-root".into(), data.clone().into_os_string()]).unwrap();
        options.install_registry = Some(registry);
        options.install_root = Some(install);
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        session.storage_authority = Some(authority.clone());
        session.begin_uninstall_with_purge(1).unwrap();
        assert!(session.storage_prompt().unwrap().lines(600).iter().any(|line| line == b"PRIVATE DATA WILL BE DELETED"));
        assert!(session.respond_storage_prompt(true).is_err());
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        session.respond_storage_prompt(true).unwrap();
        assert!(session.storage_prompt().is_none());
        assert!(session.catalog.apps.is_empty());
        assert!(session.options.install_registry.as_ref().unwrap().active_manifest_path("dev.example.purge").is_err());
        let fresh = authority.default_private_session_for_package(&package).unwrap();
        assert_eq!(fresh.read_file("delete"), Err(crate::app_data::AppDataError::NotFound));
        let sibling_private = authority.default_private_session_for_package(&sibling).unwrap();
        assert_eq!(sibling_private.read_file("keep").unwrap(), b"sibling");
        // Linux public storage is a single shared user directory.  The later
        // write through the sibling session is therefore visible here too.
        assert_eq!(target_public.read_file("shared").unwrap(), b"sibling-shared");
        assert_eq!(sibling_public.read_file("shared").unwrap(), b"sibling-shared");
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn purge_failure_removes_catalog_and_retries_after_session_closes() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let data = temp.path().join("data");
        let install = temp.path().join("install");
        std::fs::create_dir(&data).unwrap();
        std::fs::create_dir(&install).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::set_permissions(&install, std::fs::Permissions::from_mode(0o700)).unwrap();
        let manifest = temp.path().join("manifest.json");
        std::fs::write(temp.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.purge.retry","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        let package = crate::package::load_package(&manifest).unwrap();
        let authority = Arc::new(crate::app_data::PackageStorageAuthority::open_default_private(crate::app_data::AppDataRoot::open(&data).unwrap()).unwrap());
        let held = authority.default_private_session_for_package(&package).unwrap();
        held.write_file("retry", b"data").unwrap();
        let registry = crate::package_install::PackageInstallRegistry::open(&install).unwrap();
        registry.install_package(&package).unwrap();
        let catalog = Catalog { apps: vec![crate::desktop_catalog::CatalogEntry { id: 1, title: "Purge".into(), package_fingerprint: package.storage_fingerprint(), manifest_path: manifest, package_id: package.manifest.id.clone() }], autostart: vec![] };
        let mut options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into(), "--data-root".into(), data.into_os_string()]).unwrap();
        options.install_registry = Some(registry);
        options.install_root = Some(install);
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        session.storage_authority = Some(authority.clone());
        session.begin_uninstall_with_purge(1).unwrap();
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        session.respond_storage_prompt(true).unwrap();
        assert!(session.catalog.apps.is_empty());
        assert!(session.storage_prompt().unwrap().lines(600).iter().any(|line| line == b"PRIVATE DATA NOT DELETED"));
        drop(held);
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        session.respond_storage_prompt(true).unwrap();
        assert!(session.storage_prompt().is_none());
        let fresh = authority.default_private_session_for_package(&package).unwrap();
        assert_eq!(fresh.read_file("retry"), Err(crate::app_data::AppDataError::NotFound));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn purge_entry_requires_data_root_before_showing_confirmation() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let install = temp.path().join("install");
        std::fs::create_dir(&install).unwrap();
        std::fs::set_permissions(&install, std::fs::Permissions::from_mode(0o700)).unwrap();
        let manifest = temp.path().join("manifest.json");
        std::fs::write(temp.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.purge.no-root","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        let package = crate::package::load_package(&manifest).unwrap();
        let registry = crate::package_install::PackageInstallRegistry::open(&install).unwrap();
        registry.install_package(&package).unwrap();
        let catalog = Catalog {
            apps: vec![crate::desktop_catalog::CatalogEntry {
                id: 1,
                title: "Purge".into(),
                package_fingerprint: package.storage_fingerprint(),
                manifest_path: manifest,
                package_id: package.manifest.id.clone(),
            }],
            autostart: vec![],
        };
        let mut options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into()]).unwrap();
        options.install_registry = Some(registry);
        options.install_root = Some(install);
        let mut session = Session::new(options, catalog, #[cfg(feature = "gpu")] None).unwrap();
        let error = session.begin_uninstall_with_purge(1).unwrap_err();
        assert!(format!("{error:#}").contains("DESKTOP_DATA_ROOT_REQUIRED"), "{error:#}");
        assert!(session.storage_prompt().is_none());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn purge_confirmation_rejects_changed_package_fingerprint() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let data = temp.path().join("data");
        let install = temp.path().join("install");
        std::fs::create_dir(&data).unwrap();
        std::fs::create_dir(&install).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::set_permissions(&install, std::fs::Permissions::from_mode(0o700)).unwrap();
        let manifest = temp.path().join("manifest.json");
        let wasm = temp.path().join("main.wasm");
        std::fs::write(&wasm, b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.purge.changed","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
        let package = crate::package::load_package(&manifest).unwrap();
        let authority = Arc::new(crate::app_data::PackageStorageAuthority::open_default_private(crate::app_data::AppDataRoot::open(&data).unwrap()).unwrap());
        authority.default_private_session_for_package(&package).unwrap().write_file("keep", b"data").unwrap();
        let registry = crate::package_install::PackageInstallRegistry::open(&install).unwrap();
        registry.install_package(&package).unwrap();
        let catalog = Catalog { apps: vec![crate::desktop_catalog::CatalogEntry { id: 1, title: "Purge".into(), package_fingerprint: package.storage_fingerprint(), manifest_path: manifest.clone(), package_id: package.manifest.id.clone() }], autostart: vec![] };
        let mut options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into(), "--data-root".into(), data.into_os_string()]).unwrap();
        options.install_registry = Some(registry);
        options.install_root = Some(install);
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        session.storage_authority = Some(authority.clone());
        session.begin_uninstall_with_purge(1).unwrap();
        std::fs::write(&wasm, b"\0asm\x01\0\0\0changed").unwrap();
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        let error = session.respond_storage_prompt(true).unwrap_err();
        assert!(format!("{error:#}").contains("DESKTOP_PACKAGE_CHANGED"));
        assert!(session.storage_prompt().is_none());
        assert!(!session.catalog.apps.is_empty());
        assert!(!session.options.install_registry.as_ref().unwrap().is_removed("dev.example.purge.changed").unwrap());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn declared_private_storage_launch_does_not_request_consent() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let data = temp.path().join("data");
        std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        let manifest = temp.path().join("app.json");
        std::fs::write(temp.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.app","version":"1.0.0","sdkVersion":6,"entry":"main.wasm","privateStorage":true}"#).unwrap();
        let options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into(), "--data-root".into(), data.into_os_string()]).unwrap();
        let catalog = Catalog { apps: vec![crate::desktop_catalog::CatalogEntry { id: 1, title: "Example".into(), package_fingerprint: crate::package::load_package(&manifest).unwrap().storage_fingerprint(), manifest_path: manifest, package_id: "dev.example.app".into() }], autostart: vec![1] };
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        session.start().unwrap();
        assert!(session.storage_prompt().is_none());
        assert_eq!(session.instances.len(), 1);
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn public_storage_request_prompts_before_worker_and_denial_starts_without_capability() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let data = temp.path().join("data");
        std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        let manifest = temp.path().join("manifest.json");
        std::fs::write(temp.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.public","version":"1.0.0","sdkVersion":6,"entry":"main.wasm","publicStorage":true}"#).unwrap();
        let package = crate::package::load_package(&manifest).unwrap();
        let catalog = Catalog { apps: vec![crate::desktop_catalog::CatalogEntry { id: 1, title: "Public".into(), package_fingerprint: package.storage_fingerprint(), manifest_path: manifest, package_id: package.manifest.id.clone() }], autostart: vec![1] };
        let options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into(), "--data-root".into(), data.into_os_string()]).unwrap();
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        session.start().unwrap();
        assert!(session.storage_prompt().is_some());
        assert!(session.instances.is_empty());
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        session.respond_storage_prompt(false).unwrap();
        assert!(session.storage_prompt().is_none());
        assert_eq!(session.instances.len(), 1);
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn public_storage_request_allow_does_not_mark_app_denied() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let data = temp.path().join("data");
        std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        let manifest = temp.path().join("manifest.json");
        std::fs::write(
            temp.path().join("main.wasm"),
            wat::parse_str("(module (func (export \"_start\") (loop br 0)))").unwrap(),
        )
        .unwrap();
        std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.public","version":"1.0.0","sdkVersion":6,"entry":"main.wasm","publicStorage":true}"#).unwrap();
        let package = crate::package::load_package(&manifest).unwrap();
        let catalog = Catalog { apps: vec![crate::desktop_catalog::CatalogEntry { id: 1, title: "Public".into(), package_fingerprint: package.storage_fingerprint(), manifest_path: manifest, package_id: package.manifest.id.clone() }], autostart: vec![1] };
        let options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into(), "--data-root".into(), data.into_os_string()]).unwrap();
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        session.start().unwrap();
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        session.respond_storage_prompt(true).unwrap();
        assert_eq!(session.instances.len(), 1);
        let reply = session.instances[0].app_data.dispatch_public_files(
            6,
            1,
            &[],
            Some(r#"{"op":"write","path":"shared.txt","data":"c2hhcmVk"}"#),
        );
        assert_eq!(reply.value, 6);
        assert_eq!(reply.payload.as_deref(), Some("1"));
        assert_eq!(
            session
                .storage_authority
                .as_ref()
                .unwrap()
                .public_session("dev.example.public")
                .unwrap()
                .read_file("shared.txt")
                .unwrap(),
            b"shared"
        );
    }
    #[test]
    fn full_application_queue_drops_only_that_event_and_keeps_sibling_available() {
        let options = DesktopOptions::parse(
            ["unused", "--renderer", "cpu"]
                .into_iter()
                .map(std::ffi::OsString::from),
        )
        .unwrap();
        let catalog = Catalog {
            apps: vec![],
            autostart: vec![],
        };
        let mut session = Session::new(
            options,
            catalog,
            #[cfg(feature = "gpu")]
            None,
        )
        .unwrap();
        let mut ids = Vec::new();
        for app in [1, 2] {
            let id = session.scene.model_mut().open(app).unwrap().0;
            ids.push(id);
            let (bridge, events) = ViewBridge::new();
            let (_tx, result) = mpsc::sync_channel(1);
            let record = session.records.len();
            session.records.push(AppRecord {
                module_source: None, fuel_consumed: None,
                frame_clock: None,
                app,
                generation: 1,
                window: id.0,
                title: "Test".into(),
                worker_pid: 0,
                status: "running",
                completed: false,
                first_frame_ms: Some(0),
                first_ready_ms: Some(0),
                frames_submitted: None,
                last_source_sequence: 1,
                presented_source_sequence: 1,
                presented_viewport: Some((720, 448)),
                package_id: None,
                requests: None,
                input_counts: None,
                events: None,
                gpu_stats: None,
                error: None,
                isolation: None,
                app_data: None,
            });
            session.instances.push(Instance {
                #[cfg(target_os = "linux")]
                update_startup: None,
                app,
                window: Some(id),
                record,
                bridge,
                events,
                result,
                worker: None,
                pid: Arc::new(AtomicU32::new(0)),
                sequence: 1,
                closing: None,
            lifecycle_logged: Default::default(),
            deadline_cancel_logged: false,
                pointer: PointerSample::default(),
                keys: KeyLatch::default(),
                isolation: None,
                app_data: supervisor::AppDataService::default(),
                #[cfg(feature = "gpu")]
                gpu: None,
            });
        }
        let trace_before = session.trace.len();
        session.note_input_drop(WindowId(0), "rich_fifo_full");
        assert_eq!(session.dropped_inputs, 0);
        assert_eq!(session.trace.len(), trace_before);
        session.records[1].generation = 23;
        for reason in ["rich_fifo_full", "rich_invalid"] {
            session.note_input_drop(ids[1], reason);
            let entry = session.trace.last().expect("input drop trace");
            assert_eq!(
                (entry.app, entry.window, entry.generation),
                (2, ids[1].0, 23)
            );
            assert_eq!(entry.kind, "input_dropped");
            assert_eq!(entry.detail, reason);
        }
        assert_eq!(session.dropped_inputs, 2);
        assert_eq!(session.trace.len(), trace_before + 2);
        assert!(session.instances[1].bridge.poll_event().is_none());
        assert!(session.input_bridge(WindowId(0)).is_none());
        let bridge = session.input_bridge(ids[0]).expect("live input bridge");
        assert!(Arc::ptr_eq(
            &bridge.cancel,
            &session.instances[0].bridge.cancel
        ));
        session.instances[0].closing = Some(Instant::now());
        assert!(session.input_bridge(ids[0]).is_none());
        session.instances[0].closing = None;
        session.closing = Some(Instant::now());
        assert!(session.input_bridge(ids[0]).is_none());
        session.closing = None;
        for _ in 0..128 {
            session.instances[0]
                .events
                .try_send([4, 10, 10, 1])
                .unwrap();
        }
        assert!(
            session.send(ids[0], [4, 20, 20, 1]).is_ok(),
            "per-app backpressure must never reach native desktop fail()"
        );
        assert_eq!(session.dropped_inputs, 3);
        assert_eq!(session.views().len(), 2);
        assert!(session.closing.is_none());
        assert!(
            session
                .trace
                .iter()
                .any(|entry| entry.app == 1 && entry.kind == "input_dropped")
        );
        session.send(ids[1], [4, 30, 30, 1]).unwrap();
        assert_eq!(
            session.instances[1].bridge.poll_event(),
            Some([4, 30, 30, 1])
        );
        bridge.request_close();
        assert!(session.input_bridge(ids[0]).is_none());

        // Trusted persistent shutdown must work before any app is ready, even
        // when an earlier UI action is waiting for the first presentation.
        session.options.duration_ms = 0;
        session.options.actions = vec![
            crate::desktop_catalog::TimedAction {
                after_ms: 0,
                action: DesktopAction::Focus { app: 1 },
            },
            crate::desktop_catalog::TimedAction {
                after_ms: 0,
                action: DesktopAction::Shutdown,
            },
        ];
        session.sink_ready = false;
        session.tick().unwrap();
        assert!(session.closing.is_some());
        assert_eq!(session.action_index, 1, "only shutdown actually executed");
    }
}

// The FD is kept until Session::drop has joined every worker. Do not unlink the
// lock file: unlinking would permit two different inodes to be locked at once.
struct StrictSessionLock { _file: std::fs::File }
impl StrictSessionLock {
    fn acquire()->Result<Self> {
        #[cfg(all(target_os="linux",target_arch="x86_64"))] {
            use std::os::unix::{fs::{OpenOptionsExt,MetadataExt}};
            for root in [crate::linux_isolation::PRIVATE_ROOT, crate::linux_isolation::CGROUP_ROOT] {
                for path in std::path::Path::new(root).ancestors() {
                    let m=std::fs::symlink_metadata(path).with_context(||format!("LINUX_ISOLATION_UNSUPPORTED: prepared root {root}"))?;
                    if m.file_type().is_symlink() {bail!("ISOLATION_SESSION_ROOT_SYMLINK");}
                }
                let m=std::fs::metadata(root)?;
                if m.uid()!=0 || m.mode()&0o777!=0o700 {bail!("ISOLATION_SESSION_ROOT_OWNER");}
            }
            let path=std::path::Path::new(crate::linux_isolation::PRIVATE_ROOT).join("desktop.lock");
            let file=std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(false).mode(0o600).custom_flags(libc::O_NOFOLLOW|libc::O_CLOEXEC).open(path)?;
            let m=file.metadata()?;
            if !m.is_file()||m.uid()!=0||m.mode()&0o777!=0o600||m.nlink()!=1 {bail!("ISOLATION_SESSION_LOCK_OWNER");}
            file.try_lock().map_err(|e|anyhow!("ISOLATION_SESSION_ALREADY_RUNNING: {e}"))?;
            for root in [crate::linux_isolation::CGROUP_ROOT,crate::linux_isolation::PRIVATE_ROOT] {
                for entry in std::fs::read_dir(root)? {
                    let entry=entry?;let ty=entry.file_type()?;
                    if ty.is_dir() || ty.is_symlink() {bail!("ISOLATION_SESSION_STALE_OWNED_LEAF: {}",entry.path().display());}
                }
            }
            Ok(Self{_file:file})
        }
        #[cfg(not(all(target_os="linux",target_arch="x86_64")))] {
            bail!("LINUX_ISOLATION_UNSUPPORTED: strict desktop requires Linux x86_64")
        }
    }
}

#[cfg(test)]
mod close_grace_tests {
    use super::*;
    pub(super) fn closing_session(mode: crate::desktop_catalog::IsolationMode) -> (Session, ViewBridge, SyncSender<Result<Outcome>>) {
        // The test exercises the parent timer, without preparing a fake kernel sandbox.
        let options = DesktopOptions::parse(["unused", "--renderer", "cpu"].into_iter().map(std::ffi::OsString::from)).unwrap();
        let mut session = Session::new(options, Catalog { apps: vec![], autostart: vec![] }, #[cfg(feature="gpu")] None).unwrap();
        session.options.isolation = mode;
        let (bridge, events) = ViewBridge::new();
        let (keepalive, result) = mpsc::sync_channel(1);
        bridge.request_close();
        session.records.push(AppRecord {
            module_source: None, fuel_consumed: None, frame_clock: None, app_data: None,
            app:1, generation:1, window:0, title:"Closing".into(), worker_pid:0,
            status:"closing", completed:false, first_frame_ms:None, first_ready_ms:None,
            frames_submitted:None, last_source_sequence:0, presented_source_sequence:0,
            presented_viewport:None, package_id:None, requests:None, input_counts:None,
            events:None, gpu_stats:None, error:None, isolation:None,
        });
        session.instances.push(Instance {
            #[cfg(target_os = "linux")]
            update_startup: None,
            app:1, window:None, record:0, bridge:bridge.clone(), events, result,
            worker:None, pid:Arc::new(AtomicU32::new(0)), sequence:0,
            closing:Some(Instant::now()-Duration::from_millis(250)),
            lifecycle_logged:Default::default(), deadline_cancel_logged:false,
            pointer:PointerSample::default(), keys:KeyLatch::default(), isolation:None,
            app_data:supervisor::AppDataService::default(), #[cfg(feature="gpu")] gpu:None,
        });
        (session, bridge, keepalive)
    }
    #[test]
    fn strict_cpu_limited_cleanup_has_finite_wall_grace_without_early_cancel() {
        let (mut session, bridge, _keepalive) = closing_session(crate::desktop_catalog::IsolationMode::Strict);
        session.poll().unwrap();
        assert!(!bridge.cancel.load(Ordering::SeqCst), "Quarter-core strict workers must get cleanup time beyond the legacy 200ms");
        session.instances[0].closing = Some(Instant::now()-Duration::from_millis(2500));
        session.poll().unwrap();
        assert!(bridge.cancel.load(Ordering::SeqCst), "Uncooperative strict cleanup must remain bounded");
    }
    #[test]
    fn nonisolated_cleanup_keeps_legacy_200ms_cancel_boundary() {
        let (mut session, bridge, _keepalive) = closing_session(crate::desktop_catalog::IsolationMode::Off);
        session.poll().unwrap();
        assert!(bridge.cancel.load(Ordering::SeqCst));
    }

    #[test]
    fn accepted_result_survives_strict_deadline_while_supervisor_reaps() {
        let (mut session, bridge, _keepalive) = closing_session(crate::desktop_catalog::IsolationMode::Strict);
        bridge.clone().accept_worker_result();
        session.instances[0].closing = Some(Instant::now()-Duration::from_millis(2500));
        session.poll().unwrap();
        assert!(!bridge.cancel.load(Ordering::SeqCst), "A validated Result must not be cancelled while its supervisor awaits native exit/reap");
        assert!(!session.records[0].completed, "Result acceptance does not fabricate the final Outcome");
    }
    #[test]
    fn accepted_result_survives_off_deadline_while_supervisor_reaps() {
        let (mut session, bridge, _keepalive) = closing_session(crate::desktop_catalog::IsolationMode::Off);
        bridge.accept_worker_result();
        session.poll().unwrap();
        assert!(!bridge.cancel.load(Ordering::SeqCst));
        assert!(!session.records[0].completed);
    }
    #[test]
    fn explicit_cancellation_survives_result_acceptance() {
        let (mut session, bridge, _keepalive) = closing_session(crate::desktop_catalog::IsolationMode::Strict);
        bridge.cancel.store(true, Ordering::SeqCst);
        bridge.accept_worker_result();
        session.poll().unwrap();
        assert!(bridge.cancel.load(Ordering::SeqCst));
    }
    #[test]
    fn late_result_acceptance_never_erases_deadline_cancellation() {
        let (mut session, bridge, _keepalive) = closing_session(crate::desktop_catalog::IsolationMode::Off);
        session.poll().unwrap();
        assert!(bridge.cancel.load(Ordering::SeqCst));
        bridge.accept_worker_result();
        session.poll().unwrap();
        assert!(bridge.cancel.load(Ordering::SeqCst));
    }
    #[test]
    fn lifecycle_result_receipt_keeps_first_timestamp_across_clones() {
        let (bridge, _events) = ViewBridge::new();
        bridge.accept_worker_result();
        let first = bridge.worker_lifecycle().result_accepted.unwrap();
        bridge.clone().accept_worker_result();
        assert_eq!(bridge.worker_lifecycle().result_accepted, Some(first));
    }
    #[test]
    fn lifecycle_completion_flushes_receipt_created_after_result_receive() {
        let (mut session, bridge, _keepalive) = closing_session(crate::desktop_catalog::IsolationMode::Off);
        session.instances[0].closing = None;
        let (tx, rx) = mpsc::sync_channel(0);
        session.instances[0].result = rx;
        session.instances[0].worker = Some(thread::spawn(move || {
            // A rendezvous forces acceptance after poll's initial snapshot.
            tx.send(Err(anyhow::anyhow!("TEST_COMPLETION"))).unwrap();
            bridge.terminal_reply_written(crate::ui::TerminalReplyKind::Close);
            bridge.accept_worker_result();
            bridge.worker_reaped();
        }));
        let limit = Instant::now() + Duration::from_secs(2);
        while !session.instances.is_empty() {
            assert!(Instant::now() < limit);
            session.poll().unwrap();
            thread::yield_now();
        }
        let kinds: Vec<_> = session.trace.iter().map(|event| event.kind).collect();
        assert_eq!(kinds, ["terminal-reply-written", "result-accepted", "worker-reaped", "worker-result", "failed"]);
        assert!(session.records[0].completed);
    }
    #[test]
    fn lifecycle_deadline_keeps_checking_but_logs_once() {
        let (mut session, bridge, _keepalive) = closing_session(crate::desktop_catalog::IsolationMode::Off);
        session.poll().unwrap();
        assert!(bridge.cancel.swap(false, Ordering::SeqCst));
        session.poll().unwrap();
        assert!(bridge.cancel.load(Ordering::SeqCst), "Logging must not gate the existing deadline check");
        assert_eq!(session.trace.iter().filter(|event| event.kind == "close-deadline-cancel").count(), 1);
    }

    #[test]
    fn lifecycle_receipts_share_origin_identity_and_log_only_once() {
        let (mut session, bridge, _keepalive) = closing_session(crate::desktop_catalog::IsolationMode::Off);
        session.records[0].window = 37;
        session.records[0].generation = 4;
        bridge.terminal_reply_written(crate::ui::TerminalReplyKind::Close);
        bridge.accept_worker_result();
        bridge.worker_reaped();
        let first = bridge.worker_lifecycle();
        // Clones retain the first receipt, including the original terminal kind.
        bridge.clone().terminal_reply_written(crate::ui::TerminalReplyKind::Cancel);
        bridge.clone().worker_reaped();
        session.poll().unwrap();
        session.poll().unwrap();
        let events: Vec<_> = session.trace.iter().map(|event| (event.kind, event.detail.as_str())).collect();
        assert_eq!(events, [("terminal-reply-written", "close"), ("result-accepted", ""),
            ("worker-reaped", ""), ("close-deadline-skipped", "result-accepted")]);
        assert!(session.trace.iter().all(|event| (event.app, event.window, event.generation) == (1, 37, 4)));
        for (event, at) in session.trace.iter().zip([
            first.terminal_reply_written.unwrap().0, first.result_accepted.unwrap(), first.reaped.unwrap(),
        ]) {
            assert_eq!(event.at_ms, at.saturating_duration_since(session.started).as_millis());
        }
        assert_eq!(bridge.worker_lifecycle().reaped, first.reaped);
        assert!(!bridge.cancel.load(Ordering::SeqCst));
        assert!(!session.records[0].completed, "Receipts cannot fabricate completion");
    }
    #[test]
    fn lifecycle_trace_cap_counts_drops_without_repeated_emission() {
        let (mut session, bridge, _keepalive) = closing_session(crate::desktop_catalog::IsolationMode::Off);
        for _ in 0..512 {
            session.log(1, 0, 1, "test-fill", String::new());
        }
        bridge.terminal_reply_written(crate::ui::TerminalReplyKind::Cancel);
        bridge.accept_worker_result();
        bridge.worker_reaped();
        session.poll().unwrap();
        session.poll().unwrap();
        assert_eq!(session.trace.len(), 512);
        assert_eq!(session.dropped_trace, 4);
    }
    #[test]
    fn lifecycle_failed_completion_does_not_invent_result_or_reap() {
        let (mut session, _bridge, tx) = closing_session(crate::desktop_catalog::IsolationMode::Off);
        session.instances[0].closing = None;
        tx.send(Err(anyhow::anyhow!("TEST_FAILURE"))).unwrap();
        session.poll().unwrap();
        assert_eq!(session.trace.iter().map(|event| event.kind).collect::<Vec<_>>(), ["worker-result", "failed"]);
    }

    #[test]
    fn lifecycle_deadline_timestamp_orders_with_concurrent_result_acceptance() {
        for _ in 0..32 {
            let (bridge, _events) = ViewBridge::new();
            let gate = Arc::new(std::sync::Barrier::new(2));
            let worker = {
                let bridge = bridge.clone();
                let gate = gate.clone();
                thread::spawn(move || {
                    gate.wait();
                    bridge.accept_worker_result();
                })
            };
            gate.wait();
            let (decision_at, cancelled) = bridge.cancel_if_executing();
            worker.join().unwrap();
            let accepted_at = bridge.worker_lifecycle().result_accepted.unwrap();
            assert_eq!(bridge.cancel.load(Ordering::SeqCst), cancelled);
            if cancelled {
                assert!(decision_at <= accepted_at);
            } else {
                assert!(accepted_at <= decision_at);
            }
        }
    }

    #[test]
    fn normal_close_reply_does_not_inject_deadline_cancel_before_result() {
        let (bridge, _events) = ViewBridge::new();
        bridge.terminal_reply_written(crate::ui::TerminalReplyKind::Close);
        let (_, cancelled) = bridge.cancel_if_executing();
        assert!(!cancelled);
        assert!(!bridge.cancel.load(Ordering::SeqCst));
    }

}

#[cfg(test)]
mod startup_policy_tests {
    use super::*;
    #[cfg(target_os = "linux")]
    use std::{fs, os::unix::fs::PermissionsExt, path::PathBuf, sync::Arc};

    #[cfg(target_os = "linux")]
    fn pending_update_fixture() -> (tempfile::TempDir, PathBuf, PathBuf, Arc<crate::app_data::PackageStorageAuthority>, [u8; 32], [u8; 32]) {
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
        fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.desktop-update","version":"1.0.0","sdkVersion":6,"entry":"main.wasm","privateStorage":true}"#).unwrap();
        let registry = crate::package_install::PackageInstallRegistry::open(&install_root).unwrap();
        assert_eq!(registry.install_from_manifest(&manifest).unwrap(), crate::package_install::InstallState::Installed);
        let old_manifest = registry.active_manifest_path("dev.example.desktop-update").unwrap();
        let old_fingerprint = crate::package::load_package(&old_manifest).unwrap().storage_fingerprint();
        drop(registry);

        let authority = Arc::new(crate::app_data::PackageStorageAuthority::new(crate::app_data::AppDataRoot::open(&data_root).unwrap()));
        authority.authorize("dev.example.desktop-update").unwrap();
        authority.file_session("dev.example.desktop-update").unwrap().write_file("counter", b"42").unwrap();
        authority.revoke("dev.example.desktop-update").unwrap();

        fs::write(source.join("main.wasm"), b"\0asm\x01\0\0\0\x00").unwrap();
        fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.desktop-update","version":"1.1.0","sdkVersion":6,"entry":"main.wasm","privateStorage":true,"dataMigration":{"fromVersion":"1.0.0","steps":[{"op":"ensureDir","path":"state"},{"op":"renameFile","from":"counter","to":"state/counter"}]}}"#).unwrap();
        let registry = crate::package_install::PackageInstallRegistry::open(&install_root).unwrap();
        assert_eq!(registry.update_from_manifest(&manifest, &authority).unwrap(), crate::package_install::InstallState::Updated);
        let new_manifest = registry.active_manifest_path("dev.example.desktop-update").unwrap();
        let new_fingerprint = crate::package::load_package(&new_manifest).unwrap().storage_fingerprint();
        drop(registry);
        (temp, install_root, data_root, authority, old_fingerprint, new_fingerprint)
    }
    #[test]
    fn update_confirmation_requires_exact_identity_and_complete_lifecycle() {
        let expected = UpdateStartupIdentity { package_id: "dev.example.app".into(), version: "1.1.0".into(), fingerprint: [3; 32], app: 1, generation: 2, window: 7 };
        let valid = UpdateStartupEvidence { identity: expected.clone(), worker_pid: 123, input_ready: true,
            presented_source_sequence: 5, completed: true, result_accepted: true, native_exit_ok: true, reaped: true };
        assert!(valid.confirms(&expected));
        for field in 0..12 {
            let mut invalid = valid.clone();
            match field {
                0 => invalid.identity.package_id = "dev.example.sibling".into(),
                1 => invalid.identity.fingerprint = [4; 32],
                2 => invalid.identity.app = 2,
                3 => invalid.identity.generation = 1,
                4 => invalid.identity.window = 6,
                5 => invalid.worker_pid = 0,
                6 => invalid.input_ready = false,
                7 => invalid.presented_source_sequence = 0,
                8 => invalid.completed = false,
                9 => invalid.result_accepted = false,
                10 => invalid.native_exit_ok = false,
                11 => invalid.reaped = false,
                _ => unreachable!(),
            }
            assert!(!invalid.confirms(&expected), "missing confirmation field {field}");
        }
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn desktop_startup_confirmation_commits_pending_update() {
        let (_temp, install_root, data_root, authority, _old_fingerprint, new_fingerprint) = pending_update_fixture();
        let registry = crate::package_install::PackageInstallRegistry::open(&install_root).unwrap();
        let catalog = Catalog {
            apps: vec![crate::desktop_catalog::CatalogEntry {
                id: 1,
                title: "Updated".into(),
                manifest_path: registry.active_manifest_path("dev.example.desktop-update").unwrap(),
                package_id: "dev.example.desktop-update".into(),
                package_fingerprint: new_fingerprint,
            }],
            autostart: vec![],
        };
        let mut options = DesktopOptions::parse([
            "unused".into(), "--renderer".into(), "cpu".into(),
            "--data-root".into(), data_root.clone().into_os_string(),
        ]).unwrap();
        options.install_root = Some(install_root);
        options.install_registry = Some(registry);
        let mut session = Session::new(options, catalog, #[cfg(feature = "gpu")] None).unwrap();
        session.storage_authority = Some(authority.clone());
        let expected = UpdateStartupIdentity {
            package_id: "dev.example.desktop-update".into(), version: "1.1.0".into(),
            fingerprint: new_fingerprint, app: 1, generation: 2, window: 9,
        };
        assert!(session.options.install_registry.as_ref().unwrap().begin_update_startup("dev.example.desktop-update", new_fingerprint).unwrap());
        let evidence = UpdateStartupEvidence {
            identity: expected.clone(), worker_pid: 123, input_ready: true,
            presented_source_sequence: 4, completed: true, result_accepted: true,
            native_exit_ok: true, reaped: true,
        };
        session.finish_update_startup(&expected, &evidence).unwrap();
        assert!(!session.options.install_registry.as_ref().unwrap().has_pending_update().unwrap());
        authority.authorize("dev.example.desktop-update").unwrap();
        assert_eq!(authority.file_session("dev.example.desktop-update").unwrap().read_file("state/counter").unwrap(), b"42");
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn desktop_startup_failure_rolls_back_catalog_and_private_data() {
        let (_temp, install_root, data_root, authority, old_fingerprint, new_fingerprint) = pending_update_fixture();
        let registry = crate::package_install::PackageInstallRegistry::open(&install_root).unwrap();
        let catalog = Catalog {
            apps: vec![crate::desktop_catalog::CatalogEntry {
                id: 1,
                title: "Updated".into(),
                manifest_path: registry.active_manifest_path("dev.example.desktop-update").unwrap(),
                package_id: "dev.example.desktop-update".into(),
                package_fingerprint: new_fingerprint,
            }],
            autostart: vec![],
        };
        let mut options = DesktopOptions::parse([
            "unused".into(), "--renderer".into(), "cpu".into(),
            "--data-root".into(), data_root.clone().into_os_string(),
        ]).unwrap();
        options.install_root = Some(install_root);
        options.install_registry = Some(registry);
        let mut session = Session::new(options, catalog, #[cfg(feature = "gpu")] None).unwrap();
        session.storage_authority = Some(authority.clone());
        session.closing = Some(Instant::now());
        let expected = UpdateStartupIdentity {
            package_id: "dev.example.desktop-update".into(), version: "1.1.0".into(),
            fingerprint: new_fingerprint, app: 1, generation: 2, window: 9,
        };
        assert!(session.options.install_registry.as_ref().unwrap().begin_update_startup("dev.example.desktop-update", new_fingerprint).unwrap());
        let evidence = UpdateStartupEvidence {
            identity: expected.clone(), worker_pid: 123, input_ready: true,
            presented_source_sequence: 4, completed: false, result_accepted: true,
            native_exit_ok: true, reaped: true,
        };
        session.finish_update_startup(&expected, &evidence).unwrap();
        let entry = &session.catalog.apps[0];
        assert_eq!(entry.package_fingerprint, old_fingerprint);
        assert_eq!(crate::package::load_package(&entry.manifest_path).unwrap().manifest.version, "1.0.0");
        assert!(!session.options.install_registry.as_ref().unwrap().has_pending_update().unwrap());
        authority.authorize("dev.example.desktop-update").unwrap();
        let session_data = authority.file_session("dev.example.desktop-update").unwrap();
        assert_eq!(session_data.read_file("counter").unwrap(), b"42");
        assert_eq!(session_data.read_file("state/counter"), Err(crate::app_data::AppDataError::NotFound));
    }

    fn session() -> (Session, SyncSender<Result<Outcome>>) {
        let (mut s, _bridge, tx) = super::close_grace_tests::closing_session(crate::desktop_catalog::IsolationMode::Off);
        s.options.isolation = crate::desktop_catalog::IsolationMode::Strict;
        s.startup_limit = 1;
        s.options.duration_ms = 0;
        s.pending_autostart = [2].into();
        s.instances[0].closing = None;
        s.records[0].status = "starting";
        s.records[0].isolation = Some(supervisor::IsolationStatus { requested:"strict", prepared:true, limits:Some(supervisor::IsolationLimits { memory_bytes:512<<20, pids:64, cpu_max:crate::linux_isolation::STARTUP_CPU_MAX.into() }), ..Default::default() });
        s.catalog.apps.push(crate::desktop_catalog::CatalogEntry { id:2, title:"Second".into(), manifest_path:"unused".into(), package_id:"dev.example.second".into(), #[cfg(target_os = "linux")] package_fingerprint:[0; 32] });
        (s,tx)
    }
    #[test] fn strict_startup_reserves_one_cpu_and_off_keeps_parallel_boot() {
        assert_eq!(autostart_parallelism(true,0),1);assert_eq!(autostart_parallelism(true,1),1);assert_eq!(autostart_parallelism(true,2),1);assert_eq!(autostart_parallelism(true,4),3);assert_eq!(autostart_parallelism(true,20),4);assert_eq!(autostart_parallelism(false,1),4);
    }
    #[test] fn full_cpu_initialization_holds_next_autostart() {
        let (mut s,_tx)=session();assert_eq!(s.take_pending_autostart(),None);
    }
    #[test] fn applied_without_steady_quota_does_not_release_slot() {
        let (mut s,_tx)=session();s.records[0].isolation.as_mut().unwrap().applied=true;assert_eq!(s.take_pending_autostart(),None);
    }
    #[test] fn missing_quota_receipt_does_not_release_slot() {
        let (mut s,_tx)=session();let iso=s.records[0].isolation.as_mut().unwrap();iso.applied=true;iso.limits=None;assert_eq!(s.take_pending_autostart(),None);
    }
    #[test] fn steady_quota_releases_slot_without_first_frame() {
        let (mut s,_tx)=session();let iso=s.records[0].isolation.as_mut().unwrap();iso.applied=true;iso.limits.as_mut().unwrap().cpu_max=crate::linux_isolation::STEADY_CPU_MAX.into();assert_eq!(s.records[0].first_ready_ms,None);assert_eq!(s.records[0].last_source_sequence,0);assert_eq!(s.take_pending_autostart(),Some(2));
    }
    #[test] fn failed_reaped_worker_releases_slot() {
        let (mut s,_tx)=session();s.records[0].status="failed";s.instances.clear();assert_eq!(s.take_pending_autostart(),Some(2));
    }
    #[test] fn pending_autostart_prevents_premature_global_ready() {
        let (mut s,_tx)=session();s.instances.clear();s.sink_ready=true;assert!(!s.ready());
    }
    #[test] fn shutdown_discards_pending_autostart() {
        let (mut s,_tx)=session();s.begin_shutdown().unwrap();assert!(s.pending_autostart.is_empty());assert_eq!(s.take_pending_autostart(),None);
    }
    #[test] fn closing_queued_app_cancels_only_its_pending_launch() {
        let (mut s,_tx)=session();s.perform(&DesktopAction::Close { app:2 }).unwrap();assert!(s.pending_autostart.is_empty());assert_eq!(s.instances.len(),1);
    }

    #[test] fn bounded_strict_keeps_legacy_parallelism() {
        let (mut s,_tx)=session();s.options.duration_ms=60000;assert_eq!(s.take_pending_autostart(),Some(2));
    }
}
