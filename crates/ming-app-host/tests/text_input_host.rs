#![cfg(feature = "engine")]
use serde_json::{Value, json};
use std::{
    fs,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};
fn run(source: &str, sdk: u32) -> (bool, String) {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("main.wasm"),
        wat::parse_str(source).unwrap(),
    )
    .unwrap();
    fs::write(dir.path().join("app.json"),json!({"schemaVersion":1,"id":"dev.ming.input-test","version":"0.1.0","sdkVersion":sdk,"entry":"main.wasm"}).to_string()).unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_ming-app-host"))
        .arg("run")
        .arg(dir.path().join("app.json"))
        .args(["--timeout-ms", "10000", "--fuel", "1000000"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(20);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() > deadline {
            child.kill().unwrap();
            panic!("input Host deadline");
        }
        thread::sleep(Duration::from_millis(5));
    }
    let out = child.wait_with_output().unwrap();
    let ok = out.status.success();
    (
        ok,
        String::from_utf8(if ok { out.stdout } else { out.stderr }).unwrap(),
    )
}
#[test]
fn sdk5_cannot_import_typed_input() {
    let source = r#"(module(import "ming_input_v1" "poll"(func(param i32 i32)(result i32)))(func(export "_start")))"#;
    let (ok, out) = run(source, 5);
    assert!(!ok && out.contains("IMPORT_DENIED"), "{out}");
}
#[test]
fn sdk6_memory_capacity_and_inactive_focus_are_checked_before_io() {
    let source = r#"(module
(import "ming_input_v1" "poll"(func $poll(param i32 i32)(result i32)))
(import "ming_input_v1" "text_focus"(func $focus(param i32 i32)(result i32)))
(import "ming_v1" "log"(func $log(param i32 i32)(result i32)))
(memory(export "memory")1)(data(i32.const 0)"\01\00\00\00")
(func(export "_start")
i32.const 81 i32.const -1 i32.const 568 call $poll call $log drop
i32.const 82 i32.const 64 i32.const 567 call $poll call $log drop
i32.const 83 i32.const 64 i32.const 568 call $poll call $log drop
i32.const 84 i32.const 0 i32.const 24 call $focus call $log drop))"#;
    let (ok, out) = run(source, 6);
    assert!(ok, "{out}");
    let v: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(
        v["events"],
        json!([{"code":81,"value":-2},{"code":82,"value":-2},{"code":83,"value":0},{"code":84,"value":0}])
    );
    assert_eq!(v["requests"], 6);
}
#[test]
fn caret_must_fit_the_calling_app_source_not_only_abi_limits() {
    let bytes: Vec<u8> = [0x4d474632u32, 1, 10, 10, 0, 0]
        .into_iter()
        .flat_map(u32::to_le_bytes)
        .collect();
    let header: String = bytes.iter().map(|b| format!("\\{b:02x}")).collect();
    let source = format!(
        r#"(module
(import "ming_v2" "frame_submit"(func $submit(param i32 i32)(result i32)))
(import "ming_input_v1" "text_focus"(func $focus(param i32 i32)(result i32)))
(import "ming_v1" "log"(func $log(param i32 i32)(result i32)))
(memory(export "memory")1)(data(i32.const 0)"{header}")
(data(i32.const 64)"\01\00\00\00\01\00\00\00\0a\00\00\00\00\00\00\00\01\00\00\00\01\00\00\00")
(func(export "_start")i32.const 0 i32.const 24 call $submit drop
i32.const 86 i32.const 64 i32.const 24 call $focus call $log drop
i32.const 72 i32.const 9 i32.store
i32.const 87 i32.const 64 i32.const 24 call $focus call $log drop))"#
    );
    let (ok, out) = run(&source, 6);
    assert!(ok, "{out}");
    let v: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(
        v["events"],
        json!([{"code":86,"value":-2},{"code":87,"value":0}])
    );
}

#[test]
fn sdk6_still_denies_unknown_methods_shutdown_and_wrong_signatures() {
    for name in ["clipboard_read", "shutdown"] {
        let source = format!(
            r#"(module(import "ming_input_v1" "{name}"(func(param i32 i32)(result i32)))(func(export "_start")))"#
        );
        let (ok, out) = run(&source, 6);
        assert!(!ok && out.contains("IMPORT_DENIED"), "{out}");
    }
    let (ok, out) = run(
        r#"(module(import "ming_input_v1" "poll"(func(param i32)(result i32)))(func(export "_start")))"#,
        6,
    );
    assert!(!ok && out.contains("SIGNATURE_DENIED"), "{out}");
}
