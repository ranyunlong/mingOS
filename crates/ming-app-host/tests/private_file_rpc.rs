#![cfg(target_os = "linux")]

use base64::{Engine as _, engine::general_purpose::STANDARD};
use ming_app_host::{app_data::{AppDataRoot, PackageStorageAuthority}, private_file_rpc::PrivateFileRpc};
use serde_json::json;
use std::{os::unix::fs::PermissionsExt, sync::Arc};

fn fixture() -> (tempfile::TempDir, Arc<PackageStorageAuthority>, PrivateFileRpc) {
    let root = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let authority = Arc::new(PackageStorageAuthority::new(AppDataRoot::open(root.path()).unwrap()));
    authority.authorize("dev.example.alpha").unwrap();
    let rpc = PrivateFileRpc::new(authority.file_session("dev.example.alpha").unwrap());
    (root, authority, rpc)
}

#[test]
fn private_file_rpc_round_trips_bounded_data_and_lists_directory() {
    let (_root, _authority, rpc) = fixture();
    let mkdir = rpc.dispatch(1, &json!({"op":"mkdir", "path":"docs"}).to_string());
    assert!(mkdir.value >= 0);
    let write = rpc.dispatch(2, &json!({"op":"write", "path":"docs/a", "data":STANDARD.encode(b"hello")}).to_string());
    assert_eq!(write.value, 5);
    let read = rpc.dispatch(3, &json!({"op":"read", "path":"docs/a", "offset":1, "limit":3}).to_string());
    assert_eq!(read.value, 3);
    assert_eq!(STANDARD.decode(read.payload.unwrap()).unwrap(), b"ell");
    let list = rpc.dispatch(4, &json!({"op":"list", "path":"docs", "offset":0, "limit":8}).to_string());
    assert_eq!(list.value, 1);
    let decoded = STANDARD.decode(list.payload.unwrap()).unwrap();
    let entries: serde_json::Value = serde_json::from_slice(&decoded).unwrap();
    assert_eq!(entries, json!([{"name":"a", "isDirectory":false}]));
}

#[test]
fn private_file_rpc_rejects_identity_injection_and_malformed_requests() {
    let (_root, _authority, rpc) = fixture();
    for request in [
        json!({"op":"read", "path":"x", "offset":0, "limit":1, "packageId":"dev.example.beta"}).to_string(),
        json!({"op":"read", "path":"../x", "offset":0, "limit":1}).to_string(),
        json!({"op":"read", "path":"x", "offset":0, "limit":65537}).to_string(),
        json!({"op":"write", "path":"x", "data":"%%%"}).to_string(),
        json!({"op":"list", "path":"", "offset":0, "limit":33}).to_string(),
        "{".to_owned(),
    ] {
        assert_eq!(rpc.dispatch(1, &request).value, -2, "{request}");
    }
    assert_eq!(rpc.dispatch(1, &"x".repeat(90_001)).value, -2);
}

#[test]
fn private_file_rpc_close_and_user_revocation_disable_existing_session() {
    let (_root, authority, rpc) = fixture();
    let request = json!({"op":"list", "path":"", "offset":0, "limit":1}).to_string();
    authority.revoke("dev.example.alpha").unwrap();
    authority.authorize("dev.example.alpha").unwrap();
    assert_eq!(rpc.dispatch(1, &request).value, -1);
    let next = PrivateFileRpc::new(authority.file_session("dev.example.alpha").unwrap());
    assert_eq!(next.dispatch(2, &request).value, 0);
    next.close();
    assert_eq!(next.dispatch(3, &request).value, -1);
}

#[test]
fn private_file_rpc_bounds_transfers_and_supports_remaining_operations() {
    let (_root, _authority, rpc) = fixture();
    let data = vec![7; 65_536];
    let request = json!({"op":"write", "path":"large", "data":STANDARD.encode(&data)}).to_string();
    assert_eq!(rpc.dispatch(1, &request).value, data.len() as i32);
    let read = rpc.dispatch(2, &json!({"op":"read", "path":"large", "offset":65_534, "limit":2}).to_string());
    assert_eq!(STANDARD.decode(read.payload.unwrap()).unwrap(), [7, 7]);
    let oversized = json!({"op":"write", "path":"large", "data":STANDARD.encode(vec![8; 65_537])}).to_string();
    assert_eq!(rpc.dispatch(3, &oversized).value, -2);
    let unchanged = rpc.dispatch(4, &json!({"op":"read", "path":"large", "offset":0, "limit":1}).to_string());
    assert_eq!(STANDARD.decode(unchanged.payload.unwrap()).unwrap(), [7]);
    assert!(rpc.dispatch(5, &json!({"op":"mkdir", "path":"d"}).to_string()).value >= 0);
    assert!(rpc.dispatch(6, &json!({"op":"rename", "from":"large", "to":"d/file"}).to_string()).value >= 0);
    assert!(rpc.dispatch(7, &json!({"op":"delete", "path":"d/file"}).to_string()).value >= 0);
    assert!(rpc.dispatch(8, &json!({"op":"remove_dir", "path":"d"}).to_string()).value >= 0);
}

#[test]
fn private_file_rpc_listing_pages_fit_worker_message_limit() {
    let (_root, authority, rpc) = fixture();
    for index in 0..33 {
        let name = format!("{}-{index:02}", "\u{1}".repeat(230));
        authority.write_file("dev.example.alpha", &name, b"").unwrap();
    }
    let request = json!({"op":"list", "path":"", "offset":0, "limit":32}).to_string();
    let first = rpc.dispatch(1, &request);
    assert_eq!(first.value, 32);
    assert!(serde_json::to_vec(&first).unwrap().len() + 1 <= 100_000);
    let next = rpc.dispatch(2, &json!({"op":"list", "path":"", "offset":32, "limit":32}).to_string());
    assert_eq!(next.value, 1);
}

#[test]
fn chunked_write_is_invisible_until_atomic_commit_and_supports_large_files() {
    let (_root, authority, rpc) = fixture();
    let app = "dev.example.alpha";
    authority.write_file(app, "file", b"old").unwrap();
    let chunk = vec![42; 65_536];
    assert_eq!(rpc.dispatch(1, &json!({"op":"write_begin","path":"file","total":131072}).to_string()).value, 0);
    assert_eq!(rpc.dispatch(2, &json!({"op":"write_chunk","offset":0,"data":STANDARD.encode(&chunk)}).to_string()).value, 65_536);
    assert_eq!(authority.read_file(app, "file").unwrap(), b"old");
    assert_eq!(rpc.dispatch(3, &json!({"op":"write_commit"}).to_string()).value, -2);
    assert_eq!(rpc.dispatch(4, &json!({"op":"write_chunk","offset":65536,"data":STANDARD.encode(&chunk)}).to_string()).value, 65_536);
    assert_eq!(authority.read_file(app, "file").unwrap(), b"old");
    let committed = rpc.dispatch(5, &json!({"op":"write_commit"}).to_string());
    assert_eq!(committed.value, 0);
    assert!(matches!(committed.payload.as_deref(), Some("0" | "1")));
    assert_eq!(authority.read_file(app, "file").unwrap(), vec![42; 131_072]);
}

#[test]
fn chunked_write_rejects_invalid_sequences_and_discards_on_abort_or_revoke() {
    let (_root, authority, rpc) = fixture();
    let app = "dev.example.alpha";
    authority.write_file(app, "file", b"old").unwrap();
    assert_eq!(rpc.dispatch(1, &json!({"op":"write_begin","path":"../bad","total":4}).to_string()).value, -2);
    assert_eq!(rpc.dispatch(2, &json!({"op":"write_begin","path":"file","total":8388609}).to_string()).value, -2);
    assert_eq!(rpc.dispatch(3, &json!({"op":"write_begin","path":"file","total":4}).to_string()).value, 0);
    assert_eq!(rpc.dispatch(4, &json!({"op":"write_begin","path":"file","total":4}).to_string()).value, -2);
    assert_eq!(rpc.dispatch(5, &json!({"op":"write_chunk","offset":1,"data":"YQ=="}).to_string()).value, -2);
    assert_eq!(rpc.dispatch(6, &json!({"op":"write_chunk","offset":0,"data":""}).to_string()).value, -2);
    assert_eq!(rpc.dispatch(7, &json!({"op":"write_chunk","offset":0,"data":"YWJjZGU="}).to_string()).value, -2);
    assert_eq!(rpc.dispatch(8, &json!({"op":"write_chunk","offset":0,"data":"YWI="}).to_string()).value, 2);
    assert_eq!(rpc.dispatch(9, &json!({"op":"write_abort"}).to_string()).value, 0);
    assert_eq!(authority.read_file(app, "file").unwrap(), b"old");
    assert_eq!(rpc.dispatch(10, &json!({"op":"write_begin","path":"file","total":2}).to_string()).value, 0);
    assert_eq!(rpc.dispatch(11, &json!({"op":"write_chunk","offset":0,"data":"YWI="}).to_string()).value, 2);
    authority.revoke(app).unwrap();
    assert_eq!(rpc.dispatch(12, &json!({"op":"write_commit"}).to_string()).value, -1);
    authority.authorize(app).unwrap();
    assert_eq!(authority.read_file(app, "file").unwrap(), b"old");
    assert_eq!(rpc.dispatch(13, &json!({"op":"write_begin","path":"file","total":2}).to_string()).value, -1);
}

#[test]
fn chunked_write_handles_empty_files_close_and_quota_failure() {
    let (_root, authority, rpc) = fixture();
    let app = "dev.example.alpha";
    assert_eq!(rpc.dispatch(1, &json!({"op":"write_begin","path":"empty","total":0}).to_string()).value, 0);
    assert_eq!(rpc.dispatch(2, &json!({"op":"write_commit"}).to_string()).value, 0);
    assert_eq!(authority.read_file(app, "empty").unwrap(), b"");
    authority.write_file(app, "full", &vec![7; 8 * 1024 * 1024]).unwrap();
    assert_eq!(rpc.dispatch(3, &json!({"op":"write_begin","path":"empty","total":1}).to_string()).value, 0);
    assert_eq!(rpc.dispatch(4, &json!({"op":"write_chunk","offset":0,"data":"YQ=="}).to_string()).value, 1);
    assert!(rpc.dispatch(5, &json!({"op":"write_commit"}).to_string()).value < 0);
    assert_eq!(authority.read_file(app, "empty").unwrap(), b"");
    assert_eq!(rpc.dispatch(6, &json!({"op":"write_commit"}).to_string()).value, -2);
    assert_eq!(rpc.dispatch(7, &json!({"op":"write_begin","path":"full","total":1}).to_string()).value, 0);
    let clone = rpc.clone();
    clone.close();
    assert_eq!(rpc.dispatch(8, &json!({"op":"write_commit"}).to_string()).value, -1);
    assert_eq!(authority.read_file(app, "full").unwrap(), vec![7; 8 * 1024 * 1024]);
}
