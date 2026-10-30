#![cfg(feature = "desktop")]
use ming_app_host::{
    desktop_catalog::DesktopOptions,
    protocol::{Init, Reply, WorkerMessage},
};
use serde_json::json;
#[test]
fn explicit_persistent_duration_and_trusted_shutdown_are_accepted() {
    assert!(DesktopOptions::parse(["catalog.json", "--duration-ms", "0"].map(Into::into)).is_ok());
    assert!(
        serde_json::from_value::<ming_app_host::desktop_catalog::DesktopAction>(
            json!({"op":"shutdown"})
        )
        .is_ok()
    );
}
#[test]
fn optional_credit_wire_contract_and_bounded_bytes() {
    let init: Init =
        serde_json::from_value(json!({"wasm":"AA==","fuel":7,"memoryBytes":65536,"sdkVersion":2}))
            .unwrap();
    assert_eq!(
        serde_json::to_value(init).unwrap(),
        json!({"wasm":"AA==","fuel":7,"memoryBytes":65536,"sdkVersion":2})
    );
    let reply: Reply = serde_json::from_value(json!({"id":1,"value":0})).unwrap();
    assert_eq!(
        serde_json::to_string(&reply).unwrap(),
        r#"{"id":1,"value":0}"#
    );
    assert!(serde_json::from_value::<Reply>(json!({"id":1,"value":0,"fuel_grant":1})).is_ok());
    assert!(serde_json::from_value::<Reply>(json!({"id":1,"value":0,"fuel_grant":-1})).is_err());
    assert!(
        serde_json::from_value::<WorkerMessage>(
            json!({"type":"call","id":1,"method":"event_next","args":[],"remaining_fuel":0})
        )
        .is_ok()
    );
}

use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde_json::Value;
use std::{
    fs,
    io::BufReader,
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};
fn host() -> PathBuf {
    std::env::var_os("MING_7D_TEST_HOST")
        .map(PathBuf::from)
        .or_else(|| option_env!("CARGO_BIN_EXE_ming-app-host").map(PathBuf::from))
        .unwrap_or_else(|| {
            std::env::current_exe()
                .unwrap()
                .parent()
                .unwrap()
                .join("ming-app-host.exe")
        })
}
struct Worker {
    child: Child,
    input: ChildStdin,
    messages: mpsc::Receiver<Value>,
    reader: Option<thread::JoinHandle<()>>,
    ready: bool,
}
impl Worker {
    fn new(wat: &str, sdk: u32, fuel: u64, cap: Option<u64>) -> Self {
        Self::with_request_limit(wat, sdk, fuel, cap, None)
    }
    fn persistent(wat: &str, sdk: u32, fuel: u64, max_requests: u64) -> Self {
        Self::with_request_limit(wat, sdk, fuel, Some(fuel), Some(max_requests))
    }
    fn with_request_limit(
        wat: &str,
        sdk: u32,
        fuel: u64,
        cap: Option<u64>,
        max_requests: Option<u64>,
    ) -> Self {
        let mut child = Command::new(host())
            .arg("__worker")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let input = child.stdin.take().unwrap();
        let mut output = BufReader::new(child.stdout.take().unwrap());
        let (tx, messages) = mpsc::channel();
        let reader = thread::spawn(move || {
            while let Ok(Some(v)) = ming_app_host::protocol::read_json::<Value>(&mut output, 100000)
            {
                if tx.send(v).is_err() {
                    break;
                }
            }
        });
        let mut worker = Self {
            child,
            input,
            messages,
            reader: Some(reader),
            ready: false,
        };
        let mut init = json!({"wasm":STANDARD.encode(wat::parse_str(wat).unwrap()),"fuel":fuel,"memoryBytes":65536,"sdkVersion":sdk});
        if let Some(cap) = cap {
            init["fuelCapacity"] = cap.into();
        }
        if let Some(max_requests) = max_requests {
            init["maxRequests"] = max_requests.into();
        }
        worker.send(init);
        worker
    }
    fn send(&mut self, v: Value) {
        ming_app_host::protocol::write_json(&mut self.input, &v).unwrap();
    }
    fn recv(&mut self) -> Value {
        loop {
            let message = self.messages.recv_timeout(Duration::from_secs(5)).expect("worker response within deadline");
            if message["type"] == "ready" { assert!(!self.ready, "duplicate ready"); self.ready = true; }
            else { return message; }
        }
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        if let Some(t) = self.reader.take() {
            let _ = t.join();
        }
    }
}
const ONE_WAIT: &str = r#"(module(import "ming_v2" "event_next"(func $wait(param i32 i32)(result i32)))(memory(export "memory")1)(func(export "_start")i32.const 0 i32.const 16 call $wait drop))"#;
const FILE_LENGTH_CACHE: &str = r#"(module
    (import "ming_v1" "file_open" (func $open (param i32) (result i32)))
    (import "ming_v1" "file_length" (func $length (param i32) (result i32)))
    (import "ming_v1" "file_close" (func $close (param i32) (result i32)))
    (func (export "_start")
        i32.const 1 call $length drop
        i32.const 1 call $length drop
        i32.const 1 call $open drop
        i32.const 1 call $length drop
        i32.const 1 call $length drop
        i32.const 1 call $close drop
        i32.const 1 call $length drop))"#;
const FILE_LENGTH_LIMIT: &str = r#"(module
    (import "ming_v1" "file_length" (func $length (param i32) (result i32)))
    (func (export "_start")
        i32.const 1 call $length drop
        i32.const 1 call $length drop
        i32.const 1 call $length drop))"#;
const FILE_LENGTH_CACHE_BOUND: &str = r#"(module
    (import "ming_v1" "file_length" (func $length (param i32) (result i32)))
    (func (export "_start") (local $handle i32)
        i32.const 1 local.set $handle
        block $done loop $query
            local.get $handle call $length drop
            local.get $handle i32.const 1 i32.add local.tee $handle
            i32.const 66 i32.lt_u br_if $query
        end end
        i32.const 1 call $length drop))"#;
#[test]
fn real_persistent_worker_caches_file_length_and_invalidates_after_open_and_close() {
    let mut worker = Worker::persistent(FILE_LENGTH_CACHE, 2, 10000, 7);
    for (id, method, value) in [
        (1, "file_length", -2),
        (2, "file_open", 1),
        (3, "file_length", 7),
        (4, "file_close", 0),
        (5, "file_length", 9),
    ] {
        let call = worker.recv();
        assert_eq!(call["type"], "call", "{call}");
        assert_eq!(call["id"], id, "{call}");
        assert_eq!(call["method"], method, "{call}");
        worker.send(json!({"id": id, "value": value}));
    }
    let result = worker.recv();
    assert_eq!(result["type"], "result", "{result}");
}

#[test]
fn real_persistent_worker_counts_cached_file_length_calls_against_its_request_limit() {
    let mut worker = Worker::persistent(FILE_LENGTH_LIMIT, 2, 10000, 2);
    let call = worker.recv();
    assert_eq!(call["type"], "call", "{call}");
    assert_eq!(call["id"], 1, "{call}");
    assert_eq!(call["method"], "file_length", "{call}");
    worker.send(json!({"id": 1, "value": -2}));
    let error = worker.recv();
    assert_eq!(error["type"], "error", "{error}");
    assert!(error.to_string().contains("REQUEST_LIMIT: requests=3"), "{error}");
}

#[test]
fn real_persistent_worker_evicts_old_file_length_cache_entries() {
    let mut worker = Worker::persistent(FILE_LENGTH_CACHE_BOUND, 2, 10000, 66);
    for handle in 1..=65 {
        let call = worker.recv();
        assert_eq!(call["type"], "call", "{call}");
        assert_eq!(call["id"], handle, "{call}");
        assert_eq!(call["method"], "file_length", "{call}");
        assert_eq!(call["args"], json!([handle]), "{call}");
        worker.send(json!({"id": handle, "value": -2}));
    }
    let evicted = worker.recv();
    assert_eq!(evicted["type"], "call", "{evicted}");
    assert_eq!(evicted["id"], 66, "{evicted}");
    assert_eq!(evicted["method"], "file_length", "{evicted}");
    assert_eq!(evicted["args"], json!([1]), "{evicted}");
    worker.send(json!({"id": 66, "value": -2}));
    let result = worker.recv();
    assert_eq!(result["type"], "result", "{result}");
}
#[test]
fn real_worker_accumulates_credit_and_consumes_more_than_initial_burst() {
    let wat = r#"(module(import "ming_v2" "event_next"(func $wait(param i32 i32)(result i32)))(memory(export "memory")1)
        (func(export "_start")(local $i i32)(local $n i32)
        loop $outer i32.const 100 local.set $n
        loop $burn local.get $n i32.const 1 i32.sub local.tee $n br_if $burn end
        i32.const 0 i32.const 16 call $wait drop
        local.get $i i32.const 1 i32.add local.tee $i i32.const 30 i32.lt_u br_if $outer end))"#;
    let mut w = Worker::new(wat, 2, 10000, Some(10000));
    let mut total = 10000;
    let mut calls = 0;
    loop {
        let v = w.recv();
        if v["type"] == "result" {
            assert!(v["fuelConsumed"].as_u64().unwrap() > 10000, "{v}");
            assert_eq!(v["totalGranted"], total);
            evidence("worker-credit-result.json", &v);
            break;
        }
        assert_eq!(v["type"], "call", "{v}");
        assert_eq!(v["method"], "event_next");
        let remaining = v["remaining_fuel"].as_u64().unwrap();
        let grant = 10000 - remaining;
        total += grant;
        calls += 1;
        w.send(
            json!({"id":v["id"],"value":16,"payload":STANDARD.encode([0u8;16]),"fuel_grant":grant}),
        );
    }
    assert_eq!(calls, 30);
}
#[test]
fn real_worker_rejects_credit_on_bounded_or_ordinary_calls_and_excess_or_missing_grants() {
    let log = r#"(module(import "ming_v1" "log"(func $log(param i32 i32)(result i32)))(func(export "_start")i32.const 1 i32.const 2 call $log drop))"#;
    for (wat, cap, grant, expected) in [
        (ONE_WAIT, None, Some(1), "PROTOCOL_UNEXPECTED_CREDIT"),
        (log, Some(10000), Some(1), "PROTOCOL_UNEXPECTED_CREDIT"),
        (ONE_WAIT, Some(10000), Some(u64::MAX), "PROTOCOL_FUEL_GRANT"),
        (ONE_WAIT, Some(10000), None, "PROTOCOL_FUEL_GRANT"),
    ] {
        let mut w = Worker::new(wat, 2, 10000, cap);
        let v = w.recv();
        assert_eq!(v["type"], "call", "{v}");
        let mut reply = json!({"id":v["id"],"value":16,"payload":STANDARD.encode([0u8;16])});
        if let Some(grant) = grant {
            reply["fuel_grant"] = grant.into();
        }
        w.send(reply);
        let error = w.recv();
        assert_eq!(error["type"], "error", "{error}");
        assert!(error.to_string().contains(expected), "{error}");
    }
}
#[test]
fn real_busy_loop_traps_without_any_replenishment_rpc() {
    let mut w = Worker::new(
        r#"(module(func(export "_start")(loop $spin br $spin)))"#,
        2,
        10000,
        Some(10000),
    );
    let error = w.recv();
    assert_eq!(error["type"], "error", "{error}");
    assert!(error.to_string().contains("FUEL_EXHAUSTED"), "{error}");
}
#[test]
fn real_bounded_worker_keeps_legacy_call_and_result_wire() {
    let mut w = Worker::new(ONE_WAIT, 2, 10000, None);
    let call = w.recv();
    assert!(call.get("remaining_fuel").is_none());
    assert!(!w.ready, "bounded worker must not emit Ready");
    w.send(json!({"id":call["id"],"value":16,"payload":STANDARD.encode([0u8;16])}));
    let result = w.recv();
    assert_eq!(result["type"], "result", "{result}");
    assert!(result.get("totalGranted").is_none());
    assert!(result["fuelConsumed"].as_u64().unwrap() <= 10000);
}
fn fixture(dir: &Path, name: &str, failure: &str) {
    let app = dir.join(name);
    fs::create_dir(&app).unwrap();
    let data: String = [
        0x4d474632u32,
        1,
        1,
        1,
        1,
        0,
        1,
        0,
        0,
        1,
        1,
        0xffa03020,
        0,
        0,
    ]
    .into_iter()
    .flat_map(u32::to_le_bytes)
    .map(|b| format!("\\{b:02x}"))
    .collect();
    let body = match failure {
        "fuel" => "(loop $spin br $spin)",
        "requests" => "(loop $flood i32.const 1 call $len drop br $flood)",
        _ => {
            r#"block $end loop $again
            i32.const 128 i32.const 16 call $next drop
            i32.const 128 i32.load local.tee $kind i32.eqz br_if $end
            local.get $kind i32.const 1 i32.eq if
            i32.const 8 i32.const 132 i32.load i32.store i32.const 12 i32.const 136 i32.load i32.store
            i32.const 36 i32.const 132 i32.load i32.store i32.const 40 i32.const 136 i32.load i32.store
            i32.const 0 i32.const 56 call $draw drop end
            br $again end end"#
        }
    };
    let wat = format!(
        r#"(module(import "ming_v2" "frame_submit"(func $draw(param i32 i32)(result i32)))(import "ming_v2" "event_next"(func $next(param i32 i32)(result i32)))(import "ming_v1" "file_length"(func $len(param i32)(result i32)))(memory(export "memory")1)(data(i32.const 0)"{data}")(func(export "_start")(local $kind i32)i32.const 0 i32.const 56 call $draw drop {body}))"#
    );
    fs::write(app.join("main.wasm"), wat::parse_str(wat).unwrap()).unwrap();
    fs::write(app.join("app.json"),json!({"schemaVersion":1,"id":format!("dev.persistent.{name}"),"version":"0.1.0","sdkVersion":2,"entry":"main.wasm"}).to_string()).unwrap();
}
fn command_output(mut cmd: Command) -> std::process::Output {
    let mut child = cmd
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    use std::io::Read;
    let out = thread::spawn(move || {
        let mut v = Vec::new();
        let mut s = stdout;
        s.read_to_end(&mut v).unwrap();
        v
    });
    let err = thread::spawn(move || {
        let mut v = Vec::new();
        let mut s = stderr;
        s.read_to_end(&mut v).unwrap();
        v
    });
    let deadline = Instant::now() + Duration::from_secs(15);
    let status = loop {
        if let Some(s) = child.try_wait().unwrap() {
            break s;
        }
        if Instant::now() > deadline {
            let _ = child.kill();
            let _ = child.wait();
            panic!("persistent test exceeded external deadline");
        }
        thread::sleep(Duration::from_millis(10));
    };
    std::process::Output {
        status,
        stdout: out.join().unwrap(),
        stderr: err.join().unwrap(),
    }
}
#[test]
fn real_desktop_shutdown_reaps_idle_and_failure_is_isolated_to_one_app() {
    for (failure, expected) in [
        ("idle", ""),
        ("fuel", "FUEL_EXHAUSTED"),
        ("requests", "REQUEST_LIMIT"),
    ] {
        let dir = tempfile::tempdir().unwrap();
        fixture(dir.path(), "healthy", "");
        fixture(dir.path(), "target", failure);
        fs::write(dir.path().join("catalog.json"),json!({"schemaVersion":1,"apps":[{"id":1,"title":"Healthy","manifest":"healthy/app.json"},{"id":2,"title":"Target","manifest":"target/app.json"}],"autostart":[1,2]}).to_string()).unwrap();
        fs::write(
            dir.path().join("events.json"),
            json!([{"afterMs":1800,"action":{"op":"shutdown"}}]).to_string(),
        )
        .unwrap();
        let mut cmd = Command::new(host());
        cmd.arg("desktop")
            .arg(dir.path().join("catalog.json"))
            .args([
                "--backend",
                "offscreen",
                "--renderer",
                "cpu",
                "--width",
                "400",
                "--height",
                "300",
                "--duration-ms",
                "0",
                "--events",
            ])
            .arg(dir.path().join("events.json"));
        let output = command_output(cmd);
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let v: Value = serde_json::from_slice(&output.stdout).unwrap();
        evidence(&format!("desktop-{failure}-result.json"), &v);
        assert_eq!(v["activeWorkersAfterShutdown"], 0, "{v}");
        assert!(v["durationMs"].as_u64().unwrap() >= 1800, "{v}");
        assert_eq!(v["actionsExecuted"], 1);
        let apps = v["instances"].as_array().unwrap();
        assert_eq!(apps.len(), 2, "{v}");
        assert_eq!(apps[0]["status"], "closed", "{v}");
        assert_eq!(apps[0]["completed"], true);
        if expected.is_empty() {
            assert_eq!(apps[1]["status"], "closed", "{v}");
        } else {
            assert_eq!(apps[1]["status"], "failed", "{v}");
            assert!(apps[1]["error"].as_str().unwrap().contains(expected), "{v}");
        }
        assert_ne!(apps[0]["workerPid"], apps[1]["workerPid"]);
        for app in apps {
            assert_worker_reaped(app["workerPid"].as_u64().unwrap() as u32);
        }
    }
}

#[test]
fn clock_reads_nonblocking_waits_and_invalid_wait_arguments_cannot_self_credit() {
    let source = r#"(module
        (import "ming_frame_v1" "clock_monotonic_ns"(func $read(param i32 i32)(result i32)))
        (import "ming_frame_v1" "wait"(func $wait(param i32 i32 i32 i32)(result i32)))
        (memory(export "memory")1)(data(i32.const 0)"\01\00\00\00\02\00\00\00\ff\ff\ff\ff\ff\ff\ff\ff")
        (func(export "_start")i32.const 64 i32.const 8 call $read drop i32.const 0 i32.const 16 i32.const 128 i32.const 48 call $wait drop))"#;
    let mut w = Worker::new(source, 5, 10000, Some(10000));
    for (method, value, payload) in [
        ("frame_clock_read", 8, Some(STANDARD.encode([0u8; 8]))),
        ("frame_clock_wait", 0, None),
    ] {
        let call = w.recv();
        assert_eq!(call["method"], method, "{call}");
        assert!(call.get("remaining_fuel").is_none(), "{call}");
        w.send(json!({"id":call["id"],"value":value,"payload":payload}));
    }
    let result = w.recv();
    assert_eq!(result["type"], "result", "{result}");
    assert_eq!(result["totalGranted"], 10000);
    let invalid = r#"(module(import "ming_v2" "event_next"(func $wait(param i32 i32)(result i32)))(memory(export "memory")1)(func(export "_start")loop $spin i32.const 0 i32.const 0 call $wait drop br $spin end))"#;
    let mut w = Worker::new(invalid, 2, 10000, Some(10000));
    let error = w.recv();
    assert_eq!(error["type"], "error");
    assert!(error.to_string().contains("FUEL_EXHAUSTED"), "{error}");
}
#[test]
fn shutdown_is_not_a_guest_capability() {
    let source = r#"(module(import "ming_v1" "shutdown"(func $shutdown))(func(export "_start")call $shutdown))"#;
    let mut w = Worker::new(source, 2, 10000, Some(10000));
    let error = w.recv();
    assert_eq!(error["type"], "error");
    assert!(error.to_string().contains("IMPORT_DENIED"), "{error}");
}
#[test]
fn persistent_scripts_keep_one_hour_and_128_action_bounds() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("events.json");
    let parse = |duration: &str| {
        DesktopOptions::parse(vec![
            "catalog.json".into(),
            "--duration-ms".into(),
            duration.into(),
            "--events".into(),
            path.clone().into_os_string(),
        ])
    };
    fs::write(
        &path,
        json!([{"afterMs":3600000,"action":{"op":"shutdown"}}]).to_string(),
    )
    .unwrap();
    assert!(parse("0").is_ok());
    assert!(parse("60000").is_err());
    fs::write(
        &path,
        json!([{"afterMs":3600001u64,"action":{"op":"shutdown"}}]).to_string(),
    )
    .unwrap();
    assert!(parse("0").is_err());
    let steps = vec![json!({"afterMs":1,"action":{"op":"shutdown"}}); 129];
    fs::write(&path, serde_json::to_string(&steps).unwrap()).unwrap();
    assert!(parse("0").is_err());
    fs::write(
        &path,
        json!([{"afterMs":1,"action":{"op":"launch","app":0}}]).to_string(),
    )
    .unwrap();
    assert!(parse("0").is_err());
}

fn evidence(name: &str, value: &Value) {
    if let Some(dir) = std::env::var_os("MING_7D_EVIDENCE_DIR") {
        fs::write(PathBuf::from(dir).join(name), serde_json::to_vec_pretty(value).unwrap()).unwrap();
    }
}

fn assert_worker_reaped(pid: u32) {
    assert_ne!(pid, 0);
    #[cfg(windows)] {
        use std::ffi::c_void;
        #[link(name = "kernel32")]
        unsafe extern "system" {
            fn OpenProcess(access: u32, inherit: i32, pid: u32) -> *mut c_void;
            fn WaitForSingleObject(handle: *mut c_void, ms: u32) -> u32;
            fn CloseHandle(handle: *mut c_void) -> i32;
            fn GetLastError() -> u32;
        }
        let handle = unsafe { OpenProcess(0x00100000, 0, pid) };
        if handle.is_null() {
            assert_eq!(unsafe { GetLastError() }, 87, "worker PID check failed");
        } else {
            let state = unsafe { WaitForSingleObject(handle, 0) };
            unsafe { CloseHandle(handle) };
            assert_eq!(state, 0, "worker {pid} remains alive");
        }
    }
    #[cfg(target_os = "linux")]
    assert!(!PathBuf::from(format!("/proc/{pid}")).exists(), "worker {pid} remains alive");
}

#[test]
fn trusted_shutdown_reaps_an_idle_worker_that_never_submits_a_first_frame() {
    let dir = tempfile::tempdir().unwrap();
    let source = r#"(module(import "ming_v2" "event_next"(func $wait(param i32 i32)(result i32)))
        (memory(export "memory")1)(func(export "_start")block $end loop $idle
        i32.const 0 i32.const 16 call $wait drop i32.const 0 i32.load i32.eqz br_if $end br $idle end end))"#;
    fs::write(dir.path().join("main.wasm"), wat::parse_str(source).unwrap()).unwrap();
    fs::write(dir.path().join("app.json"), json!({"schemaVersion":1,"id":"dev.persistent.noframe","version":"0.1.0","sdkVersion":2,"entry":"main.wasm"}).to_string()).unwrap();
    fs::write(dir.path().join("catalog.json"), json!({"schemaVersion":1,"apps":[{"id":1,"title":"Idle","manifest":"app.json"}],"autostart":[1]}).to_string()).unwrap();
    fs::write(dir.path().join("events.json"), json!([
        {"afterMs":0,"action":{"op":"focus","app":1}},
        {"afterMs":250,"action":{"op":"shutdown"}}
    ]).to_string()).unwrap();
    let mut cmd = Command::new(host());
    cmd.arg("desktop").arg(dir.path().join("catalog.json")).args(["--backend","offscreen","--renderer","cpu","--width","400","--height","300","--duration-ms","0","--events"]).arg(dir.path().join("events.json"));
    let output = command_output(cmd);
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let value: Value = serde_json::from_slice(&output.stdout).unwrap();
    evidence("desktop-no-first-frame-result.json", &value);
    assert_eq!(value["activeWorkersAfterShutdown"], 0, "{value}");
    assert_eq!(value["actionsExecuted"], 1, "{value}");
    assert_eq!(value["instances"][0]["status"], "closed", "{value}");
    assert_eq!(value["instances"][0]["framesSubmitted"], 0, "{value}");
    assert!(value["durationMs"].as_u64().unwrap() >= 250, "{value}");
    assert!(value["durationMs"].as_u64().unwrap() < 2000, "{value}");
    assert_worker_reaped(value["instances"][0]["workerPid"].as_u64().unwrap() as u32);
}
