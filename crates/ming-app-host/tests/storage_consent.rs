#![cfg(target_os = "linux")]
use ming_app_host::{app_data::{AppDataError, AppDataRoot, PackageStorageAuthority}, package::load_package, storage_consent::{ConsentRequest, StorageConsent}};
use std::{os::unix::fs::PermissionsExt, sync::Arc};

#[test]
fn consent_controls_the_exact_loaded_package_and_rejects_replacement() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("data");
    std::fs::create_dir(&root).unwrap();
    std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o700)).unwrap();
    let manifest = temp.path().join("app.json");
    let module = temp.path().join("main.wasm");
    std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.app","version":"1.0.0","sdkVersion":6,"entry":"main.wasm","privateStorage":true}"#).unwrap();
    std::fs::write(&module, b"\0asm\x01\0\0\0").unwrap();
    let authority = Arc::new(PackageStorageAuthority::open_persistent(AppDataRoot::open(&root).unwrap()).unwrap());
    let consent = StorageConsent::new(authority.clone());

    let mut undeclared = load_package(&manifest).unwrap();
    undeclared.manifest.private_storage = false;
    assert!(matches!(consent.request(undeclared), Err(AppDataError::Denied)));

    let pending = match consent.request(load_package(&manifest).unwrap()).unwrap() {
        ConsentRequest::Prompt(pending) => pending,
        _ => panic!("expected prompt"),
    };
    assert_eq!(pending.app_id(), "dev.example.app");
    assert_eq!(pending.version(), "1.0.0");
    assert!(!pending.is_replacement());
    assert_eq!(pending.fingerprint().len(), 64);
    pending.deny();
    assert!(matches!(authority.file_session_for_package(&load_package(&manifest).unwrap()), Err(AppDataError::Denied)));

    let pending = match consent.request(load_package(&manifest).unwrap()).unwrap() {
        ConsentRequest::Prompt(pending) => pending,
        _ => panic!("expected prompt"),
    };
    let granted = pending.allow().unwrap();
    granted.session.write_file("note", b"saved").unwrap();
    drop(granted);
    assert!(matches!(consent.request(load_package(&manifest).unwrap()).unwrap(), ConsentRequest::Allowed(_)));

    std::fs::write(&module, b"\0asm\x01\0\0\0\0").unwrap();
    let pending = match consent.request(load_package(&manifest).unwrap()).unwrap() {
        ConsentRequest::Prompt(pending) => pending,
        _ => panic!("replacement must prompt"),
    };
    assert!(pending.is_replacement());
    assert!(matches!(pending.allow(), Err(AppDataError::Denied)));
    assert_eq!(authority.read_file("dev.example.app", "note").unwrap(), b"saved");
}

#[test]
fn pending_consent_is_a_snapshot_and_cannot_overwrite_a_competing_decision() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("data");
    std::fs::create_dir(&root).unwrap();
    std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o700)).unwrap();
    let manifest = temp.path().join("app.json");
    let module = temp.path().join("main.wasm");
    std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.app","version":"1.0.0","sdkVersion":6,"entry":"main.wasm","privateStorage":true}"#).unwrap();
    std::fs::write(&module, b"\0asm\x01\0\0\0").unwrap();
    let authority = Arc::new(PackageStorageAuthority::open_persistent(AppDataRoot::open(&root).unwrap()).unwrap());
    let consent = StorageConsent::new(authority.clone());
    let pending = match consent.request(load_package(&manifest).unwrap()).unwrap() {
        ConsentRequest::Prompt(pending) => pending, _ => panic!("expected prompt"),
    };
    let fingerprint = pending.fingerprint();
    std::fs::write(&module, b"\0asm\x01\0\0\0\0").unwrap();
    let changed = load_package(&manifest).unwrap();
    let granted = pending.allow().unwrap();
    assert!(granted.receipt.unwrap().persisted);
    assert_eq!(granted.package.bytes, b"\0asm\x01\0\0\0");
    assert!(matches!(authority.file_session_for_package(&changed), Err(AppDataError::Denied)));
    match consent.request(changed).unwrap() {
        ConsentRequest::Prompt(pending) => { assert!(pending.is_replacement()); assert_ne!(pending.fingerprint(), fingerprint); }
        _ => panic!("changed package must prompt"),
    }
    drop(granted);
    authority.revoke("dev.example.app").unwrap();

    let pending = match consent.request(load_package(&manifest).unwrap()).unwrap() {
        ConsentRequest::Prompt(pending) => pending, _ => panic!("expected prompt"),
    };
    std::fs::write(&module, b"\0asm\x01\0\0\0").unwrap();
    let competing = load_package(&manifest).unwrap();
    authority.authorize_package(&competing).unwrap();
    assert!(matches!(pending.allow(), Err(AppDataError::Denied)));
    assert!(authority.file_session_for_package(&competing).is_ok());
}

#[test]
fn management_revokes_only_the_displayed_authorization_generation() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("data");
    std::fs::create_dir(&root).unwrap();
    std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o700)).unwrap();
    let manifest = temp.path().join("app.json");
    std::fs::write(temp.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
    std::fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.app","version":"1.0.0","sdkVersion":6,"entry":"main.wasm","privateStorage":true}"#).unwrap();
    let root = AppDataRoot::open(root).unwrap();
    let authority = Arc::new(PackageStorageAuthority::open_persistent(root.clone()).unwrap());
    let consent = StorageConsent::new(authority.clone());
    assert!(consent.management("dev.example.app").unwrap().is_none());
    let package = load_package(&manifest).unwrap();
    authority.authorize_package(&package).unwrap();
    let session = authority.file_session_for_package(&package).unwrap();
    session.write_file("note", b"keep").unwrap();
    let stale = consent.management("dev.example.app").unwrap().unwrap();
    assert_eq!(stale.app_id(), "dev.example.app");
    assert_eq!(stale.fingerprint().unwrap().len(), 64);
    let current = consent.management("dev.example.app").unwrap().unwrap();
    assert!(current.revoke().unwrap().persisted);
    assert_eq!(session.read_file("note"), Err(AppDataError::Denied));
    authority.authorize_package(&package).unwrap();
    assert!(matches!(stale.revoke(), Err(AppDataError::Denied)));
    assert_eq!(authority.file_session_for_package(&package).unwrap().read_file("note").unwrap(), b"keep");
    drop(stale);
    drop(current);
    drop(session);
    drop(consent);
    drop(authority);
    let reopened = Arc::new(PackageStorageAuthority::open_persistent(root).unwrap());
    assert_eq!(reopened.file_session_for_package(&package).unwrap().read_file("note").unwrap(), b"keep");
}

#[test]
fn management_exposes_legacy_records_and_rejects_invalid_ids() {
    let temp = tempfile::tempdir().unwrap();
    std::fs::set_permissions(temp.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let authority = Arc::new(PackageStorageAuthority::open_persistent(AppDataRoot::open(temp.path()).unwrap()).unwrap());
    authority.authorize("dev.example.legacy").unwrap();
    let consent = StorageConsent::new(authority.clone());
    assert!(matches!(consent.management("../bad"), Err(AppDataError::InvalidArgument)));
    let management = consent.management("dev.example.legacy").unwrap().unwrap();
    assert_eq!(management.app_id(), "dev.example.legacy");
    assert_eq!(management.fingerprint(), None);
    management.revoke().unwrap();
    assert!(consent.management("dev.example.legacy").unwrap().is_none());
}
