#![cfg(target_os = "linux")]

use ming_app_host::{package::{Manifest, Package, load_package}, package_install::{InstallState, PackageInstallRegistry}};
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
