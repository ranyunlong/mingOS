#![cfg(all(feature = "engine", feature = "text"))]
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
    fs::write(dir.path().join("app.json"), json!({"schemaVersion":1,"id":"dev.ming.shadow-test","version":"0.1.0","sdkVersion":sdk,"entry":"main.wasm"}).to_string()).unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_ming-app-host"))
        .args(["run"])
        .arg(dir.path().join("app.json"))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(15);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() > deadline {
            child.kill().unwrap();
            panic!("shadow host timed out");
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
fn success(source: &str) -> Value {
    let (ok, out) = run(source, 4);
    assert!(ok, "{out}");
    serde_json::from_str(&out).unwrap()
}

#[test]
fn shadow_styles_are_sdk4_only_and_signatures_are_exact() {
    let source = r#"(module(import "ming_v4" "shadow_style_create"(func(param i32 i32)(result i32)))(func(export "_start")))"#;
    for sdk in [1, 2, 3] {
        let (ok, out) = run(source, sdk);
        assert!(!ok);
        assert!(out.contains("IMPORT_DENIED"), "{out}");
    }
    let bad = r#"(module(import "ming_v4" "shadow_style_close"(func(param i32 i32)(result i32)))(func(export "_start")))"#;
    let (ok, out) = run(bad, 4);
    assert!(!ok);
    assert!(out.contains("SIGNATURE_DENIED"), "{out}");
}
#[test]
fn immutable_style_quota_close_and_monotonic_handles_round_trip() {
    let source = r#"(module
 (import "ming_v4" "shadow_style_create"(func $create(param i32 i32)(result i32)))
 (import "ming_v4" "shadow_style_close"(func $close(param i32)(result i32)))
 (import "ming_v1" "log"(func $log(param i32 i32)(result i32)))
 (func(export "_start")(local $h i32)(local $last i32)
  i32.const 0 i32.const 0 call $create local.set $h
  i32.const 10 local.get $h i32.const 1073741825 i32.eq call $log drop
  i32.const 11 i32.const 65 i32.const 0 call $create call $log drop
  i32.const 12 i32.const 0 i32.const 17 call $create call $log drop
  i32.const 1 i32.const 1 call $create drop
  i32.const 2 i32.const 2 call $create drop
  i32.const 3 i32.const 3 call $create drop
  i32.const 4 i32.const 4 call $create drop
  i32.const 5 i32.const 5 call $create drop
  i32.const 6 i32.const 6 call $create drop
  i32.const 7 i32.const 7 call $create local.set $last
  i32.const 13 i32.const 64 i32.const 16 call $create call $log drop
  i32.const 14 local.get $h call $close call $log drop
  i32.const 15 i32.const 64 i32.const 16 call $create local.get $last i32.gt_s call $log drop
  i32.const 16 local.get $h call $close call $log drop
  i32.const 17 i32.const 805306369 call $close call $log drop))"#;
    let data = success(source);
    assert_eq!(
        data["events"],
        json!([{"code":10,"value":1},{"code":11,"value":-2},{"code":12,"value":-2},{"code":13,"value":-4},{"code":14,"value":0},{"code":15,"value":1},{"code":16,"value":-2},{"code":17,"value":-2}])
    );
    assert_eq!(data["framesSubmitted"], 0);
}

#[test]
fn real_wasm_shadow_submission_rejects_old_entry_memory_quota_and_closed_style() {
    let bytes: Vec<u8> = [
        0x4d474632i32,
        1,
        32,
        32,
        1,
        0,
        7,
        0x40000001,
        5,
        5,
        20,
        20,
        -1,
        0,
    ]
    .into_iter()
    .flat_map(i32::to_le_bytes)
    .collect();
    let data: String = bytes.iter().map(|b| format!("\\{b:02x}")).collect();
    let many: Vec<u8> = [0x4d474632i32, 1, 32, 32, 9, 0]
        .into_iter()
        .chain([7, 0x40000001, -1000, -1000, 1, 1, 0, 0].repeat(9))
        .flat_map(i32::to_le_bytes)
        .collect();
    let many: String = many.iter().map(|b| format!("\\{b:02x}")).collect();
    let source = format!(
        r#"(module
        (import "ming_v4" "shadow_style_create" (func $create(param i32 i32)(result i32)))
        (import "ming_v4" "shadow_style_close" (func $close(param i32)(result i32)))
        (import "ming_v4" "frame_submit" (func $submit(param i32 i32)(result i32)))
        (import "ming_v3" "frame_submit" (func $old(param i32 i32)(result i32)))
        (import "ming_v1" "log" (func $log(param i32 i32)(result i32)))
        (memory(export "memory") 1)
        (data(i32.const 0) "{data}")(data(i32.const 128) "{many}")
        (func(export "_start")
            i32.const 4 i32.const 2 call $create drop
            i32.const 1 i32.const 0 i32.const 56 call $submit call $log drop
            i32.const 2 i32.const 0 i32.const 56 call $old call $log drop
            i32.const 3 i32.const 65530 i32.const 56 call $submit call $log drop
            i32.const 4 i32.const 128 i32.const 312 call $submit call $log drop
            i32.const 1073741825 call $close drop
            i32.const 5 i32.const 0 i32.const 56 call $submit call $log drop))"#
    );
    let outcome = success(&source);
    assert_eq!(
        outcome["events"],
        json!([{"code":1,"value":0},{"code":2,"value":-2},{"code":3,"value":-2},{"code":4,"value":-4},{"code":5,"value":-2}])
    );
    assert_eq!(outcome["framesSubmitted"], 1);
    assert_eq!(outcome["frame"]["width"], 32);
}

#[test]
fn headless_execution_reports_no_presented_frame() {
    let outcome = success(r#"(module (func (export "_start")))"#);
    assert_eq!(outcome.get("presentedFrame"), Some(&Value::Null));
    assert_eq!(outcome["framesPresented"], 0);
}
