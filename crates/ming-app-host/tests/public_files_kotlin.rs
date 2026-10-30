#![cfg(all(feature = "engine", target_os = "linux"))]

use base64::{Engine as _, engine::general_purpose::STANDARD};
use ming_app_host::{app_data::{AppDataRoot, PackageStorageAuthority}, private_file_rpc::PublicFileRpc, protocol};
use serde_json::Value;
use std::{io::BufReader, os::unix::fs::PermissionsExt, process::{Command, Stdio}, sync::{Arc, mpsc}, thread, time::{Duration, Instant}};

fn run_worker(wasm: &[u8], rpc: &PublicFileRpc, expected: &[i32], event: i32) {
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
    protocol::write_json(&mut input, &serde_json::json!({"wasm":STANDARD.encode(wasm),"fuel":100000000,"memoryBytes":67108864,"sdkVersion":6})).unwrap();
    let mut calls = 0;
    loop {
        let message = rx.recv_timeout(Duration::from_secs(20)).expect("worker message timeout");
        match message["type"].as_str() {
            Some("call") => {
                let id = message["id"].as_u64().unwrap();
                match message["method"].as_str() {
                    Some("public_file_call") => {
                        assert_eq!(message["args"], serde_json::json!([]));
                        let reply = rpc.dispatch(id, message["payload"].as_str().unwrap());
                        assert_eq!(Some(&reply.value), expected.get(calls));
                        protocol::write_json(&mut input, &reply).unwrap();
                        calls += 1;
                    }
                    Some("log") => {
                        assert_eq!(message["args"], serde_json::json!([event, 1]));
                        protocol::write_json(&mut input, &serde_json::json!({"id":id,"value":0})).unwrap();
                    }
                    other => panic!("unexpected method {other:?}"),
                }
            }
            Some("result") => { assert_eq!(message["exitCode"], 0); break; }
            other => panic!("unexpected message type {other:?}"),
        }
    }
    assert_eq!(calls, expected.len());
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
#[ignore = "requires public typed Kotlin/Wasm probe and MING_PUBLIC_TYPED_WASM"]
fn authorized_kotlin_public_worker_manages_shared_files() {
    let wasm = std::fs::read(std::env::var_os("MING_PUBLIC_TYPED_WASM").expect("probe path required")).unwrap();
    let root = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let authority = Arc::new(PackageStorageAuthority::open_persistent(AppDataRoot::open(root.path()).unwrap()).unwrap());
    authority.authorize_public("dev.example.public").unwrap();
    authority.authorize_public("dev.example.other").unwrap();
    let rpc = PublicFileRpc::new(authority.public_session("dev.example.public").unwrap());
    run_worker(&wasm, &rpc, &[6, 6, 1, 1, 1, 1, 1, 1], 7201);
    assert_eq!(authority.public_session("dev.example.other").unwrap().read_file("shared-note.txt").unwrap(), b"shared");
}

#[test]
#[ignore = "requires public typed large Kotlin/Wasm probe and MING_PUBLIC_TYPED_LARGE_WASM"]
fn authorized_kotlin_public_worker_writes_large_file_in_chunks() {
    let wasm = std::fs::read(std::env::var_os("MING_PUBLIC_TYPED_LARGE_WASM").expect("probe path required")).unwrap();
    let root = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let authority = Arc::new(PackageStorageAuthority::open_persistent(AppDataRoot::open(root.path()).unwrap()).unwrap());
    authority.authorize_public("dev.example.public").unwrap();
    authority.authorize_public("dev.example.other").unwrap();
    let rpc = PublicFileRpc::new(authority.public_session("dev.example.public").unwrap());
    run_worker(&wasm, &rpc, &[0, 65536, 65528, 0, 6], 7202);
    assert_eq!(authority.public_session("dev.example.other").unwrap().read_file("public-large.txt").unwrap(), b"abc".repeat(43688));
}
