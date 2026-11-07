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
    collections::{BTreeMap, VecDeque},
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
// Keep fault closures out of the large transaction's optimized stage expansion.
#[cfg(all(test, target_os = "linux"))]
#[inline(never)]
fn uninstall_test_transaction(
    registry: &crate::package_install::PackageInstallRegistry,
    package: &crate::package::Package,
    hook: &mut dyn FnMut(&'static str) -> Result<()>,
) -> std::result::Result<crate::persistent_install::CommittedUninstall, crate::persistent_install::InstallFailure> {
    crate::persistent_install::uninstall_existing_with_hook(registry, package, &mut |stage| hook(stage))
}
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

// Qualification evidence is parent-owned and never supplied by a worker.
#[cfg(target_os = "linux")]
#[derive(Default)]
struct NativeEvidence {
    sequence: u64,
    press: Option<u64>,
    pending: Option<NativeInputEvidence>,
    receipts: usize,
    launches: Vec<NativeLaunchEvidence>,
}
#[cfg(target_os = "linux")]
struct NativeInputEvidence {
    sequence: u64,
    press: u64,
    down: bool,
    mouse: Option<(i32, i32)>,
    before: serde_json::Value,
    trace_start: usize,
    target: Option<u16>,
}
#[cfg(target_os = "linux")]
struct NativeLaunchEvidence {
    record: usize,
    down: u64,
    started: bool,
    ready: bool,
}

#[cfg(target_os = "linux")]
struct DesktopTrialUpdate {
    record: crate::persistent_update::UpdateRecord,
    launched: bool,
    down: Option<u64>,
    confirmation: Option<serde_json::Value>,
}

pub(crate) struct Session {
    app_data: crate::desktop_catalog::DesktopDataConfig,
    #[cfg(target_os = "linux")]
    storage_authority: Option<Arc<crate::app_data::PackageStorageAuthority>>,
    #[cfg(target_os = "linux")]
    pending_storage: Option<PendingDesktopStorage>,
    #[cfg(target_os = "linux")]
    persistent_install_blocked: bool,
    #[cfg(all(test, target_os = "linux"))]
    persistent_install_faults: BTreeSet<&'static str>,
    #[cfg(all(test, target_os = "linux"))]
    persistent_uninstall_output_faults: BTreeSet<&'static str>,
    #[cfg(target_os = "linux")]
    storage_prompt_revision: u64,
    #[cfg(target_os = "linux")]
    persistent_update: Option<DesktopTrialUpdate>,
    #[cfg(target_os = "linux")]
    pending_update_identity: Option<crate::desktop_catalog::CatalogEntry>,
    #[cfg(target_os = "linux")]
    persistent_update_state: Option<serde_json::Value>,
    #[cfg(target_os = "linux")]
    native_dock_launch_active: bool,
    #[cfg(all(test, target_os = "linux"))]
    persistent_update_output_faults: BTreeSet<&'static str>,
    #[cfg(target_os = "linux")]
    native_uninstall_confirm: Option<serde_json::Value>,
    #[cfg(target_os = "linux")]
    native_evidence: NativeEvidence,
    #[cfg(all(test, target_os = "linux"))]
    pub(crate) test_native_receipts: Vec<serde_json::Value>,
    pub options: DesktopOptions,
    pub catalog: Catalog,
    pub scene: DesktopScene,
    instances: Vec<Instance>,
    pending_autostart: VecDeque<u16>,
    #[cfg(target_os = "linux")]
    public_denied: BTreeSet<u16>,
    startup_limit: usize,
    records: Vec<AppRecord>,
    current_record_floor: BTreeMap<u16, usize>,
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
        // A trusted native install source or reviewed install action can add an
        // app after the initial catalog is loaded. Defer only Launch targets
        // to the runtime catalog lookup, which still requires a committed entry.
        let install_can_extend_catalog = (cfg!(target_os = "linux") && options.install_source.is_some())
            || options.actions.iter().any(|step| matches!(step.action, DesktopAction::Install { .. }));
        for step in &options.actions {
            if !matches!(step.action, DesktopAction::Shutdown | DesktopAction::Install { .. })
                && !catalog.apps.iter().any(|a| a.id == step.action.app())
                && !(install_can_extend_catalog && matches!(step.action, DesktopAction::Launch { .. }) && (1..=4).contains(&step.action.app()))
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
            #[cfg(target_os = "linux")]
            persistent_install_blocked: false,
            #[cfg(all(test, target_os = "linux"))]
            persistent_install_faults: BTreeSet::new(),
            #[cfg(all(test, target_os = "linux"))]
            persistent_uninstall_output_faults: BTreeSet::new(),
            #[cfg(target_os = "linux")]
            storage_prompt_revision: 0,
            #[cfg(target_os = "linux")]
            persistent_update: None,
            #[cfg(target_os = "linux")]
            pending_update_identity: None,
            #[cfg(target_os = "linux")]
            persistent_update_state: None,
            #[cfg(target_os = "linux")]
            native_dock_launch_active: false,
            #[cfg(all(test, target_os = "linux"))]
            persistent_update_output_faults: BTreeSet::new(),
            #[cfg(target_os = "linux")]
            native_uninstall_confirm: None,
            #[cfg(target_os = "linux")]
            native_evidence: NativeEvidence::default(),
            #[cfg(all(test, target_os = "linux"))]
            test_native_receipts: Vec::new(),
            options,
            catalog,
            scene,
            instances: Vec::new(),
            pending_autostart: VecDeque::new(),
            #[cfg(target_os = "linux")]
            public_denied: BTreeSet::new(),
            startup_limit,
            records: Vec::new(),
            current_record_floor: BTreeMap::new(),
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
        #[cfg(target_os = "linux")]
        if self.persistent_update.is_some() { return Ok(()); }
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
        self.current_record(app).map_or("closed", |r| r.status)
    }
    fn current_record(&self, app: u16) -> Option<&AppRecord> {
        self.records.get(*self.current_record_floor.get(&app).unwrap_or(&0)..)?.iter().rev().find(|r| r.app == app)
    }
    fn reset_catalog_binding(&mut self, app: u16) {
        self.current_record_floor.insert(app, self.records.len());
        self.app_data.grants.remove(&app);
        self.pending_autostart.retain(|id| *id != app);
        #[cfg(target_os = "linux")]
        self.public_denied.remove(&app);
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
            self.storage_prompt_revision = self.storage_prompt_revision.saturating_add(1);
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
        self.ensure_no_persistent_update()?;
        if self.persistent_install_blocked { bail!("DESKTOP_PERSISTENT_INSTALL_BLOCKED"); }
        let pending = self.pending_storage.as_ref().context("DESKTOP_STORAGE_NO_PROMPT")?;
        if self.persistent_update.is_some() { bail!("DESKTOP_UPDATE_PENDING"); }
        if !pending.presented || !self.sink_ready { bail!("DESKTOP_STORAGE_NOT_PRESENTED"); }
        if allow && matches!(&pending.pending, DesktopStorageDecision::Information) { bail!("DESKTOP_STORAGE_CONFLICT"); }
        if allow && !pending.prompt.has_allow() { bail!("DESKTOP_STORAGE_CONFLICT"); }
        let pending = self.pending_storage.take().expect("checked pending");
        let app = pending.app;
        if self.options.persistent_install_root.is_some() && matches!(&pending.pending,
            DesktopStorageDecision::Purge { .. }) {
            bail!("DESKTOP_PERSISTENT_MUTATION_UNSUPPORTED");
        }
        match pending.pending {
            DesktopStorageDecision::Install { package_id, version, fingerprint, manifest_path, update, retry: _ } => {
                if !allow {
                    self.pending_update_identity = None;
                    self.changed();
                    self.launch_pending_autostart()?;
                    return Ok(());
                }
                let registry = self.options.install_registry.as_ref().context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?;
                let package = match crate::package::load_package(&manifest_path).context("DESKTOP_INSTALL_PACKAGE") {
                    Ok(package) => package,
                    Err(error) => {
                        self.log(0, 0, 0, "install_not_saved", format!("{error:#}"));
                        let prompt = crate::graphics::desktop_shell::StoragePrompt::install(&package_id, &version, &fingerprint, update, true, false)?;
                        self.pending_storage = Some(PendingDesktopStorage { app, pending: DesktopStorageDecision::Install { package_id, version, fingerprint, manifest_path, update, retry: true }, prompt, composed: None, presented: false, presented_sequence: None });
                        self.changed();
                        return Ok(());
                    }
                };
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
                if self.options.persistent_install_root.is_some() {
                    if update { return self.begin_persistent_update(package, app); }
                    self.ensure_no_persistent_update()?;
                    return self.commit_persistent_install(package, app, package_id, version, fingerprint, manifest_path);
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
                    registry.update_loaded_package(&package, authority.as_deref().context("DESKTOP_STORAGE_AUTHORITY")?)
                } else {
                    registry.install_loaded_package(&package)
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
                if !update {
                    let bound = self.catalog.apps.iter().find(|entry| entry.package_id == package_id)
                        .context("DESKTOP_INSTALL_CATALOG_ENTRY")?.id;
                    self.reset_catalog_binding(bound);
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
                    self.verify_uninstall_target(app, &package, &manifest_path)?;
                    if self.options.persistent_install_root.is_some() {
                        return self.commit_persistent_uninstall(app, package);
                    }
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
                            self.reset_catalog_binding(app);
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
        self.ensure_no_persistent_update()?;
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
    pub(crate) fn note_install_rejection(&mut self, error: &anyhow::Error) -> Result<()> {
        let rejected_before_prompt = error.chain().any(|cause| matches!(cause.to_string().as_str(),
            "DESKTOP_INSTALL_SOURCE_REQUIRED" | "DESKTOP_INSTALL_SOURCE" | "DESKTOP_INSTALL_REGISTRY_REQUIRED"
            | "DESKTOP_INSTALL_PACKAGE" | "DESKTOP_INSTALL_PUBLISHER" | "DESKTOP_INSTALL_CURRENT"
            | "DESKTOP_INSTALL_VERSION" | "DESKTOP_CATALOG_LIMIT"
        ));
        if !rejected_before_prompt || self.pending_storage.is_some() || self.closing.is_some() || self.options.offscreen {
            bail!("{error:#}");
        }
        self.log(0, 0, 0, "install_rejected", format!("{error:#}"));
        let prompt = crate::graphics::desktop_shell::StoragePrompt::install_rejected();
        self.pending_storage = Some(PendingDesktopStorage {
            app: 0,
            pending: DesktopStorageDecision::Information,
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
    fn ensure_no_persistent_update(&self) -> Result<()> {
        if self.options.persistent_install_root.is_none() { return Ok(()); }
        if self.persistent_install_blocked { bail!("DESKTOP_PERSISTENT_INSTALL_BLOCKED"); }
        if self.persistent_update.is_some() { bail!("DESKTOP_UPDATE_PENDING"); }
        let registry = self.options.install_registry.as_ref().context("DESKTOP_UPDATE_REGISTRY")?;
        if registry.read_coordinated_marker(crate::persistent_update::UPDATE_PENDING_NAME, 4096)?.is_some()
            || registry.has_pending_update()? { bail!("DESKTOP_UPDATE_PENDING"); }
        Ok(())
    }
    #[cfg(target_os = "linux")]
    fn verify_persistent_update_target(&self, app: u16) -> Result<()> {
        self.ensure_no_persistent_update()?;
        if !(2..=4).contains(&app) { bail!("DESKTOP_BOOTSTRAP_PROTECTED"); }
        if self.closing.is_some() || self.instances.iter().any(|instance| instance.app == app)
            || self.pending_autostart.contains(&app) { bail!("DESKTOP_UPDATE_RUNNING_OR_PENDING"); }
        let entry = self.catalog.apps.iter().find(|entry| entry.id == app).context("DESKTOP_UNKNOWN_APP")?;
        let package = crate::package::load_package(&entry.manifest_path)?;
        if package.manifest.id != entry.package_id || package.storage_fingerprint() != entry.package_fingerprint
            || package.manifest.sdk_version != 6 { bail!("DESKTOP_PACKAGE_CHANGED"); }
        self.options.install_registry.as_ref().context("DESKTOP_UPDATE_REGISTRY")?.verify_package(&package)
    }
    #[cfg(target_os = "linux")]
    fn update_state(&mut self, record: &crate::persistent_update::UpdateRecord, status: &str) {
        self.persistent_update_state = Some(serde_json::json!({"transactionId":record.transaction_id,
            "app":record.app,"packageId":record.new.package_id,"newVersion":record.new.version,"status":status}));
        self.changed();
    }
    #[cfg(target_os = "linux")]
    fn update_output_checkpoint(&self, _stage: &'static str) -> Result<()> {
        #[cfg(test)]
        if self.persistent_update_output_faults.contains(_stage) { bail!("injected {_stage}"); }
        Ok(())
    }
    #[cfg(target_os = "linux")]
    fn begin_persistent_update(&mut self, package: crate::package::Package, app: u16) -> Result<()> {
        if package.manifest.sdk_version != 6 || package.manifest.entry != "main.wasm" { bail!("DESKTOP_UPDATE_PACKAGE_SCHEMA"); }
        self.verify_persistent_update_target(app)?;
        let previous = self.pending_update_identity.as_ref().context("DESKTOP_UPDATE_CONFIRMATION_IDENTITY")?;
        let current = self.catalog.apps.iter().find(|entry| entry.id == app).context("DESKTOP_UNKNOWN_APP")?;
        if previous.id != app || previous.package_id != package.manifest.id || previous.package_id != current.package_id
            || previous.package_fingerprint != current.package_fingerprint || previous.manifest_path != current.manifest_path {
            bail!("DESKTOP_PACKAGE_CHANGED");
        }
        if self.options.persistent_update_receipts && self.native_uninstall_confirm.is_none() {
            bail!("DESKTOP_UPDATE_NATIVE_CONFIRMATION_REQUIRED");
        }
        if self.storage_authority.is_none() {
            let root = self.app_data.root.clone().context("DESKTOP_DATA_ROOT_REQUIRED")?;
            self.storage_authority = Some(Arc::new(crate::app_data::PackageStorageAuthority::open_default_private(root)?));
        }
        let registry = self.options.install_registry.as_ref().context("DESKTOP_UPDATE_REGISTRY")?;
        let authority = self.storage_authority.as_ref().context("DESKTOP_UPDATE_STORAGE_AUTHORITY")?;
        let data_root = self.options.data_root_path.as_deref().context("DESKTOP_DATA_ROOT_REQUIRED")?;
        match crate::persistent_update::begin_existing_update(registry, authority, data_root, &package, app) {
            Ok(trial) => {
                self.catalog = trial.catalog;
                self.pending_update_identity = None;
                self.reset_catalog_binding(app);
                self.update_state(&trial.record, "trial");
                self.persistent_update = Some(DesktopTrialUpdate { record: trial.record.clone(), launched: false, down: None,
                    confirmation: self.native_uninstall_confirm.clone() });
                if self.options.persistent_update_receipts {
                    let output = (|| {
                        let anchor = self.native_anchor()?;
                        self.emit_native_receipt("trial-published", serde_json::json!({"record":trial.record,
                            "trialObservation":trial.observation,"confirmation":self.native_uninstall_confirm,"anchor":anchor}))
                    })();
                    if let Err(error) = output {
                        return Err(self.uninstall_session_failure("DESKTOP_PERSISTENT_UPDATE_TRIAL_OUTPUT", error));
                    }
                }
                Ok(())
            }
            Err(failure) => {
                if !matches!(failure.status.as_str(), "rejected" | "rolled-back") {
                    let record = self.options.install_registry.as_ref().and_then(|registry|
                        registry.read_coordinated_marker(crate::persistent_update::UPDATE_PENDING_NAME,4096).ok().flatten())
                        .and_then(|raw|crate::persistent_update::parse_update_record(&raw).ok());
                    if let Some(record) = record { self.update_state(&record, if failure.status=="commit-uncertain" { "uncertain" } else { "blocked" }); }
                    return Err(self.uninstall_session_failure("DESKTOP_PERSISTENT_UPDATE_FATAL", failure.into()));
                }
                self.log(app, 0, 0, "update-not-saved", failure.to_string()); self.changed();
                Ok(())
            }
        }
    }
    #[cfg(target_os = "linux")]
    fn check_persistent_launch(&self, app: u16) -> Result<()> {
        if self.persistent_install_blocked { bail!("DESKTOP_PERSISTENT_INSTALL_BLOCKED"); }
        if self.options.persistent_install_root.is_none() { return Ok(()); }
        if let Some(trial) = &self.persistent_update {
            if trial.record.app != app || trial.launched || !self.native_dock_launch_active
                || self.instances.iter().any(|instance| instance.app == app) {
                bail!("DESKTOP_UPDATE_NATIVE_TRIAL_REQUIRED");
            }
            let registry = self.options.install_registry.as_ref().context("DESKTOP_UPDATE_REGISTRY")?;
            let raw = registry.read_coordinated_marker(crate::persistent_update::UPDATE_PENDING_NAME,4096)?
                .context("DESKTOP_UPDATE_OUTER_MISSING")?;
            if crate::persistent_update::parse_update_record(&raw)? != trial.record {
                bail!("DESKTOP_UPDATE_TRANSACTION_CHANGED");
            }
            let pending = registry.pending_update()?.context("DESKTOP_UPDATE_STARTUP_PENDING_MISSING")?;
            if pending.package_id != trial.record.new.package_id || pending.new_version != trial.record.new.version
                || pending.new_fingerprint.iter().map(|byte|format!("{byte:02x}")).collect::<String>() != trial.record.new.fingerprint {
                bail!("DESKTOP_UPDATE_PACKAGE_CHANGED");
            }
        } else { self.ensure_no_persistent_update()?; }
        Ok(())
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn launch_from_native_dock(&mut self, app: u16) -> Result<()> {
        self.native_dock_launch(app);
        let trial_action = self.persistent_update.is_some();
        self.native_dock_launch_active = true;
        let result = if trial_action { self.perform(&DesktopAction::Launch { app }) }
            else { self.user_action(&DesktopAction::Launch { app }) };
        self.native_dock_launch_active = false;
        if let Err(error) = result {
            if self.persistent_install_blocked { return Err(error); }
            if self.persistent_update.as_ref().is_some_and(|trial|trial.record.app==app)
                && !self.instances.iter().any(|instance| instance.app==app) {
                self.rollback_persistent_update()?;
            }
            self.log(app,0,0,"action_rejected",format!("{error:#}"));
        }
        Ok(())
    }
    #[cfg(target_os = "linux")]
    fn rollback_persistent_update(&mut self) -> Result<()> {
        let Some(trial) = self.persistent_update.as_ref() else { return Ok(()); };
        let record = trial.record.clone();
        let result = (|| {
            let registry = self.options.install_registry.as_ref().context("DESKTOP_UPDATE_REGISTRY")?;
            let authority = self.storage_authority.as_ref().context("DESKTOP_UPDATE_STORAGE_AUTHORITY")?;
            let data = self.options.data_root_path.as_deref().context("DESKTOP_DATA_ROOT_REQUIRED")?;
            let recovered = crate::persistent_update::recover_loaded_update(registry, authority, data, &record.transaction_id)?;
            if !matches!(recovered, crate::persistent_update::UpdateRecovery::RolledBack(_)) { bail!("DESKTOP_UPDATE_ROLLBACK_DECISION"); }
            crate::desktop_catalog::load_catalog_with_install_registry(&registry.root_path().join("catalog.json"), registry)
        })();
        match result {
            Ok((catalog, _)) => {
                self.catalog = catalog; self.reset_catalog_binding(record.app);
                self.persistent_update = None; self.update_state(&record, "rolled-back");
                self.log(record.app, 0, 0, "update-rolled-back", record.new.package_id.clone());
                if self.options.persistent_update_receipts {
                    self.emit_native_receipt("update-rolled-back", serde_json::json!({"record":record}))?;
                }
                Ok(())
            }
            Err(error) => {
                let uncertain = error.downcast_ref::<crate::persistent_update::UpdateFailure>()
                    .is_some_and(|failure|failure.status=="commit-uncertain");
                self.update_state(&record, if uncertain { "uncertain" } else { "blocked" });
                Err(self.uninstall_session_failure("DESKTOP_PERSISTENT_UPDATE_RECOVERY_FAILED", error))
            }
        }
    }

    #[cfg(target_os = "linux")]
    fn commit_persistent_install(&mut self, package: crate::package::Package, app: u16,
        package_id: String, version: String, fingerprint: String, manifest_path: PathBuf) -> Result<()> {
        let registry = self.options.install_registry.as_ref().context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?;
        #[cfg(not(test))]
        let result = crate::persistent_install::install_new(registry, &package);
        #[cfg(test)]
        let result = if self.persistent_install_faults.is_empty() {
            crate::persistent_install::install_new(registry, &package)
        } else {
            let faults = self.persistent_install_faults.clone();
            crate::persistent_install::install_new_with_hook(registry, &package, &mut |stage| {
                if faults.contains(stage) { bail!("injected {stage}"); } Ok(())
            })
        };
        match result {
            Ok(committed) => {
                let bound = committed.catalog.apps.iter().find(|entry| entry.package_id == package_id)
                    .context("DESKTOP_INSTALL_CATALOG_ENTRY")?.id;
                self.catalog = committed.catalog;
                self.reset_catalog_binding(bound);
                self.emit_persistent_install_receipt("persistent-install-commit", serde_json::to_value(&committed.receipt)?)?;
                self.log(0, 0, 0, "installed", package_id);
                self.changed();
                if self.native_receipts_enabled() {
                    self.write_state(true)?;
                    self.emit_native_receipt("persistent-install-installed", serde_json::json!({
                        "schemaVersion":1,"registryRoot":committed.receipt.registry_root,
                        "catalogPath":committed.receipt.catalog_path,"stateRevision":self.state_revision,
                        "content":committed.receipt.content}))?;
                }
                self.launch_pending_autostart()?;
                Ok(())
            }
            Err(failure) => {
                if failure.fatal() {
                    self.persistent_install_blocked = true;
                    self.state_error = Some(format!("{} at {}: {}", failure.status, failure.stage, failure.error));
                    self.changed();
                }
                let mut receipt = serde_json::to_value(&failure)?;
                let fields = receipt.as_object_mut().expect("typed failure object");
                fields.insert("packageId".into(), package_id.clone().into());
                fields.insert("version".into(), version.clone().into());
                fields.insert("fingerprint".into(), fingerprint.clone().into());
                self.emit_persistent_install_receipt("persistent-install-failed", receipt)?;
                if failure.fatal() {
                    return Err(anyhow!("DESKTOP_PERSISTENT_INSTALL_FATAL {} at {}: {}", failure.status, failure.stage, failure.error));
                }
                self.log(0, 0, 0, "install_not_saved", failure.error);
                let prompt = crate::graphics::desktop_shell::StoragePrompt::install(&package_id, &version, &fingerprint, false, true, false)?;
                self.pending_storage = Some(PendingDesktopStorage { app, pending: DesktopStorageDecision::Install {
                    package_id, version, fingerprint, manifest_path, update: false, retry: true }, prompt,
                    composed: None, presented: false, presented_sequence: None });
                self.changed();
                Ok(())
            }
        }
    }
    #[cfg(target_os = "linux")]
    fn verify_uninstall_target(&self, app: u16, package: &crate::package::Package, manifest: &std::path::Path) -> Result<()> {
        self.ensure_no_persistent_update()?;
        if self.persistent_install_blocked { bail!("DESKTOP_PERSISTENT_INSTALL_BLOCKED"); }
        if self.instances.iter().any(|instance| instance.app == app) || self.pending_autostart.contains(&app) {
            bail!("DESKTOP_UNINSTALL_RUNNING_OR_PENDING");
        }
        let entry = self.catalog.apps.iter().find(|entry| entry.id == app).context("DESKTOP_UNKNOWN_APP")?;
        if entry.manifest_path != manifest || entry.package_id != package.manifest.id || entry.package_fingerprint != package.storage_fingerprint() {
            bail!("DESKTOP_PACKAGE_CHANGED");
        }
        if self.options.persistent_install_root.is_some() && app == 1 { bail!("DESKTOP_BOOTSTRAP_PROTECTED"); }
        self.options.install_registry.as_ref().context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?.verify_package(package)
    }
    #[cfg(target_os = "linux")]
    fn uninstall_session_failure(&mut self, stage: &str, error: anyhow::Error) -> anyhow::Error {
        self.persistent_install_blocked = true;
        self.state_error = Some(format!("{stage}: {error:#}").chars().take(2048).collect());
        self.changed();
        anyhow!("{stage}: {error:#}")
    }
    #[cfg(target_os = "linux")]
    fn uninstall_output_checkpoint(&self, _stage: &'static str) -> Result<()> {
        #[cfg(test)]
        if self.persistent_uninstall_output_faults.contains(_stage) { bail!("injected {_stage}"); }
        Ok(())
    }
    #[cfg(target_os = "linux")]
    fn uninstall_private_snapshot(&self, package: &crate::package::Package) -> Result<crate::app_data::PrivateContentSnapshot> {
        let root = self.options.data_root_path.as_deref().context("DESKTOP_UNINSTALL_DATA_ROOT")?;
        let snapshot = crate::app_data::snapshot_private_read_only(root, &package.manifest.id)?;
        if !snapshot.files.iter().any(|file| file.path == "note.txt" && file.size == 5
            && file.sha256 == "8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8") {
            bail!("DESKTOP_UNINSTALL_PRIVATE_SENTINEL");
        }
        Ok(snapshot)
    }
    #[cfg(target_os = "linux")]
    fn commit_persistent_uninstall(&mut self, app: u16, package: crate::package::Package) -> Result<()> {
        let cause = self.native_uninstall_confirm.take();
        let private_before = if self.options.persistent_uninstall_receipts {
            let observation = (|| {
                if cause.is_none() { bail!("DESKTOP_UNINSTALL_NATIVE_CAUSE_REQUIRED"); }
                self.uninstall_private_snapshot(&package)
            })();
            Some(observation.map_err(|error| self.uninstall_session_failure("DESKTOP_PERSISTENT_UNINSTALL_BASELINE", error))?)
        } else { None };
        let registry = self.options.install_registry.as_ref().context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?;
        #[cfg(not(test))]
        let result = crate::persistent_install::uninstall_existing(registry, &package);
        #[cfg(test)]
        let result = {
            let faults = self.persistent_install_faults.clone();
            uninstall_test_transaction(registry, &package, &mut |stage| {
                if faults.contains(stage) { bail!("injected {stage}"); } Ok(())
            })
        };
        match result {
            Ok(committed) => {
                self.catalog = committed.catalog;
                self.reset_catalog_binding(app);
                self.changed();
                let output = (|| -> Result<()> {
                    let mut receipt = serde_json::to_value(&committed.receipt)?;
                    if let Some(before) = private_before {
                        self.uninstall_output_checkpoint("private-after")?;
                        let after = self.uninstall_private_snapshot(&package)?;
                        if after != before { bail!("DESKTOP_UNINSTALL_PRIVATE_CHANGED"); }
                        receipt["cause"] = cause.expect("qualified cause checked");
                        receipt["privateBefore"] = serde_json::to_value(before)?;
                        receipt["privateAfter"] = serde_json::to_value(after)?;
                    }
                    self.uninstall_output_checkpoint("state-file")?;
                    self.write_state(true)?;
                    self.uninstall_output_checkpoint("commit-receipt")?;
                    self.emit_persistent_install_receipt("persistent-uninstall-commit", receipt)?;
                    self.uninstall_output_checkpoint("log")?;
                    self.log(app, 0, 0, "uninstalled", package.manifest.id.clone());
                    if self.dropped_trace != 0 { bail!("DESKTOP_UNINSTALL_LOG_DROPPED"); }
                    Ok(())
                })();
                output.map_err(|error| self.uninstall_session_failure("DESKTOP_PERSISTENT_UNINSTALL_COMMITTED_OUTPUT", error))?;
                self.launch_pending_autostart()?;
                Ok(())
            }
            Err(failure) => {
                let fatal = failure.fatal();
                if fatal {
                    self.persistent_install_blocked = true;
                    self.state_error = Some(format!("{} at {}: {}", failure.status, failure.stage, failure.error));
                    self.changed();
                }
                let mut receipt = serde_json::to_value(&failure)?;
                receipt["packageId"] = package.manifest.id.clone().into();
                receipt["version"] = package.manifest.version.clone().into();
                receipt["fingerprint"] = package_fingerprint_hex(&package).into();
                self.emit_persistent_install_receipt("persistent-uninstall-failed", receipt)?;
                if fatal { bail!("DESKTOP_PERSISTENT_UNINSTALL_FATAL {} at {}: {}", failure.status, failure.stage, failure.error); }
                self.log(app, 0, 0, "uninstall_not_saved", failure.error);
                let manifest_path = self.catalog.apps.iter().find(|entry| entry.id == app).context("DESKTOP_UNKNOWN_APP")?.manifest_path.clone();
                let fingerprint = package_fingerprint_hex(&package);
                let package_id = package.manifest.id;
                let version = package.manifest.version;
                let prompt = crate::graphics::desktop_shell::StoragePrompt::uninstall(&package_id, &version, &fingerprint, true)?;
                self.pending_storage = Some(PendingDesktopStorage { app,
                    pending: DesktopStorageDecision::Uninstall { package_id, version, fingerprint, manifest_path },
                    prompt, composed: None, presented: false, presented_sequence: None });
                self.changed();
                Ok(())
            }
        }
    }
    #[cfg(target_os = "linux")]
    fn native_receipts_enabled(&self) -> bool {
        (self.options.persistent_install_receipts || self.options.persistent_uninstall_receipts || self.options.persistent_update_receipts) && self.options.persistent_install_root.is_some()
            && self.options.state_file.is_some() && !self.options.offscreen
    }
    #[cfg(target_os = "linux")]
    fn check_native_evidence(&mut self) -> Result<()> {
        if self.native_receipts_enabled() && (self.persistent_install_blocked || self.dropped_trace != 0 || self.dropped_inputs != 0) {
            self.persistent_install_blocked = true;
            bail!("DESKTOP_NATIVE_EVIDENCE_DROPPED_OR_BLOCKED");
        }
        Ok(())
    }
    #[cfg(target_os = "linux")]
    fn native_anchor(&mut self) -> Result<serde_json::Value> {
        self.check_native_evidence()?;
        self.write_state(true)?;
        Ok(serde_json::json!({"state":self.state_snapshot(self.state_revision),"history":self.records,
            "traceCount":self.trace.len(),"droppedTrace":self.dropped_trace,"droppedInputs":self.dropped_inputs}))
    }
    #[cfg(target_os = "linux")]
    fn emit_native_receipt(&mut self, kind: &'static str, receipt: serde_json::Value) -> Result<()> {
        use std::io::Write;
        self.check_native_evidence()?;
        let record = serde_json::json!({"kind":kind,"bootId":self.state_boot,"sessionId":self.state_session,
            "desktopPid":std::process::id(),"receipt":receipt});
        let mut line = self.persistent_receipt_prefix().as_bytes().to_vec();
        line.extend_from_slice(&serde_json::to_vec(&record)?);
        if line.len() > 262144 || self.native_evidence.receipts >= 128 {
            self.persistent_install_blocked = true;
            bail!("DESKTOP_NATIVE_EVIDENCE_BOUND");
        }
        line.push(b'\n');
        if let Err(error) = std::io::stderr().lock().write_all(&line) {
            self.persistent_install_blocked = true;
            return Err(error).context("DESKTOP_NATIVE_EVIDENCE_WRITE");
        }
        self.native_evidence.receipts += 1;
        #[cfg(test)]
        self.test_native_receipts.push(record);
        Ok(())
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn begin_native_input(&mut self, down: bool, mouse: Option<(i32, i32)>) -> Result<()> {
        if !self.native_receipts_enabled() { return Ok(()); }
        self.check_native_evidence()?;
        if self.native_evidence.pending.is_some() || self.native_evidence.sequence >= 64
            || (down && self.native_evidence.press.is_some()) || (!down && self.native_evidence.press.is_none()) {
            self.persistent_install_blocked = true;
            bail!("DESKTOP_NATIVE_EVIDENCE_SEQUENCE");
        }
        let sequence = self.native_evidence.sequence + 1;
        let press = if down { sequence } else { self.native_evidence.press.expect("checked press") };
        let before = self.native_anchor()?;
        self.native_evidence.sequence = sequence;
        self.native_evidence.press = down.then_some(press);
        self.native_evidence.pending = Some(NativeInputEvidence { sequence, press, down, mouse, before,
            trace_start: self.trace.len(), target: None });
        Ok(())
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn native_dock_launch(&mut self, app: u16) {
        if let Some(input) = self.native_evidence.pending.as_mut().filter(|input| input.down) { input.target = Some(app); }
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn end_native_input(&mut self) -> Result<()> {
        let Some(input) = self.native_evidence.pending.take() else { return Ok(()); };
        let after = self.native_anchor()?;
        self.emit_native_receipt("native-input", serde_json::json!({"schemaVersion":1,
            "sequence":input.sequence,"pressSequence":input.press,"edge":if input.down {"down"} else {"up"},
            "x":input.mouse.map(|p|p.0),"y":input.mouse.map(|p|p.1),
            "action":if input.target.is_some() {"launch"} else {"other"},"targetApp":input.target,
            "before":input.before,"after":after,"trace":self.trace[input.trace_start..]}))
    }
    #[cfg(target_os = "linux")]
    fn emit_native_launches(&mut self) -> Result<()> {
        if !self.native_receipts_enabled() { return Ok(()); }
        self.check_native_evidence()?;
        for index in 0..self.native_evidence.launches.len() {
            let cause = &self.native_evidence.launches[index];
            let record = &self.records[cause.record];
            let phases = [(!cause.started && record.worker_pid != 0, "worker-started"),
                (!cause.ready && record.worker_pid != 0 && record.first_ready_ms.is_some(), "ready")];
            let (app,generation,window,pid,down) = (record.app,record.generation,record.window,record.worker_pid,cause.down);
            for (emit, phase) in phases {
                if !emit { continue; }
                let anchor = self.native_anchor()?;
                self.emit_native_receipt("native-launch", serde_json::json!({"schemaVersion":1,"downSequence":down,
                    "phase":phase,"app":app,"generation":generation,"window":window,"workerPid":pid,"anchor":anchor}))?;
                if phase == "worker-started" { self.native_evidence.launches[index].started = true; }
                else { self.native_evidence.launches[index].ready = true; }
            }
        }
        Ok(())
    }

    #[cfg(target_os = "linux")]
    fn emit_persistent_install_receipt(&mut self, kind: &'static str, receipt: serde_json::Value) -> Result<()> {
        use std::io::Write;
        self.log(0, 0, 0, kind, serde_json::to_string(&receipt)?);
        let record = serde_json::json!({"kind":kind,"bootId":self.state_boot,
            "sessionId":self.state_session,"desktopPid":std::process::id(),"receipt":receipt});
        let line = format!("{}{}\n", self.persistent_receipt_prefix(), serde_json::to_string(&record)?);
        if (self.options.persistent_uninstall_receipts || self.options.persistent_update_receipts) && (line.len() > 262144 || self.native_evidence.receipts >= 128) {
            self.persistent_install_blocked = true;
            bail!("DESKTOP_NATIVE_EVIDENCE_BOUND");
        }
        std::io::stderr().lock().write_all(line.as_bytes()).context("DESKTOP_PERSISTENT_RECEIPT_WRITE")?;
        if self.options.persistent_uninstall_receipts || self.options.persistent_update_receipts {
            self.native_evidence.receipts += 1;
            #[cfg(test)]
            self.test_native_receipts.push(record);
        }
        Ok(())
    }
    #[cfg(target_os = "linux")]
    fn persistent_receipt_prefix(&self) -> &'static str {
        if self.options.persistent_update_receipts { "MING_PERSISTENT_UPDATE " }
        else if self.options.persistent_uninstall_receipts { "MING_PERSISTENT_UNINSTALL " } else { "MING_PERSISTENT_INSTALL " }
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn storage_prompt_native_binding(&self) -> Option<serde_json::Value> {
        let pending = self.pending_storage.as_ref()?;
        let mut value = pending.prompt.state_value();
        value["promptRevision"] = self.storage_prompt_revision.into();
        value["presentedSequence"] = serde_json::json!(pending.presented_sequence);
        if let DesktopStorageDecision::Uninstall { package_id, version, fingerprint, manifest_path }
            | DesktopStorageDecision::Install { package_id, version, fingerprint, manifest_path, .. } = &pending.pending {
            value["packageId"] = package_id.clone().into();
            value["version"] = version.clone().into();
            value["fingerprint"] = fingerprint.clone().into();
            value["manifestPath"] = serde_json::json!(manifest_path);
        }
        Some(value)
    }
    #[cfg(target_os = "linux")]
    fn native_uninstall_cause(&self, app: u16, package_id: &str, version: &str, fingerprint: &str, presented: u64) -> Result<serde_json::Value> {
        let input = self.native_evidence.pending.as_ref().filter(|input| !input.down).context("DESKTOP_UNINSTALL_NATIVE_RELEASE")?;
        Ok(serde_json::json!({"app":app,"packageId":package_id,"version":version,"fingerprint":fingerprint,
            "releaseSequence":input.sequence,"pressSequence":input.press,"presentedSequence":presented,
            "promptRevision":self.storage_prompt_revision}))
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn begin_uninstall_from_native(&mut self, app: u16) -> Result<()> {
        if !self.options.persistent_uninstall_receipts { return self.begin_uninstall(app); }
        let before = self.native_evidence.pending.as_ref().filter(|input| !input.down)
            .context("DESKTOP_UNINSTALL_NATIVE_RELEASE")?.before.clone();
        let entry = self.catalog.apps.iter().find(|entry| entry.id == app).context("DESKTOP_UNKNOWN_APP")?;
        let package = crate::package::load_package(&entry.manifest_path)?;
        let presented = self.last_accepted.context("DESKTOP_UNINSTALL_NATIVE_PRESENTED")?.0;
        self.begin_uninstall(app)?;
        let mut receipt = self.native_uninstall_cause(app, &package.manifest.id, &package.manifest.version, &package_fingerprint_hex(&package), presented)?;
        receipt["schemaVersion"] = 1.into();
        receipt["before"] = before;
        receipt["after"] = self.native_anchor()?;
        self.emit_native_receipt("uninstall-request", receipt)
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn respond_storage_prompt_from_native(&mut self, allow: bool) -> Result<()> {
        if self.options.persistent_update_receipts && self.pending_storage.as_ref().is_some_and(|pending|
            matches!(pending.pending, DesktopStorageDecision::Install { update: true, .. })) {
            let pending = self.pending_storage.as_ref().expect("checked pending");
            let DesktopStorageDecision::Install { package_id, version, fingerprint, .. } = &pending.pending else { unreachable!() };
            let mut cause = self.native_uninstall_cause(pending.app, package_id, version, fingerprint,
                pending.presented_sequence.context("DESKTOP_STORAGE_NOT_PRESENTED")?)?;
            cause["binding"] = self.storage_prompt_native_binding().context("DESKTOP_STORAGE_NO_PROMPT")?;
            cause["before"] = self.native_evidence.pending.as_ref().expect("checked release").before.clone();
            self.native_uninstall_confirm = allow.then(|| cause.clone());
            let result = self.respond_storage_prompt(allow);
            self.native_uninstall_confirm = None;
            result?;
            cause["allow"] = allow.into(); cause["after"] = self.native_anchor()?;
            return self.emit_native_receipt("update-confirm", cause);
        }
        if !self.options.persistent_uninstall_receipts
            || !self.pending_storage.as_ref().is_some_and(|pending| matches!(pending.pending, DesktopStorageDecision::Uninstall { .. })) {
            return self.respond_storage_prompt(allow);
        }
        let pending = self.pending_storage.as_ref().expect("checked pending");
        let DesktopStorageDecision::Uninstall { package_id, version, fingerprint, .. } = &pending.pending else { unreachable!() };
        let mut receipt = self.native_uninstall_cause(pending.app, package_id, version, fingerprint,
            pending.presented_sequence.context("DESKTOP_STORAGE_NOT_PRESENTED")?)?;
        let before = self.native_evidence.pending.as_ref().expect("checked release").before.clone();
        self.native_uninstall_confirm = allow.then(|| receipt.clone());
        let result = self.respond_storage_prompt(allow);
        self.native_uninstall_confirm = None;
        result?;
        receipt["schemaVersion"] = 1.into();
        receipt["allow"] = allow.into();
        receipt["before"] = before;
        receipt["after"] = self.native_anchor()?;
        self.emit_native_receipt("uninstall-confirm", receipt)
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
        if self.persistent_install_blocked { bail!("DESKTOP_PERSISTENT_INSTALL_BLOCKED"); }
        if self.pending_storage.is_some() || self.closing.is_some() || self.options.offscreen {
            bail!("DESKTOP_INSTALL_UNAVAILABLE");
        }
        if !source.is_absolute() {
            bail!("DESKTOP_INSTALL_SOURCE");
        }
        let registry = self.options.install_registry.as_ref().context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?;
        self.ensure_no_persistent_update()?;
        let package = crate::package::load_package(source).context("DESKTOP_INSTALL_PACKAGE")?;
        registry.verify_package_source(&package).context("DESKTOP_INSTALL_PUBLISHER")?;
        let existing = self.catalog.apps.iter().find(|entry| entry.package_id == package.manifest.id).cloned();
        let (update, app) = if let Some(entry) = existing.as_ref() {
            let current = crate::package::load_package(&entry.manifest_path).context("DESKTOP_INSTALL_CURRENT")?;
            if !crate::package::version_is_newer(&package.manifest.version, &current.manifest.version) {
                bail!("DESKTOP_INSTALL_VERSION");
            }
            if self.options.persistent_install_root.is_some() {
                if package.manifest.sdk_version != 6 || package.manifest.entry != "main.wasm" { bail!("DESKTOP_UPDATE_PACKAGE_SCHEMA"); }
                self.verify_persistent_update_target(entry.id)?;
            }
            (true, entry.id)
        } else {
            if self.catalog.apps.len() >= 4 { bail!("DESKTOP_CATALOG_LIMIT"); }
            (false, 0)
        };
        let mut fingerprint = package_fingerprint_hex(&package);
        let blocked = update && (self.instances.iter().any(|instance| instance.app == app) || self.pending_autostart.contains(&app));
        let prompt = crate::graphics::desktop_shell::StoragePrompt::install(
            &package.manifest.id,
            &package.manifest.version,
            &fingerprint,
            update,
            false,
            blocked,
        )?;
        self.pending_update_identity = existing;
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
        self.ensure_no_persistent_update()?;
        if self.persistent_install_blocked { bail!("DESKTOP_PERSISTENT_INSTALL_BLOCKED"); }
        if self.pending_storage.is_some() || self.closing.is_some() || self.options.offscreen {
            bail!("DESKTOP_UNINSTALL_UNAVAILABLE");
        }
        let entry = self.catalog.apps.iter().find(|entry| entry.id == app).context("DESKTOP_UNKNOWN_APP")?;
        let registry = self.options.install_registry.as_ref().context("DESKTOP_INSTALL_REGISTRY_REQUIRED")?;
        let package = crate::package::load_package(&entry.manifest_path).context("DESKTOP_UNINSTALL_PACKAGE")?;
        registry.verify_package(&package).context("DESKTOP_UNINSTALL_PACKAGE")?;
        if package.manifest.id != entry.package_id || package.storage_fingerprint() != entry.package_fingerprint { bail!("DESKTOP_PACKAGE_CHANGED"); }
        let (pending, prompt) = if self.options.persistent_install_root.is_some() && app == 1 {
            (DesktopStorageDecision::UninstallBlocked, crate::graphics::desktop_shell::StoragePrompt::uninstall_protected(&package.manifest.id)?)
        } else if self.instances.iter().any(|instance| instance.app == app) || self.pending_autostart.contains(&app) {
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
        if self.persistent_install_blocked { bail!("DESKTOP_PERSISTENT_INSTALL_BLOCKED"); }
        if self.options.persistent_install_root.is_some() { bail!("DESKTOP_PERSISTENT_MUTATION_UNSUPPORTED"); }
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
        #[cfg(target_os = "linux")]
        self.check_persistent_launch(app)?;
        #[cfg(target_os = "linux")]
        if self.persistent_install_blocked { bail!("DESKTOP_PERSISTENT_INSTALL_BLOCKED"); }
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
                        crate::storage_consent::PublicConsentRequest::Prompt(_) if self.persistent_update.is_some() => {
                            drop(session);
                            self.rollback_persistent_update()?;
                            bail!("DESKTOP_UPDATE_PUBLIC_AUTHORIZATION_REQUIRED");
                        }
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
        #[cfg(target_os = "linux")]
        if self.persistent_install_blocked { bail!("DESKTOP_PERSISTENT_INSTALL_BLOCKED"); }
        let app = entry.id;
        #[cfg(target_os = "linux")]
        self.check_persistent_launch(app)?;
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
        let startup_result: Result<Option<UpdateStartupIdentity>> = (|| {
            Ok(if let Some(registry) = self.options.install_registry.as_ref() {
            match registry.pending_update()?.filter(|pending| pending.package_id == entry.package_id) {
                Some(pending) => {
                    if pending.new_fingerprint != entry.package_fingerprint { bail!("DESKTOP_UPDATE_PACKAGE_CHANGED"); }
                    let started = registry.begin_update_startup(&pending.package_id, pending.new_fingerprint)?;
                    if let Some(trial) = self.persistent_update.as_mut() {
                        if !started { bail!("DESKTOP_UPDATE_STARTUP_ALREADY_ATTEMPTED"); }
                        trial.launched = true;
                        trial.down = self.native_evidence.pending.as_ref().filter(|input| input.down).map(|input| input.sequence);
                    }
                    Some(UpdateStartupIdentity { package_id: pending.package_id, version: pending.new_version,
                        fingerprint: pending.new_fingerprint, app, generation, window: window.0 })
                }
                None if self.persistent_update.is_some() => bail!("DESKTOP_UPDATE_STARTUP_PENDING_MISSING"),
                None => None,
            }
            } else { None })
        })();
        #[cfg(target_os = "linux")]
        let update_startup = match startup_result {
            Ok(identity) => identity,
            Err(error) => {
                let effects = self.scene.model_mut().close(window).map_err(model_error)?;
                self.effects(effects)?;
                return Err(error);
            }
        };
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
        #[cfg(target_os = "linux")]
        if let Some(input) = self.native_evidence.pending.as_ref().filter(|input| input.down && input.target == Some(app)) {
            self.native_evidence.launches.push(NativeLaunchEvidence { record, down: input.sequence, started: false, ready: false });
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
            // Kernel cleanup may finish after this poll's initial snapshot.
            // Joining makes the shared isolation receipt final, including failure.
            self.records[a.record].isolation = a.isolation.as_ref().map(|s| s.snapshot());
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
        #[cfg(target_os = "linux")]
        self.emit_native_launches()?;
        Ok(())
    }
    #[cfg(target_os = "linux")]
    fn finish_update_startup(&mut self, expected: &UpdateStartupIdentity, evidence: &UpdateStartupEvidence) -> Result<()> {
        if let Some(trial) = self.persistent_update.as_ref() {
            let transaction = trial.record.clone(); let down = trial.down; let confirmation = trial.confirmation.clone();
            let identity_matches = expected.app == transaction.app && expected.package_id == transaction.new.package_id
                && expected.version == transaction.new.version
                && expected.fingerprint.iter().map(|byte| format!("{byte:02x}")).collect::<String>() == transaction.new.fingerprint;
            let record_matches = self.current_record(expected.app).is_some_and(|record| record.completed
                && record.generation == expected.generation && record.window == expected.window
                && record.worker_pid == evidence.worker_pid && record.worker_pid != 0
                && record.package_id.as_deref() == Some(expected.package_id.as_str())
                && record.error.is_none() && record.first_ready_ms.is_some()
                && record.presented_source_sequence == evidence.presented_source_sequence);
            if !trial.launched || !identity_matches || !record_matches || !evidence.confirms(expected)
                || (self.options.persistent_update_receipts && (down.is_none() || confirmation.is_none())) {
                return self.rollback_persistent_update();
            }
            let proof = crate::persistent_update::UpdateLifecycleProof { app:expected.app, package:transaction.new.clone(),
                generation:expected.generation, window:expected.window, worker_pid:evidence.worker_pid,
                input_ready:evidence.input_ready, presented_source_sequence:evidence.presented_source_sequence,
                completed:evidence.completed, result_accepted:evidence.result_accepted,
                native_exit_ok:evidence.native_exit_ok, reaped:evidence.reaped };
            let registry = self.options.install_registry.as_ref().context("DESKTOP_UPDATE_REGISTRY")?;
            let authority = self.storage_authority.as_ref().context("DESKTOP_UPDATE_STORAGE_AUTHORITY")?;
            let data = self.options.data_root_path.as_deref().context("DESKTOP_DATA_ROOT_REQUIRED")?;
            let committed = match crate::persistent_update::confirm_existing_update(registry, authority, data, &proof) {
                Ok(committed) => committed,
                Err(failure) => {
                    if failure.status == "rejected" { return self.rollback_persistent_update(); }
                    self.update_state(&transaction, match failure.status.as_str() {
                        "commit-uncertain" => "uncertain", "commit-pending" => "commit-decided", _ => "blocked",
                    });
                    return Err(self.uninstall_session_failure("DESKTOP_PERSISTENT_UPDATE_COMMIT_FAILED", failure.into()));
                }
            };
            // The coordinator has completed strict verification and cleared both journals.
            // Align memory before any state/log/receipt operation that can fail.
            self.persistent_update = None;
            self.update_state(&committed.record, "committed");
            let output = (|| {
                self.update_output_checkpoint("state-file")?; self.write_state(true)?;
                self.update_output_checkpoint("startup-receipt")?;
                if self.options.persistent_update_receipts {
                    let anchor = self.native_anchor()?;
                    self.emit_native_receipt("startup-confirmed", serde_json::json!({"transactionId":transaction.transaction_id,
                        "confirmation":confirmation,"downSequence":down,"proof":proof,"anchor":anchor}))?;
                }
                self.update_output_checkpoint("commit-receipt")?;
                if self.options.persistent_update_receipts {
                    self.emit_native_receipt("persistent-update-commit", serde_json::json!({"record":committed.record,
                        "confirmation":confirmation,"downSequence":down,"content":committed.content}))?;
                }
                self.update_output_checkpoint("log")?;
                self.log(expected.app, expected.window, expected.generation, "startup-confirmed", expected.package_id.clone());
                if self.dropped_trace != 0 { bail!("DESKTOP_UPDATE_LOG_DROPPED"); }
                Ok(())
            })();
            return output.map_err(|error| self.uninstall_session_failure("DESKTOP_PERSISTENT_UPDATE_COMMITTED_OUTPUT", error));
        }
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
        #[cfg(target_os = "linux")]
        self.emit_native_launches()?;
        self.write_state(false)?;
        Ok(())
    }
    pub fn begin_shutdown(&mut self) -> Result<()> {
        #[cfg(target_os = "linux")]
        if self.persistent_update.as_ref().is_some_and(|trial| !trial.launched) { self.rollback_persistent_update()?; }
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
        let result = (|| {
            self.begin_shutdown()?;
            while !self.instances.is_empty() {
                self.poll()?;
                thread::sleep(Duration::from_millis(5));
            }
            self.write_state(true)
        })();
        if result.is_err() {
            // Output or scene failures must not bypass cancel, reap, or private
            // session release. Preserve any active journal for explicit recovery.
            for instance in &self.instances { instance.bridge.cancel.store(true, Ordering::SeqCst); }
            for mut instance in self.instances.drain(..) {
                if let Some(worker) = instance.worker.take() { let _ = worker.join(); }
                instance.app_data.revoke();
            }
            #[cfg(target_os = "linux")]
            if self.persistent_update.is_some() { self.persistent_install_blocked = true; }
        }
        result
    }
    pub fn note_state_failure(&mut self, error: &anyhow::Error) {
        self.state_error = Some(format!("{error:#}").chars().take(2048).collect());
        let _ = self.write_state(true);
    }
    fn state_snapshot(&self, revision: u64) -> serde_json::Value {
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
        let apps:Vec<_>=self.catalog.apps.iter().filter_map(|app|self.current_record(app.id)).map(|r|{
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
            #[cfg(target_os = "linux")]
            let prompt_presented_sequence = self.pending_storage.as_ref()
                .filter(|pending| self.sink_ready && pending.presented)
                .and_then(|pending| pending.presented_sequence)
                .filter(|presented| *presented == sequence);
            #[cfg(not(target_os = "linux"))]
            let prompt_presented_sequence: Option<u64> = None;
            value["storagePromptPresentedSequence"] = serde_json::json!(prompt_presented_sequence);
            value
        });
        let mut state = serde_json::json!({"schemaVersion":1,"bootId":self.state_boot,"sessionId":self.state_session,"revision":revision,"phase":phase,"root":root,"apps":apps,"error":self.state_error});
        #[cfg(target_os = "linux")]
        if self.options.persistent_update_receipts {
            if let Some(update) = &self.persistent_update_state { state["persistentUpdate"] = update.clone(); }
        }
        state
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
        let state = self.state_snapshot(revision);
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
    if let Some(root) = options.persistent_install_root.as_ref() {
        options.install_registry = Some(crate::persistent_install::open_existing(root)?.registry);
    } else if let Some(root) = options.install_root.as_ref() {
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
    #[ignore = "requires packaged persistent-uninstall-demo Wasm"]
    fn persistent_uninstall_fixture_writes_private_data_and_closes() {
        use std::os::unix::fs::PermissionsExt;
        let wasm = PathBuf::from(std::env::var_os("MING_UNINSTALL_FIXTURE_WASM").expect("packaged fixture path required"));
        assert!(wasm.is_absolute());
        let worker = PathBuf::from(std::env::var_os("MING_TEST_PRIVATE_WORKER").expect("real release Host worker path required"));
        assert!(worker.is_absolute() && worker.is_file());
        let temp = tempfile::tempdir().unwrap();
        std::fs::set_permissions(temp.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let data = temp.path().join("data");
        std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::copy(&wasm, temp.path().join("main.wasm")).unwrap();
        std::fs::write(temp.path().join("app.json"), include_bytes!("../../../samples/persistent-uninstall-demo/app.json")).unwrap();
        let catalog_path = temp.path().join("catalog.json");
        std::fs::write(&catalog_path, br#"{"schemaVersion":1,"apps":[{"id":1,"title":"Private fixture","manifest":"app.json"}],"autostart":[1]}"#).unwrap();
        let catalog = crate::desktop_catalog::load_catalog(&catalog_path).unwrap();
        let options = DesktopOptions::parse([catalog_path.into_os_string(), "--renderer".into(), "cpu".into(), "--data-root".into(), data.clone().into_os_string()]).unwrap();
        let mut session = Session::new(options, catalog, #[cfg(feature="gpu")] None).unwrap();
        session.start().unwrap();
        let deadline = Instant::now() + Duration::from_secs(90);
        let mut requested_close = false;
        while Instant::now() < deadline {
            session.tick().unwrap();
            if session.dirty {
                let frame = session.compose().unwrap();
                session.accepted(frame.sequence, true).unwrap();
            }
            if !requested_close && session.records.last().is_some_and(|r| r.first_ready_ms.is_some())
                && session.views().iter().any(|v| v.input_ready && v.presented_sequence != 0) {
                let snapshot = crate::app_data::snapshot_private_read_only(&data, "dev.ming.install-demo").unwrap();
                assert_eq!(snapshot.files, vec![crate::app_data::PrivateContentFile { path:"note.txt".into(),size:5,
                    sha256:"8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8".into() }]);
                session.perform(&DesktopAction::Close { app:1 }).unwrap();
                requested_close = true;
            }
            if requested_close && session.instances.is_empty() { break; }
            std::thread::sleep(Duration::from_millis(2));
        }
        assert!(requested_close, "fixture never reached presented Ready: {:?}", session.records);
        assert!(session.instances.is_empty(), "fixture did not reap after Close");
        let record = session.records.last().unwrap();
        assert!(record.first_ready_ms.is_some());
        assert!(record.completed); assert_eq!(record.status,"closed"); assert!(record.error.is_none(), "{:?}", record.error);
        assert!(record.frames_submitted.is_some_and(|frames| frames > 0));
        assert!(session.trace.iter().any(|e| e.kind == "result-accepted"));
        assert!(session.trace.iter().any(|e| e.kind == "worker-reaped"));
        eprintln!("fixture Ready→Close result: {}", serde_json::to_string(record).unwrap());
        session.finish().unwrap();
        assert!(session.instances.is_empty());
        let snapshot = crate::app_data::snapshot_private_read_only(&data,"dev.ming.install-demo").unwrap();
        eprintln!("privateContent: {}",serde_json::to_string(&snapshot).unwrap());
        assert_eq!(snapshot.files.len(),1); assert_eq!(snapshot.files[0].path,"note.txt");
    }

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
    fn install_event_validation_only_defers_bounded_launch_targets() {
        for (source, action, allowed) in [
            (false, DesktopAction::Launch { app: 2 }, false),
            (true, DesktopAction::Launch { app: 2 }, true),
            (true, DesktopAction::Launch { app: 5 }, false),
            (true, DesktopAction::Focus { app: 2 }, false),
            (true, DesktopAction::Close { app: 2 }, false),
        ] {
            let mut options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into()]).unwrap();
            if source { options.install_source = Some("/trusted/install-source/app.json".into()); }
            options.actions = vec![crate::desktop_catalog::TimedAction { after_ms: 0, action }];
            let result = Session::new(options, Catalog { apps: vec![], autostart: vec![] }, #[cfg(feature = "gpu")] None);
            if allowed {
                assert!(result.is_ok(), "trusted native install must allow a future launch");
            } else {
                let error = result.err().expect("unknown action target must remain invalid");
                assert!(format!("{error:#}").contains("DESKTOP_EVENT_UNKNOWN_APP"), "{error:#}");
            }
        }
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn install_event_validation_keeps_scripted_install_launch_compatible() {
        let mut options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into()]).unwrap();
        options.actions = vec![
            crate::desktop_catalog::TimedAction { after_ms: 0, action: DesktopAction::Install { source: "/trusted/install-source/app.json".into() } },
            crate::desktop_catalog::TimedAction { after_ms: 1, action: DesktopAction::Launch { app: 2 } },
        ];
        assert!(Session::new(options, Catalog { apps: vec![], autostart: vec![] }, #[cfg(feature = "gpu")] None).is_ok());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn install_event_validation_does_not_authorize_an_uninstalled_app() {
        let mut options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into()]).unwrap();
        options.install_source = Some("/trusted/install-source/app.json".into());
        options.actions = vec![crate::desktop_catalog::TimedAction { after_ms: 0, action: DesktopAction::Launch { app: 2 } }];
        let mut session = Session::new(options, Catalog { apps: vec![], autostart: vec![] }, #[cfg(feature = "gpu")] None).unwrap();

        let error = session.perform(&DesktopAction::Launch { app: 2 }).unwrap_err();
        assert!(format!("{error:#}").contains("DESKTOP_UNKNOWN_APP"), "{error:#}");
        assert!(session.instances.is_empty());
        assert!(session.catalog.apps.is_empty());
        let error = session.begin_install_from_dock().unwrap_err();
        session.note_install_rejection(&error).unwrap();
        let error = session.perform(&DesktopAction::Launch { app: 2 }).unwrap_err();
        assert!(format!("{error:#}").contains("DESKTOP_STORAGE_PROMPT_ACTIVE"), "{error:#}");
        assert!(session.instances.is_empty());
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
    fn install_rejection_opens_information_without_closing_the_session() {
        let options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into()]).unwrap();
        let mut session = Session::new(options, Catalog { apps: vec![], autostart: vec![] }, #[cfg(feature = "gpu")] None).unwrap();
        let error = session.begin_install_from_dock().unwrap_err();

        session.note_install_rejection(&error).unwrap();

        let pending = session.pending_storage.as_ref().unwrap();
        assert!(matches!(&pending.pending, DesktopStorageDecision::Information));
        assert!(!pending.prompt.has_allow());
        assert!(session.closing.is_none());
        assert!(!session.pointer_sink_ready());
        assert!(session.trace.iter().any(|record| record.kind == "install_rejected"));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn install_rejection_preserves_internal_errors_and_existing_prompt() {
        let options = DesktopOptions::parse(["unused".into(), "--renderer".into(), "cpu".into()]).unwrap();
        let mut session = Session::new(options, Catalog { apps: vec![], autostart: vec![] }, #[cfg(feature = "gpu")] None).unwrap();
        assert!(session.note_install_rejection(&anyhow!("INTERNAL_INPUT_FAILURE")).is_err());
        assert!(session.storage_prompt().is_none());
        let error = session.begin_install_from_dock().unwrap_err();
        session.note_install_rejection(&error).unwrap();
        let existing = session.storage_prompt().unwrap().state_value();

        assert!(session.note_install_rejection(&error).is_err());

        assert_eq!(session.storage_prompt().unwrap().state_value(), existing);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn storage_prompt_state_only_marks_the_accepted_native_frame() {
        let temp = tempfile::tempdir().unwrap();
        let state_path = temp.path().join("state.json");
        let options = DesktopOptions::parse([
            "unused".into(), "--renderer".into(), "cpu".into(),
            "--state-file".into(), state_path.clone().into_os_string(),
        ]).unwrap();
        let mut session = Session::new(options, Catalog { apps: vec![], autostart: vec![] }, #[cfg(feature = "gpu")] None).unwrap();
        let read_state = || serde_json::from_slice::<serde_json::Value>(&std::fs::read(&state_path).unwrap()).unwrap();
        let frame = session.compose().unwrap();
        session.accepted(frame.sequence, false).unwrap();
        let error = session.begin_install_from_dock().unwrap_err();
        session.note_install_rejection(&error).unwrap();
        session.write_state(true).unwrap();
        let pending = read_state();
        assert_eq!(pending["root"]["storagePrompt"], serde_json::json!({"mode":22,"packageId":"","version":"","hasAllow":false}));
        assert_eq!(pending["root"].get("storagePromptPresentedSequence"), Some(&serde_json::Value::Null));

        let frame = session.compose().unwrap();
        session.accepted(frame.sequence, false).unwrap();
        session.write_state(true).unwrap();
        assert_eq!(read_state()["root"]["storagePromptPresentedSequence"], serde_json::Value::Null, "an unrelated accepted frame must not mark the prompt presented");

        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
        session.write_state(true).unwrap();
        let presented = read_state();
        assert_eq!(presented["root"]["storagePrompt"], pending["root"]["storagePrompt"]);
        assert_eq!(presented["root"]["storagePromptPresentedSequence"].as_u64(), Some(frame.sequence));
        assert_eq!(presented["root"]["storagePromptPresentedSequence"], presented["root"]["presentedSequence"]);

        session.sink_ready = false;
        session.write_state(true).unwrap();
        assert_eq!(read_state()["root"]["storagePromptPresentedSequence"], serde_json::Value::Null);
        session.sink_ready = true;
        session.pending_storage.as_mut().unwrap().presented_sequence = Some(frame.sequence + 1);
        session.write_state(true).unwrap();
        assert_eq!(read_state()["root"]["storagePromptPresentedSequence"], serde_json::Value::Null, "a prompt from another frame must not inherit the root presentation");
        session.changed();
        session.write_state(true).unwrap();
        assert_eq!(read_state()["root"]["storagePromptPresentedSequence"], serde_json::Value::Null);
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, true).unwrap();
        session.write_state(true).unwrap();
        assert_eq!(read_state()["root"]["storagePromptPresentedSequence"], serde_json::Value::Null, "offscreen composition is not native presentation");
        session.pending_storage = None;
        session.write_state(true).unwrap();
        assert_eq!(read_state()["root"]["storagePromptPresentedSequence"], serde_json::Value::Null);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn storage_prompt_state_is_published_after_throttled_acceptance() {
        let temp = tempfile::tempdir().unwrap();
        let state_path = temp.path().join("state.json");
        let options = DesktopOptions::parse([
            "unused".into(), "--renderer".into(), "cpu".into(),
            "--state-file".into(), state_path.clone().into_os_string(),
        ]).unwrap();
        let mut session = Session::new(options, Catalog { apps: vec![], autostart: vec![] }, #[cfg(feature = "gpu")] None).unwrap();
        let read_state = || serde_json::from_slice::<serde_json::Value>(&std::fs::read(&state_path).unwrap()).unwrap();
        let frame = session.compose().unwrap();
        session.accepted(frame.sequence, false).unwrap();
        let error = session.begin_install_from_dock().unwrap_err();
        session.note_install_rejection(&error).unwrap();
        session.write_state(true).unwrap();
        let pending = read_state();
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.state_last_write = Some(Instant::now());
        session.accepted(frame.sequence, false).unwrap();
        assert_eq!(read_state(), pending, "acceptance inside the write interval must remain throttled");

        session.state_last_write = Some(Instant::now() - Duration::from_millis(101));
        session.tick().unwrap();

        let presented = read_state();
        assert!(presented["revision"].as_u64().unwrap() > pending["revision"].as_u64().unwrap());
        assert_eq!(presented["root"]["storagePromptPresentedSequence"].as_u64(), Some(frame.sequence));
        assert_eq!(presented["root"]["storagePromptPresentedSequence"], presented["root"]["presentedSequence"]);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn install_source_load_failure_preserves_confirmation_and_can_retry() {
        use std::os::unix::fs::PermissionsExt;
        for missing_name in ["app.json", "main.wasm"] {
            let temp = tempfile::tempdir().unwrap();
            std::fs::set_permissions(temp.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
            let source = temp.path().join("source");
            let install = temp.path().join("install");
            for path in [&source, &install] {
                std::fs::create_dir(path).unwrap();
                std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700)).unwrap();
            }
            std::fs::write(source.join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
            let manifest = source.join("app.json");
            std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.install.retry","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
            let registry = crate::package_install::PackageInstallRegistry::open(&install).unwrap();
            let mut options = DesktopOptions::parse([
                temp.path().join("catalog.json").into_os_string(),
                "--renderer".into(), "cpu".into(),
            ]).unwrap();
            options.install_registry = Some(registry);
            let mut session = Session::new(options, Catalog { apps: vec![], autostart: vec![] }, #[cfg(feature = "gpu")] None).unwrap();
            session.begin_install(&manifest).unwrap();
            let expected_fingerprint = match &session.pending_storage.as_ref().unwrap().pending {
                DesktopStorageDecision::Install { fingerprint, .. } => fingerprint.clone(),
                _ => panic!("expected install confirmation"),
            };
            let frame = session.compose().unwrap();
            session.storage_prompt_composed(frame.sequence);
            session.accepted(frame.sequence, false).unwrap();
            let missing_path = source.join(missing_name);
            let contents = std::fs::read(&missing_path).unwrap();
            std::fs::remove_file(&missing_path).unwrap();

            session.respond_storage_prompt(true).unwrap();

            let pending = session.pending_storage.as_ref().unwrap();
            assert_eq!(pending.prompt.state_value()["mode"], 18);
            assert!(matches!(&pending.pending, DesktopStorageDecision::Install {
                package_id, version, fingerprint, retry: true, ..
            } if package_id == "dev.example.install.retry" && version == "1.0.0" && fingerprint == &expected_fingerprint));
            assert!(session.catalog.apps.is_empty());
            assert!(session.options.install_registry.as_ref().unwrap().active_manifest_path("dev.example.install.retry").is_err());

            std::fs::write(&missing_path, contents).unwrap();
            let frame = session.compose().unwrap();
            session.storage_prompt_composed(frame.sequence);
            session.accepted(frame.sequence, false).unwrap();
            session.respond_storage_prompt(true).unwrap();
            assert!(session.storage_prompt().is_none());
            assert_eq!(session.catalog.apps.len(), 1);
            assert_eq!(session.catalog.apps[0].package_id, "dev.example.install.retry");
            let active = session.options.install_registry.as_ref().unwrap().active_manifest_path("dev.example.install.retry").unwrap();
            assert_eq!(crate::package::load_package(&active).unwrap().manifest.version, "1.0.0");
        }
    }

    #[cfg(target_os = "linux")]
    fn persistent_install_session() -> (tempfile::TempDir, Session, PathBuf) {
        use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
        use std::io::Write;
        let (temp, opened, package) = crate::persistent_install::transaction_tests::fixture();
        let root = opened.registry.root_path().to_path_buf();
        let source = temp.path().join("source");
        std::fs::create_dir(&source).unwrap();
        std::fs::set_permissions(&source, std::fs::Permissions::from_mode(0o700)).unwrap();
        let manifest = source.join("app.json");
        for (path, bytes) in [(manifest.clone(), serde_json::to_vec(&package.manifest).unwrap()),
            (source.join("main.wasm"), package.bytes)] {
            std::fs::OpenOptions::new().create_new(true).write(true).mode(0o600).open(path).unwrap().write_all(&bytes).unwrap();
        }
        let (catalog, _) = crate::desktop_catalog::load_catalog_with_install_registry(&root.join("catalog.json"), &opened.registry).unwrap();
        let mut options = DesktopOptions::parse([root.join("catalog.json").into_os_string(),
            "--persistent-install-state".into(), root.into_os_string(), "--renderer".into(), "cpu".into()]).unwrap();
        options.install_registry = Some(opened.registry);
        let session = Session::new(options, catalog, #[cfg(feature = "gpu")] None).unwrap();
        (temp, session, manifest)
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn native_receipts_pair_real_edges_and_capture_full_history() {
        let (temp, mut session, _) = persistent_install_session();
        session.options.state_file = Some(temp.path().join("state.json"));
        session.options.persistent_install_receipts = true;
        session.begin_native_input(true, Some((17, 23))).unwrap();
        session.end_native_input().unwrap();
        session.begin_native_input(false, Some((17, 23))).unwrap();
        session.end_native_input().unwrap();
        let events = &session.test_native_receipts;
        assert_eq!(events.len(), 2);
        let down = &events[0]["receipt"];
        let up = &events[1]["receipt"];
        assert_eq!(down["sequence"], 1);
        assert_eq!(down["pressSequence"], 1);
        assert_eq!(up["sequence"], 2);
        assert_eq!(up["pressSequence"], 1);
        assert_eq!(down["before"]["history"], serde_json::json!([]));
        assert_eq!(down["before"]["state"]["revision"], 1);
        assert_eq!(down["after"]["state"]["revision"], 2);
        assert!(down["after"]["state"]["revision"].as_u64().unwrap() <= up["before"]["state"]["revision"].as_u64().unwrap());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn native_receipts_fail_closed_for_drops_and_unpaired_edges() {
        for trace_drop in [false, true] {
            let (temp, mut session, _) = persistent_install_session();
            session.options.state_file = Some(temp.path().join("state.json"));
            session.options.persistent_install_receipts = true;
            if trace_drop { session.dropped_trace = 1; } else { session.dropped_inputs = 1; }
            assert!(session.begin_native_input(true, Some((1, 2))).is_err());
            assert!(session.persistent_install_blocked);
            assert!(session.launch(1).is_err());
        }
        let (temp, mut session, _) = persistent_install_session();
        session.options.state_file = Some(temp.path().join("state.json"));
        session.options.persistent_install_receipts = true;
        assert!(session.begin_native_input(false, Some((1, 2))).is_err());
        assert!(session.persistent_install_blocked);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn native_ready_receipt_requires_actual_scene_acceptance_and_emits_once() {
        // Synthetic parent fixture tests the presentation boundary; it is not
        // worker or VM qualification. Native route/PID is tested separately.
        let (temp, mut session, _) = persistent_install_session();
        session.options.state_file = Some(temp.path().join("state.json"));
        session.options.persistent_install_receipts = true;
        let (mut fixture, _, _keepalive) = super::close_grace_tests::closing_session(crate::desktop_catalog::IsolationMode::Off);
        session.records = std::mem::take(&mut fixture.records);
        session.instances = std::mem::take(&mut fixture.instances);
        let window = session.scene.model_mut().open(1).unwrap().0;
        session.instances[0].window = Some(window);
        session.instances[0].closing = None;
        session.instances[0].pid.store(42, Ordering::SeqCst);
        session.records[0].window = window.0;
        session.records[0].status = "starting";
        session.native_evidence.launches.push(NativeLaunchEvidence { record:0, down:1, started:false, ready:false });
        let content = session.scene.model().view(window).unwrap().outer.content();
        session.instances[0].bridge.publish(RenderedFrame { width:content.width as usize, height:content.height as usize,
            pixels:crate::graphics::FramePixels::Cpu(Arc::new(vec![0; content.width as usize * content.height as usize])),
            sequence:1, render_us:0, text_prepare_us:None, gpu_timing:None, commands:0 });
        session.poll().unwrap();
        assert_eq!(session.test_native_receipts.len(), 1);
        assert_eq!(session.test_native_receipts[0]["receipt"]["phase"], "worker-started");
        assert!(session.records[0].first_ready_ms.is_none());
        let frame = session.compose().unwrap();
        // Composition alone cannot attest input readiness.
        assert_eq!(session.test_native_receipts.len(), 1);
        session.accepted(frame.sequence, false).unwrap();
        assert_eq!(session.test_native_receipts.len(), 2);
        let ready = &session.test_native_receipts[1]["receipt"];
        assert_eq!(ready["phase"], "ready");
        assert_eq!(ready["workerPid"], 42);
        assert_eq!(ready["anchor"]["state"]["apps"][0]["inputReady"], true);
        assert_eq!(ready["anchor"]["history"][0]["presentedSourceSequence"], 1);
        session.emit_native_launches().unwrap();
        assert_eq!(session.test_native_receipts.len(), 2);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn native_receipts_bound_sequences_and_bytes_without_affecting_ordinary_mode() {
        let (temp, mut ordinary, _) = persistent_install_session();
        // Ordinary persistent native sessions may use state files indefinitely.
        ordinary.options.state_file = Some(temp.path().join("ordinary-state.json"));
        for _ in 0..70 {
            ordinary.begin_native_input(true, None).unwrap();
            ordinary.end_native_input().unwrap();
            ordinary.begin_native_input(false, None).unwrap();
            ordinary.end_native_input().unwrap();
        }
        ordinary.dropped_inputs = 1;
        ordinary.begin_native_input(false, None).unwrap();
        ordinary.end_native_input().unwrap();
        assert!(!ordinary.persistent_install_blocked);
        assert!(ordinary.test_native_receipts.is_empty());
        for oversized in [false, true] {
            let (temp, mut session, _) = persistent_install_session();
            session.options.state_file = Some(temp.path().join("state.json"));
            session.options.persistent_install_receipts = true;
            if oversized {
                assert!(session.emit_native_receipt("native-input", serde_json::json!({"oversized":"x".repeat(262144)})).is_err());
            } else {
                session.native_evidence.sequence = 64;
                assert!(session.begin_native_input(true, None).is_err());
            }
            assert!(session.persistent_install_blocked);
            assert!(session.test_native_receipts.is_empty());
        }
        let (temp, mut session, _) = persistent_install_session();
        session.options.state_file = Some(temp.path().join("state.json"));
        session.options.persistent_install_receipts = true;
        session.native_evidence.receipts = 128;
        assert!(session.emit_native_receipt("native-input", serde_json::json!({})).is_err());
        assert!(session.persistent_install_blocked);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn installed_receipt_contains_committed_content_and_published_revision() {
        let (temp, mut session, source) = persistent_install_session();
        session.options.state_file = Some(temp.path().join("state.json"));
        session.options.persistent_install_receipts = true;
        session.begin_install(&source).unwrap();
        present_install_confirmation(&mut session);
        session.respond_storage_prompt(true).unwrap();
        let event = session.test_native_receipts.iter().find(|e| e["kind"] == "persistent-install-installed").unwrap();
        let committed = session.trace.iter().find(|e| e.kind == "persistent-install-commit").unwrap();
        let commit: serde_json::Value = serde_json::from_str(&committed.detail).unwrap();
        assert_eq!(event["receipt"]["content"], commit["content"]);
        assert_eq!(event["receipt"]["stateRevision"], session.state_revision);
        assert_eq!(session.catalog.apps.len(), 2);
        assert!(session.records.is_empty());
    }

    #[cfg(target_os = "linux")]
    fn present_install_confirmation(session: &mut Session) {
        let frame = session.compose().unwrap();
        session.storage_prompt_composed(frame.sequence);
        session.accepted(frame.sequence, false).unwrap();
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_install_commit_publishes_catalog_without_launching_app2() {
        let (_temp, mut session, source) = persistent_install_session();
        session.begin_install(&source).unwrap();
        present_install_confirmation(&mut session);
        session.respond_storage_prompt(true).unwrap();
        assert_eq!(session.catalog.apps.len(), 2);
        assert_eq!(session.catalog.autostart, [1]);
        assert!(!session.persistent_install_blocked);
        assert!(session.records.is_empty() && session.instances.is_empty());
        let commit = session.trace.iter().position(|record| record.kind == "persistent-install-commit").unwrap();
        let installed = session.trace.iter().position(|record| record.kind == "installed").unwrap();
        assert!(commit < installed);
        assert!(!session.trace.iter().any(|record| record.kind == "launch"));
        let receipt: serde_json::Value = serde_json::from_str(&session.trace[commit].detail).unwrap();
        let registry = session.options.install_registry.as_ref().unwrap();
        assert_eq!(receipt["status"], "committed");
        assert_eq!(receipt["packageId"], "dev.ming.install-demo");
        assert_eq!(receipt["content"], serde_json::to_value(crate::persistent_install::snapshot(registry).unwrap()).unwrap());
        assert!(!registry.root_path().join(crate::persistent_install::PENDING_NAME).exists());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_install_recoverable_failure_keeps_catalog_and_allows_explicit_retry() {
        let (_temp, mut session, source) = persistent_install_session();
        let before = crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap();
        session.persistent_install_faults.insert("catalog-directory-sync");
        session.begin_install(&source).unwrap();
        present_install_confirmation(&mut session);
        session.respond_storage_prompt(true).unwrap();
        assert_eq!(session.catalog.apps.len(), 1);
        assert_eq!(session.catalog.autostart, [1]);
        assert!(!session.persistent_install_blocked);
        assert!(session.records.is_empty() && session.instances.is_empty());
        assert!(!session.trace.iter().any(|record| ["installed", "persistent-install-commit", "launch"].contains(&record.kind)));
        let after = crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap();
        assert_eq!(after.apps, before.apps);
        assert_eq!(after.registry_sha256, before.registry_sha256);
        assert_eq!(after.catalog_sha256, before.catalog_sha256);
        assert!(matches!(session.pending_storage.as_ref().unwrap().pending, DesktopStorageDecision::Install { retry: true, .. }));
        session.persistent_install_faults.clear();
        present_install_confirmation(&mut session);
        session.respond_storage_prompt(true).unwrap();
        assert_eq!(session.catalog.apps.len(), 2);
        assert!(session.storage_prompt().is_none());
        assert_eq!(session.trace.iter().filter(|record| record.kind == "installed").count(), 1);
        assert!(session.records.is_empty() && session.instances.is_empty());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_install_fatal_failure_blocks_every_new_worker_path() {
        for (trigger, secondary) in [("pending-remove-directory-sync", None),
            ("pending-remove-directory-sync", Some("compensation-create")),
            ("pending-remove-directory-sync", Some("compensation-identity")),
            ("pending-remove-directory-sync", Some("compensation-write")),
            ("pending-remove-directory-sync", Some("compensation-file-sync")),
            ("pending-remove-directory-sync", Some("compensation-directory-sync")),
            ("pending-remove-directory-sync", Some("compensation-readback")),
            ("catalog-write", Some("registry-restore")),
            ("catalog-write", Some("catalog-restore-rename")),
            ("catalog-write", Some("rollback-readback")), ("final-readback", None)] {
            let (_temp, mut session, source) = persistent_install_session();
            session.persistent_install_faults.insert(trigger);
            if let Some(fault) = secondary { session.persistent_install_faults.insert(fault); }
            session.begin_install(&source).unwrap();
            present_install_confirmation(&mut session);
            let error = session.respond_storage_prompt(true).unwrap_err();
            assert!(format!("{error:#}").contains("DESKTOP_PERSISTENT_INSTALL_FATAL"));
            assert!(session.persistent_install_blocked);
            assert_eq!(session.catalog.apps.len(), 1);
            assert_eq!(session.catalog.autostart, [1]);
            assert!(session.records.is_empty() && session.instances.is_empty());
            assert!(!session.trace.iter().any(|record| ["installed", "persistent-install-commit", "launch"].contains(&record.kind)));
            let receipt: serde_json::Value = serde_json::from_str(&session.trace.iter().find(|record| record.kind == "persistent-install-failed").unwrap().detail).unwrap();
            let uncertain = secondary.is_some_and(|fault| fault.starts_with("compensation-"));
            assert_eq!(receipt["status"], if uncertain { "commit-uncertain" } else { "persistent-blocked" });
            assert_eq!(receipt["persistentBlockConfirmed"], !uncertain);
            assert_eq!(receipt["compensationAttempted"], trigger != "catalog-write");
            assert_eq!(receipt["packageId"], "dev.ming.install-demo");
            assert!(session.state_error.is_some());
            for app in [1, 2] {
                let error = session.perform(&DesktopAction::Launch { app }).unwrap_err();
                assert!(format!("{error:#}").contains("DESKTOP_PERSISTENT_INSTALL_BLOCKED"));
            }
            let entry = session.catalog.apps[0].clone();
            assert!(session.launch_resolved(entry, None, None).unwrap_err().to_string().contains("DESKTOP_PERSISTENT_INSTALL_BLOCKED"));
            assert!(session.begin_install(&source).unwrap_err().to_string().contains("DESKTOP_PERSISTENT_INSTALL_BLOCKED"));
            assert!(session.records.is_empty() && session.instances.is_empty());
        }
    }

    #[cfg(target_os = "linux")]
    fn persistent_update_session() -> (tempfile::TempDir, Session, PathBuf) {
        use base64::{Engine as _, engine::general_purpose::STANDARD};
        use ed25519_dalek::{Signer, SigningKey};
        use std::os::unix::fs::PermissionsExt;
        let (temp, mut session, source) = persistent_uninstall_session();
        let data = temp.path().join("data");
        std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        let root = crate::app_data::AppDataRoot::open(&data).unwrap();
        session.storage_authority = Some(Arc::new(crate::app_data::PackageStorageAuthority::open_default_private(root.clone()).unwrap()));
        session.app_data.root = Some(root);
        session.options.data_root_path = Some(data);
        let mut package = crate::package::load_package(&source).unwrap();
        package.manifest.version = "2.0.0".into();
        package.manifest.publisher_signature = None;
        package.manifest.publisher_signature = Some(STANDARD.encode(SigningKey::from_bytes(&[7;32]).sign(&package.signature_message("mingos-dev-2026").unwrap()).to_bytes()));
        std::fs::write(&source, serde_json::to_vec(&package.manifest).unwrap()).unwrap();
        (temp, session, source)
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_confirmation_publishes_trial_without_rewriting_catalog() {
        let (_temp, mut session, source) = persistent_update_session();
        let catalog = session.options.install_registry.as_ref().unwrap().root_path().join("catalog.json");
        let before = std::fs::read(&catalog).unwrap();
        let title = session.catalog.apps.iter().find(|entry| entry.id == 2).unwrap().title.clone();
        session.begin_install(&source).unwrap();
        present_install_confirmation(&mut session);
        session.respond_storage_prompt(true).unwrap();
        assert_eq!(std::fs::read(catalog).unwrap(), before);
        assert_eq!(session.catalog.apps.iter().find(|entry| entry.id == 2).unwrap().title, title);
        let registry = session.options.install_registry.as_ref().unwrap();
        assert!(registry.pending_update().unwrap().is_some());
        assert!(crate::persistent_install::snapshot(registry).is_err());
        assert!(session.instances.is_empty());
    }

    #[cfg(target_os = "linux")]
    fn prepared_desktop_trial() -> (tempfile::TempDir, Session, PathBuf) {
        let (temp, mut session, source) = persistent_update_session();
        session.begin_install(&source).unwrap(); present_install_confirmation(&mut session);
        session.respond_storage_prompt(true).unwrap();
        (temp, session, source)
    }
    #[cfg(target_os = "linux")]
    fn completed_desktop_trial(session: &mut Session) -> (UpdateStartupIdentity, UpdateStartupEvidence) {
        let trial = session.persistent_update.as_mut().unwrap();
        trial.launched = true; trial.down = Some(7); trial.confirmation = Some(serde_json::json!({"pressSequence":1,"releaseSequence":2}));
        let new = trial.record.new.clone();
        let entry = session.catalog.apps.iter().find(|entry| entry.id == 2).unwrap();
        session.options.install_registry.as_ref().unwrap().begin_update_startup(&new.package_id, entry.package_fingerprint).unwrap();
        let expected = UpdateStartupIdentity { package_id:new.package_id.clone(), version:new.version,
            fingerprint:entry.package_fingerprint, app:2, generation:4, window:9 };
        let (mut fixture, _, _keepalive) = super::close_grace_tests::closing_session(crate::desktop_catalog::IsolationMode::Off);
        let mut record = fixture.records.pop().unwrap();
        record.app=2; record.generation=4; record.window=9; record.worker_pid=123;
        record.package_id=Some(new.package_id); record.completed=true; record.error=None;
        record.status="closed"; record.first_ready_ms=Some(1); record.presented_source_sequence=5;
        session.records.push(record);
        let evidence = UpdateStartupEvidence { identity:expected.clone(), worker_pid:123, input_ready:true,
            presented_source_sequence:5, completed:true, result_accepted:true, native_exit_ok:true, reaped:true };
        (expected, evidence)
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_rechecks_confirmation_and_cancel_preserves_old_state() {
        for unavailable in ["instance", "pending", "closing", "identity", "source", "cancel"] {
            let (_temp, mut session, source) = persistent_update_session();
            let before=crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap();
            session.begin_install(&source).unwrap(); present_install_confirmation(&mut session);
            let mut held = None;
            match unavailable {
                "instance" => { let (mut other, _, keepalive)=super::close_grace_tests::closing_session(crate::desktop_catalog::IsolationMode::Off);
                    other.instances[0].app=2; session.instances.push(other.instances.remove(0)); held=Some(keepalive); },
                "pending" => session.pending_autostart.push_back(2),
                "closing" => session.closing=Some(Instant::now()),
                "identity" => session.catalog.apps.iter_mut().find(|entry|entry.id==2).unwrap().package_fingerprint=[0;32],
                "source" => { let mut package=crate::package::load_package(&source).unwrap(); package.manifest.version="3.0.0".into();
                    std::fs::write(&source,serde_json::to_vec(&package.manifest).unwrap()).unwrap(); },
                _ => {},
            }
            let result=session.respond_storage_prompt(unavailable!="cancel");
            if matches!(unavailable,"instance"|"pending"|"closing"|"identity") { assert!(result.is_err(),"{unavailable}"); }
            else { result.unwrap(); }
            assert!(session.persistent_update.is_none(),"{unavailable}");
            assert_eq!(crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap(),before);
            session.instances.clear(); drop(held);
        }
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_trial_blocks_mutation_script_and_resolved_launch() {
        let (_temp, mut session, source)=prepared_desktop_trial();
        let entry=session.catalog.apps.iter().find(|entry|entry.id==2).unwrap().clone();
        assert!(session.launch(2).is_err()); assert!(session.launch(1).is_err());
        assert!(session.launch_resolved(entry,None,None).is_err());
        assert!(session.begin_install(&source).is_err()); assert!(session.begin_uninstall(2).is_err());
        assert!(session.begin_uninstall_with_purge(2).is_err()); assert!(session.begin_storage_management(2).is_err());
        assert!(session.respond_storage_prompt(true).is_err());
        session.begin_shutdown().unwrap();
        assert!(session.persistent_update.is_none()); assert!(!session.persistent_install_blocked);
        assert_eq!(session.persistent_update_state.as_ref().unwrap()["status"],"rolled-back");
        assert_eq!(crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap().apps.iter().find(|app|app.app==2).unwrap().version,"1.0.0");
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_complete_proof_commits_and_every_missing_field_rolls_back() {
        for field in 0..16 {
            let (_temp,mut session,_)=prepared_desktop_trial();
            let (expected,mut evidence)=completed_desktop_trial(&mut session);
            match field {
                0=>evidence.identity.package_id.push('x'), 1=>evidence.identity.version.push('x'),
                2=>evidence.identity.fingerprint[0]^=1, 3=>evidence.identity.app=3,
                4=>evidence.identity.generation+=1, 5=>evidence.identity.window+=1,
                6=>evidence.worker_pid+=1, 7=>evidence.input_ready=false,
                8=>evidence.presented_source_sequence=0, 9=>evidence.completed=false,
                10=>evidence.result_accepted=false,11=>evidence.native_exit_ok=false,12=>evidence.reaped=false,
                13=>session.records.last_mut().unwrap().worker_pid+=1,
                14=>session.records.last_mut().unwrap().completed=false,
                _=>{},
            }
            session.finish_update_startup(&expected,&evidence).unwrap();
            assert!(session.persistent_update.is_none()); assert!(session.instances.is_empty());
            let snapshot=crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap();
            assert_eq!(snapshot.apps.iter().find(|app|app.app==2).unwrap().version,if field==15 {"2.0.0"} else {"1.0.0"},"field {field}");
            assert_eq!(session.persistent_update_state.as_ref().unwrap()["status"],if field==15 {"committed"} else {"rolled-back"});
        }
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_every_orphan_inner_phase_blocks_all_entries() {
        for phase in ["planned","dataSwapped","recordPublished","startupAttempted","startupConfirmed","rollingBack"] {
            let (_temp,mut session,source)=prepared_desktop_trial();
            let root=session.options.install_registry.as_ref().unwrap().root_path().to_path_buf();
            std::fs::remove_file(root.join(crate::persistent_update::UPDATE_PENDING_NAME)).unwrap();
            session.persistent_update=None;
            let journal=root.join("update-journal-v1");
            let mut value:serde_json::Value=serde_json::from_slice(&std::fs::read(&journal).unwrap()).unwrap();
            value["phase"]=phase.into();std::fs::write(&journal,serde_json::to_vec(&value).unwrap()).unwrap();
            let before=std::fs::read(&journal).unwrap();
            let entry=session.catalog.apps.iter().find(|entry|entry.id==2).unwrap().clone();
            for result in [session.begin_install(&source),session.begin_uninstall(2),session.begin_storage_management(2),
                session.launch(2),session.launch_resolved(entry,None,None)] {
                assert!(result.unwrap_err().to_string().contains("DESKTOP_UPDATE_PENDING"),"{phase}");
            }
            assert_eq!(std::fs::read(journal).unwrap(),before);assert!(session.instances.is_empty() && session.views().is_empty());
        }
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_local_trial_with_missing_inner_never_opens_worker_or_window() {
        let (_temp,mut session,_)=prepared_desktop_trial();
        let root=session.options.install_registry.as_ref().unwrap().root_path().to_path_buf();
        std::fs::remove_file(root.join("update-journal-v1")).unwrap();
        assert!(session.launch_from_native_dock(2).is_err());
        assert!(session.persistent_install_blocked && session.state_error.is_some());
        assert!(session.instances.is_empty() && session.views().is_empty() && session.records.is_empty());
        assert!(root.join(crate::persistent_update::UPDATE_PENDING_NAME).exists());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_startup_write_failure_closes_new_window_and_restores_old_state() {
        let (_temp,mut session,_)=prepared_desktop_trial();
        session.options.install_registry.as_ref().unwrap().fail_next_record_persist_at("registry-journal-file-sync");
        session.launch_from_native_dock(2).unwrap();
        assert!(session.trace.iter().any(|trace|trace.kind=="action_rejected"));
        assert!(session.instances.is_empty() && session.views().is_empty());
        assert!(session.persistent_update.is_none());
        assert_eq!(crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap().apps.iter().find(|app|app.app==2).unwrap().version,"1.0.0");
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_binding_resets_current_state_and_keeps_generation_history() {
        let (_temp,mut session,source)=persistent_update_session();
        let (mut fixture,_,_keepalive)=super::close_grace_tests::closing_session(crate::desktop_catalog::IsolationMode::Off);
        let mut record=fixture.records.pop().unwrap();record.app=2;record.generation=8;record.worker_pid=999;
        record.first_ready_ms=Some(1);record.error=Some("old failure".into());
        session.records.push(record);session.public_denied.insert(2);
        session.begin_install(&source).unwrap();present_install_confirmation(&mut session);session.respond_storage_prompt(true).unwrap();
        assert!(session.current_record(2).is_none());assert!(!session.public_denied.contains(&2));
        assert_eq!(session.records[0].generation,8);assert_eq!(session.records[0].worker_pid,999);
        session.rollback_persistent_update().unwrap();assert_eq!(session.records.len(),1);
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_qualification_is_explicit_and_requires_native_state() {
        let (temp,session,_)=persistent_update_session();
        let root=session.options.persistent_install_root.as_ref().unwrap();
        let args=vec![root.join("catalog.json").into_os_string(),"--persistent-install-state".into(),root.clone().into_os_string(),
            "--qualification-receipts".into(),"persistent-update-pointer-ready-v1".into()];
        assert!(DesktopOptions::parse(args.clone()).is_err());
        let mut complete=args;complete.extend(["--state-file".into(),temp.path().join("state.json").into_os_string()]);
        let options=DesktopOptions::parse(complete.clone()).unwrap();
        assert!(options.persistent_update_receipts && !options.persistent_install_receipts && !options.persistent_uninstall_receipts);
        complete.extend(["--backend".into(),"offscreen".into()]);assert!(DesktopOptions::parse(complete).is_err());
        assert!(session.state_snapshot(0).get("persistentUpdate").is_none());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_second_dock_down_preserves_first_launch_cause() {
        let (_temp,mut session,_)=prepared_desktop_trial();
        let (_expected,_evidence)=completed_desktop_trial(&mut session);
        let (mut fixture,_,_keepalive)=super::close_grace_tests::closing_session(crate::desktop_catalog::IsolationMode::Off);
        fixture.instances[0].app=2;
        session.instances.push(fixture.instances.remove(0));
        session.native_evidence.pending=Some(NativeInputEvidence { sequence:19,press:19,down:true,mouse:None,
            before:serde_json::Value::Null,trace_start:0,target:None });
        session.launch_from_native_dock(2).unwrap();
        assert!(session.trace.iter().any(|trace|trace.kind=="action_rejected" && trace.detail.contains("DESKTOP_UPDATE_NATIVE_TRIAL_REQUIRED")));
        assert_eq!(session.persistent_update.as_ref().unwrap().down,Some(7));
        session.instances.clear(); session.rollback_persistent_update().unwrap();
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_rollback_compensation_failure_reports_uncertain_not_durable_block() {
        let (_temp,mut session,_)=prepared_desktop_trial();
        let stages=std::rc::Rc::new(std::cell::RefCell::new(Vec::new()));let observed=stages.clone();
        crate::app_data::UPDATE_TEST_HOOK.with(|slot|*slot.borrow_mut()=Some(Box::new(move |stage| {
            if matches!(stage,"outer-remove-directory-sync"|"compensation-create") {
                observed.borrow_mut().push(stage.to_owned());bail!("injected {stage}");
            } Ok(())
        })));
        let result=session.rollback_persistent_update();
        crate::app_data::UPDATE_TEST_HOOK.with(|slot|*slot.borrow_mut()=None);
        let error=result.unwrap_err().to_string();assert!(error.contains("commit-uncertain"),"{error}");
        assert_eq!(*stages.borrow(),["outer-remove-directory-sync","compensation-create"]);
        assert!(session.persistent_install_blocked && session.state_error.is_some());
        assert_eq!(session.persistent_update_state.as_ref().unwrap()["status"],"uncertain");
        let registry=session.options.install_registry.as_ref().unwrap();
        assert!(!registry.root_path().join(crate::persistent_update::UPDATE_PENDING_NAME).exists());
        assert_eq!(crate::package::load_package(&registry.active_manifest_path("dev.ming.install-demo").unwrap()).unwrap().manifest.version,"1.0.0");
        assert!(session.launch(2).is_err());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_shutdown_output_failure_reaps_and_releases_sessions_for_explicit_recovery() {
        let (temp,mut session,_)=prepared_desktop_trial();
        let (_expected,_evidence)=completed_desktop_trial(&mut session);
        let transaction=session.persistent_update.as_ref().unwrap().record.transaction_id.clone();
        let authority=session.storage_authority.as_ref().unwrap().clone();
        let package=crate::package::load_package(&session.catalog.apps.iter().find(|entry|entry.id==2).unwrap().manifest_path).unwrap();
        let private=authority.default_private_session_for_package(&package).unwrap();
        private.write_file("trial-data",b"new").unwrap();
        let (mut fixture,bridge,_keepalive)=super::close_grace_tests::closing_session(crate::desktop_catalog::IsolationMode::Off);
        let mut instance=fixture.instances.remove(0);instance.app=2;
        let window=session.scene.model_mut().open(2).unwrap().0;
        instance.window=Some(window);instance.record=0;
        instance.app_data=supervisor::AppDataService::new(None).with_private_files_for_package(private,package);
        session.records[0].window=window.0;session.records[0].completed=false;
        session.instances.push(instance);
        session.options.state_file=Some(temp.path().join("absent").join("state.json"));
        assert!(session.finish().is_err());assert!(bridge.cancel.load(Ordering::SeqCst));
        assert!(session.instances.is_empty() && session.persistent_install_blocked);
        let registry=session.options.install_registry.as_ref().unwrap();
        assert!(registry.root_path().join(crate::persistent_update::UPDATE_PENDING_NAME).exists());
        let recovered=crate::persistent_update::recover_loaded_update(registry,&authority,
            session.options.data_root_path.as_deref().unwrap(),&transaction).unwrap();
        assert!(matches!(recovered,crate::persistent_update::UpdateRecovery::RolledBack(_)));
        assert!(authority.observe_update("dev.ming.install-demo").unwrap().files.is_none());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_durable_decision_or_cleanup_failure_stays_sealed_without_rollback() {
        for stage in ["decision-directory-sync","registry-journal-directory-sync"] {
            let (_temp,mut session,_)=prepared_desktop_trial();
            let (expected,evidence)=completed_desktop_trial(&mut session);
            let fired=std::rc::Rc::new(std::cell::Cell::new(false));let hook_fired=fired.clone();
            crate::app_data::UPDATE_TEST_HOOK.with(|slot|*slot.borrow_mut()=Some(Box::new(move |point| {
                if point==stage && !hook_fired.replace(true) { bail!("injected {stage}"); } Ok(())
            })));
            let result=session.finish_update_startup(&expected,&evidence);
            crate::app_data::UPDATE_TEST_HOOK.with(|slot|*slot.borrow_mut()=None);
            assert!(fired.get());assert!(result.unwrap_err().to_string().contains("DESKTOP_PERSISTENT_UPDATE_COMMIT_FAILED"));
            assert!(session.persistent_install_blocked && session.state_error.is_some());
            assert_eq!(session.persistent_update_state.as_ref().unwrap()["status"],"commit-decided");
            let registry=session.options.install_registry.as_ref().unwrap();
            let record=crate::persistent_update::parse_update_record(&std::fs::read(registry.root_path().join(crate::persistent_update::UPDATE_PENDING_NAME)).unwrap()).unwrap();
            assert_eq!(record.decision,crate::persistent_update::UpdateDecision::CommitDecided);
            assert_eq!(crate::package::load_package(&registry.active_manifest_path(&expected.package_id).unwrap()).unwrap().manifest.version,"2.0.0");
            assert!(crate::persistent_install::snapshot(registry).is_err());
            assert!(!session.trace.iter().any(|trace|matches!(trace.kind,"update-not-saved"|"update-rolled-back")));
            assert!(session.launch(2).is_err());
        }
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_committed_output_failures_preserve_stable_new_disk_and_memory() {
        for stage in ["state-file","startup-receipt","commit-receipt","log"] {
            let (temp,mut session,_)=prepared_desktop_trial();
            let (expected,evidence)=completed_desktop_trial(&mut session);
            session.options.persistent_update_receipts=true;
            session.options.state_file=Some(temp.path().join("state.json"));
            session.persistent_update_output_faults.insert(stage);
            let error=session.finish_update_startup(&expected,&evidence).unwrap_err();
            assert!(error.to_string().contains("DESKTOP_PERSISTENT_UPDATE_COMMITTED_OUTPUT"));
            assert!(session.persistent_install_blocked && session.state_error.is_some());
            assert!(session.persistent_update.is_none());
            assert_eq!(session.persistent_update_state.as_ref().unwrap()["status"],"committed");
            let registry=session.options.install_registry.as_ref().unwrap();
            assert!(!registry.root_path().join(crate::persistent_update::UPDATE_PENDING_NAME).exists());
            assert!(registry.pending_update().unwrap().is_none());
            assert_eq!(crate::persistent_install::snapshot(registry).unwrap().apps.iter().find(|app|app.app==2).unwrap().version,"2.0.0");
            assert_eq!(session.catalog.apps.iter().find(|entry|entry.id==2).unwrap().package_fingerprint,expected.fingerprint);
            assert!(!session.trace.iter().any(|trace|trace.kind=="update-not-saved"));
        }
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_update_public_trial_rejects_new_authorization_without_policy_write() {
        use base64::{Engine as _,engine::general_purpose::STANDARD};
        use ed25519_dalek::{Signer,SigningKey};
        for existing_grant in [false,true] { for fail_recovery in [false,true] {
        let (_temp,mut session,source)=persistent_update_session();
        let authority=session.storage_authority.as_ref().unwrap().clone();
        let old=session.catalog.apps.iter().find(|entry|entry.id==2).unwrap().package_fingerprint;
        if existing_grant { authority.authorize_public_bound("dev.ming.install-demo",old).unwrap(); }
        let data=session.options.data_root_path.clone().unwrap();
        let before=authority.observe_update_authority(&data).unwrap();
        let mut package=crate::package::load_package(&source).unwrap(); package.manifest.public_storage=true;
        package.manifest.publisher_signature=None;
        package.manifest.publisher_signature=Some(STANDARD.encode(SigningKey::from_bytes(&[7;32]).sign(&package.signature_message("mingos-dev-2026").unwrap()).to_bytes()));
        std::fs::write(&source,serde_json::to_vec(&package.manifest).unwrap()).unwrap();
        session.begin_install(&source).unwrap();present_install_confirmation(&mut session);session.respond_storage_prompt(true).unwrap();
        let attempts=std::rc::Rc::new(std::cell::Cell::new(0));let hook_attempts=attempts.clone();
        if fail_recovery {
            crate::app_data::UPDATE_TEST_HOOK.with(|slot|*slot.borrow_mut()=Some(Box::new(move |stage| {
                if stage=="recovery-bound" { hook_attempts.set(hook_attempts.get()+1);bail!("injected recovery-bound"); } Ok(())
            })));
        }
        let launch=session.launch_from_native_dock(2);
        crate::app_data::UPDATE_TEST_HOOK.with(|slot|*slot.borrow_mut()=None);
        if fail_recovery {
            assert!(launch.is_err());assert_eq!(attempts.get(),1);
            assert!(session.persistent_install_blocked && session.persistent_update.is_some());
            assert_eq!(authority.observe_update_authority(&data).unwrap(),before);
            assert!(session.instances.is_empty() && session.records.is_empty());continue;
        }
        launch.unwrap();
        assert!(session.trace.iter().any(|trace|trace.kind=="action_rejected" && trace.detail.contains("DESKTOP_UPDATE_PUBLIC_AUTHORIZATION_REQUIRED")));
        assert!(session.pending_storage.is_none() && session.persistent_update.is_none());
        assert_eq!(authority.observe_update_authority(&data).unwrap(),before);
        assert!(session.records.is_empty());
        if existing_grant { assert_eq!(authority.public_binding_status("dev.ming.install-demo",old).unwrap(),crate::app_data::PackageBinding::Matched); }
        assert_eq!(crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap().apps.iter().find(|app|app.app==2).unwrap().version,"1.0.0");
        } }
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_install_rejects_update_and_purge_before_writes() {
        use base64::{Engine as _, engine::general_purpose::STANDARD};
        use ed25519_dalek::{Signer, SigningKey};
        let (_temp, mut session, source) = persistent_install_session();
        let before = crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap();
        let mut package = crate::package::load_package(&source).unwrap();
        package.manifest.id = "dev.ming.text-entry-demo".into();
        package.manifest.version = "0.2.0".into();
        package.manifest.publisher_signature = None;
        package.manifest.publisher_signature = Some(STANDARD.encode(SigningKey::from_bytes(&[7;32]).sign(&package.signature_message("mingos-dev-2026").unwrap()).to_bytes()));
        std::fs::write(&source, serde_json::to_vec(&package.manifest).unwrap()).unwrap();
        assert!(session.begin_install(&source).unwrap_err().to_string().contains("DESKTOP_BOOTSTRAP_PROTECTED"));
        assert!(session.begin_uninstall_with_purge(1).unwrap_err().to_string().contains("DESKTOP_PERSISTENT_MUTATION_UNSUPPORTED"));
        for operation in ["update", "purge"] {
            let package_id = package.manifest.id.clone();
            let version = package.manifest.version.clone();
            let fingerprint = package_fingerprint_hex(&package);
            let manifest_path = source.clone();
            let pending = match operation {
                "update" => DesktopStorageDecision::Install { package_id, version, fingerprint, manifest_path, update: true, retry: false },
                _ => DesktopStorageDecision::Purge { package_id, version, fingerprint, manifest_path, retry: false },
            };
            let prompt = crate::graphics::desktop_shell::StoragePrompt::install(&package.manifest.id, &package.manifest.version,
                &package_fingerprint_hex(&package), true, false, false).unwrap();
            session.pending_storage = Some(PendingDesktopStorage { app: 1, pending, prompt, composed: None, presented: false, presented_sequence: None });
            present_install_confirmation(&mut session);
            let error = session.respond_storage_prompt(true).unwrap_err().to_string();
            assert!(error.contains(if operation == "update" { "DESKTOP_BOOTSTRAP_PROTECTED" } else { "DESKTOP_PERSISTENT_MUTATION_UNSUPPORTED" }), "{error}");
            assert_eq!(crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap(), before);
        }
        let after = crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap();
        assert_eq!(after, before);
        assert!(session.pending_storage.is_none());
        assert!(session.records.is_empty() && session.instances.is_empty());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_uninstall_qualification_options_keep_existing_install_mode() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("state");
        let data = temp.path().join("data");
        std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data, std::fs::Permissions::from_mode(0o700)).unwrap();
        let args = |mode: &str| vec![
            root.join("catalog.json").into_os_string(),
            "--persistent-install-state".into(), root.clone().into_os_string(),
            "--qualification-receipts".into(), mode.into(),
            "--state-file".into(), temp.path().join("desktop.json").into_os_string(),
            "--data-root".into(), data.clone().into_os_string(),
        ];
        let uninstall = DesktopOptions::parse(args("persistent-uninstall-pointer-ready-v1")).unwrap();
        assert!(uninstall.persistent_uninstall_receipts && !uninstall.persistent_install_receipts);
        assert_eq!(uninstall.data_root_path.as_deref(), Some(data.as_path()));
        let install = DesktopOptions::parse(args("persistent-install-pointer-ready-v1")).unwrap();
        assert!(install.persistent_install_receipts && !install.persistent_uninstall_receipts);
        for omitted in ["--persistent-install-state", "--state-file"] {
            let mut invalid = args("persistent-uninstall-pointer-ready-v1");
            let index = invalid.iter().position(|value| value == omitted).unwrap();
            invalid.drain(index..index + 2);
            assert!(DesktopOptions::parse(invalid).unwrap_err().to_string().contains("DESKTOP_QUALIFICATION_RECEIPTS_REQUIRES_PERSISTENT_NATIVE_STATE"));
        }
        let mut offscreen = args("persistent-uninstall-pointer-ready-v1");
        offscreen.extend(["--backend".into(), "offscreen".into()]);
        assert!(DesktopOptions::parse(offscreen).unwrap_err().to_string().contains("DESKTOP_QUALIFICATION_RECEIPTS_REQUIRES_PERSISTENT_NATIVE_STATE"));
        assert!(DesktopOptions::parse(args("persistent-uninstall-pointer-ready-v2")).unwrap_err().to_string().contains("DESKTOP_QUALIFICATION_RECEIPTS"));
    }

    #[cfg(target_os = "linux")]
    fn persistent_uninstall_session() -> (tempfile::TempDir, Session, PathBuf) {
        let (temp, mut session, source) = persistent_install_session();
        session.begin_install(&source).unwrap();
        present_install_confirmation(&mut session);
        session.respond_storage_prompt(true).unwrap();
        (temp, session, source)
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_uninstall_protects_bootstrap_and_requires_fresh_presented_confirmation() {
        let (_temp, mut session, _) = persistent_uninstall_session();
        let before = crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap();
        session.begin_uninstall(1).unwrap();
        let prompt = session.storage_prompt().unwrap();
        assert_eq!(prompt.state_value()["mode"], 23);
        assert!(!prompt.has_allow());
        assert!(prompt.lines(600).contains(&b"SYSTEM APP CANNOT BE UNINSTALLED".to_vec()));
        present_install_confirmation(&mut session);
        assert!(session.respond_storage_prompt(true).is_err());
        session.respond_storage_prompt(false).unwrap();
        session.begin_uninstall(2).unwrap();
        assert!(session.respond_storage_prompt(true).is_err());
        assert_eq!(crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap(), before);
        present_install_confirmation(&mut session);
        session.respond_storage_prompt(false).unwrap();
        assert_eq!(crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap(), before);
        session.begin_uninstall(2).unwrap();
        present_install_confirmation(&mut session);
        session.respond_storage_prompt(true).unwrap();
        assert_eq!(session.catalog.apps.iter().map(|e| e.id).collect::<Vec<_>>(), [1]);
        assert_eq!(session.catalog.autostart, [1]);
        assert!(session.options.install_registry.as_ref().unwrap().is_removed("dev.ming.install-demo").unwrap());
        assert!(session.respond_storage_prompt(true).is_err());
        assert_eq!(session.trace.iter().filter(|e| e.kind == "persistent-uninstall-commit").count(), 1);
        assert!(session.instances.is_empty());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_uninstall_rechecks_pending_launch_and_unreaped_instances_at_confirmation() {
        for confirm in [false, true] {
            for status in ["running", "closing", "completed", "pending-launch"] {
                let (_temp, mut session, _) = persistent_uninstall_session();
                let before = crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap();
                if confirm { session.begin_uninstall(2).unwrap(); present_install_confirmation(&mut session); }
                let (_other, _bridge, _keepalive) = if status == "pending-launch" {
                    session.pending_autostart.push_back(2);
                    close_grace_tests::closing_session(crate::desktop_catalog::IsolationMode::Off)
                } else {
                    let (mut other, bridge, keepalive) = close_grace_tests::closing_session(crate::desktop_catalog::IsolationMode::Off);
                    let mut instance = other.instances.pop().unwrap();
                    let mut record = other.records.pop().unwrap();
                    instance.app = 2; instance.record = session.records.len();
                    record.app = 2; record.status = status; record.completed = status == "completed";
                    session.instances.push(instance); session.records.push(record);
                    (other, bridge, keepalive)
                };
                if confirm {
                    assert!(session.respond_storage_prompt(true).is_err(), "{status}");
                } else {
                    session.begin_uninstall(2).unwrap();
                    assert!(!session.storage_prompt().unwrap().has_allow(), "{status}");
                }
                assert_eq!(crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap(), before);
            }
        }
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_uninstall_rechecks_catalog_identity_and_preserves_binding_on_rollback() {
        let (_temp, mut session, _) = persistent_uninstall_session();
        session.public_denied.insert(2);
        session.app_data.grants.insert(2, crate::desktop_catalog::TrustedAppData { data_key: "old-key".into(), slots: vec![0] });
        session.begin_uninstall(2).unwrap(); present_install_confirmation(&mut session);
        let old = session.catalog.apps[1].package_fingerprint;
        session.catalog.apps[1].package_fingerprint = [9;32];
        assert!(session.respond_storage_prompt(true).is_err());
        assert!(session.public_denied.contains(&2) && session.app_data.grants.contains_key(&2));
        session.catalog.apps[1].package_fingerprint = old;
        session.begin_uninstall(2).unwrap(); present_install_confirmation(&mut session);
        session.persistent_install_faults.insert("catalog-directory-sync");
        let before = crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap();
        session.respond_storage_prompt(true).unwrap();
        assert_eq!(session.storage_prompt().unwrap().state_value()["mode"],11);
        assert!(!session.persistent_install_blocked);
        assert!(session.public_denied.contains(&2) && session.app_data.grants.contains_key(&2));
        assert_eq!(crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap(),before);
        session.persistent_install_faults.clear(); present_install_confirmation(&mut session);
        session.respond_storage_prompt(true).unwrap();
        assert!(!session.public_denied.contains(&2) && !session.app_data.grants.contains_key(&2));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_uninstall_reused_id_starts_with_fresh_binding_but_keeps_generation_history() {
        use base64::{Engine as _, engine::general_purpose::STANDARD};
        use ed25519_dalek::{Signer, SigningKey};
        use std::os::unix::fs::PermissionsExt;
        let (temp, mut session, source) = persistent_uninstall_session();
        let (mut old, _bridge, _keepalive) = close_grace_tests::closing_session(crate::desktop_catalog::IsolationMode::Off);
        let mut record = old.records.pop().unwrap();
        record.app=2; record.generation=9; record.worker_pid=4242; record.status="failed";
        record.first_ready_ms=Some(7); record.completed=true;
        session.records.push(record);
        session.public_denied.insert(2);
        session.app_data.grants.insert(2,crate::desktop_catalog::TrustedAppData { data_key:"old-key".into(),slots:vec![0] });
        session.begin_uninstall(2).unwrap(); present_install_confirmation(&mut session); session.respond_storage_prompt(true).unwrap();
        assert!(session.current_record(2).is_none());
        assert_eq!(session.records.len(),1);
        let mut package=crate::package::load_package(&source).unwrap();
        package.manifest.id="dev.ming.replacement".into(); package.manifest.public_storage=true;
        package.manifest.publisher_signature=Some(STANDARD.encode(SigningKey::from_bytes(&[7;32]).sign(&package.signature_message("mingos-dev-2026").unwrap()).to_bytes()));
        std::fs::write(&source,serde_json::to_vec(&package.manifest).unwrap()).unwrap();
        session.begin_install(&source).unwrap(); present_install_confirmation(&mut session); session.respond_storage_prompt(true).unwrap();
        assert_eq!(session.catalog.apps[1].id,2); assert_eq!(session.app_status(2),"closed");
        assert!(session.state_snapshot(0)["apps"].as_array().unwrap().is_empty());
        assert!(!session.public_denied.contains(&2) && !session.app_data.grants.contains_key(&2));
        let data=temp.path().join("data"); std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data,std::fs::Permissions::from_mode(0o700)).unwrap();
        session.app_data.root=Some(crate::app_data::AppDataRoot::open(&data).unwrap());
        session.launch(2).unwrap();
        assert_eq!(session.storage_prompt().unwrap().state_value()["mode"],6);
        assert_eq!(session.records.len(),1);
        present_install_confirmation(&mut session); session.respond_storage_prompt(false).unwrap();
        assert_eq!(session.records[1].generation,10);
        assert_eq!(session.records[0].worker_pid,4242);
        session.finish().unwrap();
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_uninstall_committed_output_failures_never_restore_or_create_pending() {
        for fault in ["commit-receipt","state-file","log"] {
            let (temp, mut session, source)=persistent_uninstall_session();
            let root=session.options.install_registry.as_ref().unwrap().root_path().to_path_buf();
            session.options.state_file=Some(temp.path().join("state.json"));
            session.public_denied.insert(2);
            session.begin_uninstall(2).unwrap(); present_install_confirmation(&mut session);
            session.persistent_uninstall_output_faults.insert(fault);
            assert!(session.respond_storage_prompt(true).unwrap_err().to_string().contains("DESKTOP_PERSISTENT_UNINSTALL_COMMITTED_OUTPUT"));
            assert!(session.persistent_install_blocked && session.state_error.is_some());
            assert_eq!(session.catalog.apps.len(),1); assert!(!session.public_denied.contains(&2));
            assert!(session.storage_prompt().is_none()); assert!(!root.join(crate::persistent_install::PENDING_NAME).exists());
            assert!(session.options.install_registry.as_ref().unwrap().is_removed("dev.ming.install-demo").unwrap());
            assert!(!session.trace.iter().any(|e|e.kind=="uninstall_not_saved"));
            for result in [session.begin_install(&source),session.begin_uninstall(1),session.begin_uninstall_with_purge(1),session.launch(1)] { assert!(result.is_err()); }
            drop(session);
            let output=std::process::Command::new(std::env::current_exe().unwrap()).args(["--exact","ming_desktop::input_backpressure_tests::persistent_uninstall_output_reopen_child","--nocapture"])
                .env("MING_UNINSTALL_OUTPUT_ROOT",&root).output().unwrap();
            assert!(output.status.success(),"{}",String::from_utf8_lossy(&output.stderr));
            assert!(String::from_utf8_lossy(&output.stdout).contains("UNINSTALL_OUTPUT_REOPEN_OK"));
            print!("{}",String::from_utf8_lossy(&output.stdout));
        }
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_uninstall_output_reopen_child() {
        let Some(root)=std::env::var_os("MING_UNINSTALL_OUTPUT_ROOT") else {return;};
        let opened=crate::persistent_install::open_existing(std::path::Path::new(&root)).unwrap();
        assert_eq!(opened.content.apps.len(),1);
        assert!(opened.registry.is_removed("dev.ming.install-demo").unwrap());
        println!("UNINSTALL_OUTPUT_REOPEN_OK");
    }

    #[cfg(target_os = "linux")]
    fn native_uninstall_confirmation(session: &mut Session) {
        let frame=session.compose().unwrap(); session.accepted(frame.sequence,false).unwrap();
        session.begin_native_input(true,None).unwrap(); session.end_native_input().unwrap();
        session.begin_native_input(false,None).unwrap(); session.begin_uninstall_from_native(2).unwrap(); session.end_native_input().unwrap();
        present_install_confirmation(session);
        session.begin_native_input(true,None).unwrap(); session.end_native_input().unwrap();
        session.begin_native_input(false,None).unwrap();
    }

    #[cfg(target_os = "linux")]
    fn uninstall_private_fixture(temp: &tempfile::TempDir, session: &mut Session, source: &std::path::Path) -> PathBuf {
        use std::os::unix::fs::PermissionsExt;
        let data=temp.path().join("data"); std::fs::create_dir(&data).unwrap();
        std::fs::set_permissions(&data,std::fs::Permissions::from_mode(0o700)).unwrap();
        let root=crate::app_data::AppDataRoot::open(&data).unwrap();
        let authority=Arc::new(crate::app_data::PackageStorageAuthority::open_default_private(root.clone()).unwrap());
        let package=crate::package::load_package(source).unwrap();
        authority.default_private_session_for_package(&package).unwrap().write_file("note.txt",b"alpha").unwrap();
        session.app_data.root=Some(root); session.storage_authority=Some(authority); session.options.data_root_path=Some(data.clone());
        data
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_uninstall_qualification_requires_baseline_before_any_business_write() {
        for missing in ["cause","data-root","sentinel"] {
            let (temp,mut session,source)=persistent_uninstall_session();
            session.options.persistent_uninstall_receipts=true; session.options.state_file=Some(temp.path().join("state.json"));
            if missing=="sentinel" {
                let data=uninstall_private_fixture(&temp,&mut session,&source);
                let package=crate::package::load_package(&source).unwrap();
                session.storage_authority.as_ref().unwrap().default_private_session_for_package(&package).unwrap().write_file("note.txt",b"wrong").unwrap();
                assert!(crate::app_data::snapshot_private_read_only(&data,&package.manifest.id).is_ok());
            }
            let before=crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap();
            let error=if missing=="cause" {
                session.begin_uninstall(2).unwrap(); present_install_confirmation(&mut session); session.respond_storage_prompt(true).unwrap_err()
            } else {
                native_uninstall_confirmation(&mut session); session.respond_storage_prompt_from_native(true).unwrap_err()
            };
            assert!(error.to_string().contains("DESKTOP_PERSISTENT_UNINSTALL_BASELINE"));
            assert!(session.persistent_install_blocked && session.state_error.is_some());
            assert_eq!(crate::persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap(),before);
            assert!(!session.trace.iter().any(|e|e.kind=="persistent-uninstall-commit"));
        }
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_uninstall_after_snapshot_failure_is_a_committed_output_failure() {
        let (temp,mut session,source)=persistent_uninstall_session();
        let data=uninstall_private_fixture(&temp,&mut session,&source);
        let private=crate::app_data::snapshot_private_read_only(&data,"dev.ming.install-demo").unwrap();
        session.options.persistent_uninstall_receipts=true; session.options.state_file=Some(temp.path().join("state.json"));
        native_uninstall_confirmation(&mut session);
        session.persistent_uninstall_output_faults.insert("private-after");
        assert!(session.respond_storage_prompt_from_native(true).unwrap_err().to_string().contains("DESKTOP_PERSISTENT_UNINSTALL_COMMITTED_OUTPUT"));
        assert!(session.persistent_install_blocked); assert_eq!(session.catalog.apps.len(),1);
        let registry=session.options.install_registry.as_ref().unwrap();
        assert!(registry.is_removed("dev.ming.install-demo").unwrap());
        assert!(!registry.root_path().join(crate::persistent_install::PENDING_NAME).exists());
        assert!(session.storage_prompt().is_none());
        assert_eq!(crate::app_data::snapshot_private_read_only(&data,"dev.ming.install-demo").unwrap(),private);
        assert!(!session.test_native_receipts.iter().any(|e|e["kind"]=="persistent-uninstall-commit"));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_uninstall_same_package_reinstall_keeps_private_namespace() {
        let (temp,mut session,source)=persistent_uninstall_session();
        let data=uninstall_private_fixture(&temp,&mut session,&source);
        let before=crate::app_data::snapshot_private_read_only(&data,"dev.ming.install-demo").unwrap();
        session.begin_uninstall(2).unwrap(); present_install_confirmation(&mut session); session.respond_storage_prompt(true).unwrap();
        session.begin_install(&source).unwrap(); present_install_confirmation(&mut session); session.respond_storage_prompt(true).unwrap();
        assert_eq!(session.catalog.apps[1].id,2);
        assert_eq!(crate::app_data::snapshot_private_read_only(&data,"dev.ming.install-demo").unwrap(),before);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn persistent_uninstall_transaction_fatal_blocks_all_mutation_and_worker_entries() {
        let (_temp,mut session,source)=persistent_uninstall_session();
        session.begin_uninstall(2).unwrap(); present_install_confirmation(&mut session);
        session.persistent_install_faults.extend(["catalog-write","registry-restore"]);
        assert!(session.respond_storage_prompt(true).unwrap_err().to_string().contains("DESKTOP_PERSISTENT_UNINSTALL_FATAL"));
        assert!(session.persistent_install_blocked && session.state_error.is_some());
        for result in [session.begin_install(&source),session.begin_uninstall(2),session.begin_uninstall_with_purge(2),session.launch(1)] {
            assert!(result.unwrap_err().to_string().contains("DESKTOP_PERSISTENT_INSTALL_BLOCKED"));
        }
        assert!(session.instances.is_empty());
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
    fn terminal_isolation_snapshot_refreshes_after_worker_join() {
        let (mut session, _bridge, _keepalive) = closing_session(crate::desktop_catalog::IsolationMode::Strict);
        session.instances[0].closing = None;
        let isolation = supervisor::StrictLaunch::new(1, 1, 20001).unwrap();
        {
            let mut status = isolation.status.lock().unwrap();
            status.prepared = true;
            status.applied = true;
        }
        session.instances[0].isolation = Some(isolation.clone());
        let (tx, rx) = mpsc::sync_channel(0);
        session.instances[0].result = rx;
        let cleanup = isolation.clone();
        session.instances[0].worker = Some(thread::spawn(move || {
            // Rendezvous cannot finish until poll has taken its initial isolation
            // snapshot. Joining must observe this later shared cleanup receipt.
            tx.send(Err(anyhow!("TEST_TERMINAL_EXECUTION_FAILURE"))).unwrap();
            cleanup.status.lock().unwrap().cleaned = true;
        }));
        let limit = Instant::now() + Duration::from_secs(2);
        while !session.instances.is_empty() {
            assert!(Instant::now() < limit);
            session.poll().unwrap();
            thread::yield_now();
        }
        assert!(isolation.snapshot().cleaned, "worker joined after publishing its cleanup receipt");
        let record = &session.records[0];
        assert!(record.completed);
        assert_eq!(record.status, "failed");
        assert!(record.error.as_deref().unwrap().contains("TEST_TERMINAL_EXECUTION_FAILURE"));
        assert!(record.isolation.as_ref().unwrap().cleaned,
            "terminal history must refresh the early poll snapshot after worker join");
    }
    #[test]
    fn terminal_isolation_snapshot_does_not_invent_failed_cleanup() {
        let (mut session, _bridge, _keepalive) = closing_session(crate::desktop_catalog::IsolationMode::Strict);
        session.instances[0].closing = None;
        let isolation = supervisor::StrictLaunch::new(1, 1, 20001).unwrap();
        session.instances[0].isolation = Some(isolation.clone());
        let (tx, rx) = mpsc::sync_channel(0);
        session.instances[0].result = rx;
        session.instances[0].worker = Some(thread::spawn(move || {
            tx.send(Err(anyhow!("TEST_ISOLATION_CLEANUP_FAILURE"))).unwrap();
        }));
        let limit = Instant::now() + Duration::from_secs(2);
        while !session.instances.is_empty() {
            assert!(Instant::now() < limit);
            session.poll().unwrap();
            thread::yield_now();
        }
        assert!(!isolation.snapshot().cleaned);
        let record = &session.records[0];
        assert!(record.completed);
        assert_eq!(record.status, "failed");
        assert!(record.error.as_deref().unwrap().contains("TEST_ISOLATION_CLEANUP_FAILURE"));
        assert!(!record.isolation.as_ref().unwrap().cleaned,
            "native worker completion must not fabricate successful kernel cleanup");
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

#[cfg(all(test,target_os="linux"))]
mod persistent_update_runtime {
    use super::*;
    use crate::{package::{Manifest,Package,DataMigration,MigrationStep},persistent_install,persistent_update::{self,UpdateFixtureLayout}};
    use base64::{Engine as _,engine::general_purpose::STANDARD};
    use ed25519_dalek::{Signer,SigningKey};
    use std::{fs,os::unix::fs::PermissionsExt,path::Path};
    const ID:&str="dev.ming.persistent-update-demo";
    struct TestSession(Session);
    impl std::ops::Deref for TestSession { type Target=Session; fn deref(&self)->&Session { &self.0 } }
    impl std::ops::DerefMut for TestSession { fn deref_mut(&mut self)->&mut Session { &mut self.0 } }
    impl Drop for TestSession { fn drop(&mut self) { let _=self.0.finish(); } }
    fn wasm(name:&str)->Vec<u8> {
        let path=PathBuf::from(std::env::var_os(name).unwrap_or_else(||panic!("{name} explicit packaged Wasm required")));
        assert!(path.is_absolute()&&path.is_file());fs::read(path).unwrap()
    }
    fn sign(id:&str,version:&str,bytes:Vec<u8>,migration:Option<DataMigration>)->Package {
        let key=SigningKey::from_bytes(&[7;32]);
        let mut p=Package{manifest:Manifest{schema_version:1,id:id.into(),version:version.into(),sdk_version:6,entry:"main.wasm".into(),
            private_storage:false,public_storage:false,data_migration:migration,publisher_key_id:Some("mingos-dev-2026".into()),publisher_signature:None},bytes};
        p.manifest.publisher_signature=Some(STANDARD.encode(key.sign(&p.signature_message("mingos-dev-2026").unwrap()).to_bytes()));p
    }
    fn setup_at(parent:&Path,layout:UpdateFixtureLayout,trap:bool)->(TestSession,PathBuf,PathBuf,PathBuf) {
        let worker=PathBuf::from(std::env::var_os("MING_TEST_PRIVATE_WORKER").expect("real release Host required"));assert!(worker.is_absolute()&&worker.is_file());
        let data=parent.join("data");fs::create_dir(&data).unwrap();fs::set_permissions(&data,fs::Permissions::from_mode(0o700)).unwrap();let root=data.join("install-root");
        let bootstrap=sign("dev.ming.text-entry-demo","0.1.0",wasm("MING_UPDATE_BOOTSTRAP_WASM"),None);
        let old=sign(ID,"0.1.0",wasm("MING_UPDATE_OLD_WASM"),None);
        let new_bytes=if layout==UpdateFixtureLayout::NoteAlpha {wasm("MING_UPDATE_NEW_WASM")}else{wasm("MING_UPDATE_ABSENT_WASM")};
        let new_bytes=if trap {trap_after_private_write()}else{new_bytes};
        let migration=(layout==UpdateFixtureLayout::NoteAlpha).then(||DataMigration{from_version:"0.1.0".into(),steps:vec![
            MigrationStep::EnsureDir{path:"state".into()},MigrationStep::RenameFile{from:"note.txt".into(),to:"state/note.txt".into()}]});
        let new=sign(ID,"0.2.0",new_bytes,migration);
        let catalog=br#"{"schemaVersion":3,"apps":[{"id":1,"title":"Text Lab","packageId":"dev.ming.text-entry-demo"}],"autostart":[1]}"#;
        for name in ["lost+found",".mingos-boot"] {let path=data.join(name);fs::create_dir(&path).unwrap();fs::set_permissions(path,fs::Permissions::from_mode(0o700)).unwrap();}
        let marker=data.join(".mingos-boot/current.json");
        fs::write(&marker,serde_json::to_vec(&serde_json::json!({"bootId":fs::read_to_string("/proc/sys/kernel/random/boot_id").unwrap().trim(),"counter":1})).unwrap()).unwrap();
        fs::set_permissions(marker,fs::Permissions::from_mode(0o600)).unwrap();
        persistent_update::initialize_update_fixture_first(&root,&data,&bootstrap,&old,"mingos-dev-2026",SigningKey::from_bytes(&[7;32]).verifying_key().as_bytes(),catalog,layout).unwrap();
        let source=parent.join("source");fs::create_dir(&source).unwrap();fs::write(source.join("main.wasm"),&new.bytes).unwrap();
        let manifest=source.join("app.json");fs::write(&manifest,serde_json::to_vec(&new.manifest).unwrap()).unwrap();
        let opened=persistent_install::open_existing(&root).unwrap();let (catalog,_)=crate::desktop_catalog::load_catalog_with_install_registry(&root.join("catalog.json"),&opened.registry).unwrap();
        let mut options=DesktopOptions::parse([root.join("catalog.json").into_os_string(),"--renderer".into(),"cpu".into(),
            "--persistent-install-state".into(),root.clone().into_os_string(),"--data-root".into(),data.clone().into_os_string(),
            "--state-file".into(),parent.join("state.json").into_os_string(),"--qualification-receipts".into(),"persistent-update-pointer-ready-v1".into()]).unwrap();
        options.install_registry=Some(opened.registry);
        let session=Session::new(options,catalog,#[cfg(feature="gpu")]None).unwrap();
        (TestSession(session),root,data,manifest)
    }
    fn fixture(layout:UpdateFixtureLayout,trap:bool)->(tempfile::TempDir,TestSession,PathBuf,PathBuf,PathBuf) {
        let temp=tempfile::tempdir().unwrap();fs::set_permissions(temp.path(),fs::Permissions::from_mode(0o700)).unwrap();
        let (session,root,data,source)=setup_at(temp.path(),layout,trap);(temp,session,root,data,source)
    }
    fn present(session:&mut Session) {
        let frame=session.compose().unwrap();if session.storage_prompt().is_some(){session.storage_prompt_composed(frame.sequence);}
        session.accepted(frame.sequence,false).unwrap();
    }
    fn begin_trial(session:&mut Session,source:&Path) {
        session.begin_install(source).unwrap();present(session);
        session.begin_native_input(true,None).unwrap();session.end_native_input().unwrap();
        session.begin_native_input(false,None).unwrap();session.respond_storage_prompt_from_native(true).unwrap();session.end_native_input().unwrap();
        assert!(session.persistent_update.is_some());assert!(session.instances.is_empty());
        let receipt=&session.test_native_receipts.iter().find(|r|r["kind"]=="trial-published").unwrap()["receipt"];
        let observation=&receipt["trialObservation"];
        assert_eq!(observation["schemaVersion"],1,"trial receipt must observe durable transaction before launch");
        let record=&session.persistent_update.as_ref().unwrap().record;
        assert_eq!(observation["transactionId"],record.transaction_id);
        assert_eq!(observation["innerPhase"],"recordPublished");
        let registry=session.options.install_registry.as_ref().unwrap();
        let outer=registry.read_coordinated_marker(persistent_update::UPDATE_PENDING_NAME,4096).unwrap().unwrap();
        let inner=registry.read_coordinated_marker(persistent_update::INNER_JOURNAL_NAME,16*1024).unwrap().unwrap();
        assert_eq!(observation["outerRecordSha256"],persistent_install::sha256(&outer));
        assert_eq!(observation["innerJournalSha256"],persistent_install::sha256(&inner));
        assert_eq!(STANDARD.decode(observation["outerRecordBase64"].as_str().unwrap()).unwrap(),outer);
        assert_eq!(STANDARD.decode(observation["innerJournalBase64"].as_str().unwrap()).unwrap(),inner);
        let authority=session.storage_authority.as_ref().unwrap();
        let data=session.options.data_root_path.as_deref().unwrap();
        let content=persistent_update::validate_transaction_snapshot(registry,authority,data,record,&outer,Some(&inner)).unwrap();
        assert_eq!(observation["content"],serde_json::to_value(content).unwrap());
        let private=authority.observe_coordinated_update(&crate::app_data::CoordinatedPrivateUpdate {
            transaction_id:record.transaction_id.clone(),baseline:record.private_before.clone(),
            backup_name:format!(".ming-update-backup-{}",record.transaction_id),
        }).unwrap();
        assert_eq!(observation["privateContent"],serde_json::to_value(private).unwrap());
        assert!(persistent_install::open_existing(registry.root_path()).is_err(),"trial observation must not relax strict reopen");
        session.begin_native_input(true,None).unwrap();session.launch_from_native_dock(2).unwrap();session.end_native_input().unwrap();
        session.begin_native_input(false,None).unwrap();session.end_native_input().unwrap();
        assert_eq!(session.instances.len(),1,"native Dock must create exactly one real runtime");
    }
    fn wait_ready(session:&mut Session) {
        let deadline=Instant::now()+Duration::from_secs(90);
        while Instant::now()<deadline {
            session.tick().unwrap();if session.dirty{present(session);}
            if session.current_record(2).is_some_and(|r|r.first_ready_ms.is_some()&&r.presented_source_sequence>0)
                &&session.views().iter().any(|v|v.input_ready&&v.presented_sequence>0){return;}
            assert!(!session.instances.is_empty(),"worker ended before presented Ready: {:?}",session.records);
            thread::sleep(Duration::from_millis(2));
        }
        panic!("real worker did not become presented Ready: {:?}",session.records);
    }
    fn drain(session:&mut Session) {
        let deadline=Instant::now()+Duration::from_secs(90);
        while Instant::now()<deadline&&!session.instances.is_empty(){session.tick().unwrap();if session.dirty{present(session);}thread::sleep(Duration::from_millis(2));}
        assert!(session.instances.is_empty(),"real supervisor did not finish/reap: {:?}",session.records);
    }
    fn assert_reaped(session:&Session,success:bool) {
        let record=session.records.iter().rev().find(|r|r.app==2).unwrap();assert!(record.completed&&record.worker_pid>0);
        assert_eq!(record.error.is_none(),success,"{:?}",record.error);
        assert!(session.trace.iter().any(|e|e.app==2&&e.generation==record.generation&&e.kind=="worker-reaped"));
        if success {
            assert!(record.first_ready_ms.is_some()&&record.presented_source_sequence>0&&record.frames_submitted.unwrap_or(0)>0);
            assert!(session.trace.iter().any(|e|e.app==2&&e.generation==record.generation&&e.kind=="result-accepted"));
        }
        assert_eq!(unsafe{libc::kill(record.worker_pid as i32,0)},-1,"worker PID still exists after reap");
        eprintln!("REAL_UPDATE_RECORD {}",serde_json::to_string(record).unwrap());
    }
    fn assert_alpha(data:&Path,path:&str) {
        assert_eq!(fs::read(data.join(crate::app_data::package_directory_name(ID)).join("files").join(path)).unwrap(),b"alpha");
    }
    #[test]
    #[ignore="requires three packaged Kotlin fixtures and a real release Host"]
    fn migrated_and_absent_write_commit_only_after_real_ready_close_reap() {
        for layout in [UpdateFixtureLayout::NoteAlpha,UpdateFixtureLayout::Absent] {
            let (_temp,mut session,root,data,source)=fixture(layout,false);
            let before=persistent_install::snapshot(session.options.install_registry.as_ref().unwrap()).unwrap();let catalog=fs::read(root.join("catalog.json")).unwrap();
            begin_trial(&mut session,&source);wait_ready(&mut session);
            assert!(root.join(persistent_update::UPDATE_PENDING_NAME).exists());
            assert_eq!(session.state_snapshot(session.state_revision)["persistentUpdate"]["status"],"trial");
            assert_alpha(&data,if layout==UpdateFixtureLayout::NoteAlpha{"state/note.txt"}else{"note.txt"});
            session.perform(&DesktopAction::Close{app:2}).unwrap();drain(&mut session);assert_reaped(&session,true);
            assert_eq!(session.state_snapshot(session.state_revision)["persistentUpdate"]["status"],"committed");assert!(!root.join(persistent_update::UPDATE_PENDING_NAME).exists());
            assert!(session.test_native_receipts.iter().any(|r|r["kind"]=="persistent-update-commit"));
            session.finish().unwrap();drop(session);
            let helper=std::env::var_os("MING_UPDATE_FIXTURE_HELPER").expect("explicit release helper required");
            let observed=std::process::Command::new(helper).arg("open-existing").arg(&root).arg(&data).arg(_temp.path().join("committed-open.json")).output().unwrap();
            assert!(observed.status.success(),"{}",String::from_utf8_lossy(&observed.stderr));
            let observed:serde_json::Value=serde_json::from_slice(&observed.stdout).unwrap();
            assert!(observed["actions"].as_object().unwrap().values().all(|v|v==0));
            let opened=persistent_install::open_existing(&root).unwrap();assert_eq!(fs::read(root.join("catalog.json")).unwrap(),catalog);
            assert!(opened.content.apps.iter().any(|a|a.app==2&&a.version=="0.2.0"));assert!(before.files.iter().filter(|f|f.path!="installed-packages-v1").all(|f|opened.content.files.contains(f)));
            assert_eq!(before.apps.iter().find(|a|a.app==1),opened.content.apps.iter().find(|a|a.app==1));
            drop(opened);run_independent_stable_host(_temp.path(),&root,&data,"new-0.2.0");
        }
    }
    fn trap_after_private_write()->Vec<u8> {
        let request=|path:&str,bytes:&[u8]| {
            let mut out=vec![2];out.extend_from_slice(&(path.len() as u32).to_le_bytes());out.extend_from_slice(path.as_bytes());
            out.extend_from_slice(&(bytes.len() as u32).to_le_bytes());out.extend_from_slice(bytes);out
        };
        let first=request("note.txt",b"alpha");let second=request("synced.txt",b"1");
        let encode=|bytes:&[u8]|bytes.iter().map(|b|format!("\\{b:02x}")).collect::<String>();
        wat::parse_str(format!(r#"(module
            (import "ming_private_files_v2" "call" (func $call (param i32 i32 i32 i32) (result i32)))
            (import "ming_v2" "event_next" (func $next (param i32 i32) (result i32)))
            (memory (export "memory") 2)
            (data (i32.const 0) "{}") (data (i32.const 128) "{}")
            (func $trap_after_synced_write unreachable)
            (func (export "_start")
                (if (i32.ne (call $call (i32.const 0) (i32.const {}) (i32.const 1024) (i32.const 65536)) (i32.const 5)) (then unreachable))
                (if (i32.ne (i32.load8_u (i32.const 1024)) (i32.const 1)) (then unreachable))
                (if (i32.ne (call $call (i32.const 128) (i32.const {}) (i32.const 1024) (i32.const 65536)) (i32.const 1)) (then unreachable))
                (if (i32.ne (i32.load8_u (i32.const 1024)) (i32.const 1)) (then unreachable))
                (loop $wait (drop (call $next (i32.const 2048) (i32.const 16))) (if (i32.eqz (i32.load (i32.const 2048))) (then (call $trap_after_synced_write))) (br $wait))))"#,
                encode(&first),encode(&second),first.len(),second.len())).unwrap()
    }
    fn wait_synced_files(session:&mut Session,data:&Path) {
        let deadline=Instant::now()+Duration::from_secs(30);
        let files=data.join(crate::app_data::package_directory_name(ID)).join("files");
        while Instant::now()<deadline {
            session.tick().unwrap();if session.dirty{present(session);}
            if fs::read(files.join("synced.txt")).is_ok_and(|bytes|bytes==b"1") {
                assert_alpha(data,"note.txt");assert!(!session.instances.is_empty());return;
            }
            assert!(!session.instances.is_empty(),"trap fixture failed before synced writes: {:?}",session.records);
            thread::sleep(Duration::from_millis(2));
        }
        panic!("trap fixture did not finish real synced writes");
    }
    #[test]
    #[ignore="requires packaged old fixture and real release Host"]
    fn absent_nonempty_runtime_failure_rolls_back_original_missing_namespace() {
        let (_temp,mut session,root,data,source)=fixture(UpdateFixtureLayout::Absent,true);
        let before=crate::app_data::observe_private_update_read_only(&data,ID).unwrap();assert!(!before.namespace_exists);
        begin_trial(&mut session,&source);wait_synced_files(&mut session,&data);
        session.perform(&DesktopAction::Close{app:2}).unwrap();drain(&mut session);assert_reaped(&session,false);
        assert!(session.records.last().unwrap().error.as_deref().unwrap().contains("unreachable"));
        assert_eq!(session.state_snapshot(session.state_revision)["persistentUpdate"]["status"],"rolled-back");assert_eq!(crate::app_data::observe_private_update_read_only(&data,ID).unwrap(),before);
        session.finish().unwrap();drop(session);assert!(persistent_install::open_existing(&root).unwrap().content.apps.iter().any(|a|a.app==2&&a.version=="0.1.0"));
    }
    #[test]
    #[ignore="subprocess entry only; launched by killed_host_recovers_in_independent_process"]
    fn killed_host_child_entry() {
        let Some(parent)=std::env::var_os("MING_UPDATE_KILL_CHILD_ROOT") else {return};
        let parent=PathBuf::from(parent);
        let layout=if std::env::var_os("MING_UPDATE_KILL_LAYOUT").as_deref()==Some(std::ffi::OsStr::new("seed")){UpdateFixtureLayout::NoteAlpha}else{UpdateFixtureLayout::Absent};
        let (mut session,root,data,source)=setup_at(&parent,layout,false);
        let before=crate::app_data::observe_private_update_read_only(&data,ID).unwrap();
        assert_eq!(before.namespace_exists,layout==UpdateFixtureLayout::NoteAlpha);
        begin_trial(&mut session,&source);wait_ready(&mut session);assert_alpha(&data,if layout==UpdateFixtureLayout::NoteAlpha{"state/note.txt"}else{"note.txt"});
        session.perform(&DesktopAction::Close{app:2}).unwrap();
        // Intentionally stop controller polling after Close. The real supervisor
        // consumes Result and wait/reaps the worker while Prepared stays durable.
        let deadline=Instant::now()+Duration::from_secs(90);
        loop {
            let lifecycle=session.instances[0].bridge.worker_lifecycle();
            if lifecycle.result_accepted.is_some()&&lifecycle.reaped.is_some(){break;}
            assert!(Instant::now()<deadline,"real supervisor did not accept Result and reap");
            thread::sleep(Duration::from_millis(2));
        }
        let instance=&mut session.instances[0];
        let pid=instance.pid.load(Ordering::SeqCst);
        instance.worker.take().unwrap().join().unwrap();
        assert!(pid>0);assert_eq!(unsafe{libc::kill(pid as i32,0)},-1);
        let record=persistent_update::parse_update_record(&fs::read(root.join(persistent_update::UPDATE_PENDING_NAME)).unwrap()).unwrap();
        assert_eq!(record.decision,persistent_update::UpdateDecision::Prepared);
        assert_alpha(&data,if layout==UpdateFixtureLayout::NoteAlpha{"state/note.txt"}else{"note.txt"});
        let receipt=serde_json::json!({"workerPid":pid,"resultAccepted":true,"reaped":true,"before":before});
        let mut ready=fs::OpenOptions::new().create_new(true).write(true).open(parent.join("child-ready.json")).unwrap();
        std::io::Write::write_all(&mut ready,serde_json::to_string(&receipt).unwrap().as_bytes()).unwrap();ready.sync_all().unwrap();
        loop {thread::park();}
    }
    fn run_independent_stable_host(parent:&Path,root:&Path,data:&Path,label:&str) {
        let events=parent.join(format!("{label}-events.json"));
        fs::write(&events,br#"[{"afterMs":0,"action":{"op":"close","app":1}},{"afterMs":0,"action":{"op":"launch","app":2}},{"afterMs":1,"action":{"op":"close","app":2}}]"#).unwrap();
        let stdout=parent.join(format!("{label}-host.stdout"));let stderr=parent.join(format!("{label}-host.stderr"));
        let mut host=ChildGuard(std::process::Command::new(std::env::var_os("MING_TEST_PRIVATE_WORKER").unwrap())
            .arg("desktop").arg(root.join("catalog.json")).args(["--backend","offscreen","--renderer","cpu","--duration-ms","100","--persistent-install-state"])
            .arg(root).arg("--data-root").arg(data).arg("--events").arg(events)
            .stdout(fs::File::create(&stdout).unwrap()).stderr(fs::File::create(&stderr).unwrap()).spawn().unwrap());
        let deadline=Instant::now()+Duration::from_secs(120);
        let status=loop {if let Some(status)=host.0.try_wait().unwrap(){break status;}assert!(Instant::now()<deadline,"{label} independent Host timeout");thread::sleep(Duration::from_millis(5));};
        assert!(status.success(),"{label}: {}",fs::read_to_string(stderr).unwrap());assert!(fs::metadata(&stdout).unwrap().len()<2*1024*1024);
        let outcome:serde_json::Value=serde_json::from_slice(&fs::read(stdout).unwrap()).unwrap();assert_eq!(outcome["activeWorkersAfterShutdown"],0);
        let record=outcome["instances"].as_array().unwrap().iter().find(|r|r["app"]==2).unwrap();
        assert_eq!(record["completed"],true);assert!(record["error"].is_null());assert!(record["firstReadyMs"].is_number());
        assert!(record["presentedSourceSequence"].as_u64().unwrap()>0&&record["framesSubmitted"].as_u64().unwrap()>0);
        let pid=record["workerPid"].as_u64().unwrap() as i32;assert!(pid>0);assert_eq!(unsafe{libc::kill(pid,0)},-1);
        let events=record["events"].as_array().unwrap();
        for code in [9500,9510] {assert!(events.iter().any(|e|e["code"]==code&&e["value"]==0),"{label}: {record}");}
        assert!(events.iter().any(|e|e["code"]==9511&&e["value"].as_i64().unwrap()>0));
        for kind in ["result-accepted","worker-reaped"] {assert!(outcome["trace"].as_array().unwrap().iter().any(|e|e["app"]==2&&e["kind"]==kind));}
        eprintln!("REAL_INDEPENDENT_HOST_RECORD {} {}",label,record);
    }
    struct ChildGuard(std::process::Child);
    impl Drop for ChildGuard {fn drop(&mut self){let _=self.0.kill();let _=self.0.wait();}}
    #[test]
    #[ignore="requires packaged fixtures, release Host and fixture helper"]
    fn killed_host_recovers_in_independent_process() {
        use std::os::unix::process::ExitStatusExt;
        for layout in ["seed","absent"] {
        let temp=tempfile::tempdir().unwrap();fs::set_permissions(temp.path(),fs::Permissions::from_mode(0o700)).unwrap();
        let output=fs::File::create(temp.path().join("child.stdout.log")).unwrap();
        let errors=fs::File::create(temp.path().join("child.stderr.log")).unwrap();
        let mut child=ChildGuard(std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact","ming_desktop::persistent_update_runtime::killed_host_child_entry","--ignored","--nocapture"])
            .env("MING_UPDATE_KILL_CHILD_ROOT",temp.path()).env("MING_UPDATE_KILL_LAYOUT",layout).stdout(output).stderr(errors).spawn().unwrap());
        let deadline=Instant::now()+Duration::from_secs(120);
        let ready=loop {
            if let Ok(bytes)=fs::read(temp.path().join("child-ready.json")) {
                assert!(bytes.len()<16*1024);
                if let Ok(value)=serde_json::from_slice::<serde_json::Value>(&bytes){break value;}
            }
            assert!(child.0.try_wait().unwrap().is_none(),"child exited before kill barrier: {}",fs::read_to_string(temp.path().join("child.stderr.log")).unwrap());
            assert!(Instant::now()<deadline,"child did not reach real write/Result/reap barrier");thread::sleep(Duration::from_millis(5));
        };
        let pid=ready["workerPid"].as_u64().unwrap() as i32;
        assert!(pid>0&&ready["resultAccepted"]==true&&ready["reaped"]==true);assert_eq!(unsafe{libc::kill(pid,0)},-1);
        let root=temp.path().join("data/install-root");let data=temp.path().join("data");assert_alpha(&data,if layout=="seed"{"state/note.txt"}else{"note.txt"});
        child.0.kill().unwrap();let status=child.0.wait().unwrap();assert_eq!(status.signal(),Some(libc::SIGKILL));
        let helper=PathBuf::from(std::env::var_os("MING_UPDATE_FIXTURE_HELPER").expect("explicit release fixture helper required"));
        let recovered=std::process::Command::new(&helper).arg("recover-update").arg(&root).arg(&data).arg(temp.path().join("recovered.json")).output().unwrap();
        assert!(recovered.status.success(),"{}",String::from_utf8_lossy(&recovered.stderr));
        let recovery:serde_json::Value=serde_json::from_slice(&recovered.stdout).unwrap();assert_eq!(recovery["status"],"recovered-rolled-back");
        assert_eq!(recovery["privateContent"],ready["before"]);assert!(!root.join(persistent_update::UPDATE_PENDING_NAME).exists());
        let reopened=std::process::Command::new(helper).arg("open-existing").arg(&root).arg(&data).arg(temp.path().join("reopened.json")).output().unwrap();
        assert!(reopened.status.success(),"{}",String::from_utf8_lossy(&reopened.stderr));
        let reopened:serde_json::Value=serde_json::from_slice(&reopened.stdout).unwrap();
        assert_eq!(reopened["privateContent"],ready["before"]);assert!(reopened["actions"].as_object().unwrap().values().all(|v|v==0));
        assert!(reopened["content"]["apps"].as_array().unwrap().iter().any(|a|a["app"]==2&&a["version"]=="0.1.0"));
        if layout=="seed" {assert_alpha(&data,"note.txt");run_independent_stable_host(temp.path(),&root,&data,"old-0.1.0-after-rollback");}
        eprintln!("REAL_KILLED_HOST_RECOVERY {} {}",layout,recovery);
        }
    }

}
