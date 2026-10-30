#![cfg(target_os = "linux")]

use base64::{Engine as _, engine::general_purpose::STANDARD};
use ed25519_dalek::{Signer, SigningKey};
use ming_app_host::{package::{Manifest, Package}, publisher::PublisherTrustStore};
use std::{fs, os::unix::fs::PermissionsExt};

fn unsigned_package() -> Package {
    Package {
        manifest: Manifest {
            schema_version: 1,
            id: "dev.example.signed".into(),
            version: "1.0.0".into(),
            sdk_version: 6,
            entry: "main.wasm".into(),
            private_storage: false,
            public_storage: false,
            publisher_key_id: None,
            publisher_signature: None,
        },
        bytes: b"\0asm\x01\0\0\0".to_vec(),
    }
}

fn signed_package(key_id: &str, signing_key: &SigningKey) -> Package {
    let mut package = unsigned_package();
    let signature = signing_key.sign(&package.signature_message(key_id).unwrap());
    package.manifest.publisher_key_id = Some(key_id.into());
    package.manifest.publisher_signature = Some(STANDARD.encode(signature.to_bytes()));
    package
}

#[test]
fn active_publisher_signature_is_verified_over_package_identity_and_bytes() {
    let temp = tempfile::tempdir().unwrap();
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let signing_key = SigningKey::from_bytes(&[7; 32]);
    let package = signed_package("release-2026", &signing_key);
    PublisherTrustStore::provision(temp.path(), "release-2026", signing_key.verifying_key().as_bytes()).unwrap();
    let store = PublisherTrustStore::open(temp.path()).unwrap();
    assert!(store.verify_package(&package).is_ok());

    let mut tampered = package;
    tampered.bytes.push(0);
    assert!(store.verify_package(&tampered).is_err());
}

#[test]
fn signature_metadata_must_be_complete() {
    let temp = tempfile::tempdir().unwrap();
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let signing_key = SigningKey::from_bytes(&[8; 32]);
    PublisherTrustStore::provision(temp.path(), "release-2026", signing_key.verifying_key().as_bytes()).unwrap();
    let store = PublisherTrustStore::open(temp.path()).unwrap();
    let mut package = unsigned_package();
    package.manifest.publisher_key_id = Some("release-2026".into());
    assert!(store.verify_package(&package).is_err());
}

#[test]
fn key_rotation_revokes_old_signatures_and_accepts_new_key() {
    let temp = tempfile::tempdir().unwrap();
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let old_key = SigningKey::from_bytes(&[9; 32]);
    let new_key = SigningKey::from_bytes(&[10; 32]);
    PublisherTrustStore::provision(temp.path(), "release-old", old_key.verifying_key().as_bytes()).unwrap();
    let old_package = signed_package("release-old", &old_key);
    let store = PublisherTrustStore::open(temp.path()).unwrap();
    assert!(store.verify_package(&old_package).is_ok());
    drop(store);

    PublisherTrustStore::provision(temp.path(), "release-new", new_key.verifying_key().as_bytes()).unwrap();
    PublisherTrustStore::revoke(temp.path(), "release-old").unwrap();
    let store = PublisherTrustStore::open(temp.path()).unwrap();
    let error = store.verify_package(&old_package).unwrap_err();
    assert!(format!("{error:#}").contains("PUBLISHER_KEY_REVOKED"));
    assert!(store.verify_package(&signed_package("release-new", &new_key)).is_ok());
}

#[test]
fn malformed_keyring_is_rejected_without_fallback() {
    let temp = tempfile::tempdir().unwrap();
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let keyring = temp.path().join("trusted-publishers-v1");
    fs::write(&keyring, "ming-trusted-publishers-v1\nrelease-2026\tactive\tbad\n").unwrap();
    fs::set_permissions(&keyring, fs::Permissions::from_mode(0o600)).unwrap();
    let error = PublisherTrustStore::open(temp.path()).unwrap_err();
    assert!(format!("{error:#}").contains("PUBLISHER_KEYRING_KEY"));
}
