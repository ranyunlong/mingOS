#![cfg(all(feature = "engine", target_os = "linux"))]

use base64::{Engine as _, engine::general_purpose::STANDARD};
use ming_app_host::{app_data::{AppDataError, AppDataRoot, PackageStorageAuthority}, private_file_rpc::PrivateFileRpc, protocol};
use serde_json::{Value, json};
use std::{io::BufReader, os::unix::fs::PermissionsExt, process::{Command, Stdio}, sync::{Arc, mpsc}, thread, time::{Duration, Instant}};

fn run_worker(wasm: &[u8], rpc: &PrivateFileRpc, expected: &[i32], event: i32, timeout: Duration) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_ming-app-host"))
        .arg("__worker").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let mut input = child.stdin.take().unwrap();
    let mut output = BufReader::new(child.stdout.take().unwrap());
    let (tx, rx) = mpsc::channel();
    let reader = thread::spawn(move || {
        while let Ok(Some(message)) = protocol::read_json::<Value>(&mut output, 100_000) {
            if tx.send(message).is_err() { break; }
        }
    });
    protocol::write_json(&mut input, &json!({"wasm":STANDARD.encode(wasm),"fuel":100000000,"memoryBytes":67108864,"sdkVersion":6})).unwrap();
    let mut file_calls = 0;
    loop {
        let message = rx.recv_timeout(timeout).expect("worker message timeout");
        match message["type"].as_str() {
            Some("call") => {
                let id = message["id"].as_u64().unwrap();
                match message["method"].as_str() {
                    Some("private_file_call") => {
                        assert_eq!(message["args"], json!([]));
                        assert!(message.get("remaining_fuel").is_none());
                        let payload = message["payload"].as_str().unwrap();
                        let reply = rpc.dispatch(id, payload);
                        assert_eq!(Some(&reply.value), expected.get(file_calls));
                        protocol::write_json(&mut input, &reply).unwrap();
                        file_calls += 1;
                    }
                    Some("log") => {
                        assert_eq!(message["args"], json!([event, 1]));
                        protocol::write_json(&mut input, &json!({"id":id,"value":0})).unwrap();
                    }
                    other => panic!("unexpected method {other:?}"),
                }
            }
            Some("result") => { assert_eq!(message["exitCode"], 0); break; }
            _ => panic!("unexpected worker message: {message}"),
        }
    }
    assert_eq!(file_calls, expected.len());
    drop(input);
    let deadline = Instant::now() + Duration::from_secs(5);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() > deadline { let _ = child.kill(); let _ = child.wait(); panic!("worker not reaped"); }
        thread::sleep(Duration::from_millis(5));
    }
    reader.join().unwrap();
    rpc.close();
}

#[test]
#[ignore = "requires the authorized Kotlin/Wasm probe built and MING_PRIVATE_KOTLIN_WASM set"]
fn authorized_kotlin_workers_write_read_and_remain_isolated() {
    let wasm = std::fs::read(std::env::var_os("MING_PRIVATE_KOTLIN_WASM").expect("probe path required")).unwrap();
    let root = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let data_root = AppDataRoot::open(root.path()).unwrap();
    let authority = Arc::new(PackageStorageAuthority::open_persistent(data_root.clone()).unwrap());
    let a = "dev.example.alpha";
    let b = "dev.example.beta";
    authority.authorize(a).unwrap();
    authority.authorize(b).unwrap();
    let first = PrivateFileRpc::new(authority.file_session(a).unwrap());
    run_worker(&wasm, &first, &[5, 5], 7102, Duration::from_secs(20));
    assert_eq!(first.dispatch(1, r#"{"op":"list","path":"","offset":0,"limit":1}"#).value, -1);
    drop(first);
    assert_eq!(authority.read_file(a, "note.txt").unwrap(), b"alpha");
    assert_eq!(authority.read_file(b, "note.txt"), Err(AppDataError::NotFound));
    run_worker(&wasm, &PrivateFileRpc::new(authority.file_session(b).unwrap()), &[5, 5], 7102, Duration::from_secs(20));
    authority.write_file(a, "note.txt", b"changed").unwrap();
    assert_eq!(authority.read_file(a, "note.txt").unwrap(), b"changed");
    assert_eq!(authority.read_file(b, "note.txt").unwrap(), b"alpha");
    drop(authority);
    let reopened = Arc::new(PackageStorageAuthority::open_persistent(data_root).unwrap());
    assert_eq!(reopened.read_file(a, "note.txt").unwrap(), b"changed");
    assert_eq!(reopened.read_file(b, "note.txt").unwrap(), b"alpha");
    let old = reopened.file_session(a).unwrap();
    reopened.revoke(a).unwrap();
    assert_eq!(old.read_file("note.txt"), Err(AppDataError::Denied));
    reopened.authorize(a).unwrap();
    assert_eq!(old.read_file("note.txt"), Err(AppDataError::Denied));
    assert_eq!(reopened.file_session(a).unwrap().read_file("note.txt").unwrap(), b"changed");
}

#[test]
#[ignore = "requires the large Kotlin/Wasm probe built and MING_PRIVATE_LARGE_WASM set"]
fn authorized_kotlin_worker_writes_large_file_in_chunks() {
    let wasm = std::fs::read(std::env::var_os("MING_PRIVATE_LARGE_WASM").expect("probe path required")).unwrap();
    let (_root, authority, rpc) = {
        let root = tempfile::tempdir().unwrap();
        std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let authority = Arc::new(PackageStorageAuthority::new(AppDataRoot::open(root.path()).unwrap()));
        authority.authorize("dev.example.alpha").unwrap();
        let rpc = PrivateFileRpc::new(authority.file_session("dev.example.alpha").unwrap());
        (root, authority, rpc)
    };
    run_worker(&wasm, &rpc, &[0, 32766, 32766, 32766, 32766, 0, 6], 7103, Duration::from_secs(20));
    assert_eq!(authority.read_file("dev.example.alpha", "large.txt").unwrap(), b"abc".repeat(43688));
}

#[test]
#[ignore = "requires typed Kotlin/Wasm probe and MING_PRIVATE_TYPED_WASM; does not qualify startup latency"]
fn typed_kotlin_workers_read_write_and_remain_isolated() {
    let wasm = std::fs::read(std::env::var_os("MING_PRIVATE_TYPED_WASM").expect("probe path required")).unwrap();
    let root = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let authority = Arc::new(PackageStorageAuthority::open_persistent(AppDataRoot::open(root.path()).unwrap()).unwrap());
    for id in ["dev.example.alpha", "dev.example.beta"] { authority.authorize(id).unwrap(); }
    run_worker(&wasm, &PrivateFileRpc::new(authority.file_session("dev.example.alpha").unwrap()), &[5, 5], 7102, Duration::from_secs(20));
    assert_eq!(authority.read_file("dev.example.beta", "note.txt"), Err(AppDataError::NotFound));
    run_worker(&wasm, &PrivateFileRpc::new(authority.file_session("dev.example.beta").unwrap()), &[5, 5], 7102, Duration::from_secs(20));
    authority.write_file("dev.example.alpha", "note.txt", b"changed").unwrap();
    assert_eq!(authority.read_file("dev.example.beta", "note.txt").unwrap(), b"alpha");
}

#[test]
#[ignore = "requires typed large Kotlin/Wasm probe and MING_PRIVATE_TYPED_LARGE_WASM; does not qualify startup latency"]
fn typed_kotlin_worker_writes_large_file_in_chunks() {
    let wasm = std::fs::read(std::env::var_os("MING_PRIVATE_TYPED_LARGE_WASM").expect("probe path required")).unwrap();
    let root = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let authority = Arc::new(PackageStorageAuthority::new(AppDataRoot::open(root.path()).unwrap()));
    authority.authorize("dev.example.alpha").unwrap();
    let rpc = PrivateFileRpc::new(authority.file_session("dev.example.alpha").unwrap());
    run_worker(&wasm, &rpc, &[0, 65536, 65528, 0, 6], 7103, Duration::from_secs(20));
    assert_eq!(authority.read_file("dev.example.alpha", "large.txt").unwrap(), b"abc".repeat(43688));
}

#[test]
#[ignore = "requires typed directory Kotlin/Wasm probe and MING_PRIVATE_TYPED_DIRECTORY_WASM; does not qualify startup latency"]
fn typed_kotlin_worker_manages_directories_and_handles_missing_files() {
    let wasm = std::fs::read(std::env::var_os("MING_PRIVATE_TYPED_DIRECTORY_WASM").expect("probe path required")).unwrap();
    let root = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let authority = Arc::new(PackageStorageAuthority::new(AppDataRoot::open(root.path()).unwrap()));
    authority.authorize("dev.example.alpha").unwrap();
    let rpc = PrivateFileRpc::new(authority.file_session("dev.example.alpha").unwrap());
    run_worker(&wasm, &rpc, &[1, 1, 1, 1, -3, 1, 1], 7104, Duration::from_secs(20));
    assert!(authority.list_file_directory("dev.example.alpha", "").unwrap().is_empty());
}

#[test]
#[ignore = "requires typed denied Kotlin/Wasm probe and MING_PRIVATE_TYPED_DENIED_WASM; does not qualify startup latency"]
fn typed_kotlin_worker_reports_revoked_access() {
    let wasm = std::fs::read(std::env::var_os("MING_PRIVATE_TYPED_DENIED_WASM").expect("probe path required")).unwrap();
    let root = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let authority = Arc::new(PackageStorageAuthority::new(AppDataRoot::open(root.path()).unwrap()));
    authority.authorize("dev.example.alpha").unwrap();
    let rpc = PrivateFileRpc::new(authority.file_session("dev.example.alpha").unwrap());
    authority.revoke("dev.example.alpha").unwrap();
    run_worker(&wasm, &rpc, &[-1, -1, -1, -1], 7105, Duration::from_secs(20));
}
