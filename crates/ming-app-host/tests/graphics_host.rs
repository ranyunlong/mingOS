#![cfg(feature = "engine")]
use serde_json::{Value, json};
use std::{
    fs,
    path::Path,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};
fn package(source: &str, sdk: u32) -> tempfile::TempDir {
    let d = tempfile::tempdir().unwrap();
    fs::write(d.path().join("main.wasm"), wat::parse_str(source).unwrap()).unwrap();
    fs::write(d.path().join("app.json"),json!({"schemaVersion":1,"id":"dev.ming.graphics-test","version":"0.1.0","sdkVersion":sdk,"entry":"main.wasm"}).to_string()).unwrap();
    d
}
fn run(manifest: &Path, args: &[&str]) -> (bool, String) {
    let mut c = Command::new(env!("CARGO_BIN_EXE_ming-app-host"))
        .arg("run")
        .arg(manifest)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let end = Instant::now() + Duration::from_secs(15);
    while c.try_wait().unwrap().is_none() {
        if Instant::now() > end {
            c.kill().unwrap();
            panic!("graphics host hung");
        }
        thread::sleep(Duration::from_millis(5));
    }
    let o = c.wait_with_output().unwrap();
    (
        o.status.success(),
        String::from_utf8(if o.status.success() {
            o.stdout
        } else {
            o.stderr
        })
        .unwrap(),
    )
}
#[test]
fn v1_cannot_import_v2_and_v2_checks_signature() {
    let source = r#"(module(import "ming_v2" "frame_submit"(func(param i32 i32)(result i32)))(func(export "_start")))"#;
    let d = package(source, 1);
    let (ok, out) = run(&d.path().join("app.json"), &[]);
    assert!(!ok);
    assert!(out.contains("IMPORT_DENIED"), "{out}");
    let d = package(
        r#"(module(import "ming_v2" "file_read"(func(param i32)(result i32)))(func(export "_start")))"#,
        2,
    );
    let (ok, out) = run(&d.path().join("app.json"), &[]);
    assert!(!ok);
    assert!(out.contains("SIGNATURE_DENIED"), "{out}");
}
#[test]
fn invalid_guest_memory_is_rejected_before_rpc() {
    for ptr in [-1, 65530] {
        let source = format!(
            r#"(module(import "ming_v2" "file_read"(func $read(param i32 i32 i32 i32)(result i32)))(import "ming_v1" "log"(func $log(param i32 i32)(result i32)))(memory(export "memory")1)(func(export "_start")i32.const 11 i32.const 1 i32.const 0 i32.const {ptr} i32.const 64 call $read call $log drop))"#
        );
        let d = package(&source, 2);
        let (ok, out) = run(&d.path().join("app.json"), &[]);
        assert!(ok, "{out}");
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["events"], json!([{"code":11,"value":-2}]));
        assert_eq!(v["requests"], 1);
    }
}
#[test]
fn a_64k_read_uses_one_bulk_rpc_and_writes_guest_memory() {
    let d = package(
        r#"(module(import "ming_v1" "file_open"(func $open(param i32)(result i32)))(import "ming_v1" "log"(func $log(param i32 i32)(result i32)))(import "ming_v2" "file_read"(func $read(param i32 i32 i32 i32)(result i32)))(memory(export "memory")1)(func(export "_start")(local $h i32)i32.const 1 call $open local.set $h i32.const 20 local.get $h i32.const 0 i32.const 0 i32.const 65536 call $read call $log drop i32.const 21 i32.const 65535 i32.load8_u call $log drop))"#,
        2,
    );
    let p = d.path().join("bytes");
    fs::write(&p, vec![77; 70000]).unwrap();
    let grant = format!("1={}", p.display());
    let (ok, out) = run(&d.path().join("app.json"), &["--grant", &grant]);
    assert!(ok, "{out}");
    let v: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["requests"], 4);
    assert_eq!(v["bulkBytesRead"], 65536);
    assert_eq!(
        v["events"],
        json!([{"code":20,"value":65536},{"code":21,"value":77}])
    );
}
#[test]
fn real_kotlin_viewer_renders_and_handles_scripted_input() {
    // The real Kotlin artifact is a functional integration fixture; its diagnostic timeout is
    // intentionally wider than the host's 5s default and is not a startup-performance claim.
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let manifest = root.join("artifacts/image-viewer/app.json");
    assert!(manifest.is_file(), "Build real Kotlin viewer first");
    let fixture = root.join("samples/image-viewer/data/coastal.png");
    let d = tempfile::tempdir().unwrap();
    let image = d.path().join("view.png");
    let script = d.path().join("events.json");
    fs::write(
        &script,
        "[[1,1000,700,0],[2,200,0,0],[4,500,400,1],[3,50,-25,0],[0,0,0,0]]",
    )
    .unwrap();
    let grant = format!("1={}", fixture.display());
    let (ok, out) = run(
        &manifest,
        &[
            "--grant",
            &grant,
            "--events",
            script.to_str().unwrap(),
            "--snapshot",
            image.to_str().unwrap(),
            "--timeout-ms",
            "20000",
        ],
    );
    assert!(ok, "{out}");
    let v: Value = serde_json::from_str(&out).unwrap();
    assert!(v["framesSubmitted"].as_u64().unwrap() >= 4);
    assert_eq!(v["framesPresented"], 0);
    assert_eq!(v["windowSystem"], "headless");
    assert!(v["bulkBytesRead"].as_u64().unwrap() >= 8);
    assert_eq!(v["frame"]["width"], 1000);
    assert_eq!(v["frame"]["height"], 700);
    assert!(fs::read(image).unwrap().starts_with(b"\x89PNG"));
    let (ok, out) = run(&manifest, &["--timeout-ms", "20000"]);
    assert!(ok, "{out}");
    let v: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["framesSubmitted"], 0);
    assert_eq!(v["events"], json!([{"code":5,"value":-1}]));
}

#[test]
fn entropy_is_a_bounded_sdk_capability() {
    let d = package(
        r#"(module(import "ming_v2" "random_fill"(func $r(param i32 i32)(result i32)))(import "ming_v1" "log"(func $log(param i32 i32)(result i32)))(memory(export "memory")1)(func(export "_start")i32.const 1 i32.const 0 i32.const 16 call $r call $log drop i32.const 2 i32.const -1 i32.const 16 call $r call $log drop i32.const 3 i32.const 0 i32.const 4097 call $r call $log drop))"#,
        2,
    );
    let (ok, out) = run(&d.path().join("app.json"), &[]);
    assert!(ok, "{out}");
    let v: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["entropyBytes"], 16);
    assert_eq!(
        v["events"],
        json!([{"code":1,"value":0},{"code":2,"value":-2},{"code":3,"value":-2}])
    );
}
#[test]
fn entropy_session_budget_is_enforced() {
    let d = package(
        r#"(module(import "ming_v2" "random_fill"(func $r(param i32 i32)(result i32)))(import "ming_v1" "log"(func $log(param i32 i32)(result i32)))(memory(export "memory")1)(func(export "_start")(local $i i32)(local $ret i32)(loop $loop i32.const 0 i32.const 4096 call $r local.set $ret local.get $i i32.const 1 i32.add local.tee $i i32.const 257 i32.lt_u br_if $loop)i32.const 1 local.get $ret call $log drop))"#,
        2,
    );
    let (ok, out) = run(&d.path().join("app.json"), &[]);
    assert!(ok, "{out}");
    let v: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["entropyBytes"], 1048576);
    assert_eq!(v["events"], json!([{"code":1,"value":-4}]));
}
