#![cfg(all(feature = "engine", feature = "desktop"))]
use ming_app_host::{
    desktop_catalog::load_catalog,
    protocol::{Init, Reply},
};
use serde_json::{Value, json};
use std::{
    fs,
    path::Path,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};
fn package(root: &Path, name: &str, source: &str, sdk: u32) -> std::path::PathBuf {
    let dir = root.join(name);
    fs::create_dir(&dir).unwrap();
    fs::write(dir.join("main.wasm"), wat::parse_str(source).unwrap()).unwrap();
    let manifest = dir.join("app.json");
    fs::write(&manifest,json!({"schemaVersion":1,"id":"dev.ming.data-test","version":"0.1.0","sdkVersion":sdk,"entry":"main.wasm"}).to_string()).unwrap();
    manifest
}
fn run(args: Vec<std::ffi::OsString>) -> (bool, String) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_ming-app-host"))
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(25);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() > deadline {
            let _ = child.kill();
            let _ = child.wait();
            panic!("data host deadline");
        }
        thread::sleep(Duration::from_millis(5));
    }
    let output = child.wait_with_output().unwrap();
    let ok = output.status.success();
    (
        ok,
        String::from_utf8(if ok { output.stdout } else { output.stderr }).unwrap(),
    )
}
const IMPORTS: &str = r#"(import "ming_data_v1" "read" (func $read(param i32 i32 i32 i32)(result i32)))
(import "ming_data_v1" "write" (func $write(param i32 i32 i32)(result i32)))
(import "ming_data_v1" "size" (func $size(param i32)(result i32)))
(import "ming_data_v1" "delete" (func $delete(param i32)(result i32)))
(import "ming_v1" "log" (func $log(param i32 i32)(result i32)))"#;
#[test]
fn missing_grant_is_denied_and_invalid_guest_arguments_make_zero_rpc() {
    let d = tempfile::tempdir().unwrap();
    let wat = format!(
        r#"(module {IMPORTS}(memory(export "memory")1)
 (func(export "_start")
 i32.const 1 i32.const 1 call $size call $log drop
 i32.const 2 i32.const 1 i32.const 0 i32.const 0 i32.const 4 call $read call $log drop
 i32.const 3 i32.const 1 i32.const 0 i32.const 3 call $write call $log drop
 i32.const 4 i32.const 1 call $delete call $log drop
 i32.const 5 i32.const 0 call $size call $log drop
 i32.const 6 i32.const 9 call $delete call $log drop
 i32.const 7 i32.const 1 i32.const 0 i32.const 65535 i32.const 2 call $read call $log drop
 i32.const 8 i32.const 1 i32.const -1 i32.const 1 call $write call $log drop
 i32.const 9 i32.const 1 i32.const -1 i32.const 0 i32.const 1 call $read call $log drop
 i32.const 10 i32.const 1 i32.const 0 i32.const 65537 call $write call $log drop))"#
    );
    let p = package(d.path(), "app", &wat, 6);
    let (ok, text) = run(vec!["run".into(), p.into_os_string()]);
    assert!(ok, "{text}");
    let v: Value = serde_json::from_str(&text).unwrap();
    assert_eq!(v["requests"], 14);
    for i in 0..10 {
        assert_eq!(v["events"][i]["value"], if i < 4 { -1 } else { -2 });
    }
}
#[test]
fn sdk5_and_bad_signatures_and_unknown_data_imports_are_denied() {
    let d = tempfile::tempdir().unwrap();
    for (n, sdk, import, error) in [
        (
            "old",
            5,
            r#"(import "ming_data_v1" "size" (func(param i32)(result i32)))"#,
            "IMPORT_DENIED",
        ),
        (
            "sig",
            6,
            r#"(import "ming_data_v1" "write" (func(param i32 i32)(result i32)))"#,
            "SIGNATURE_DENIED",
        ),
        (
            "unknown",
            6,
            r#"(import "ming_data_v1" "open" (func(param i32)(result i32)))"#,
            "IMPORT_DENIED",
        ),
    ] {
        let p = package(
            d.path(),
            n,
            &format!("(module {import}(func(export \"_start\")))").replace("\\\"", "\""),
            sdk,
        );
        let (ok, text) = run(vec!["run".into(), p.into_os_string()]);
        assert!(!ok);
        assert!(text.contains(error), "{text}");
    }
}
fn catalog(root: &Path, apps: Value, autostart: Value) -> std::path::PathBuf {
    let p = root.join("catalog.json");
    fs::write(
        &p,
        json!({"schemaVersion":1,"apps":apps,"autostart":autostart}).to_string(),
    )
    .unwrap();
    p
}
#[test]
fn trusted_catalog_data_fields_are_strict_and_cannot_alias() {
    let d = tempfile::tempdir().unwrap();
    package(
        d.path(),
        "app",
        "(module(func(export \"_start\")))"
            .replace("\\\"", "\"")
            .as_str(),
        6,
    );
    let entry =
        json!({"id":1,"title":"A","manifest":"app/app.json","dataKey":"catalog-A","slots":[1,8]});
    let p = catalog(d.path(), json!([entry]), json!([]));
    assert!(load_catalog(&p).is_ok());
    for patch in [
        json!({"dataKey":"../escape","slots":[1]}),
        json!({"dataKey":"A","slots":[0]}),
        json!({"dataKey":"A","slots":[9]}),
        json!({"dataKey":"A","slots":[1,1]}),
        json!({"dataKey":"A","slots":[]}),
        json!({"dataKey":"A"}),
        json!({"slots":[1]}),
    ] {
        let mut e = json!({"id":1,"title":"A","manifest":"app/app.json"});
        for (k, v) in patch.as_object().unwrap() {
            e[k] = v.clone();
        }
        assert!(
            load_catalog(&catalog(d.path(), json!([e]), json!([]))).is_err(),
            "{patch}"
        );
    }
    let apps = json!([{"id":1,"title":"A","manifest":"app/app.json","dataKey":"same","slots":[1]},{"id":2,"title":"B","manifest":"app/app.json","dataKey":"same","slots":[2]}]);
    assert!(load_catalog(&catalog(d.path(), apps, json!([]))).is_err());
}
#[test]
fn desktop_data_root_must_be_prepared_absolute_and_private() {
    use ming_app_host::desktop_catalog::DesktopOptions;
    let d = tempfile::tempdir().unwrap();
    assert!(
        DesktopOptions::parse([
            "x".into(),
            "--data-root".into(),
            d.path().as_os_str().to_owned()
        ])
        .is_ok()
    );
    for p in [
        std::path::PathBuf::from("relative-root"),
        d.path().join("missing"),
    ] {
        assert!(
            DesktopOptions::parse(["x".into(), "--data-root".into(), p.into_os_string()]).is_err()
        );
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(d.path(), fs::Permissions::from_mode(0o777)).unwrap();
        assert!(
            DesktopOptions::parse([
                "x".into(),
                "--data-root".into(),
                d.path().as_os_str().to_owned()
            ])
            .is_err()
        );
    }
}
#[test]
fn bounded_wire_is_byte_identical() {
    let init: Init =
        serde_json::from_value(json!({"wasm":"AA==","fuel":7,"memoryBytes":65536,"sdkVersion":6}))
            .unwrap();
    assert_eq!(
        serde_json::to_value(init).unwrap(),
        json!({"wasm":"AA==","fuel":7,"memoryBytes":65536,"sdkVersion":6})
    );
    let reply: Reply = serde_json::from_str(r#"{"id":1,"value":0}"#).unwrap();
    assert_eq!(
        serde_json::to_string(&reply).unwrap(),
        r#"{"id":1,"value":0}"#
    );
}

fn desktop_guest(marker: u8, length: usize) -> String {
    let words = [0x4d474632u32, 1, 1, 1, 0, 0];
    let frame: String = words
        .into_iter()
        .flat_map(u32::to_le_bytes)
        .map(|b| format!("\\{b:02x}"))
        .collect();
    format!(
        r#"(module {IMPORTS}
 (import "ming_v2" "frame_submit" (func $draw(param i32 i32)(result i32)))
 (import "ming_v2" "event_next" (func $next(param i32 i32)(result i32)))
 (memory(export "memory")3)(data(i32.const 65536)"{frame}")
 (func(export "_start")
 i32.const 0 i32.const {marker} i32.const {length} memory.fill
 i32.const 65536 i32.const 24 call $draw drop
 i32.const 10 i32.const 1 call $size call $log drop
 i32.const 11 i32.const 1 i32.const 0 i32.const {length} call $write call $log drop
 i32.const 12 i32.const 1 i32.const 0 i32.const 131072 i32.const 65536 call $read call $log drop
 i32.const 131072 i32.load8_u i32.const {marker} i32.ne if unreachable end
 i32.const 13 i32.const 8 call $size call $log drop
 i32.const 14 i32.const 2 call $delete call $log drop
 i32.const 15 i32.const 2 i32.const 0 i32.const 0 call $write call $log drop
 i32.const 16 i32.const 2 call $size call $log drop
 i32.const 17 i32.const 2 call $delete call $log drop
 i32.const 18 i32.const 1 i32.const {length} i32.const 131072 i32.const 1 call $read call $log drop
 block $end loop $wait
 i32.const 65664 i32.const 16 call $next drop
 i32.const 65664 i32.load i32.eqz br_if $end
 i32.const 65664 i32.load i32.const 1 i32.eq if
 i32.const 65544 i32.const 65668 i32.load i32.store
 i32.const 65548 i32.const 65672 i32.load i32.store
 i32.const 65536 i32.const 24 call $draw drop
 end
 br $wait end end
 ;; Cleanup is allowed after Close and before the Parent's reap/revoke boundary.
 i32.const 99 i32.const 1 i32.const 0 i32.const {length} call $write call $log drop))"#
    )
}
#[test]
fn parent_grants_bulk_isolation_cleanup_receipts_and_generation_reopen() {
    let d = tempfile::tempdir().unwrap();
    package(d.path(), "a", &desktop_guest(65, 65536), 6);
    package(d.path(), "b", &desktop_guest(66, 3), 6);
    let data = d.path().join("data");
    fs::create_dir(&data).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&data, fs::Permissions::from_mode(0o700)).unwrap();
    }
    let cat = catalog(
        d.path(),
        json!([{"id":1,"title":"A","manifest":"a/app.json","dataKey":"catalog-A","slots":[1,2]},{"id":2,"title":"B","manifest":"b/app.json","dataKey":"catalog-B","slots":[1,2]}]),
        json!([1, 2]),
    );
    let events = d.path().join("events.json");
    fs::write(&events,json!([{"afterMs":250,"action":{"op":"close","app":1}},{"afterMs":650,"action":{"op":"launch","app":1}},{"afterMs":1800,"action":{"op":"shutdown"}}]).to_string()).unwrap();
    let state = d.path().join("state.json");
    let (ok, text) = run(vec![
        "desktop".into(),
        cat.into_os_string(),
        "--backend".into(),
        "offscreen".into(),
        "--renderer".into(),
        "cpu".into(),
        "--duration-ms".into(),
        "0".into(),
        "--data-root".into(),
        data.clone().into_os_string(),
        "--events".into(),
        events.into_os_string(),
        "--state-file".into(),
        state.clone().into_os_string(),
    ]);
    assert!(ok, "{text}");
    let v: Value = serde_json::from_str(&text).unwrap();
    let records = v["instances"].as_array().unwrap();
    assert_eq!(records.len(), 3, "{text}");
    let a: Vec<_> = records.iter().filter(|r| r["app"] == 1).collect();
    assert_eq!(a.len(), 2);
    for (index, r) in a.iter().enumerate() {
        assert_eq!(r["completed"], true, "{r}");
        assert!(r["fuelConsumed"].as_u64().is_some_and(|n| n > 0));
        assert!(r["frameClock"].is_object());
        assert_eq!(r["status"], "closed", "{r}");
        let es = r["events"].as_array().unwrap();
        let value = |code| {
            es.iter().find(|e| e["code"] == code).unwrap()["value"]
                .as_i64()
                .unwrap()
        };
        assert_eq!(value(10), if index == 0 { -3 } else { 65536 });
        assert_eq!(value(11), 65536);
        assert_eq!(value(12), 65536);
        assert_eq!(value(13), -1);
        assert_eq!(value(14), -3);
        assert_eq!(value(15), 0);
        assert_eq!(value(16), 0);
        assert_eq!(value(17), 0);
        assert_eq!(value(18), 0);
        assert_eq!(value(99), 65536);
        assert_eq!(r["appData"]["writes"], 3);
        assert_eq!(r["appData"]["deletes"], 1);
        #[cfg(windows)]
        {
            assert_eq!(r["appData"]["directorySyncUnconfirmed"], 4);
            assert_eq!(r["appData"]["lastReceipt"]["directorySynced"], false);
        }
    }
    assert_eq!(v["activeWorkersAfterShutdown"], 0);
    let root = ming_app_host::app_data::AppDataRoot::open(&data).unwrap();
    assert_eq!(
        root.grant("catalog-A", &[1])
            .unwrap()
            .read(1, 0, 65536)
            .unwrap(),
        vec![65; 65536]
    );
    assert_eq!(
        root.grant("catalog-B", &[1])
            .unwrap()
            .read(1, 0, 3)
            .unwrap(),
        vec![66; 3]
    );
    let state: Value = serde_json::from_slice(&fs::read(state).unwrap()).unwrap();
    assert_eq!(state["phase"], "stopped");
    assert!(
        state["apps"]
            .as_array()
            .unwrap()
            .iter()
            .all(|a| a["appData"]["lastReceipt"].is_object())
    );
}
#[test]
fn parent_root_retains_authority_while_revoked_old_generation_stays_denied() {
    use ming_app_host::app_data::{AppDataError, AppDataRoot};
    let d = tempfile::tempdir().unwrap();
    let root = AppDataRoot::open(d.path()).unwrap();
    let first = root.grant("parent-assigned", &[1]).unwrap();
    let old = first.clone();
    first.write(1, b"persistent").unwrap();
    first.revoke();
    drop(first);
    assert_eq!(old.read(1, 0, 1), Err(AppDataError::Denied));
    assert!(matches!(old.write(1, b"bad"), Err(AppDataError::Denied)));
    let next = root.clone().grant("parent-assigned", &[1]).unwrap();
    assert_eq!(next.read(1, 0, 65536).unwrap(), b"persistent");
    assert_eq!(
        root.grant("other-key", &[1]).unwrap().size(1),
        Err(AppDataError::NotFound)
    );
}

#[test]
fn desktop_missing_root_or_missing_catalog_grant_is_denied() {
    for root_enabled in [false, true] {
        let d = tempfile::tempdir().unwrap();
        let source = format!(
            r#"(module {IMPORTS}(memory(export "memory")1)(func(export "_start")
 i32.const 1 i32.const 1 call $size call $log drop
 i32.const 2 i32.const 1 i32.const 0 i32.const 0 i32.const 4 call $read call $log drop
 i32.const 3 i32.const 1 i32.const 0 i32.const 0 call $write call $log drop
 i32.const 4 i32.const 1 call $delete call $log drop))"#
        );
        package(d.path(), "a", &source, 6);
        let mut entry = json!({"id":1,"title":"A","manifest":"a/app.json"});
        if !root_enabled {
            entry["dataKey"] = json!("declared-but-no-root");
            entry["slots"] = json!([1]);
        }
        let cat = catalog(d.path(), json!([entry]), json!([1]));
        let ev = d.path().join("events.json");
        fs::write(&ev, r#"[{"afterMs":400,"action":{"op":"shutdown"}}]"#).unwrap();
        let mut args = vec![
            "desktop".into(),
            cat.into_os_string(),
            "--backend".into(),
            "offscreen".into(),
            "--renderer".into(),
            "cpu".into(),
            "--duration-ms".into(),
            "0".into(),
            "--events".into(),
            ev.into_os_string(),
        ];
        if root_enabled {
            args.extend(["--data-root".into(), d.path().as_os_str().to_owned()]);
        }
        let (ok, text) = run(args);
        assert!(ok, "{text}");
        let v: Value = serde_json::from_str(&text).unwrap();
        let record = &v["instances"][0];
        assert_eq!(record["status"], "closed", "{text}");
        for e in record["events"].as_array().unwrap() {
            assert_eq!(e["value"], -1);
        }
    }
}
#[test]
fn ordinary_data_rpc_never_reports_remaining_fuel_and_rejects_unsolicited_credit() {
    use base64::{Engine as _, engine::general_purpose::STANDARD};
    use std::{io::BufReader, sync::mpsc};
    let wasm=wat::parse_str(r#"(module(import "ming_data_v1" "size"(func $size(param i32)(result i32)))(func(export "_start")i32.const 1 call $size drop))"#).unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_ming-app-host"))
        .arg("__worker")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let mut input = child.stdin.take().unwrap();
    let mut output = BufReader::new(child.stdout.take().unwrap());
    let (tx, rx) = mpsc::channel();
    let reader = thread::spawn(move || {
        while let Ok(Some(v)) = ming_app_host::protocol::read_json::<Value>(&mut output, 100000) {
            if tx.send(v).is_err() {
                break;
            }
        }
    });
    ming_app_host::protocol::write_json(&mut input,&json!({"wasm":STANDARD.encode(wasm),"fuel":10000,"memoryBytes":65536,"sdkVersion":6,"fuelCapacity":10000})).unwrap();
    assert_eq!(
        rx.recv_timeout(Duration::from_secs(5)).unwrap()["type"],
        "ready"
    );
    let call = rx.recv_timeout(Duration::from_secs(5)).unwrap();
    assert_eq!(call["method"], "app_data_size");
    assert!(call.get("remaining_fuel").is_none());
    assert!(call.get("payload").is_none());
    ming_app_host::protocol::write_json(
        &mut input,
        &json!({"id":call["id"],"value":-1,"fuel_grant":0}),
    )
    .unwrap();
    let error = rx.recv_timeout(Duration::from_secs(5)).unwrap();
    assert_eq!(error["type"], "error");
    assert!(
        error.to_string().contains("PROTOCOL_UNEXPECTED_CREDIT"),
        "{error}"
    );
    drop(input);
    let deadline = Instant::now() + Duration::from_secs(5);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() > deadline {
            let _ = child.kill();
            let _ = child.wait();
            panic!("worker not reaped");
        }
        thread::sleep(Duration::from_millis(2));
    }
    reader.join().unwrap();
}
