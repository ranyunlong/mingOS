//! Parent-side, identity-bound private file request handling.
//! Requests are bound to a parent-issued session, not a guest-supplied identity.
use crate::{
    app_data::{AppDataError, PrivateFileSession, PublicFileSession, MAX_PRIVATE_FILE_BYTES, validate_private_path},
    protocol::Reply,
};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde::Deserialize;
use std::sync::{Arc, Mutex, atomic::{AtomicUsize, Ordering}};

const MAX_REQUEST_BYTES: usize = 90_000;
const MAX_TRANSFER_BYTES: usize = 65_536;
const MAX_LIST_ENTRIES: usize = 32;
const MAX_STAGED_BYTES: usize = 64 * 1024 * 1024;
static STAGED_BYTES: AtomicUsize = AtomicUsize::new(0);

struct PendingUpload { path: String, total: usize, bytes: Vec<u8> }
impl Drop for PendingUpload {
    fn drop(&mut self) { STAGED_BYTES.fetch_sub(self.total, Ordering::AcqRel); }
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Request {
    Read { path: String, offset: u32, limit: u32 },
    Write { path: String, data: String },
    WriteBegin { path: String, total: u32 },
    WriteChunk { offset: u32, data: String },
    WriteCommit {},
    WriteAbort {},
    List { path: String, offset: u32, limit: u32 },
    Mkdir { path: String },
    RemoveDir { path: String },
    Delete { path: String },
    Rename { from: String, to: String },
}

/// Constructed only by the trusted parent with a session already bound to an application.
#[derive(Clone)]
enum FileCapability { Private(PrivateFileSession), Public(PublicFileSession) }
#[derive(Clone)]
pub struct PrivateFileRpc { capability: FileCapability, pending: Arc<Mutex<Option<PendingUpload>>> }

#[cfg(target_os = "linux")]
#[derive(Clone)]
pub struct PublicFileRpc { inner: PrivateFileRpc }

impl PrivateFileRpc {
    pub fn new(session: PrivateFileSession) -> Self { Self { capability: FileCapability::Private(session), pending: Arc::new(Mutex::new(None)) } }
    #[cfg(target_os = "linux")]
    pub(crate) fn new_public(session: PublicFileSession) -> Self { Self { capability: FileCapability::Public(session), pending: Arc::new(Mutex::new(None)) } }
    pub fn close(&self) {
        let mut pending = self.pending.lock().unwrap_or_else(|poison| poison.into_inner());
        *pending = None;
        match &self.capability {
            FileCapability::Private(session) => session.close(),
            FileCapability::Public(session) => session.close(),
        }
    }
    pub(crate) fn belongs_to(&self, package_id: &str) -> bool {
        match &self.capability {
            FileCapability::Private(session) => session.belongs_to(package_id),
            FileCapability::Public(session) => session.belongs_to(package_id),
        }
    }
    pub(crate) fn belongs_to_package(&self, package: &crate::package::Package) -> bool {
        match &self.capability {
            FileCapability::Private(session) => session.belongs_to_package(package),
            FileCapability::Public(session) => session.belongs_to_package(package),
        }
    }

    pub fn dispatch(&self, id: u64, payload: &str) -> Reply {
        let result = self.handle(payload);
        let (value, payload) = result.unwrap_or_else(|error| (error.code(), None));
        Reply { id, value, payload, fuel_grant: None }
    }

    fn handle(&self, payload: &str) -> Result<(i32, Option<String>), AppDataError> {
        if payload.len() > MAX_REQUEST_BYTES { return Err(AppDataError::InvalidArgument); }
        let request: Request = serde_json::from_str(payload).map_err(|_| AppDataError::InvalidArgument)?;
        match request {
            Request::Read { path, offset, limit } => {
                validate_private_path(&path)?;
                if limit as usize > MAX_TRANSFER_BYTES { return Err(AppDataError::InvalidArgument); }
                let bytes = match &self.capability { FileCapability::Private(s) => s.read_file(&path)?, FileCapability::Public(s) => s.read_file(&path)? };
                let start = (offset as usize).min(bytes.len());
                let end = start.saturating_add(limit as usize).min(bytes.len());
                let selected = &bytes[start..end];
                Ok((selected.len() as i32, (!selected.is_empty()).then(|| STANDARD.encode(selected))))
            }
            Request::Write { path, data } => {
                validate_private_path(&path)?;
                if data.len() > 87_384 { return Err(AppDataError::InvalidArgument); }
                let bytes = STANDARD.decode(data).map_err(|_| AppDataError::InvalidArgument)?;
                if bytes.len() > MAX_TRANSFER_BYTES { return Err(AppDataError::InvalidArgument); }
                let receipt = match &self.capability { FileCapability::Private(s) => s.write_file(&path, &bytes)?, FileCapability::Public(s) => s.write_file(&path, &bytes)? };
                Ok((receipt.length as i32, Some(if receipt.directory_synced { "1" } else { "0" }.to_owned())))
            }
            Request::WriteBegin { path, total } => {
                validate_private_path(&path)?;
                let total = total as usize;
                if total > MAX_PRIVATE_FILE_BYTES { return Err(AppDataError::InvalidArgument); }
                let mut pending = self.pending.lock().map_err(|_| AppDataError::Denied)?;
                if let Err(error) = match &self.capability { FileCapability::Private(s) => s.check(), FileCapability::Public(s) => s.check() } { *pending = None; return Err(error); }
                if pending.is_some() { return Err(AppDataError::InvalidArgument); }
                STAGED_BYTES.fetch_update(Ordering::AcqRel, Ordering::Acquire, |used| {
                    used.checked_add(total).filter(|sum| *sum <= MAX_STAGED_BYTES)
                }).map_err(|_| AppDataError::Io)?;
                let mut upload = PendingUpload { path, total, bytes: Vec::new() };
                upload.bytes.try_reserve_exact(total).map_err(|_| AppDataError::Io)?;
                *pending = Some(upload);
                Ok((0, None))
            }
            Request::WriteChunk { offset, data } => {
                let mut pending = self.pending.lock().map_err(|_| AppDataError::Denied)?;
                if let Err(error) = match &self.capability { FileCapability::Private(s) => s.check(), FileCapability::Public(s) => s.check() } { *pending = None; return Err(error); }
                let upload = pending.as_mut().ok_or(AppDataError::InvalidArgument)?;
                if data.len() > 87_384 { return Err(AppDataError::InvalidArgument); }
                let bytes = STANDARD.decode(data).map_err(|_| AppDataError::InvalidArgument)?;
                if bytes.is_empty() || bytes.len() > MAX_TRANSFER_BYTES || offset as usize != upload.bytes.len()
                    || bytes.len() > upload.total - upload.bytes.len() { return Err(AppDataError::InvalidArgument); }
                upload.bytes.extend_from_slice(&bytes);
                Ok((bytes.len() as i32, None))
            }
            Request::WriteCommit {} => {
                let mut pending = self.pending.lock().map_err(|_| AppDataError::Denied)?;
                if let Err(error) = match &self.capability { FileCapability::Private(s) => s.check(), FileCapability::Public(s) => s.check() } { *pending = None; return Err(error); }
                let upload = pending.as_ref().ok_or(AppDataError::InvalidArgument)?;
                if upload.bytes.len() != upload.total { return Err(AppDataError::InvalidArgument); }
                let result = match &self.capability { FileCapability::Private(s) => s.write_file(&upload.path, &upload.bytes), FileCapability::Public(s) => s.write_file(&upload.path, &upload.bytes) };
                *pending = None;
                let receipt = result?;
                Ok((0, Some(if receipt.directory_synced { "1" } else { "0" }.to_owned())))
            }
            Request::WriteAbort {} => {
                let mut pending = self.pending.lock().map_err(|_| AppDataError::Denied)?;
                if let Err(error) = match &self.capability { FileCapability::Private(s) => s.check(), FileCapability::Public(s) => s.check() } { *pending = None; return Err(error); }
                if pending.take().is_none() { return Err(AppDataError::InvalidArgument); }
                Ok((0, None))
            }
            Request::List { path, offset, limit } => {
                if !path.is_empty() { validate_private_path(&path)?; }
                if limit as usize > MAX_LIST_ENTRIES { return Err(AppDataError::InvalidArgument); }
                let entries = match &self.capability { FileCapability::Private(s) => s.list_directory(&path)?, FileCapability::Public(s) => s.list_directory(&path)? };
                let start = (offset as usize).min(entries.len());
                let end = start.saturating_add(limit as usize).min(entries.len());
                let page: Vec<_> = entries[start..end].iter().map(|entry| {
                    serde_json::json!({"name": entry.name, "isDirectory": entry.is_directory})
                }).collect();
                if page.is_empty() { return Ok((0, None)); }
                let bytes = serde_json::to_vec(&page).map_err(|_| AppDataError::Io)?;
                if bytes.len() > MAX_TRANSFER_BYTES { return Err(AppDataError::Io); }
                Ok((page.len() as i32, Some(STANDARD.encode(bytes))))
            }
            Request::Mkdir { path } => Ok((i32::from(match &self.capability { FileCapability::Private(s) => s.create_directory(&path)?, FileCapability::Public(s) => s.create_directory(&path)? }), None)),
            Request::RemoveDir { path } => Ok((i32::from(match &self.capability { FileCapability::Private(s) => s.delete_directory(&path)?, FileCapability::Public(s) => s.delete_directory(&path)? }), None)),
            Request::Delete { path } => Ok((i32::from(match &self.capability { FileCapability::Private(s) => s.delete_file(&path)?, FileCapability::Public(s) => s.delete_file(&path)? }), None)),
            Request::Rename { from, to } => Ok((i32::from(match &self.capability { FileCapability::Private(s) => s.rename_file(&from, &to)?, FileCapability::Public(s) => s.rename_file(&from, &to)? }), None)),
        }
    }
}

#[cfg(target_os = "linux")]
impl PublicFileRpc {
    pub fn new(session: PublicFileSession) -> Self { Self { inner: PrivateFileRpc::new_public(session) } }
    pub fn close(&self) { self.inner.close(); }
    pub(crate) fn belongs_to_package(&self, package: &crate::package::Package) -> bool { self.inner.belongs_to_package(package) }
    pub fn dispatch(&self, id: u64, payload: &str) -> Reply { self.inner.dispatch(id, payload) }
}
