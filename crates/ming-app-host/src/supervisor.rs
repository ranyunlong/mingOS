use crate::{
    broker::{Broker, Event},
    graphics::RenderedFrame,
    package::load_package,
    protocol::{Init, Reply, WorkerMessage, read_json, write_json},
    ui::{InputEvent, ViewBridge},
};
use anyhow::{Context, Result, bail};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde::Serialize;
use std::{
    collections::{BTreeMap, VecDeque},
    io::BufReader,
    path::PathBuf,
    process::{Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant},
};
#[derive(Clone, Debug)]
pub struct Options {
    pub manifest_path: PathBuf,
    pub renderer: crate::graphics::RendererKind,
    pub grants: BTreeMap<u16, PathBuf>,
    pub timeout_ms: u64,
    pub max_requests: u64,
    pub fuel: u64,
    pub memory_bytes: usize,
    pub input_events: Vec<InputEvent>,
    pub live: Option<ViewBridge>,
    #[cfg(feature = "gpu")]
    pub(crate) gpu_session: Option<crate::graphics::GpuSession>,
    pub preview: bool,
    pub snapshot: Option<PathBuf>,
    pub auto_close_ms: Option<u64>,
}
impl Options {
    pub fn new(manifest_path: PathBuf) -> Self {
        Self {
            manifest_path,
            renderer: crate::graphics::RendererKind::Cpu,
            grants: BTreeMap::new(),
            timeout_ms: 5000,
            max_requests: 32768,
            fuel: 10_000_000,
            memory_bytes: 64 * 1024 * 1024,
            input_events: Vec::new(),
            live: None,
            #[cfg(feature = "gpu")]
            gpu_session: None,
            preview: false,
            snapshot: None,
            auto_close_ms: None,
        }
    }
    pub fn validate(&self) -> Result<()> {
        let max_timeout = if self.preview { 300000 } else { 60000 };
        if !(1..=max_timeout).contains(&self.timeout_ms)
            || !(1..=1_000_000).contains(&self.max_requests)
            || self.fuel == 0
            || !(65536..=268_435_456).contains(&self.memory_bytes)
            || self.input_events.len() > 128
            || self
                .auto_close_ms
                .is_some_and(|n| !(100..=max_timeout).contains(&n))
        {
            bail!("INVALID_LIMIT");
        }
        for e in &self.input_events {
            if !(0..=8).contains(&e[0])
                || e[1..].iter().any(|n| !(-1_000_000..=1_000_000).contains(n))
            {
                bail!("INVALID_EVENTS");
            }
        }
        Ok(())
    }
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FrameSummary {
    pub width: usize,
    pub height: usize,
    pub commands: usize,
    pub sequence: u64,
    pub render_micros: u128,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub text_prepare_micros: Option<u128>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gpu_timing: Option<crate::graphics::GpuTiming>,
    pub backend: &'static str,
}
/// Last source frame accepted by the native presentation API, not a scan-out timestamp.
#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PresentedFrameSummary {
    pub sequence: u64,
    pub width: usize,
    pub height: usize,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Outcome {
    pub module_source: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub isolation: Option<IsolationStatus>,
    pub app_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gpu: Option<crate::graphics::GpuInfo>,
    pub window_system: &'static str,
    pub frames_presented: u64,
    pub presented_frame: Option<PresentedFrameSummary>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub frame_clock: Option<crate::frame_clock_service::FrameClockStats>,
    pub presentation_backend: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gpu_stats: Option<crate::graphics::GpuStats>,
    #[cfg(feature = "gpu")]
    #[serde(skip)]
    gpu_session: Option<crate::graphics::GpuSession>,
    pub events: Vec<Event>,
    pub exit_code: i32,
    pub requests: u64,
    pub runtime: &'static str,
    pub duration_ms: u128,
    pub fuel_consumed: u64,
    pub entropy_bytes: usize,
    pub input_counts: [u64; 9],
    pub frames_submitted: u64,
    pub bulk_bytes_read: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub frame: Option<FrameSummary>,
    #[serde(skip)]
    pub rendered_frame: Option<RenderedFrame>,
}
// Parent-only service: no path or key is included in Init/WorkerMessage.
#[derive(Clone, Default)]
pub(crate) struct AppDataService {
    grant: Option<crate::app_data::AppDataGrant>,
    telemetry: Arc<Mutex<AppDataStats>>,
}
#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppDataStats {
    pub writes: u64,
    pub deletes: u64,
    pub bytes_written: u64,
    pub directory_sync_confirmed: u64,
    pub directory_sync_unconfirmed: u64,
    pub last_receipt: Option<AppDataReceipt>,
}
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppDataReceipt {
    pub slot: i32,
    pub operation: &'static str,
    pub length: usize,
    pub directory_synced: bool,
}
impl AppDataService {
    #[cfg(feature = "desktop")]
    pub(crate) fn new(grant: Option<crate::app_data::AppDataGrant>) -> Self {
        Self { grant, ..Self::default() }
    }
    pub(crate) fn revoke(&self) { if let Some(grant) = &self.grant { grant.revoke(); } }
    #[cfg(feature = "desktop")]
    pub(crate) fn snapshot(&self) -> Option<AppDataStats> {
        self.grant.as_ref().map(|_| self.telemetry.lock().unwrap_or_else(|e| e.into_inner()).clone())
    }
    fn committed(&self, slot: i32, operation: &'static str, length: usize, synced: bool) {
        let mut stats = self.telemetry.lock().unwrap_or_else(|e| e.into_inner());
        if operation == "write" { stats.writes = stats.writes.saturating_add(1); stats.bytes_written = stats.bytes_written.saturating_add(length as u64); }
        else { stats.deletes = stats.deletes.saturating_add(1); }
        if synced { stats.directory_sync_confirmed = stats.directory_sync_confirmed.saturating_add(1); }
        else { stats.directory_sync_unconfirmed = stats.directory_sync_unconfirmed.saturating_add(1); }
        stats.last_receipt = Some(AppDataReceipt { slot, operation, length, directory_synced: synced });
    }
    fn dispatch(&self, sdk: u32, id: u64, method: &str, args: &[i32], payload: Option<&str>) -> Reply {
        use crate::app_data::{AppDataError as E, MAX_SLOT_BYTES};
        let result = (|| -> std::result::Result<(i32, Option<String>), E> {
            let slot = *args.first().ok_or(E::InvalidArgument)?;
            if !(1..=8).contains(&slot) { return Err(E::InvalidArgument); }
            // Validate every untrusted RPC field before acquiring/using filesystem authority.
            let write_bytes = match method {
                "app_data_read" if args.len() == 3 && payload.is_none()
                    && (0..=MAX_SLOT_BYTES as i32).contains(&args[1])
                    && (0..=MAX_SLOT_BYTES as i32).contains(&args[2]) => None,
                "app_data_size" | "app_data_delete" if args.len() == 1 && payload.is_none() => None,
                "app_data_write" if args.len() == 2 && (0..=MAX_SLOT_BYTES as i32).contains(&args[1]) => {
                    let encoded = payload.filter(|p| p.len() <= 87384).ok_or(E::InvalidArgument)?;
                    let bytes = STANDARD.decode(encoded).map_err(|_| E::InvalidArgument)?;
                    if bytes.len() != args[1] as usize { return Err(E::InvalidArgument); }
                    Some(bytes)
                }
                _ => return Err(E::InvalidArgument),
            };
            if sdk < 6 { return Err(E::Denied); }
            let grant = self.grant.as_ref().ok_or(E::Denied)?;
            match method {
                "app_data_read" => {
                    let bytes = grant.read(slot, args[1], args[2])?;
                    Ok((bytes.len() as i32, (!bytes.is_empty()).then(|| STANDARD.encode(bytes))))
                }
                "app_data_size" => Ok((grant.size(slot)? as i32, None)),
                "app_data_write" => {
                    let bytes = write_bytes.as_deref().ok_or(E::InvalidArgument)?;
                    let receipt = grant.write(slot, bytes)?;
                    self.committed(slot, "write", receipt.length, receipt.directory_synced);
                    Ok((receipt.length as i32, None))
                }
                "app_data_delete" => {
                    let synced = grant.delete(slot)?;
                    self.committed(slot, "delete", 0, synced);
                    Ok((0, None))
                }
                _ => Err(E::InvalidArgument),
            }
        })();
        let (value, payload) = result.unwrap_or_else(|e| (e.code(), None));
        Reply { id, value, payload, fuel_grant: None }
    }
}
#[cfg(feature = "desktop")]
pub(crate) struct DesktopLaunchContext {
    pub isolation: Option<StrictLaunch>,
    pub app_data: AppDataService,
}
#[cfg(feature = "desktop")]
struct RevokeAppData(AppDataService);
#[cfg(feature = "desktop")]
impl Drop for RevokeAppData { fn drop(&mut self) { self.0.revoke(); } }

pub fn run(options: Options) -> Result<Outcome> {
    run_inner(
        options,
        false,
        None,
        AppDataService::default(),
        #[cfg(feature = "desktop")]
        None,
    )
}
#[cfg(feature = "desktop")]
pub(crate) fn run_desktop(
    options: Options,
    worker_pid: Arc<std::sync::atomic::AtomicU32>,
    persistent: bool,
    context: DesktopLaunchContext,
) -> Result<Outcome> {
    let monitor = context.isolation.as_ref().map(|i| i.status.clone());
    let _revoke = RevokeAppData(context.app_data.clone());
    let result = run_inner(options, persistent, context.isolation, context.app_data, Some(worker_pid));
    if let (Some(status), Err(error)) = (monitor, &result) {
        status.lock().unwrap_or_else(|e|e.into_inner()).error = Some(format!("{error:#}").chars().take(2048).collect());
    }
    result
}
fn run_inner(
    options: Options,
    persistent: bool,
    isolation: Option<StrictLaunch>,
    app_data: AppDataService,
    #[cfg(feature = "desktop")] worker_pid: Option<Arc<std::sync::atomic::AtomicU32>>,
) -> Result<Outcome> {
    options.validate()?;
    if persistent && options.fuel > crate::execution_budget::FUEL_CAP {
        bail!("INVALID_LIMIT");
    }
    let started = Instant::now();
    let package = load_package(&options.manifest_path)?;
    let sdk = package.manifest.sdk_version;
    let module_source = if crate::system_modules::lookup(sdk, &package.bytes).is_some() { "system-image" } else { "jit" };
    let mut broker = Broker::new_with_sdk(&options.grants, sdk)?;
    #[cfg(feature = "gpu")]
    if let Some(session) = options.gpu_session.clone() {
        broker.set_gpu_session(session);
    } else {
        broker.set_renderer(options.renderer)?;
    }
    #[cfg(not(feature = "gpu"))]
    broker.set_renderer(options.renderer)?;
    let mut input_events: VecDeque<_> = options.input_events.iter().copied().collect();
    // Resolve all fallible executable lookup before creating owned isolation leaves.
    let worker_executable = std::env::current_exe()?;
    let mut kernel = if let Some(config) = &isolation {
        let limits = crate::linux_isolation::Limits::new(512 << 20, 64)?;
        let group = crate::linux_isolation::WorkerIsolation::prepare(config.key, limits)?;
        config.update(|s| {
            s.prepared = true;
            s.cgroup = Some(format!("{}/{}", crate::linux_isolation::CGROUP_ROOT, config.key.name()));
            s.limits = Some(IsolationLimits { memory_bytes: 512 << 20, pids: 64, cpu_max: crate::linux_isolation::STARTUP_CPU_MAX.into() });
        });
        Some(group)
    } else { None };
    let strict = isolation.is_some();
    let mut attached = false;
    let mut command = Command::new(worker_executable);
    command
        .arg("__worker")
        .env_clear()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    if let Some(root) = std::env::var_os("SystemRoot") {
        command.env("SystemRoot", root);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            if let Some(group) = &mut kernel {
                let cleanup = group.discard_unattached();
                if let Some(config)=&isolation { config.update(|s|s.cleaned=cleanup.is_ok()); }
                cleanup.context("ISOLATION_PRESPAWN_CLEANUP")?;
            }
            return Err(error).context("WORKER_SPAWN");
        }
    };
    #[cfg(feature = "desktop")]
    if let Some(pid) = &worker_pid {
        pid.store(child.id(), Ordering::SeqCst);
    }
    let pipes = (child.stdin.take(), child.stdout.take());
    let (Some(mut input), Some(stdout)) = pipes else {
        if let Some(group)=&mut kernel { let _ = group.kill(); }
        let _ = child.kill();
        child.wait().context("WORKER_BOOTSTRAP_REAP")?;
        cleanup_kernel(&mut kernel, &mut child, false, isolation.as_ref())?;
        bail!("WORKER_STDIO");
    };
    let mut output = BufReader::new(stdout);
    let child = Arc::new(Mutex::new(child));
    let timed_out = Arc::new(AtomicBool::new(false));
    let cancelled = Arc::new(AtomicBool::new(false));
    let (cancel, rx) = mpsc::channel();
    let monitored = Arc::clone(&child);
    let timeout_flag = Arc::clone(&timed_out);
    let cancel_flag = Arc::clone(&cancelled);
    let ui_cancel = options.live.as_ref().map(|v| Arc::clone(&v.cancel));
    let timeout = Duration::from_millis(options.timeout_ms);
    let progress = Arc::new(Mutex::new(crate::execution_budget::Progress::new()));
    let watched_progress = Arc::clone(&progress);
    let watcher = thread::spawn(move || {
        let clock = Instant::now();
        loop {
            match rx.recv_timeout(Duration::from_millis(10)) {
                Ok(()) => return,
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    if let Ok(mut c) = monitored.lock() {
                        let _ = c.kill();
                    }
                    return;
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
            }
            if ui_cancel.as_ref().is_some_and(|c| c.load(Ordering::SeqCst)) {
                cancel_flag.store(true, Ordering::SeqCst);
            } else if (persistent
                && watched_progress
                    .lock()
                    .expect("progress mutex")
                    .expired(Instant::now(), timeout))
                || (!persistent && clock.elapsed() >= timeout)
            {
                timeout_flag.store(true, Ordering::SeqCst);
            } else {
                continue;
            }
            if let Ok(mut c) = monitored.lock() {
                let _ = c.kill();
            }
            return;
        }
    });
    let mut requests = 0u64;
    let mut consumed = None;
    let mut entropy_bytes = 0;
    let mut terminal_wait_seen = false;
    let mut input_counts = [0u64; 9];
    let mut frame_clock = if persistent {
        crate::frame_clock_service::FrameClockService::persistent(started)
    } else {
        crate::frame_clock_service::FrameClockService::new(started, timeout)
    };
    let mut fuel_budget = crate::execution_budget::FuelBudget::new(options.fuel);
    let mut request_budget = crate::execution_budget::RequestBudget::new(options.max_requests);
    let mut service_budget = crate::execution_budget::ServiceBudget::new();
    let result: Result<()> = (|| {
        let isolation_policy = if let (Some(group), Some(config)) = (&mut kernel, &isolation) {
            let mut worker = child.lock().expect("worker mutex");
            group.attach(&mut worker)?;
            attached = true;
            Some(crate::protocol::IsolationPolicy::from_native(&group.child_policy(config.uid, config.gid, std::process::id())?))
        } else { None };
        write_json(
            &mut input,
            &Init {
                wasm: STANDARD.encode(&package.bytes),
                fuel: options.fuel,
                memory_bytes: options.memory_bytes,
                sdk_version: sdk,
                fuel_capacity: persistent.then_some(options.fuel),
                isolation: isolation_policy.clone(),
            },
        )?;
        while let Some(message) =
            read_json::<WorkerMessage>(&mut output, if sdk >= 2 { 100000 } else { 8192 })?
        {
            if consumed.is_some() {
                bail!("PROTOCOL_AFTER_RESULT");
            }
            match message {
                WorkerMessage::Ready { isolation: report } => {
                    let mut state = progress.lock().expect("progress mutex");
                    if (!persistent && !strict) || state.initialized {
                        bail!("PROTOCOL_READY");
                    }
                    if state.expired(Instant::now(), timeout) {
                        bail!("TIMEOUT: worker startup");
                    }
                    match (&isolation_policy, report) {
                        (Some(policy), Some(report)) => {
                            report.validate_for(policy)?;
                            let config = isolation.as_ref().context("ISOLATION_CONFIG")?;
                            config.update(|s| { s.applied=true; s.uid=Some(report.uid); s.gid=Some(report.gid); s.namespace_ids=Some(report.namespace_ids); });
                            let group = kernel.as_mut().context("ISOLATION_GROUP")?;
                            group.mark_ready()?;
                            config.update(|s| { if let Some(limits)=&mut s.limits { limits.cpu_max=crate::linux_isolation::STEADY_CPU_MAX.into(); } });
                            config.observe(group)?;
                        }
                        (None, None) => {}
                        _ => bail!("PROTOCOL_ISOLATION_REPORT"),
                    }
                    state.initialized = true;
                    state.last = Instant::now();
                }
                WorkerMessage::Call {
                    id,
                    method,
                    args,
                    payload,
                    remaining_fuel,
                } => {
                    let next = requests.checked_add(1).context("PROTOCOL_SEQUENCE")?;
                    if id != next {
                        bail!("PROTOCOL_SEQUENCE");
                    }
                    requests = next;
                    if persistent {
                        request_budget
                            .accept(started.elapsed())
                            .map_err(anyhow::Error::msg)?;
                    } else if requests > options.max_requests {
                        bail!("REQUEST_LIMIT");
                    }
                    let cooperative = (sdk >= 2
                        && method == "event_next"
                        && args.is_empty()
                        && payload.is_none())
                        || (sdk >= 5
                            && method == "frame_clock_wait"
                            && args.is_empty()
                            && payload
                                .as_deref()
                                .and_then(|p| STANDARD.decode(p).ok())
                                .and_then(|b| crate::frame_clock::WaitRequest::decode(&b).ok())
                                .is_some_and(|r| !r.nonblocking));
                    if remaining_fuel.is_some() != (persistent && cooperative) {
                        bail!("PROTOCOL_UNEXPECTED_CREDIT");
                    }
                    if remaining_fuel.is_some_and(|r| r > options.fuel) {
                        bail!("PROTOCOL_FUEL_REMAINING");
                    }
                    {
                        let mut state = progress.lock().expect("progress mutex");
                        if !persistent || state.initialized {
                            state.last = Instant::now();
                        }
                        state.parked = persistent && cooperative;
                    }
                    let service_started = Instant::now();
                    let mut reply = if method.starts_with("app_data_") {
                        app_data.dispatch(sdk, id, &method, &args, payload.as_deref())
                    } else if sdk >= 6
                        && method == "input_poll"
                        && args.is_empty()
                        && payload.is_none()
                    {
                        if let Some(live) = &options.live {
                            live.enable_text_input();
                        }
                        match options.live.as_ref().and_then(|live| live.input_packet()) {
                            Some(event) => match event.encode() {
                                Ok(bytes) => Reply {
                                    id,
                                    fuel_grant: None,
                                    value: bytes.len() as i32,
                                    payload: Some(STANDARD.encode(bytes)),
                                },
                                Err(_) => Reply {
                                    id,
                                    fuel_grant: None,
                                    value: -2,
                                    payload: None,
                                },
                            },
                            None => Reply {
                                id,
                                fuel_grant: None,
                                value: 0,
                                payload: None,
                            },
                        }
                    } else if sdk >= 6 && method == "input_text_focus" && args.is_empty() {
                        let request = payload
                            .as_deref()
                            .and_then(|p| STANDARD.decode(p).ok())
                            .and_then(|b| crate::text_input::FocusRequest::decode(&b).ok());
                        let value = if let Some(request) = request {
                            let valid = !request.active
                                || broker.last_frame().is_some_and(|f| {
                                    request.x >= 0
                                        && request.y >= 0
                                        && (request.x + request.width) as usize <= f.width
                                        && (request.y + request.height) as usize <= f.height
                                });
                            if !valid {
                                -2
                            } else if let Some(live) = &options.live {
                                live.enable_text_input();
                                match live.set_text_target(request) {
                                    Ok(()) => 0,
                                    Err(_) => -2,
                                }
                            } else {
                                0
                            }
                        } else {
                            -2
                        };
                        Reply {
                            id,
                            fuel_grant: None,
                            value,
                            payload: None,
                        }
                    } else if sdk >= 5
                        && method == "frame_clock_read"
                        && args.is_empty()
                        && payload.is_none()
                    {
                        match frame_clock.read() {
                            Ok(bytes) => Reply {
                                id,
                                fuel_grant: None,
                                value: 8,
                                payload: Some(STANDARD.encode(bytes)),
                            },
                            Err(error) => Reply {
                                id,
                                fuel_grant: None,
                                value: error.code(),
                                payload: None,
                            },
                        }
                    } else if sdk >= 5 && method == "frame_clock_wait" && args.is_empty() {
                        let request = payload
                            .as_deref()
                            .and_then(|p| STANDARD.decode(p).ok())
                            .and_then(|bytes| crate::frame_clock::WaitRequest::decode(&bytes).ok());
                        if let Some(request) = request {
                            let event = frame_clock.wait(
                                request,
                                options.live.as_ref(),
                                &mut input_events,
                                &mut input_counts,
                            );
                            broker.check_render_backend()?;
                            match event {
                                Ok(Some(event)) => {
                                    terminal_wait_seen |= matches!(
                                        event.kind,
                                        crate::frame_clock::EventKind::Close
                                            | crate::frame_clock::EventKind::Cancel
                                    );
                                    Reply {
                                        id,
                                        fuel_grant: None,
                                        value: 48,
                                        payload: Some(STANDARD.encode(event.encode())),
                                    }
                                }
                                Ok(None) => Reply {
                                    id,
                                    fuel_grant: None,
                                    value: 0,
                                    payload: None,
                                },
                                Err(error) => Reply {
                                    id,
                                    fuel_grant: None,
                                    value: error.code(),
                                    payload: None,
                                },
                            }
                        } else {
                            Reply {
                                id,
                                fuel_grant: None,
                                value: -2,
                                payload: None,
                            }
                        }
                    } else if sdk >= 3 && method == "text_layout_create" && args.len() == 3 {
                        let bytes = payload
                            .as_deref()
                            .and_then(|s| STANDARD.decode(s).ok())
                            .filter(|b| b.len() <= 512);
                        Reply {
                            id,
                            fuel_grant: None,
                            value: if let Some(bytes) = bytes {
                                broker.text_create(&bytes, args[0], args[1], args[2])?
                            } else {
                                -2
                            },
                            payload: None,
                        }
                    } else if sdk >= 3
                        && method == "text_layout_metrics"
                        && args.len() == 1
                        && payload.is_none()
                    {
                        let bytes = broker.text_metrics(args[0]);
                        Reply {
                            id,
                            fuel_grant: None,
                            value: if bytes.is_some() { 48 } else { -2 },
                            payload: bytes.map(|b| STANDARD.encode(b)),
                        }
                    } else if sdk >= 2
                        && method == "file_read"
                        && args.len() == 3
                        && payload.is_none()
                    {
                        let chunk = broker.read_chunk(args[0], args[1], args[2]);
                        Reply {
                            id,
                            fuel_grant: None,
                            value: chunk.value,
                            payload: chunk.bytes.map(|b| STANDARD.encode(b)),
                        }
                    } else if sdk >= 2
                        && (method == "frame_submit" || (sdk >= 4 && method == "frame_submit_v4"))
                        && args.is_empty()
                    {
                        let bytes = payload
                            .as_deref()
                            .and_then(|s| STANDARD.decode(s).ok())
                            .filter(|b| b.len() <= 65536);
                        let value = bytes.map_or(-2, |b| {
                            if method == "frame_submit_v4" {
                                broker.submit_frame_v4(&b)
                            } else {
                                broker.submit_frame(&b)
                            }
                        });
                        broker.check_render_backend()?;
                        if value == 0
                            && let (Some(live), Some(frame)) = (&options.live, broker.last_frame())
                        {
                            live.publish(frame);
                        }
                        Reply {
                            id,
                            fuel_grant: None,
                            value,
                            payload: None,
                        }
                    } else if sdk >= 2
                        && method == "event_next"
                        && args.is_empty()
                        && payload.is_none()
                    {
                        let event = if let Some(live) = &options.live {
                            live.next_event()
                        } else {
                            input_events.pop_front().unwrap_or([0; 4])
                        };
                        terminal_wait_seen |= event[0] == 0;
                        if let Some(count) = input_counts.get_mut(event[0] as usize) {
                            *count += 1;
                        }
                        let bytes: Vec<u8> = event.into_iter().flat_map(i32::to_le_bytes).collect();
                        Reply {
                            id,
                            fuel_grant: None,
                            value: 16,
                            payload: Some(STANDARD.encode(bytes)),
                        }
                    } else {
                        Reply {
                            id,
                            fuel_grant: None,
                            value: if payload.is_none() {
                                broker.dispatch(&method, &args)
                            } else {
                                -2
                            },
                            payload: None,
                        }
                    };
                    // Blocking SDK wait is excluded from host service wall-time work.
                    if persistent && !cooperative {
                        let delay =
                            service_budget.charge(service_started.elapsed(), started.elapsed());
                        if !delay.is_zero() {
                            progress.lock().expect("progress mutex").parked = true;
                            let until = Instant::now()
                                .checked_add(delay)
                                .context("SERVICE_BUDGET_OVERFLOW")?;
                            while Instant::now() < until {
                                if options
                                    .live
                                    .as_ref()
                                    .is_some_and(|v| v.cancel.load(Ordering::SeqCst) || v.closed())
                                {
                                    bail!("APP_CANCELLED");
                                }
                                thread::sleep(
                                    until
                                        .saturating_duration_since(Instant::now())
                                        .min(Duration::from_millis(10)),
                                );
                            }
                        }
                    }
                    if let Some(remaining) = remaining_fuel {
                        reply.fuel_grant = Some(wait_fuel_grant(
                            &mut fuel_budget,
                            remaining,
                            started.elapsed(),
                            &mut terminal_wait_seen,
                            options.live.as_ref(),
                            reply.value >= 0,
                        )?);
                    }
                    write_json(&mut input, &reply)?;
                    {
                        let mut state = progress.lock().expect("progress mutex");
                        if !persistent || state.initialized {
                            state.last = Instant::now();
                        }
                        state.parked = false;
                    }
                }
                WorkerMessage::Result {
                    exit_code,
                    fuel_consumed,
                    total_granted,
                    entropy_bytes: entropy,
                } => {
                    let expected_total = if persistent {
                        fuel_budget.total
                    } else {
                        options.fuel
                    };
                    if ((persistent || strict) && !progress.lock().expect("progress mutex").initialized)
                        || total_granted != persistent.then_some(expected_total)
                        || exit_code != 0
                        || fuel_consumed > expected_total
                        || entropy > 1048576
                    {
                        bail!("PROTOCOL_RESULT");
                    }
                    consumed = Some(fuel_consumed);
                    entropy_bytes = entropy;
                    if let Some(live) = &options.live {
                        live.accept_worker_result();
                    }
                }
                WorkerMessage::Error { code, message } => bail!("{code}: {message}"),
            }
        }
        if consumed.is_none() {
            bail!("WORKER_FAILED: missing result");
        }
        Ok(())
    })();
    drop(input);
    drop(output);
    if result.is_err() {
        if let Some(group)=&mut kernel { let _ = group.kill(); }
        let _ = child.lock().map(|mut c| c.kill());
    }
    let status = loop {
        let state = child.lock().expect("worker mutex").try_wait();
        match state {
            Ok(Some(s)) => break Ok(s),
            Ok(None) => thread::sleep(Duration::from_millis(2)),
            Err(e) => break Err(e),
        }
    };
    let _ = cancel.send(());
    let _ = watcher.join();
    // Cleanup is performed only after try_wait has reaped this exact Child.
    if status.is_ok() {
        cleanup_kernel(&mut kernel, &mut child.lock().expect("worker mutex"), attached, isolation.as_ref())?;
    }
    if timed_out.load(Ordering::SeqCst) {
        bail!("TIMEOUT: worker exceeded {}ms", options.timeout_ms);
    }
    if cancelled.load(Ordering::SeqCst) {
        bail!("APP_CANCELLED");
    }
    result.with_context(|| {
        format!(
            "APP_EXECUTION requests={} frames={} bulkBytes={} inputCounts={:?}",
            requests,
            broker.frames_submitted(),
            broker.bulk_bytes_read(),
            input_counts
        )
    })?;
    if !status.context("WORKER_WAIT")?.success() {
        bail!("WORKER_FAILED: nonzero exit");
    }
    broker.check_render_backend()?;
    let rendered = broker.last_frame();
    let frame = rendered.as_ref().map(|f| FrameSummary {
        width: f.width,
        height: f.height,
        commands: f.commands,
        sequence: f.sequence,
        render_micros: f.render_us,
        text_prepare_micros: f.text_prepare_us,
        gpu_timing: f.gpu_timing.clone(),
        backend: broker.graphics_backend(),
    });
    let mut outcome = Outcome {
        module_source,
        isolation: isolation.as_ref().map(|s|s.snapshot()),
        app_id: package.manifest.id,
        gpu: broker.gpu_info().cloned(),
        window_system: "headless",
        frames_presented: 0,
        presented_frame: None,
        frame_clock: (sdk >= 5).then(|| frame_clock.stats.clone()),
        presentation_backend: "none",
        gpu_stats: None,
        #[cfg(feature = "gpu")]
        gpu_session: broker.gpu_session(),
        events: broker.events().to_vec(),
        exit_code: 0,
        requests,
        runtime: "rust-wasmtime",
        duration_ms: started.elapsed().as_millis(),
        fuel_consumed: consumed.context("PROTOCOL_RESULT")?,
        entropy_bytes,
        input_counts,
        frames_submitted: broker.frames_submitted(),
        bulk_bytes_read: broker.bulk_bytes_read(),
        frame,
        rendered_frame: rendered,
    };
    outcome.refresh_gpu_stats()?;
    Ok(outcome)
}

impl Outcome {
    pub fn refresh_gpu_stats(&mut self) -> Result<()> {
        #[cfg(feature = "gpu")]
        if let Some(session) = &self.gpu_session {
            self.gpu_stats = Some(session.stats()?);
        }
        Ok(())
    }
}

// A terminal wait or closed bridge is a one-way boundary. Keep Some(0) in the
// persistent reply so worker wire validation and cleanup retain their contract.
fn wait_fuel_grant(
    budget: &mut crate::execution_budget::FuelBudget,
    remaining: u64,
    elapsed: Duration,
    terminal: &mut bool,
    live: Option<&ViewBridge>,
    successful: bool,
) -> Result<u64> {
    *terminal |= live.is_some_and(|bridge| bridge.closed());
    if *terminal || !successful {
        return Ok(0);
    }
    budget.grant(remaining, elapsed).map_err(anyhow::Error::msg)
}

#[cfg(test)]
mod terminal_credit_tests {
    use super::*;
    #[test]
    fn terminal_reply_or_closed_cancelled_bridge_never_issues_credit() {
        let mut budget = crate::execution_budget::FuelBudget::new(100);
        let mut terminal = false;
        assert_eq!(
            wait_fuel_grant(
                &mut budget,
                0,
                Duration::from_secs(1),
                &mut terminal,
                None,
                true
            )
            .unwrap(),
            100
        );
        assert_eq!(budget.total, 200);
        for cancel in [false, true] {
            let (bridge, _sender) = ViewBridge::new();
            if cancel {
                bridge.cancel.store(true, Ordering::SeqCst);
            } else {
                bridge.request_close();
            }
            let mut terminal = false;
            assert_eq!(
                wait_fuel_grant(
                    &mut budget,
                    0,
                    Duration::from_secs(2),
                    &mut terminal,
                    Some(&bridge),
                    true
                )
                .unwrap(),
                0
            );
            assert!(terminal);
            assert_eq!(
                wait_fuel_grant(
                    &mut budget,
                    0,
                    Duration::from_secs(3),
                    &mut terminal,
                    None,
                    true
                )
                .unwrap(),
                0
            );
        }
        let mut terminal = true;
        assert_eq!(
            wait_fuel_grant(
                &mut budget,
                0,
                Duration::from_secs(4),
                &mut terminal,
                None,
                true
            )
            .unwrap(),
            0
        );
        assert_eq!(budget.total, 200);
    }
}

#[derive(Clone,Debug,Default,Serialize)]
#[serde(rename_all="camelCase")]
pub struct IsolationStatus {
    pub requested: &'static str, pub prepared:bool, pub applied:bool, pub cleaned:bool,
    pub uid:Option<u32>,pub gid:Option<u32>,pub cgroup:Option<String>,
    pub limits:Option<IsolationLimits>,pub namespace_ids:Option<[u64;4]>,
    pub metrics:Option<IsolationMetrics>,pub error:Option<String>,
}
#[derive(Clone,Debug,Serialize)]
#[serde(rename_all="camelCase")]
pub struct IsolationLimits {pub memory_bytes:u64,pub pids:u32,pub cpu_max:String}
#[derive(Clone,Debug,Serialize)]
#[serde(rename_all="camelCase")]
pub struct IsolationMetrics {pub memory_current:u64,pub memory_peak:u64,pub pids_current:u64,pub vm_hwm_bytes:Option<u64>,pub cpu_stat:String,pub memory_events:String,pub pids_events:String}
#[derive(Clone)]
pub(crate) struct StrictLaunch {
    pub key:crate::linux_isolation::WorkerKey,pub uid:u32,pub gid:u32,
    pub status:Arc<Mutex<IsolationStatus>>,
}
impl StrictLaunch {
    pub fn new(app:u16,generation:u64,uid:u32)->Result<Self> {
        if !(20000..60000).contains(&uid) {bail!("ISOLATION_IDENTITY_EXHAUSTED");}
        Ok(Self {key:crate::linux_isolation::WorkerKey::new(app,generation)?,uid,gid:uid,status:Arc::new(Mutex::new(IsolationStatus{requested:"strict",..Default::default()}))})
    }
    fn update(&self,f:impl FnOnce(&mut IsolationStatus)) {f(&mut self.status.lock().unwrap_or_else(|e|e.into_inner()));}
    pub fn snapshot(&self)->IsolationStatus {self.status.lock().unwrap_or_else(|e|e.into_inner()).clone()}
    fn observe(&self,group:&crate::linux_isolation::WorkerIsolation)->Result<()> {
        let m=group.metrics()?;
        self.update(|s| {let previous=s.metrics.as_ref().and_then(|m|m.vm_hwm_bytes);s.metrics=Some(IsolationMetrics{memory_current:m.memory_current,memory_peak:m.memory_peak,pids_current:m.pids_current,vm_hwm_bytes:m.vm_hwm_bytes.or(previous),cpu_stat:m.cpu_stat,memory_events:m.memory_events,pids_events:m.pids_events});});Ok(())
    }
}
fn cleanup_kernel(group:&mut Option<crate::linux_isolation::WorkerIsolation>,child:&mut std::process::Child,attached:bool,config:Option<&StrictLaunch>)->Result<()> {
    let Some(group)=group else{return Ok(());};
    group.kill().context("ISOLATION_KILL")?;
    let observed = if let Some(config)=config {config.observe(group)} else {Ok(())};
    let mut cleanup=if attached {group.cleanup_after_reap(child)} else {group.discard_unattached()};
    // A reaped leader may briefly precede the final kernel task detach.
    for _ in 0..50 {
        if cleanup.as_ref().err().and_then(|e|e.source.raw_os_error())!=Some(16) {break;}
        thread::sleep(Duration::from_millis(2));
        cleanup=if attached {group.cleanup_after_reap(child)} else {group.discard_unattached()};
    }
    if let Some(config)=config {config.update(|s|s.cleaned=cleanup.is_ok());}
    cleanup.context("ISOLATION_CLEANUP")?;
    observed.context("ISOLATION_METRICS")
}

#[cfg(all(test, feature = "desktop"))]
mod app_data_host_tests {
    use super::*;
    #[test]
    fn malformed_parent_bulk_never_writes_and_data_replies_never_grant_fuel() {
        let temp = tempfile::tempdir().unwrap();
        let root = crate::app_data::AppDataRoot::open(temp.path()).unwrap();
        let grant = root.grant("trusted", &[1]).unwrap();
        let service = AppDataService::new(Some(grant.clone()));
        for (args, payload) in [(vec![1, 3], None), (vec![1, 3], Some("AA==")), (vec![1, 0], Some("%%%")), (vec![0, 0], Some("")), (vec![1, 65537], Some(""))] {
            let reply = service.dispatch(6, 1, "app_data_write", &args, payload);
            assert_eq!(reply.value, -2); assert!(reply.fuel_grant.is_none()); assert!(reply.payload.is_none());
        }
        assert_eq!(grant.size(1), Err(crate::app_data::AppDataError::NotFound));
        let reply = service.dispatch(6, 1, "app_data_write", &[1, 0], Some(""));
        assert_eq!(reply.value, 0); assert!(reply.fuel_grant.is_none());
        let reply = service.dispatch(6, 2, "app_data_read", &[1, 0, 0], None);
        assert_eq!(reply.value, 0); assert!(reply.payload.is_none()); assert!(reply.fuel_grant.is_none());
    }
    #[test]
    fn committed_unsynced_receipt_survives_revoke_and_is_not_rollback_error() {
        let temp = tempfile::tempdir().unwrap();
        let grant = crate::app_data::AppDataRoot::open(temp.path()).unwrap().grant("trusted", &[1]).unwrap();
        let service = AppDataService::new(Some(grant.clone())); let monitor = service.clone();
        grant.fail_next_directory_sync();
        let reply = service.dispatch(6, 7, "app_data_write", &[1, 3], Some("YWJj"));
        assert_eq!(reply.value, 3); assert!(reply.fuel_grant.is_none());
        assert_eq!(grant.read(1, 0, 3).unwrap(), b"abc");
        service.revoke(); drop(service);
        assert_eq!(monitor.dispatch(6, 8, "app_data_size", &[1], None).value, -1);
        assert_eq!(grant.read(1, 0, 3), Err(crate::app_data::AppDataError::Denied));
        let stats = monitor.snapshot().unwrap();assert_eq!(stats.writes, 1);assert_eq!(stats.directory_sync_unconfirmed, 1);
        assert!(!stats.last_receipt.unwrap().directory_synced);
    }
}
