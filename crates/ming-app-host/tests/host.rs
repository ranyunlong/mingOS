#![cfg(feature = "engine")]
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

fn package(wat: &str) -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("main.wasm"), wat::parse_str(wat).unwrap()).unwrap();
    fs::write(dir.path().join("app.json"),json!({"schemaVersion":1,"id":"dev.ming.native-test","version":"0.1.0","sdkVersion":1,"entry":"main.wasm"}).to_string()).unwrap();
    dir
}
fn run(dir: &Path, options: &[&str]) -> (bool, String) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_ming-app-host"))
        .arg("run")
        .arg(dir.join("app.json"))
        .args(options)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(15);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() > deadline {
            child.kill().unwrap();
            panic!("native host did not terminate");
        }
        thread::sleep(Duration::from_millis(5));
    }
    let out = child.wait_with_output().unwrap();
    (
        out.status.success(),
        String::from_utf8(if out.status.success() {
            out.stdout
        } else {
            out.stderr
        })
        .unwrap(),
    )
}
#[test]
fn native_cli_runs_and_denies_ungranted_resource() {
    let dir = package(
        r#"(module (import "ming_v1" "file_open" (func $open (param i32) (result i32))) (import "ming_v1" "log" (func $log (param i32 i32) (result i32))) (func (export "_start") i32.const 3 i32.const 2 call $open call $log drop))"#,
    );
    let (ok, out) = run(dir.path(), &[]);
    assert!(ok, "{out}");
    let v: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["events"], json!([{"code":3,"value":-1}]));
    assert_eq!(v["runtime"], "rust-wasmtime");
}
#[test]
fn rejects_wasi_unknown_names_nonfunctions_and_wrong_sdk_signatures() {
    for (source, code) in [
        (
            r#"(module (import "wasi_snapshot_preview1" "fd_read" (func)) (func (export "_start")))"#,
            "IMPORT_DENIED",
        ),
        (
            r#"(module (import "ming_v1" "network_open" (func)) (func (export "_start")))"#,
            "IMPORT_DENIED",
        ),
        (
            r#"(module (import "ming_v1" "file_open" (memory 1)) (func (export "_start")))"#,
            "IMPORT_DENIED",
        ),
        (
            r#"(module (import "ming_v1" "file_open" (func (param i64) (result i32))) (func (export "_start")))"#,
            "SIGNATURE_DENIED",
        ),
        (
            r#"(module (func (export "_start") (param i32)))"#,
            "ENTRYPOINT_INVALID",
        ),
    ] {
        let dir = package(source);
        let (ok, out) = run(dir.path(), &[]);
        assert!(!ok, "{source}");
        assert!(out.contains(code), "{out}");
    }
}
#[test]
fn traps_fuel_timeout_and_request_flood_terminate_and_next_app_runs() {
    for (source, opts, code) in [
        (
            r#"(module (func (export "_start") unreachable))"#,
            vec![],
            "APP_TRAP",
        ),
        (
            r#"(module (func (export "_start") (loop br 0)))"#,
            vec!["--fuel", "100"],
            "FUEL_EXHAUSTED",
        ),
        (
            r#"(module (func (export "_start") (loop br 0)))"#,
            vec!["--fuel", "18446744073709551615", "--timeout-ms", "30"],
            "TIMEOUT",
        ),
        (
            r#"(module (import "ming_v1" "file_open" (func $open (param i32) (result i32))) (func (export "_start") (loop i32.const 1 call $open drop br 0)))"#,
            vec!["--max-requests", "3"],
            "REQUEST_LIMIT",
        ),
    ] {
        let dir = package(source);
        let (ok, out) = run(dir.path(), &opts);
        assert!(!ok);
        assert!(out.contains(code), "{out}");
    }
    let dir = package(r#"(module (func (export "_start")))"#);
    let (ok, out) = run(dir.path(), &[]);
    assert!(ok, "{out}");
}
#[test]
fn linear_memory_growth_is_limited() {
    let dir = package(
        r#"(module (import "ming_v1" "log" (func $log (param i32 i32) (result i32))) (memory 1) (func (export "_start") i32.const 20 i32.const 1 memory.grow call $log drop))"#,
    );
    let (ok, out) = run(dir.path(), &["--memory-bytes", "65536"]);
    assert!(ok, "{out}");
    let v: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["events"], json!([{"code":20,"value":-1}]));
    let dir = package(r#"(module (memory 2) (func (export "_start")))"#);
    let (ok, out) = run(dir.path(), &["--memory-bytes", "65536"]);
    assert!(!ok);
    assert!(out.contains("RESOURCE_LIMIT"), "{out}");
}
#[test]
fn manifest_cannot_self_grant_and_cli_options_are_strict() {
    let dir = package(r#"(module (func (export "_start")))"#);
    let manifest = dir.path().join("app.json");
    let mut value: Value = serde_json::from_slice(&fs::read(&manifest).unwrap()).unwrap();
    value["grants"] = json!({"1":"/etc/passwd"});
    fs::write(&manifest, value.to_string()).unwrap();
    let (ok, out) = run(dir.path(), &[]);
    assert!(!ok);
    assert!(out.contains("MANIFEST_INVALID"), "{out}");
    let dir = package(r#"(module (func (export "_start")))"#);
    for options in [
        &["--unknown", "1"][..],
        &["--grant", "1=x", "--grant", "1=y"][..],
        &["--fuel", "0"][..],
        &["--timeout-ms", "10junk"][..],
    ] {
        let (ok, out) = run(dir.path(), options);
        assert!(!ok);
        assert!(
            out.contains("INVALID_") || out.contains("DUPLICATE_"),
            "{out}"
        );
    }
}
fn real_package() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../artifacts/read-file")
}
#[test]
fn actual_kotlin_artifact_reads_utf8_and_preserves_denial_semantics() {
    let dir = real_package();
    assert!(
        dir.join("main.wasm").is_file(),
        "Build the real Kotlin sample first"
    );
    let data = tempfile::tempdir().unwrap();
    let file = data.path().join("hello.txt");
    let content = "Native Kotlin 你好\n";
    fs::write(&file, content).unwrap();
    let grant = format!("1={}", file.display());
    let (ok, out) = run(&dir, &["--grant", &grant]);
    assert!(ok, "{out}");
    let v: Value = serde_json::from_str(&out).unwrap();
    let bytes: Vec<u8> = v["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|e| e["code"] == 2)
        .map(|e| e["value"].as_u64().unwrap() as u8)
        .collect();
    assert_eq!(String::from_utf8(bytes).unwrap(), content);
    assert!(
        v["events"]
            .as_array()
            .unwrap()
            .contains(&json!({"code":3,"value":-1}))
    );
    let (ok, out) = run(&dir, &[]);
    assert!(ok, "{out}");
    let v: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["events"], json!([{"code":5,"value":-1}]));
}
