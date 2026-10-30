#![cfg(target_os = "linux")]

use base64::{Engine as _, engine::general_purpose::STANDARD};
use ming_app_host::{app_data::{AppDataRoot, PackageStorageAuthority}, private_file_rpc::PublicFileRpc};
use serde_json::json;
use std::{os::unix::fs::PermissionsExt, sync::Arc};

#[test]
fn public_rpc_uses_shared_authorized_namespace_and_fails_closed_after_revoke() {
    let root = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let authority = Arc::new(PackageStorageAuthority::open_persistent(AppDataRoot::open(root.path()).unwrap()).unwrap());
    authority.authorize_public("dev.example.alpha").unwrap();
    authority.authorize_public("dev.example.beta").unwrap();
    let alpha = PublicFileRpc::new(authority.public_session("dev.example.alpha").unwrap());
    let beta = PublicFileRpc::new(authority.public_session("dev.example.beta").unwrap());

    assert_eq!(alpha.dispatch(1, &json!({"op":"write","path":"note","data":STANDARD.encode(b"shared")}).to_string()).value, 6);
    let read = beta.dispatch(2, &json!({"op":"read","path":"note","offset":0,"limit":32}).to_string());
    assert_eq!(STANDARD.decode(read.payload.unwrap()).unwrap(), b"shared");
    authority.revoke_public("dev.example.alpha").unwrap();
    assert_eq!(alpha.dispatch(3, &json!({"op":"read","path":"note","offset":0,"limit":32}).to_string()).value, -1);
    assert_eq!(beta.dispatch(4, &json!({"op":"read","path":"note","offset":0,"limit":32}).to_string()).value, 6);
}
