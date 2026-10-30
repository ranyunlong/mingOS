//! Trusted-parent consent preparation. No guest request can approve itself.
use crate::{
    app_data::{AppDataError, AuthorizationReceipt, AuthorizationSnapshot, PackageBinding, PackageStorageAuthority, PrivateFileSession, PublicFileSession},
    package::Package,
};
use std::{fmt::Write as _, sync::Arc};

pub struct StorageConsent { authority: Arc<PackageStorageAuthority> }
pub struct PublicStorageConsent { authority: Arc<PackageStorageAuthority> }
pub struct GrantedStorage {
    pub package: Package,
    pub session: PrivateFileSession,
    /// Present only for a new approval; callers must check durability separately.
    pub receipt: Option<AuthorizationReceipt>,
}
pub struct GrantedPublicStorage {
    pub app_id: String,
    pub session: PublicFileSession,
    pub receipt: Option<AuthorizationReceipt>,
}
pub enum ConsentRequest { Allowed(GrantedStorage), Prompt(PendingConsent) }
pub enum PublicConsentRequest { Allowed(GrantedPublicStorage), Prompt(PendingPublicConsent) }
pub struct PendingConsent {
    authority: Arc<PackageStorageAuthority>,
    package: Package,
    replacement: bool,
}
pub struct PendingPublicConsent {
    authority: Arc<PackageStorageAuthority>,
    app_id: String,
    fingerprint: Option<[u8; 32]>,
}
pub struct StorageManagement {
    authority: Arc<PackageStorageAuthority>,
    app_id: String,
    snapshot: AuthorizationSnapshot,
}
pub struct PublicStorageManagement {
    authority: Arc<PackageStorageAuthority>,
    app_id: String,
    snapshot: AuthorizationSnapshot,
}

impl StorageConsent {
    pub fn new(authority: Arc<PackageStorageAuthority>) -> Self { Self { authority } }
    /// The ID and resulting confirmation must be controlled by the trusted parent.
    pub fn management(&self, app_id: &str) -> Result<Option<StorageManagement>, AppDataError> {
        Ok(self.authority.authorization_snapshot(app_id)?.map(|snapshot| StorageManagement {
            authority: self.authority.clone(), app_id: app_id.to_owned(), snapshot,
        }))
    }
    /// The package must be loaded by the trusted parent, not supplied by a guest.
    pub fn request(&self, package: Package) -> Result<ConsentRequest, AppDataError> {
        if !package.manifest.private_storage || package.manifest.sdk_version < 6 { return Err(AppDataError::Denied); }
        let status = self.authority.binding_status(&package.manifest.id, package.storage_fingerprint())?;
        match status {
            PackageBinding::Matched => {
                let session = self.authority.file_session_for_package(&package)?;
                Ok(ConsentRequest::Allowed(GrantedStorage { package, session, receipt: None }))
            }
            PackageBinding::Missing | PackageBinding::Conflict => Ok(ConsentRequest::Prompt(PendingConsent {
                authority: self.authority.clone(), package, replacement: status == PackageBinding::Conflict,
            })),
        }
    }
}
impl PublicStorageConsent {
    pub fn new(authority: Arc<PackageStorageAuthority>) -> Self { Self { authority } }
    /// Returns a trusted snapshot for viewing or revoking the current public authorization.
    #[cfg(target_os = "linux")]
    pub fn management(&self, app_id: &str) -> Result<Option<PublicStorageManagement>, AppDataError> {
        Ok(self.authority.public_authorization_snapshot(app_id)?.map(|snapshot| PublicStorageManagement {
            authority: self.authority.clone(), app_id: app_id.to_owned(), snapshot,
        }))
    }
    /// The app ID must come from the trusted installed catalog, not from a guest request.
    #[cfg(target_os = "linux")]
    pub fn request(&self, app_id: &str) -> Result<PublicConsentRequest, AppDataError> {
        if !crate::app_data::valid_package_id(app_id) { return Err(AppDataError::InvalidArgument); }
        match self.authority.public_session(app_id) {
            Ok(session) => Ok(PublicConsentRequest::Allowed(GrantedPublicStorage { app_id: app_id.to_owned(), session, receipt: None })),
            Err(AppDataError::Denied) => Ok(PublicConsentRequest::Prompt(PendingPublicConsent { authority: self.authority.clone(), app_id: app_id.to_owned(), fingerprint: None })),
            Err(error) => Err(error),
        }
    }
    #[cfg(target_os = "linux")]
    pub fn request_package(&self, package: &Package) -> Result<PublicConsentRequest, AppDataError> {
        if !package.manifest.public_storage || package.manifest.sdk_version < 6 { return Err(AppDataError::Denied); }
        match self.authority.public_binding_status(&package.manifest.id, package.storage_fingerprint())? {
            PackageBinding::Matched => {
                let session = self.authority.public_session_for_package(package)?;
                Ok(PublicConsentRequest::Allowed(GrantedPublicStorage { app_id: package.manifest.id.clone(), session, receipt: None }))
            }
            PackageBinding::Missing | PackageBinding::Conflict => Ok(PublicConsentRequest::Prompt(PendingPublicConsent {
                authority: self.authority.clone(), app_id: package.manifest.id.clone(), fingerprint: Some(package.storage_fingerprint()),
            })),
        }
    }
}
impl PendingPublicConsent {
    pub fn app_id(&self) -> &str { &self.app_id }
    pub fn allow(self) -> Result<GrantedPublicStorage, AppDataError> {
        let (receipt, session) = if let Some(fingerprint) = self.fingerprint {
            let receipt = self.authority.authorize_public_bound(&self.app_id, fingerprint)?;
            let session = self.authority.public_session_bound(&self.app_id, fingerprint)?;
            (receipt, session)
        } else {
            let receipt = self.authority.authorize_public(&self.app_id)?;
            let session = self.authority.public_session(&self.app_id)?;
            (receipt, session)
        };
        Ok(GrantedPublicStorage { app_id: self.app_id, session, receipt: Some(receipt) })
    }
    pub fn deny(self) {}
}
impl StorageManagement {
    pub fn app_id(&self) -> &str { &self.app_id }
    pub fn revocation_pending(&self) -> bool { self.snapshot.revocation_pending() }
    pub fn fingerprint(&self) -> Option<String> {
        self.snapshot.fingerprint.map(|fingerprint| {
            let mut text = String::with_capacity(64);
            for byte in fingerprint { write!(&mut text, "{byte:02x}").expect("string write"); }
            text
        })
    }
    /// A persistence error after revocation invalidates live access but may leave the
    /// previous disk record intact. Check both receipt fields before reporting durability.
    pub fn revoke(&self) -> Result<AuthorizationReceipt, AppDataError> {
        self.authority.revoke_snapshot(&self.app_id, &self.snapshot)
    }
}
impl PublicStorageManagement {
    pub fn app_id(&self) -> &str { &self.app_id }
    pub fn revocation_pending(&self) -> bool { self.snapshot.revocation_pending() }
    pub fn fingerprint(&self) -> Option<String> {
        self.snapshot.fingerprint.map(|fingerprint| {
            let mut text = String::with_capacity(64);
            for byte in fingerprint { write!(&mut text, "{byte:02x}").expect("string write"); }
            text
        })
    }
    pub fn revoke(&self) -> Result<AuthorizationReceipt, AppDataError> {
        self.authority.revoke_public_snapshot(&self.app_id, &self.snapshot)
    }
}
impl PendingConsent {
    pub fn app_id(&self) -> &str { &self.package.manifest.id }
    pub fn version(&self) -> &str { &self.package.manifest.version }
    pub fn is_replacement(&self) -> bool { self.replacement }
    pub fn fingerprint(&self) -> String {
        let mut text = String::with_capacity(64);
        for byte in self.package.storage_fingerprint() { write!(&mut text, "{byte:02x}").expect("string write"); }
        text
    }
    /// A conflict requires a separate explicit revocation; ordinary approval cannot replace it.
    pub fn allow(self) -> Result<GrantedStorage, AppDataError> {
        if self.replacement { return Err(AppDataError::Denied); }
        let receipt = self.authority.authorize_package(&self.package)?;
        let session = self.authority.file_session_for_package(&self.package)?;
        Ok(GrantedStorage { package: self.package, session, receipt: Some(receipt) })
    }
    pub fn deny(self) {}
}
