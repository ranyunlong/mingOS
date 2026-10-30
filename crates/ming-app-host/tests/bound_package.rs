#![cfg(target_os = "linux")]
use ming_app_host::{app_data::{AppDataError, AppDataRoot, PackageStorageAuthority}, package::{Manifest, Package}};
use std::{os::unix::fs::PermissionsExt, sync::Arc};

fn package(version: &str, bytes: &[u8]) -> Package {
    Package {
        manifest: Manifest { schema_version: 1, id: "dev.example.app".into(), version: version.into(), sdk_version: 6, entry: "main.wasm".into(), private_storage: true, public_storage: false, publisher_key_id: None, publisher_signature: None },
        bytes: bytes.to_vec(),
    }
}

#[test]
fn persistent_package_authorization_is_bound_to_exact_approved_content() {
    let root_dir = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root_dir.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let root = AppDataRoot::open(root_dir.path()).unwrap();
    let authority = Arc::new(PackageStorageAuthority::open_persistent(root.clone()).unwrap());
    let original = package("1.0.0", b"\0asm\x01\0\0\0");
    let changed = package("1.0.0", b"\0asm\x01\0\0\0\x00");
    let updated = package("1.0.1", b"\0asm\x01\0\0\0");
    authority.authorize("dev.example.app").unwrap();
    assert!(matches!(authority.file_session_for_package(&original), Err(AppDataError::Denied)));
    assert!(matches!(authority.authorize_package(&original), Err(AppDataError::Denied)));
    authority.revoke("dev.example.app").unwrap();
    assert!(authority.authorize_package(&original).unwrap().persisted);
    assert!(matches!(authority.file_session("dev.example.app"), Err(AppDataError::Denied)));
    assert!(matches!(authority.grant("dev.example.app", &[1]), Err(AppDataError::Denied)));
    assert_eq!(authority.authorize("dev.example.app"), Err(AppDataError::Denied));
    let old = authority.file_session_for_package(&original).unwrap();
    old.write_file("note", b"saved").unwrap();
    assert!(matches!(authority.file_session_for_package(&changed), Err(AppDataError::Denied)));
    assert!(matches!(authority.authorize_package(&updated), Err(AppDataError::Denied)));
    drop(old);
    drop(authority);
    let reopened = Arc::new(PackageStorageAuthority::open_persistent(root).unwrap());
    assert_eq!(reopened.file_session_for_package(&original).unwrap().read_file("note").unwrap(), b"saved");
    assert!(matches!(reopened.file_session_for_package(&changed), Err(AppDataError::Denied)));
    let stale = reopened.file_session_for_package(&original).unwrap();
    reopened.revoke("dev.example.app").unwrap();
    assert_eq!(stale.read_file("note"), Err(AppDataError::Denied));
    assert!(matches!(reopened.file_session_for_package(&original), Err(AppDataError::Denied)));
    reopened.authorize_package(&updated).unwrap();
    assert_eq!(reopened.file_session_for_package(&updated).unwrap().read_file("note").unwrap(), b"saved");
    assert!(matches!(reopened.file_session_for_package(&original), Err(AppDataError::Denied)));
    assert_eq!(stale.read_file("note"), Err(AppDataError::Denied));
}

#[test]
fn malformed_bound_policy_is_rejected_on_reopen() {
    let root_dir = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root_dir.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let root = AppDataRoot::open(root_dir.path()).unwrap();
    let authority = PackageStorageAuthority::open_persistent(root.clone()).unwrap();
    authority.authorize_package(&package("1.0.0", b"\0asm\x01\0\0\0")).unwrap();
    drop(authority);
    let record = root_dir.path().join("system-private-storage-policy-v1/slot-1");
    for value in [
        "ming-private-storage-policy-v2\ndev.example.app\tabc\n".to_string(),
        format!("ming-private-storage-policy-v2\ndev.example.app\t{}\n", "A".repeat(64)),
        format!("ming-private-storage-policy-v2\ndev.example.app\t{}\ndev.example.app\t-\n", "a".repeat(64)),
        format!("ming-private-storage-policy-v2\ndev.example.app\t{}", "a".repeat(64)),
    ] {
        std::fs::write(&record, value).unwrap();
        assert!(matches!(PackageStorageAuthority::open_persistent(root.clone()), Err(AppDataError::Io)));
    }
}

#[test]
fn mixed_legacy_and_bound_records_survive_restart_without_mixing_authority() {
    let root_dir = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root_dir.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let root = AppDataRoot::open(root_dir.path()).unwrap();
    let authority = Arc::new(PackageStorageAuthority::open_persistent(root.clone()).unwrap());
    authority.authorize("dev.example.legacy").unwrap();
    let approved = package("1.0.0", b"\0asm\x01\0\0\0");
    authority.authorize_package(&approved).unwrap();
    drop(authority);
    let reopened = Arc::new(PackageStorageAuthority::open_persistent(root).unwrap());
    assert!(reopened.file_session("dev.example.legacy").is_ok());
    assert!(reopened.file_session_for_package(&approved).is_ok());
    assert!(matches!(reopened.file_session("dev.example.app"), Err(AppDataError::Denied)));
    let mut changed = package("1.0.0", b"\0asm\x01\0\0\0");
    changed.manifest.sdk_version = 5;
    assert!(matches!(reopened.file_session_for_package(&changed), Err(AppDataError::Denied)));
    changed.manifest.sdk_version = 6;
    changed.manifest.entry = "other.wasm".into();
    assert!(matches!(reopened.file_session_for_package(&changed), Err(AppDataError::Denied)));
    changed.manifest.entry = "main.wasm".into();
    changed.manifest.private_storage = false;
    assert!(matches!(reopened.file_session_for_package(&changed), Err(AppDataError::Denied)));
    changed.manifest.private_storage = true;
    changed.manifest.id = "dev.example.legacy".into();
    assert!(matches!(reopened.file_session_for_package(&changed), Err(AppDataError::Denied)));
}
