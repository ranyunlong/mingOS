#![cfg(all(feature = "engine", feature = "text"))]
use serde_json::{Value, json};
use std::{
    fs,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

fn frame(commands: &[[i32; 8]]) -> Vec<u8> {
    [0x4d474632, 1, 64, 64, commands.len() as i32, 0]
        .into_iter()
        .chain(commands.iter().flatten().copied())
        .flat_map(i32::to_le_bytes)
        .collect()
}
fn escaped(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("\\{b:02x}")).collect()
}
fn run(source: &str, sdk: u32) -> (bool, String) {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("main.wasm"),
        wat::parse_str(source).unwrap(),
    )
    .unwrap();
    fs::write(dir.path().join("app.json"), json!({"schemaVersion":1,"id":"dev.ming.aa-test","version":"0.1.0","sdkVersion":sdk,"entry":"main.wasm"}).to_string()).unwrap();
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
            panic!("AA host timed out");
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
fn sdk4_aa_requires_the_new_submit_namespace_and_exact_signature() {
    let import = r#"(module(import "ming_v4" "frame_submit"(func(param i32 i32)(result i32)))(func(export "_start")))"#;
    for sdk in [1, 2, 3] {
        let (ok, out) = run(import, sdk);
        assert!(!ok);
        assert!(out.contains("IMPORT_DENIED"), "{out}");
    }
    let bad = r#"(module(import "ming_v4" "frame_submit"(func(param i32)(result i32)))(func(export "_start")))"#;
    let (ok, out) = run(bad, 4);
    assert!(!ok);
    assert!(out.contains("SIGNATURE_DENIED"), "{out}");
    let bytes = frame(&[[6, 2, 2, 60, 60, -1, 12, 0]]);
    let source = format!(
        r#"(module
       (import "ming_v4" "frame_submit"(func $new(param i32 i32)(result i32)))
       (import "ming_v3" "frame_submit"(func $old3(param i32 i32)(result i32)))
       (import "ming_v2" "frame_submit"(func $old2(param i32 i32)(result i32)))
       (import "ming_v1" "log"(func $log(param i32 i32)(result i32)))
       (memory(export "memory")1)(data(i32.const 0)"{}")
       (func(export "_start")
        i32.const 1 i32.const 0 i32.const {len} call $old3 call $log drop
        i32.const 2 i32.const 0 i32.const {len} call $old2 call $log drop
        i32.const 3 i32.const 0 i32.const {len} call $new call $log drop))"#,
        escaped(&bytes),
        len = bytes.len()
    );
    let data = success(&source);
    assert_eq!(
        data["events"],
        json!([{"code":1,"value":-2},{"code":2,"value":-2},{"code":3,"value":0}])
    );
    assert_eq!(data["framesSubmitted"], 1);
}
#[test]
fn invalid_guest_aa_ranges_stay_local_and_quota_rejection_is_recoverable() {
    let valid = frame(&[[6, 2, 2, 60, 60, -1, 12, 0]]);
    let excess = frame(&[[6, 0, 0, 1, 1, -1, 0, 0]; 17]);
    let source = format!(
        r#"(module
       (import "ming_v4" "frame_submit"(func $submit(param i32 i32)(result i32)))
       (import "ming_v1" "log"(func $log(param i32 i32)(result i32)))
       (memory(export "memory")1)(data(i32.const 0)"{}")
       (data(i32.const 1024)"{}")
       (func(export "_start")
        i32.const 10 i32.const -1 i32.const {len} call $submit call $log drop
        i32.const 11 i32.const 65530 i32.const {len} call $submit call $log drop
        i32.const 12 i32.const 0 i32.const 65537 call $submit call $log drop
        i32.const 13 i32.const 0 i32.const {len} call $submit call $log drop
        i32.const 14 i32.const 1024 i32.const {excess_len} call $submit call $log drop
        i32.const 15 i32.const 0 i32.const {len} call $submit call $log drop))"#,
        escaped(&valid),
        escaped(&excess),
        len = valid.len(),
        excess_len = excess.len()
    );
    let data = success(&source);
    assert_eq!(
        data["events"],
        json!([{"code":10,"value":-2},{"code":11,"value":-2},{"code":12,"value":-2},{"code":13,"value":0},{"code":14,"value":-4},{"code":15,"value":0}])
    );
    assert_eq!(
        data["requests"], 9,
        "three invalid copies must not cross RPC"
    );
    assert_eq!(data["framesSubmitted"], 2);
}
#[test]
fn sdk4_inherits_layout_lifetime_and_draws_text_with_aa() {
    let bytes = frame(&[
        [6, 1, 1, 62, 62, -1, 10, 0],
        [5, 0, 8 * 64, 32 * 64, 0xff20354fu32 as i32, 0, 0, 0],
    ]);
    let source = format!(
        r#"(module
       (import "ming_v4" "frame_submit"(func $submit(param i32 i32)(result i32)))
       (import "ming_v3" "text_layout_create"(func $create(param i32 i32 i32 i32 i32)(result i32)))
       (import "ming_v3" "text_layout_metrics"(func $metrics(param i32 i32 i32)(result i32)))
       (import "ming_v3" "text_layout_close"(func $close(param i32)(result i32)))
       (import "ming_v1" "log"(func $log(param i32 i32)(result i32)))
       (memory(export "memory")1)(data(i32.const 0)"{}")
       (data(i32.const 4096)"\e4\b8\ad")
       (func(export "_start")(local $h i32)
        i32.const 4096 i32.const 3 i32.const 1 i32.const 1024 i32.const 0 call $create local.set $h
        i32.const 20 local.get $h i32.const 0 i32.gt_s call $log drop
        i32.const 60 local.get $h i32.store
        i32.const 21 local.get $h i32.const 8192 i32.const 48 call $metrics call $log drop
        i32.const 22 i32.const 0 i32.const {len} call $submit call $log drop
        i32.const 23 local.get $h call $close call $log drop
        i32.const 24 i32.const 0 i32.const {len} call $submit call $log drop))"#,
        escaped(&bytes),
        len = bytes.len()
    );
    let data = success(&source);
    assert_eq!(
        data["events"],
        json!([{"code":20,"value":1},{"code":21,"value":48},{"code":22,"value":0},{"code":23,"value":0},{"code":24,"value":-2}])
    );
    assert_eq!(data["framesSubmitted"], 1);
    assert!(data["frame"]["textPrepareMicros"].is_number());
}
