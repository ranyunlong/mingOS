#![cfg(feature = "engine")]
use serde_json::{Value, json};
use std::{
    fs,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};
fn run(source: &str, sdk: u32, events: &str) -> (bool, String) {
    let dir = tempfile::tempdir().unwrap();
    fs::write(
        dir.path().join("main.wasm"),
        wat::parse_str(source).unwrap(),
    )
    .unwrap();
    fs::write(dir.path().join("app.json"),json!({"schemaVersion":1,"id":"dev.ming.frame-test","version":"0.1.0","sdkVersion":sdk,"entry":"main.wasm"}).to_string()).unwrap();
    fs::write(dir.path().join("events.json"), events).unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_ming-app-host"))
        .arg("run")
        .arg(dir.path().join("app.json"))
        .args(["--timeout-ms", "60000", "--fuel", "300000000", "--events"])
        .arg(dir.path().join("events.json"))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let end = Instant::now() + Duration::from_secs(15);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() > end {
            child.kill().unwrap();
            panic!("frame API integration timed out")
        };
        thread::sleep(Duration::from_millis(5));
    }
    let out = child.wait_with_output().unwrap();
    let ok = out.status.success();
    (
        ok,
        String::from_utf8(if ok { out.stdout } else { out.stderr }).unwrap(),
    )
}
fn success(source: &str, events: &str) -> Value {
    let (ok, out) = run(source, 5, events);
    assert!(ok, "{out}");
    serde_json::from_str(&out).unwrap()
}
#[test]
fn namespace_is_sdk5_only_and_exactly_typed() {
    let source = r#"(module(import "ming_frame_v1" "clock_monotonic_ns"(func(param i32 i32)(result i32)))(func(export "_start")))"#;
    for sdk in 1..=4 {
        let (ok, out) = run(source, sdk, "[]");
        assert!(!ok);
        let expected = if sdk >= 3 && !cfg!(feature = "text") {
            "TEXT_FEATURE_REQUIRED"
        } else {
            "IMPORT_DENIED"
        };
        assert!(out.contains(expected), "{out}");
    }
    let bad = r#"(module(import "ming_frame_v1" "wait"(func(param i32 i32)(result i32)))(func(export "_start")))"#;
    let (ok, out) = run(bad, 5, "[]");
    assert!(!ok);
    assert!(out.contains("SIGNATURE_DENIED"), "{out}");
}
#[test]
fn real_clock_timer_frame_sequence_and_pending_memory_contract() {
    let source = r#"(module
 (import "ming_frame_v1" "clock_monotonic_ns"(func $now(param i32 i32)(result i32)))
 (import "ming_frame_v1" "wait"(func $wait(param i32 i32 i32 i32)(result i32)))
 (import "ming_v1" "log"(func $log(param i32 i32)(result i32)))
 (memory(export "memory") 1)
 (func(export "_start")(local $first i64)
 i32.const 1 i32.const 0 i32.const 8 call $now call $log drop
 i32.const 2 i32.const 0 i64.load i64.const 0 i64.ge_s call $log drop
 i32.const 8 i32.const 8 call $now drop
 i32.const 3 i32.const 8 i64.load i32.const 0 i64.load i64.ge_s call $log drop
 i32.const 64 i32.const 1 i32.store i32.const 68 i32.const 0 i32.store i32.const 72 i64.const 0 i64.store
 i32.const 4 i32.const 64 i32.const 16 i32.const 128 i32.const 48 call $wait call $log drop
 i32.const 5 i32.const 132 i32.load call $log drop
 i32.const 6 i32.const 144 i64.load i64.const 0 i64.eq call $log drop
 i32.const 68 i32.const 1 i32.store i32.const 72 i64.const -1 i64.store
 i32.const 7 i32.const 64 i32.const 16 i32.const 128 i32.const 48 call $wait call $log drop
 i32.const 8 i32.const 132 i32.load call $log drop
 i32.const 136 i64.load local.set $first
 i32.const 9 i32.const 144 i64.load i64.const 1 i64.eq call $log drop
 i32.const 10 i32.const 64 i32.const 16 i32.const 128 i32.const 48 call $wait call $log drop
 i32.const 11 i32.const 144 i64.load i64.const 2 i64.eq call $log drop
 i32.const 12 i32.const 136 i64.load local.get $first i64.sub i64.const 16666667 i64.ge_u call $log drop
 i32.const 68 i32.const 2 i32.store i32.const 128 i64.const 1234567 i64.store
 i32.const 13 i32.const 64 i32.const 16 i32.const 128 i32.const 48 call $wait call $log drop
 i32.const 14 i32.const 128 i64.load i64.const 1234567 i64.eq call $log drop))"#;
    let data = success(source, "[]");
    let values = [8, 1, 1, 48, 2, 1, 48, 1, 1, 48, 1, 1, 0, 1];
    assert_eq!(
        data["events"],
        Value::Array(
            values
                .into_iter()
                .enumerate()
                .map(|(i, v)| json!({"code":i+1,"value":v}))
                .collect()
        )
    );
    assert_eq!(data["presentedFrame"], Value::Null);
    assert_eq!(data["framesSubmitted"], 0);
}
#[test]
fn invalid_guest_memory_is_rejected_before_clock_rpc() {
    let source = r#"(module
 (import "ming_frame_v1" "clock_monotonic_ns"(func $now(param i32 i32)(result i32)))
 (import "ming_frame_v1" "wait"(func $wait(param i32 i32 i32 i32)(result i32)))
 (import "ming_v1" "log"(func $log(param i32 i32)(result i32)))
 (memory(export "memory") 1)
 (func(export "_start")
 i32.const 1 i32.const -1 i32.const 8 call $now call $log drop
 i32.const 2 i32.const 65532 i32.const 8 call $now call $log drop
 i32.const 3 i32.const 0 i32.const 7 call $now call $log drop
 i32.const 4 i32.const 0 i32.const 9 call $now call $log drop
 i32.const 5 i32.const -1 i32.const 16 i32.const 128 i32.const 48 call $wait call $log drop
 i32.const 6 i32.const 0 i32.const 15 i32.const 128 i32.const 48 call $wait call $log drop
 i32.const 7 i32.const 0 i32.const 16 i32.const 65520 i32.const 48 call $wait call $log drop
 i32.const 8 i32.const 0 i32.const 16 i32.const 128 i32.const 47 call $wait call $log drop))"#;
    let data = success(source, "[]");
    assert_eq!(data["requests"], 8);
    assert_eq!(
        data["events"],
        Value::Array((1..=8).map(|c| json!({"code":c,"value":-2})).collect())
    );
}
#[test]
fn input_and_close_have_priority_over_frame_and_preserve_legacy_counts() {
    let source = r#"(module(import "ming_frame_v1" "wait"(func $wait(param i32 i32 i32 i32)(result i32)))(import "ming_v1" "log"(func $log(param i32 i32)(result i32)))(memory(export "memory")1)
 (func(export "_start")
 i32.const 0 i32.const 1 i32.store i32.const 4 i32.const 1 i32.store i32.const 8 i64.const -1 i64.store
 i32.const 1 i32.const 0 i32.const 16 i32.const 128 i32.const 48 call $wait call $log drop
 i32.const 2 i32.const 132 i32.load call $log drop
 i32.const 3 i32.const 152 i32.load call $log drop
 i32.const 4 i32.const 156 i32.load call $log drop
 i32.const 5 i32.const 0 i32.const 16 i32.const 128 i32.const 48 call $wait call $log drop
 i32.const 6 i32.const 132 i32.load call $log drop))"#;
    let d = success(source, "[[4,40,120,0],[0,0,0,0]]");
    assert_eq!(
        d["events"],
        json!([{"code":1,"value":48},{"code":2,"value":3},{"code":3,"value":4},{"code":4,"value":40},{"code":5,"value":48},{"code":6,"value":4}])
    );
    assert_eq!(d["inputCounts"][4], 1);
    assert_eq!(d["inputCounts"][0], 1);
}

#[test]
fn malformed_requests_do_not_write_and_valid_ranges_may_overlap_or_be_unaligned() {
    let source = r#"(module
      (import "ming_frame_v1" "wait" (func $wait(param i32 i32 i32 i32)(result i32)))
      (import "ming_v1" "log" (func $log(param i32 i32)(result i32)))
      (memory(export "memory")1)
      (func(export "_start")
       i32.const 128 i64.const 1234567 i64.store
       i32.const 64 i32.const 2 i32.store
       i32.const 1 i32.const 64 i32.const 16 i32.const 128 i32.const 48 call $wait call $log drop
       i32.const 2 i32.const 128 i64.load i64.const 1234567 i64.eq call $log drop
       i32.const 64 i32.const 1 i32.store i32.const 68 i32.const 4 i32.store
       i32.const 3 i32.const 64 i32.const 16 i32.const 128 i32.const 48 call $wait call $log drop
       i32.const 68 i32.const 0 i32.store i32.const 72 i64.const -2 i64.store
       i32.const 4 i32.const 64 i32.const 16 i32.const 128 i32.const 48 call $wait call $log drop
       i32.const 5 i32.const 128 i64.load i64.const 1234567 i64.eq call $log drop
       i32.const 72 i64.const 0 i64.store
       i32.const 6 i32.const 64 i32.const 16 i32.const 64 i32.const 48 call $wait call $log drop
       i32.const 7 i32.const 68 i32.load call $log drop
       i32.const 193 i32.const 1 i32.store i32.const 197 i32.const 0 i32.store i32.const 201 i64.const 0 i64.store
       i32.const 8 i32.const 193 i32.const 16 i32.const 257 i32.const 48 call $wait call $log drop
       i32.const 9 i32.const 261 i32.load call $log drop))"#;
    let d = success(source, "[]");
    let v = [-2, 1, -2, -2, 1, 48, 2, 48, 2];
    assert_eq!(
        d["events"],
        Value::Array(
            v.into_iter()
                .enumerate()
                .map(|(i, v)| json!({"code":i+1,"value":v}))
                .collect()
        )
    );
}
#[cfg(feature = "text")]
#[test]
fn sdk5_inherits_v4_graphics_but_old_submit_stays_v3() {
    let bytes: Vec<u8> = [
        0x4d474632i32,
        1,
        16,
        16,
        1,
        0,
        7,
        0x40000001,
        2,
        2,
        8,
        8,
        -1,
        0,
    ]
    .into_iter()
    .flat_map(i32::to_le_bytes)
    .collect();
    let data: String = bytes.iter().map(|b| format!("\\{b:02x}")).collect();
    let source = format!(
        r#"(module
      (import "ming_v4" "shadow_style_create"(func $create(param i32 i32)(result i32)))
      (import "ming_v3" "frame_submit"(func $old(param i32 i32)(result i32)))
      (import "ming_v4" "frame_submit"(func $new(param i32 i32)(result i32)))
      (import "ming_v1" "log"(func $log(param i32 i32)(result i32)))
      (memory(export "memory")1)(data(i32.const 256)"{data}")
      (func(export "_start")
       i32.const 284 i32.const 2 i32.const 1 call $create i32.store
       i32.const 1 i32.const 256 i32.const 56 call $old call $log drop
       i32.const 2 i32.const 256 i32.const 56 call $new call $log drop))"#
    );
    let d = success(&source, "[]");
    assert_eq!(
        d["events"],
        json!([{"code":1,"value":-2},{"code":2,"value":0}])
    );
    assert_eq!(d["framesSubmitted"], 1);
}

#[test]
fn inherited_input_and_frame_wait_share_fifo_even_after_fairness_yield() {
    let source = r#"(module
      (import "ming_frame_v1" "wait"(func $wait(param i32 i32 i32 i32)(result i32)))
      (import "ming_v2" "event_next"(func $legacy(param i32 i32)(result i32)))
      (import "ming_v1" "log"(func $log(param i32 i32)(result i32)))
      (memory(export "memory")1)
      (func(export "_start")(local $i i32)
       i32.const 0 i32.const 1 i32.store i32.const 4 i32.const 3 i32.store i32.const 8 i64.const -1 i64.store
       (loop $take
         i32.const 0 i32.const 16 i32.const 128 i32.const 48 call $wait drop
         local.get $i i32.const 1 i32.add local.tee $i i32.const 8 i32.lt_s br_if $take)
       i32.const 0 i32.const 16 i32.const 128 i32.const 48 call $wait drop
       i32.const 1 i32.const 132 i32.load call $log drop
       i32.const 256 i32.const 16 call $legacy drop
       i32.const 2 i32.const 260 i32.load call $log drop
       i32.const 0 i32.const 16 i32.const 128 i32.const 48 call $wait drop
       i32.const 3 i32.const 156 i32.load call $log drop))"#;
    let events =
        serde_json::to_string(&(0..10).map(|i| [4, i, 20, 0]).collect::<Vec<_>>()).unwrap();
    let d = success(source, &events);
    assert_eq!(
        d["events"],
        json!([{"code":1,"value":1},{"code":2,"value":8},{"code":3,"value":9}])
    );
}
