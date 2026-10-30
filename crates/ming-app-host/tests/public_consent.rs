#![cfg(target_os = "linux")]

use ming_app_host::{
    app_data::{AppDataRoot, AppDataError, PackageStorageAuthority},
    storage_consent::{PublicConsentRequest, PublicStorageConsent},
};
use std::{os::unix::fs::PermissionsExt, sync::Arc};

#[test]
fn public_consent_is_explicit_and_denial_does_not_create_access() {
    let root = tempfile::tempdir().unwrap();
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let authority = Arc::new(PackageStorageAuthority::open_persistent(AppDataRoot::open(root.path()).unwrap()).unwrap());
    let consent = PublicStorageConsent::new(authority.clone());

    let pending = match consent.request("dev.example.alpha").unwrap() {
        PublicConsentRequest::Prompt(pending) => pending,
        PublicConsentRequest::Allowed(_) => panic!("public storage must not be implicit"),
    };
    pending.deny();
    assert!(matches!(authority.public_session("dev.example.alpha"), Err(AppDataError::Denied)));

    let pending = match consent.request("dev.example.alpha").unwrap() {
        PublicConsentRequest::Prompt(pending) => pending,
        PublicConsentRequest::Allowed(_) => panic!("missing grant must prompt"),
    };
    let granted = pending.allow().unwrap();
    assert!(granted.receipt.unwrap().persisted);
    assert!(matches!(consent.request("../bad"), Err(AppDataError::InvalidArgument)));
    assert!(matches!(consent.request("dev.example.alpha"), Ok(PublicConsentRequest::Allowed(_))));
}
