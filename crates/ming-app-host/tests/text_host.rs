#![cfg(all(feature = "engine", feature = "text"))]
use serde_json::{Value, json};
use std::{
    fs,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};
fn package(source: &str, sdk: u32) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("main.wasm"),
        wat::parse_str(source).unwrap(),
    )
    .unwrap();
    fs::write(dir.path().join("app.json"),json!({"schemaVersion":1,"id":"dev.ming.text-test","version":"0.1.0","sdkVersion":sdk,"entry":"main.wasm"}).to_string()).unwrap();
    dir
}
fn run(source: &str, sdk: u32) -> (bool, String) {
    let dir = package(source, sdk);
    let mut child = Command::new(env!("CARGO_BIN_EXE_ming-app-host"))
        .arg("run")
        .arg(dir.path().join("app.json"))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let end = Instant::now() + Duration::from_secs(15);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() > end {
            child.kill().unwrap();
            panic!("text host timed out");
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
#[test]
fn sdk3_layout_metrics_and_close_round_trip_without_wasi_or_paths() {
    let source = r#"(module
  (import "ming_v3" "text_layout_create"(func $create(param i32 i32 i32 i32 i32)(result i32)))
  (import "ming_v3" "text_layout_metrics"(func $metrics(param i32 i32 i32)(result i32)))
  (import "ming_v3" "text_layout_close"(func $close(param i32)(result i32)))
  (import "ming_v1" "log"(func $log(param i32 i32)(result i32)))
  (memory(export "memory")1)(data(i32.const 0)"MingOS 中文")
  (func(export "_start")(local $h i32)
   i32.const 0 i32.const 13 i32.const 1 i32.const 1280 i32.const 0 call $create local.set $h
   i32.const 10 local.get $h i32.const 0 i32.gt_s call $log drop
   i32.const 11 local.get $h i32.const 128 i32.const 48 call $metrics call $log drop
   i32.const 12 i32.const 128 i32.load i32.const 0 i32.gt_s call $log drop
   i32.const 13 local.get $h call $close call $log drop
   i32.const 14 local.get $h i32.const 128 i32.const 48 call $metrics call $log drop))"#;
    let (ok, out) = run(source, 3);
    assert!(ok, "{out}");
    let data: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(
        data["events"],
        json!([{"code":10,"value":1},{"code":11,"value":48},{"code":12,"value":1},{"code":13,"value":0},{"code":14,"value":-2}])
    );
}
#[test]
fn sdk2_cannot_import_text_and_sdk3_enforces_exact_signature() {
    let source = r#"(module(import "ming_v3" "text_layout_close"(func(param i32)(result i32)))(func(export "_start")))"#;
    let (ok, out) = run(source, 2);
    assert!(!ok);
    assert!(out.contains("IMPORT_DENIED"), "{out}");
    let source = r#"(module(import "ming_v3" "text_layout_create"(func(param i32)(result i32)))(func(export "_start")))"#;
    let (ok, out) = run(source, 3);
    assert!(!ok);
    assert!(out.contains("SIGNATURE_DENIED"), "{out}");
}
#[test]
fn text_guest_ranges_fail_before_rpc_and_invalid_utf8_is_rejected() {
    let source = r#"(module
 (import "ming_v3" "text_layout_create"(func $create(param i32 i32 i32 i32 i32)(result i32)))
 (import "ming_v3" "text_layout_metrics"(func $metrics(param i32 i32 i32)(result i32)))
 (import "ming_v1" "log"(func $log(param i32 i32)(result i32)))
 (memory(export "memory")1)(data(i32.const 0)"\ff")
 (func(export "_start")
  i32.const 20 i32.const -1 i32.const 1 i32.const 1 i32.const 1280 i32.const 0 call $create call $log drop
  i32.const 21 i32.const 0 i32.const 513 i32.const 1 i32.const 1280 i32.const 0 call $create call $log drop
  i32.const 22 i32.const 1 i32.const 65530 i32.const 48 call $metrics call $log drop
  i32.const 23 i32.const 0 i32.const 1 i32.const 1 i32.const 1280 i32.const 0 call $create call $log drop))"#;
    let (ok, out) = run(source, 3);
    assert!(ok, "{out}");
    let data: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(
        data["events"],
        json!([{"code":20,"value":-2},{"code":21,"value":-2},{"code":22,"value":-2},{"code":23,"value":-2}])
    );
    assert_eq!(
        data["requests"], 5,
        "only invalid UTF8 and four logs reach broker"
    );
}
