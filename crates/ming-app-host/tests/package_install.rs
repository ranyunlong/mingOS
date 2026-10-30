#![cfg(target_os = "linux")]

use base64::{Engine as _, engine::general_purpose::STANDARD};
use ed25519_dalek::{Signer, SigningKey};
use ming_app_host::{
    app_data::{AppDataRoot, PackageStorageAuthority},
    package::{Manifest, Package, load_package},
    package_install::{InstallState, PackageInstallRegistry, UninstallDataPolicy},
    publisher::PublisherTrustStore,
};
use std::{fs, os::unix::fs::PermissionsExt};

fn package(version: &str, bytes: &[u8]) -> Package {
    Package {
        manifest: Manifest {
            schema_version: 1,
            id: "dev.example.installed".into(),
            version: version.into(),
            sdk_version: 6,
            entry: "main.wasm".into(),
            private_storage: false,
            public_storage: true,
            publisher_key_id: None,
            publisher_signature: None,
        },
        bytes: bytes.to_vec(),
    }
}

fn signed_package(version: &str, bytes: &[u8], key_id: &str, signing_key: &SigningKey) -> Package {
    let mut package = package(version, bytes);
    let signature = signing_key.sign(&package.signature_message(key_id).unwrap());
    package.manifest.publisher_key_id = Some(key_id.into());
    package.manifest.publisher_signature = Some(STANDARD.encode(signature.to_bytes()));
    package
}

#[test]
fn install_update_and_uninstall_are_atomic_and_versioned() {
    let temp = tempfile::tempdir().unwrap();
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let registry = PackageInstallRegistry::open(temp.path()).unwrap();
    let original = package("1.0.0", b"\0asm\x01\0\0\0");
    let updated = package("1.1.0", b"\0asm\x01\0\0\0\x00");

    assert_eq!(registry.install_package(&original).unwrap(), InstallState::Installed);
    assert!(registry.verify_package(&original).is_ok());
    assert_eq!(registry.install_package(&original).unwrap(), InstallState::Unchanged);
    assert_eq!(registry.install_package(&updated).unwrap(), InstallState::Updated);
    assert!(registry.verify_package(&original).is_err());
    assert!(registry.verify_package(&updated).is_ok());
    assert!(registry.install_package(&original).is_err());

    registry.uninstall(&updated.manifest.id).unwrap();
    assert!(registry.verify_package(&updated).is_err());
    assert_eq!(registry.install_package(&updated).unwrap(), InstallState::Reinstalled);
    assert!(registry.verify_package(&updated).is_ok());
}

#[test]
fn malformed_or_unsafe_registry_is_rejected() {
    let temp = tempfile::tempdir().unwrap();
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let registry = PackageInstallRegistry::open(temp.path()).unwrap();
    registry.install_package(&package("1.0.0", b"\0asm\x01\0\0\0")).unwrap();
    let record = temp.path().join("installed-packages-v1");
    for contents in [
        "ming-installed-packages-v1\ndev.example.installed\t1.0.0\tbad\n",
        "ming-installed-packages-v1\ndev.example.installed\t1.1.0\t0000000000000000000000000000000000000000000000000000000000000000\n\n",
        "ming-installed-packages-v1\ndev.example.installed\t1.0.0\t0000000000000000000000000000000000000000000000000000000000000000\ndev.example.installed\t1.1.0\t0000000000000000000000000000000000000000000000000000000000000000\n",
    ] {
        fs::write(&record, contents).unwrap();
        assert!(PackageInstallRegistry::open(temp.path()).is_err());
    }
}

#[test]
fn registry_lock_is_exclusive_and_uninstall_does_not_delete_sibling_data() {
    let temp = tempfile::tempdir().unwrap();
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let sentinel = temp.path().join("user-data");
    fs::write(&sentinel, b"keep").unwrap();
    let registry = PackageInstallRegistry::open(temp.path()).unwrap();
    assert!(PackageInstallRegistry::open(temp.path()).is_err());
    let installed = package("1.0.0", b"\0asm\x01\0\0\0");
    registry.install_package(&installed).unwrap();
    assert!(registry.uninstall(&installed.manifest.id).unwrap());
    assert_eq!(fs::read(&sentinel).unwrap(), b"keep");
    drop(registry);
    assert!(PackageInstallRegistry::open(temp.path()).is_ok());
}

#[test]
fn install_from_manifest_stores_versioned_content_and_keeps_old_update_content() {
    let temp = tempfile::tempdir().unwrap();
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let source = temp.path().join("source");
    fs::create_dir(&source).unwrap();
    fs::write(source.join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
    let manifest = source.join("app.json");
    fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.installed","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
    let registry_root = temp.path().join("registry");
    fs::create_dir(&registry_root).unwrap();
    fs::set_permissions(&registry_root, fs::Permissions::from_mode(0o700)).unwrap();
    let registry = PackageInstallRegistry::open(&registry_root).unwrap();

    assert_eq!(registry.install_from_manifest(&manifest).unwrap(), InstallState::Installed);
    let first = registry.active_manifest_path("dev.example.installed").unwrap();
    assert_eq!(load_package(&first).unwrap().manifest.version, "1.0.0");

    fs::write(source.join("main.wasm"), b"\0asm\x01\0\0\0\x00").unwrap();
    fs::write(&manifest, r#"{"schemaVersion":1,"id":"dev.example.installed","version":"1.1.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
    assert_eq!(registry.install_from_manifest(&manifest).unwrap(), InstallState::Updated);
    let second = registry.active_manifest_path("dev.example.installed").unwrap();
    assert_ne!(first, second);
    assert_eq!(load_package(&first).unwrap().manifest.version, "1.0.0");
    assert_eq!(load_package(&second).unwrap().manifest.version, "1.1.0");

    registry.uninstall("dev.example.installed").unwrap();
    assert!(registry.active_manifest_path("dev.example.installed").is_err());
    assert!(first.exists() && second.exists());
}

#[test]
fn uninstall_preserves_private_data_by_default() {
    let temp = tempfile::tempdir().unwrap();
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let install_root = temp.path().join("install");
    let data_root = temp.path().join("data");
    fs::create_dir(&install_root).unwrap();
    fs::create_dir(&data_root).unwrap();
    fs::set_permissions(&install_root, fs::Permissions::from_mode(0o700)).unwrap();
    fs::set_permissions(&data_root, fs::Permissions::from_mode(0o700)).unwrap();
    let registry = PackageInstallRegistry::open(&install_root).unwrap();
    let installed = package("1.0.0", b"\0asm\x01\0\0\0");
    registry.install_package(&installed).unwrap();
    let authority = std::sync::Arc::new(PackageStorageAuthority::new(AppDataRoot::open(&data_root).unwrap()));
    authority.authorize(&installed.manifest.id).unwrap();
    let session = authority.file_session(&installed.manifest.id).unwrap();
    session.write_file("note", b"keep").unwrap();
    drop(session);

    let outcome = registry
        .uninstall_with_data_policy(&installed.manifest.id, UninstallDataPolicy::PreservePrivateData)
        .unwrap();
    assert!(outcome.registry_changed);
    assert!(!outcome.private_data_purged);
    authority.revoke(&installed.manifest.id).unwrap();
    authority.authorize(&installed.manifest.id).unwrap();
    assert_eq!(authority.file_session(&installed.manifest.id).unwrap().read_file("note").unwrap(), b"keep");
}

#[test]
fn explicit_private_data_purge_is_retryable_after_uninstall_and_closed_session() {
    let temp = tempfile::tempdir().unwrap();
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let install_root = temp.path().join("install");
    let data_root = temp.path().join("data");
    fs::create_dir(&install_root).unwrap();
    fs::create_dir(&data_root).unwrap();
    fs::set_permissions(&install_root, fs::Permissions::from_mode(0o700)).unwrap();
    fs::set_permissions(&data_root, fs::Permissions::from_mode(0o700)).unwrap();
    let registry = PackageInstallRegistry::open(&install_root).unwrap();
    let installed = package("1.0.0", b"\0asm\x01\0\0\0");
    registry.install_package(&installed).unwrap();
    let authority = std::sync::Arc::new(PackageStorageAuthority::new(AppDataRoot::open(&data_root).unwrap()));
    authority.authorize(&installed.manifest.id).unwrap();
    let session = authority.file_session(&installed.manifest.id).unwrap();
    session.write_file("note", b"delete").unwrap();

    let failed = registry.uninstall_with_data_policy(
        &installed.manifest.id,
        UninstallDataPolicy::PurgePrivateData(&authority),
    );
    assert!(failed.is_err());
    assert!(registry.verify_package(&installed).is_err());
    drop(session);
    authority.revoke(&installed.manifest.id).unwrap();
    let outcome = registry
        .uninstall_with_data_policy(&installed.manifest.id, UninstallDataPolicy::PurgePrivateData(&authority))
        .unwrap();
    assert!(!outcome.registry_changed);
    assert!(outcome.private_data_purged);
    authority.authorize(&installed.manifest.id).unwrap();
    assert_eq!(authority.file_session(&installed.manifest.id).unwrap().read_file("note"), Err(ming_app_host::app_data::AppDataError::NotFound));
}

#[test]
fn verified_registry_requires_active_publisher_signature() {
    let temp = tempfile::tempdir().unwrap();
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let key = SigningKey::from_bytes(&[11; 32]);
    PublisherTrustStore::provision(temp.path(), "release-2026", key.verifying_key().as_bytes()).unwrap();
    let registry = PackageInstallRegistry::open_verified(temp.path()).unwrap();
    let signed = signed_package("1.0.0", b"\0asm\x01\0\0\0", "release-2026", &key);
    assert_eq!(registry.install_package(&signed).unwrap(), InstallState::Installed);
    assert!(registry.install_package(&package("1.1.0", b"\0asm\x01\0\0\0\x00")).is_err());
}

#[test]
fn verified_registry_rechecks_revocation_after_reopen() {
    let temp = tempfile::tempdir().unwrap();
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let key = SigningKey::from_bytes(&[12; 32]);
    PublisherTrustStore::provision(temp.path(), "release-2026", key.verifying_key().as_bytes()).unwrap();
    let signed = signed_package("1.0.0", b"\0asm\x01\0\0\0", "release-2026", &key);
    let registry = PackageInstallRegistry::open_verified(temp.path()).unwrap();
    registry.install_package(&signed).unwrap();
    drop(registry);
    PublisherTrustStore::revoke(temp.path(), "release-2026").unwrap();
    let registry = PackageInstallRegistry::open_verified(temp.path()).unwrap();
    assert!(format!("{:#}", registry.verify_package(&signed).unwrap_err()).contains("PUBLISHER_KEY_REVOKED"));
}
