#![cfg(target_os = "linux")]

use ming_app_host::{
    app_data::{AppDataError, AppDataRoot, PackageStorageAuthority},
    package::{Manifest, Package},
    storage_consent::{PublicConsentRequest, PublicStorageConsent},
};
use std::{os::unix::fs::PermissionsExt, sync::Arc};

fn authority() -> (tempfile::TempDir, Arc<PackageStorageAuthority>) {
    let root = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let authority = Arc::new(
        PackageStorageAuthority::open_persistent(AppDataRoot::open(root.path()).unwrap())
            .unwrap(),
    );
    (root, authority)
}

#[test]
fn public_storage_requires_a_separate_user_grant_and_is_shared() {
    let (_root, authority) = authority();
    assert!(matches!(authority.public_session("dev.example.alpha"), Err(AppDataError::Denied)));

    authority.authorize_public("dev.example.alpha").unwrap();
    authority.authorize_public("dev.example.beta").unwrap();
    let alpha = authority.public_session("dev.example.alpha").unwrap();
    let beta = authority.public_session("dev.example.beta").unwrap();

    alpha.write_file("shared/note.txt", b"hello").unwrap_err();
    alpha.create_directory("shared").unwrap();
    alpha.write_file("shared/note.txt", b"hello").unwrap();
    assert_eq!(beta.read_file("shared/note.txt").unwrap(), b"hello");
    assert!(matches!(authority.file_session("dev.example.alpha"), Err(AppDataError::Denied)));
}

#[test]
fn public_revoke_invalidates_old_sessions_but_reopen_restores_only_granted_identity() {
    let (root, authority) = authority();
    authority.authorize_public("dev.example.alpha").unwrap();
    let old = authority.public_session("dev.example.alpha").unwrap();
    old.write_file("note.txt", b"saved").unwrap();

    authority.revoke_public("dev.example.alpha").unwrap();
    assert_eq!(old.read_file("note.txt"), Err(AppDataError::Denied));
    assert!(matches!(authority.public_session("dev.example.alpha"), Err(AppDataError::Denied)));

    drop(old);
    drop(authority);
    let reopened = Arc::new(
        PackageStorageAuthority::open_persistent(AppDataRoot::open(root.path()).unwrap())
            .unwrap(),
    );
    assert!(matches!(reopened.public_session("dev.example.alpha"), Err(AppDataError::Denied)));
    reopened.authorize_public("dev.example.alpha").unwrap();
    assert_eq!(reopened.public_session("dev.example.alpha").unwrap().read_file("note.txt").unwrap(), b"saved");
}

#[test]
fn public_sessions_are_path_bounded_and_do_not_expose_private_files() {
    let (_root, authority) = authority();
    authority.authorize_public("dev.example.alpha").unwrap();
    authority.authorize("dev.example.alpha").unwrap();
    let public = authority.public_session("dev.example.alpha").unwrap();

    for path in ["../outside", "/absolute", "a//b", "a/./b", "a/../b"] {
        assert_eq!(public.write_file(path, b"x"), Err(AppDataError::InvalidArgument));
    }
    assert_eq!(public.read_file("private-only"), Err(AppDataError::NotFound));
    assert_eq!(authority.write_file("dev.example.alpha", "private-only", b"secret").unwrap().length, 6);
    assert_eq!(public.read_file("private-only"), Err(AppDataError::NotFound));
}

fn package(bytes: &[u8]) -> Package {
    Package {
        manifest: Manifest {
            schema_version: 1,
            id: "dev.example.public".into(),
            version: "1.0.0".into(),
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
fn public_authorization_is_bound_to_package_content_and_reapproval_rotates_sessions() {
    let (root, authority) = authority();
    let consent = PublicStorageConsent::new(authority.clone());
    let first = package(b"first-package");
    let pending = match consent.request_package(&first).unwrap() {
        PublicConsentRequest::Prompt(pending) => pending,
        PublicConsentRequest::Allowed(_) => panic!("new package must prompt"),
    };
    let granted = pending.allow().unwrap();
    let old = granted.session;
    old.write_file("shared.txt", b"old").unwrap();

    let changed = package(b"changed-package");
    assert!(matches!(
        authority.public_session_for_package(&changed),
        Err(AppDataError::Denied)
    ));
    let pending = match consent.request_package(&changed).unwrap() {
        PublicConsentRequest::Prompt(pending) => pending,
        PublicConsentRequest::Allowed(_) => panic!("changed package must prompt again"),
    };
    let granted = pending.allow().unwrap();
    assert_eq!(old.read_file("shared.txt"), Err(AppDataError::Denied));
    assert_eq!(granted.session.read_file("shared.txt").unwrap(), b"old");

    drop(granted.session);
    drop(old);
    drop(consent);
    drop(authority);
    let reopened = std::sync::Arc::new(
        PackageStorageAuthority::open_persistent(AppDataRoot::open(root.path()).unwrap())
            .unwrap(),
    );
    assert!(reopened.public_session_for_package(&package(b"first-package")).is_err());
    assert!(reopened.public_session_for_package(&package(b"changed-package")).is_ok());
}
